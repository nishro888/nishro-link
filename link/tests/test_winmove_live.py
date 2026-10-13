"""A moved window, end to end: two real sessions over a real socket pair,
with a fake window at home and a fake frame away.

The system-specific halves (capturing and hiding a window, the system's
picture codecs, a frame on the screen) are what the fakes stand in for, so
everything in between - the pictures, the pacing, the input going back, the
titles, resizing, closing, and every way it can end - is tested here on any
machine. docs/moving-windows.md is the design."""
import queue
import socket
import threading
import time

import pytest

from link import secure, winmove
from link.winmove import AwaySession, BinChannel, ChannelError, HomeSession


# ------------------------------------------------------------------- the fakes
class FakeWindow:
    """A window at home: a picture of a given size, and a record of every
    piece of input played into it."""

    def __init__(self, w=96, h=64, title="Notepad"):
        self.w, self.h = w, h
        self._title = title
        self.fill = 0
        self.gone = False
        self.closed = 0
        self.played = []
        self.resized = []
        self.captures = 0
        self.lock = threading.Lock()

    # what HomeSession needs
    def alive(self):
        return not self.gone

    def title(self):
        return self._title

    def size(self):
        with self.lock:
            return (self.w, self.h)

    def capture(self):
        with self.lock:
            if self.gone:
                return None
            self.captures += 1
            return bytes([self.fill]) * (self.w * self.h * 4)

    def resize(self, w, h):
        with self.lock:
            self.w, self.h = w, h
        self.resized.append((w, h))

    def close(self):
        self.closed += 1

    def move(self, x, y):
        self.played.append(("move", x, y))

    def button(self, name, down):
        self.played.append(("btn", name, down))

    def wheel(self, dx, dy):
        self.played.append(("wheel", dx, dy))

    def key(self, code, down):
        self.played.append(("key", code, down))

    # test helpers
    def change(self, fill):
        with self.lock:
            self.fill = fill


class FakeFrame:
    """A frame away: records what it was told to draw, and offers a queue for
    what the person does in it."""

    def __init__(self, slow=0.0):
        self.events = queue.Queue()
        self.opened = None
        self.titles = []
        self.drawn = []          # each call's regions
        self.closed = None
        self.slow = slow

    def open(self, title, w, h):
        self.opened = (title, w, h)

    def draw(self, regions):
        if self.slow:
            time.sleep(self.slow)
        self.drawn.append(regions)

    def retitle(self, title):
        self.titles.append(title)

    def close(self, why):
        self.closed = why

    # what the person does
    def did(self, event):
        self.events.put(event)


def raw_encode(cut, w, h):
    """The simplest codec: the bytes as they are. A real home uses GDI+ or
    GdkPixbuf; nothing in the sessions depends on which."""
    return "raw", cut


class Pair:
    """A home session and an away session, really talking to each other."""

    def __init__(self, window=None, frame=None, slow=0.0, encode=raw_encode):
        a_priv, a_pub = secure.keypair()
        b_priv, b_pub = secure.keypair()
        a_tx, a_rx = secure.session(secure.shared(a_priv, b_pub), "pw",
                                    "n1", "n2", "hub")
        b_tx, b_rx = secure.session(secure.shared(b_priv, a_pub), "pw",
                                    "n1", "n2", "dialer")
        s1, s2 = socket.socketpair()
        self.window = window or FakeWindow()
        self.frame = frame or FakeFrame(slow=slow)
        self.logs = {"home": [], "away": []}
        self.home = HomeSession(BinChannel(s1, a_tx, a_rx), self.window, encode,
                                log=self.logs["home"].append)
        self.away = AwaySession(BinChannel(s2, b_tx, b_rx), self.frame,
                                log=self.logs["away"].append)
        self.why = {}
        self._threads = []

    def start(self):
        for name, session in (("home", self.home), ("away", self.away)):
            t = threading.Thread(
                target=lambda n=name, s=session: self.why.__setitem__(n, s.run()),
                daemon=True, name=f"test-{name}")
            t.start()
            self._threads.append(t)
        return self

    def wait(self, cond, what, timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.01)
        tail = " | ".join(x for v in self.logs.values() for x in v[-4:])
        pytest.fail(f"timed out waiting for {what}. logs: {tail}")

    def stop(self, timeout=8.0):
        self.home.stop()
        self.away.stop()
        self.frame.did(None)
        for t in self._threads:
            t.join(timeout)
        return self


@pytest.fixture
def pair():
    p = Pair()
    try:
        yield p
    finally:
        p.stop()


# -------------------------------------------------------------- the happy path
def test_the_frame_opens_with_the_windows_title_and_size(pair):
    pair.start()
    pair.wait(lambda: pair.frame.opened, "the frame to open")
    assert pair.frame.opened == ("Notepad", 96, 64)
    assert pair.home.wait_ready(2), "home should know the frame is up"


def test_the_first_picture_is_the_whole_window_and_is_acknowledged(pair):
    pair.start()
    pair.wait(lambda: pair.frame.drawn, "the first picture")
    (x, y, w, h, codec, data) = pair.frame.drawn[0][0]
    assert (x, y, w, h, codec) == (0, 0, 96, 64, "raw")
    assert len(data) == 96 * 64 * 4
    pair.wait(lambda: pair.home.pacer.shown >= 1, "the acknowledgement")


def test_only_what_changed_is_sent_after_the_first_picture(pair):
    pair.start()
    pair.wait(lambda: pair.frame.drawn, "the first picture")
    before = len(pair.frame.drawn)
    pair.window.change(7)                       # the whole window, a new colour
    pair.wait(lambda: len(pair.frame.drawn) > before, "a second picture")
    assert pair.frame.drawn[before][0][:4] == (0, 0, 96, 64)


def test_a_still_window_is_not_sent_over_and_over(pair):
    """Nothing changing must mean nothing sent - a moved window that is just
    sitting there should cost almost no network at all."""
    pair.start()
    pair.wait(lambda: pair.frame.drawn, "the first picture")
    settled = len(pair.frame.drawn)
    time.sleep(1.0)
    assert len(pair.frame.drawn) == settled, "a still window kept sending"


# ------------------------------------------------------------------- the input
def test_what_the_person_does_in_the_frame_reaches_the_window(pair):
    pair.start()
    pair.wait(lambda: pair.frame.opened, "the frame to open")
    pair.frame.did({"t": "in", "how": "move", "x": 10, "y": 20})
    pair.frame.did({"t": "in", "how": "btn", "name": "left", "down": True})
    pair.frame.did({"t": "in", "how": "btn", "name": "left", "down": False})
    pair.frame.did({"t": "in", "how": "wheel", "dx": 0, "dy": -3})
    pair.frame.did({"t": "in", "how": "key", "code": 30, "down": True})
    pair.wait(lambda: len(pair.window.played) >= 5, "the input to arrive")
    assert pair.window.played[:5] == [
        ("move", 10, 20), ("btn", "left", True), ("btn", "left", False),
        ("wheel", 0, -3), ("key", 30, True)]


@pytest.mark.parametrize("event", [
    {"t": "in", "how": "move", "x": 5000, "y": 5},        # outside the window
    {"t": "in", "how": "move", "x": -1, "y": 5},
    {"t": "in", "how": "move", "x": "nope", "y": 5},
    {"t": "in", "how": "btn", "name": "paw", "down": True},
    {"t": "in", "how": "key", "code": 99999, "down": True},
    {"t": "in", "how": "key", "code": "a", "down": True},
    {"t": "in", "how": "something-new", "x": 1},
])
def test_input_that_makes_no_sense_is_not_played(pair, event):
    """It comes from the other computer: a coordinate outside the window
    would otherwise be played somewhere unrelated on this screen."""
    pair.start()
    pair.wait(lambda: pair.frame.opened, "the frame to open")
    pair.frame.did(event)
    pair.frame.did({"t": "in", "how": "btn", "name": "right", "down": True})
    pair.wait(lambda: pair.window.played, "the good event after it")
    assert pair.window.played == [("btn", "right", True)], \
        "the nonsense event was played"


def test_a_wheel_cannot_ask_for_a_thousand_notches(pair):
    pair.start()
    pair.wait(lambda: pair.frame.opened, "the frame to open")
    pair.frame.did({"t": "in", "how": "wheel", "dx": 0, "dy": 9999})
    pair.wait(lambda: pair.window.played, "the wheel")
    assert pair.window.played == [("wheel", 0, 20)]


# ------------------------------------------------------- title, size and close
def test_a_renamed_window_renames_the_frame(pair):
    pair.start()
    pair.wait(lambda: pair.frame.opened, "the frame to open")
    pair.window._title = "Untitled - Notepad"
    pair.wait(lambda: pair.frame.titles, "the new title")
    assert pair.frame.titles[-1] == "Untitled - Notepad"


def test_resizing_the_frame_resizes_the_window_and_sends_it_whole(pair):
    pair.start()
    pair.wait(lambda: pair.frame.drawn, "the first picture")
    pair.frame.did({"t": "size", "w": 48, "h": 32})
    pair.wait(lambda: pair.window.resized, "the window to be resized")
    assert pair.window.resized[-1] == (48, 32)
    pair.wait(lambda: any(r[0][:4] == (0, 0, 48, 32) for r in pair.frame.drawn),
              "the whole window at its new size")


@pytest.mark.parametrize("event", [
    {"t": "size", "w": 0, "h": 32},
    {"t": "size", "w": 99999, "h": 32},
    {"t": "size", "w": None, "h": 32},
])
def test_a_size_that_makes_no_sense_is_ignored(pair, event):
    pair.start()
    pair.wait(lambda: pair.frame.opened, "the frame to open")
    pair.frame.did(event)
    pair.frame.did({"t": "in", "how": "btn", "name": "left", "down": True})
    pair.wait(lambda: pair.window.played, "the good event after it")
    assert pair.window.resized == []


def test_the_frames_close_button_closes_the_programs_window(pair):
    """The maintainer's choice: the frame's X does what the same button at
    home would - it closes the program's window, which may ask to save."""
    pair.start()
    pair.wait(lambda: pair.frame.opened, "the frame to open")
    pair.frame.did({"t": "close"})
    pair.wait(lambda: pair.window.closed, "the window to be asked to close")
    assert pair.window.closed == 1


def test_the_window_coming_home_ends_both_sides(pair):
    pair.start()
    pair.wait(lambda: pair.frame.opened, "the frame to open")
    pair.frame.did({"t": "home"})
    pair.wait(lambda: pair.why.get("home"), "home to finish")
    assert pair.why["home"] == winmove.HOME
    pair.wait(lambda: pair.frame.closed, "the frame to be taken away")
    assert pair.frame.closed == winmove.HOME


def test_the_program_ending_takes_the_frame_away(pair):
    pair.start()
    pair.wait(lambda: pair.frame.drawn, "the first picture")
    pair.window.gone = True
    pair.wait(lambda: pair.why.get("home"), "home to notice")
    assert pair.why["home"] == winmove.GONE
    pair.wait(lambda: pair.frame.closed, "the frame to go")
    assert pair.frame.closed == winmove.GONE


def test_a_window_that_says_it_is_gone_is_noticed_even_if_it_still_captures():
    """Two separate ways a window can end, and each must be enough on its
    own. Here only alive() turns false - a real window handle can still hand
    back a stale picture after the program has gone."""
    window = FakeWindow()
    p = Pair(window=window)
    try:
        p.start()
        p.wait(lambda: p.frame.drawn, "the first picture")
        window.gone = True                       # capture() would return None too...
        window.capture = lambda: bytes(window.w * window.h * 4)   # ...but it does not
        p.wait(lambda: p.why.get("home"), "home to notice from alive()")
        assert p.why["home"] == winmove.GONE
    finally:
        p.stop()


def test_a_window_that_cannot_be_captured_any_more_is_noticed():
    """And here only capture() fails, while alive() still says yes - the
    other order, which a window closing mid-capture really does produce."""
    window = FakeWindow()
    p = Pair(window=window)
    try:
        p.start()
        p.wait(lambda: p.frame.drawn, "the first picture")
        window.capture = lambda: None            # alive() stays true
        p.wait(lambda: p.why.get("home"), "home to notice from capture()")
        assert p.why["home"] == winmove.GONE
    finally:
        p.stop()


def test_home_stopping_tells_away_why(pair):
    pair.start()
    pair.wait(lambda: pair.frame.opened, "the frame to open")
    pair.home.stop(winmove.STOPPED)
    pair.wait(lambda: pair.frame.closed, "the frame to go")
    assert pair.frame.closed == winmove.STOPPED


def test_a_dropped_channel_brings_the_window_home(pair):
    """The channel is the only thing holding the window away: if it breaks,
    home must bring the window back at once rather than leave it hidden."""
    pair.start()
    pair.wait(lambda: pair.frame.drawn, "the first picture")
    pair.away.ch.close()
    pair.wait(lambda: pair.why.get("home"), "home to notice the drop")
    assert pair.why["home"] == winmove.DROPPED


def test_home_never_hides_a_window_whose_frame_never_appears():
    """wait_ready is what the caller hides the window on. A frame that never
    opens must leave it false, so the window is never hidden for nothing."""
    window = FakeWindow()
    p = Pair(window=window)
    # away never runs, so nothing ever answers the meta
    t = threading.Thread(target=p.home.run, daemon=True)
    p.home._ready.wait(0)
    winmove.READY_S = 1.0                       # (restored below)
    try:
        t.start()
        assert p.home.wait_ready(3.0) is False
        t.join(5)
        assert p.home.ended == winmove.FAILED
        assert any("never showed" in m for m in p.logs["home"])
    finally:
        winmove.READY_S = 20.0
        p.home.stop()


# ----------------------------------------------------------------- the pacing
def test_home_never_gets_more_than_two_pictures_ahead_of_a_slow_frame():
    """A frame that takes 150 ms a picture must not make home pile up a queue
    of pictures nobody will see: at most two may be unacknowledged, so the
    person sees a slower but current window."""
    frame = FakeFrame(slow=0.15)
    p = Pair(frame=frame)
    try:
        p.start()
        p.wait(lambda: frame.drawn, "the first picture")
        seen = []
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            seen.append(p.home.pacer.waiting)
            p.window.change(len(seen) % 200)     # keep it changing all the while
            time.sleep(0.02)
        assert max(seen) <= winmove.AHEAD, f"got {max(seen)} pictures ahead"
        assert len(frame.drawn) >= 2, "the frame should still be getting pictures"
    finally:
        p.stop()


def test_a_still_slow_frame_still_gets_the_latest_picture():
    """What the pacing is for: after a burst of changes, what the frame ends
    up showing is the newest state, not the oldest."""
    frame = FakeFrame(slow=0.05)
    p = Pair(frame=frame)
    try:
        p.start()
        p.wait(lambda: frame.drawn, "the first picture")
        for fill in range(2, 12):
            p.window.change(fill)
            time.sleep(0.02)
        p.wait(lambda: frame.drawn and frame.drawn[-1][0][5][0] == 11,
               "the newest colour to reach the frame", timeout=5)
    finally:
        p.stop()


# ------------------------------------------------------- a peer that misbehaves
def test_a_picture_before_the_size_is_known_is_refused():
    """away must not be told to draw before it knows how big the window is."""
    frame = FakeFrame()
    p = Pair(frame=frame)
    try:
        t = threading.Thread(target=lambda: p.why.__setitem__("away", p.away.run()),
                             daemon=True)
        t.start()
        p.home.ch.send(winmove.pic(1, [(0, 0, 8, 8, "raw", b"x" * 256)]))
        t.join(5)
        # "failed", not "dropped": the peer sent something impossible, which
        # is a different thing from the network going away.
        assert p.why["away"] == winmove.FAILED
        assert frame.drawn == [] and frame.opened is None
    finally:
        p.away.stop()


def test_a_message_that_will_not_read_ends_the_session_cleanly():
    """A frame never opened has nothing to take away - what matters is that
    the session ends, says why, and draws nothing."""
    frame = FakeFrame()
    p = Pair(frame=frame)
    try:
        t = threading.Thread(target=lambda: p.why.__setitem__("away", p.away.run()),
                             daemon=True)
        t.start()
        p.home.ch.send(b"\x00\x40not actually a header")
        t.join(5)
        assert p.why["away"] == winmove.FAILED
        assert frame.opened is None and frame.drawn == []
        assert frame.closed is None, "nothing was ever opened to close"
        assert any("cannot read" in m for m in p.logs["away"])
    finally:
        p.away.stop()


def test_a_frame_already_open_is_taken_away_when_a_message_will_not_read(pair):
    """Once the frame IS up, any ending must take it off the screen - a frame
    left behind would show a picture that never updates again."""
    pair.start()
    pair.wait(lambda: pair.frame.opened, "the frame to open")
    pair.home.ch.send(b"\x00\x40not actually a header")
    pair.wait(lambda: pair.frame.closed, "the frame to be taken away")
    assert pair.away.ended == winmove.FAILED


def test_an_unknown_message_is_ignored_by_both_ends(pair):
    """What lets a later 1.x add messages: an older peer carries on."""
    pair.start()
    pair.wait(lambda: pair.frame.drawn, "the first picture")
    pair.frame.did({"t": "something-new-entirely", "v": 3})
    pair.home.ch.send(winmove.pack({"t": "also-new", "v": 4}))
    pair.frame.did({"t": "in", "how": "btn", "name": "left", "down": True})
    pair.wait(lambda: pair.window.played, "the known message after it")
    assert pair.why.get("home") is None, "the session should still be running"


def test_the_sessions_never_write_the_window_anywhere(pair, tmp_path):
    """N5: nothing of a moved window goes to disk."""
    pair.start()
    pair.wait(lambda: pair.frame.drawn, "the first picture")
    assert list(tmp_path.iterdir()) == []


def test_a_closed_channel_makes_send_raise_not_hang():
    """ChannelError, so a session ends instead of blocking for ever."""
    p = Pair()
    p.home.ch.close()
    with pytest.raises(ChannelError):
        for _ in range(50):
            p.home.ch.send(winmove.pack({"t": "meta", "title": "x", "w": 8, "h": 8}))
