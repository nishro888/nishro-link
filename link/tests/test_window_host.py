"""The whole Windows path for real: the service starts the helper, the helper
hides and captures a real window, and the pictures come back down the relay.

This is the test that would have caught anything the pieces got wrong between
them - the only fakes left are the other computer (a socket) and the frame
(replaced by reading the frames as they arrive).

Skipped off Windows, and where there is no desktop.
"""
import socket
import subprocess
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

if sys.platform == "win32":
    from link import picture, winmove, window_service
    from link.window_win import GWL_EXSTYLE, WS_EX_LAYERED, user32

from test_window_win import Subject, TITLE          # noqa: E402


@pytest.fixture(autouse=True)
def own_record(tmp_path, monkeypatch):
    """The helper runs as its own process and would otherwise write its
    record of hidden windows into the person's real one. Give it a folder of
    its own - and the helper inherits this through the environment."""
    monkeypatch.setenv("NISHRO_LINK_MOVED_RECORD",
                       str(tmp_path / "moved-windows.json"))


@pytest.fixture
def subject():
    try:
        s = Subject()
    except Exception as e:
        pytest.skip(f"could not put a window on the screen: {e}")
    try:
        yield s
    finally:
        s.close()


class FarEnd:
    """The other computer: reads the frames a moved window sends, and can
    answer as a frame would."""

    def __init__(self, ch):
        self.ch = ch
        self.frames = []
        self.pictures = []
        self.stop = False
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        try:
            while not self.stop:
                header, payload = winmove.unpack(self.ch.recv())
                self.frames.append(header)
                if header.get("t") == "pic":
                    self.pictures.append(winmove.regions(header, payload))
                    # Acknowledge, or home stops after two (the pacing).
                    self.ch.send(winmove.pack({"t": "ack",
                                               "n": header.get("n")}))
        except winmove.ChannelError:
            pass

    def meta(self):
        return next((f for f in self.frames if f.get("t") == "meta"), None)

    def say_ready(self):
        self.ch.send(winmove.pack({"t": "ready"}))

    def wait(self, cond, what, timeout=30.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.05)
        pytest.fail(f"timed out waiting for {what}. frames so far: "
                    f"{[f.get('t') for f in self.frames]}")


def lend_real(subject, log=None):
    """Start the real helper for one of our windows, as the service does."""
    far_sock, near_sock = socket.socketpair()
    far = winmove.plain_channel(far_sock)
    near = winmove.plain_channel(near_sock)
    said = []
    done = []

    def run():
        done.append(window_service.attach(
            near, "lend", str(subject.hwnd), "the-other-computer",
            log=(log or said.append)))
    threading.Thread(target=run, daemon=True, name="service-side").start()
    return FarEnd(far), said, done


# ------------------------------------------------------------------ the path
def test_a_real_window_is_offered_captured_and_hidden(subject):
    """Everything at once: the helper starts in this session, says what the
    window is, hides it only once the far end says the frame is up, and sends
    pictures of it while it is hidden."""
    far, said, done = lend_real(subject)
    far.wait(lambda: far.meta(), "the window's name and size")
    meta = far.meta()
    assert meta["title"] == TITLE
    assert meta["w"] >= 320 and meta["h"] >= 240

    # Not hidden yet: nothing has said a frame exists.
    time.sleep(0.5)
    assert not user32.GetWindowLongW(subject.hwnd, GWL_EXSTYLE) & WS_EX_LAYERED, \
        "the window was hidden before the frame was up"

    far.say_ready()
    far.wait(lambda: user32.GetWindowLongW(subject.hwnd, GWL_EXSTYLE)
             & WS_EX_LAYERED, "the window to be hidden once the frame is up")
    far.wait(lambda: far.pictures, "the first picture")

    first = far.pictures[0][0]
    x, y, w, h, codec, data = first
    assert (x, y) == (0, 0) and codec == "png"
    assert picture.size(data) == (w, h)
    assert (w, h) == (meta["w"], meta["h"]), "the first picture is the whole window"

    # It is still drawing while hidden: change its colour and watch.
    before = len(far.pictures)
    subject.tell("colour 10a0f0")
    far.wait(lambda: len(far.pictures) > before,
             "a picture of the change, while hidden")

    # And it comes home when the far end asks.
    far.ch.send(winmove.pack({"t": "home"}))
    far.wait(lambda: done, "the window to come home", timeout=20)
    assert done == [""]
    assert not user32.GetWindowLongW(subject.hwnd, GWL_EXSTYLE) & WS_EX_LAYERED, \
        "the window was left hidden"


def test_what_the_far_end_does_reaches_the_real_window(subject):
    far, said, done = lend_real(subject)
    far.wait(lambda: far.meta(), "the window's name")
    far.say_ready()
    far.wait(lambda: far.pictures, "the first picture")
    subject.saw()                       # forget everything so far
    far.ch.send(winmove.pack({"t": "in", "how": "move", "x": 60, "y": 90}))
    far.ch.send(winmove.pack({"t": "in", "how": "btn", "name": "left",
                              "down": True}))
    far.ch.send(winmove.pack({"t": "in", "how": "btn", "name": "left",
                              "down": False}))
    end = time.monotonic() + 15
    got = []
    while time.monotonic() < end and not any(e[0] == "left-up" for e in got):
        got += subject.saw()
        time.sleep(0.1)
    kinds = [e[0] for e in got]
    assert "left-down" in kinds and "left-up" in kinds, f"got {got}"
    far.ch.send(winmove.pack({"t": "home"}))
    far.wait(lambda: done, "the window to come home", timeout=20)


def test_the_window_comes_home_if_the_connection_breaks(subject):
    """The one thing that must never leave a window invisible."""
    far, said, done = lend_real(subject)
    far.wait(lambda: far.meta(), "the window's name")
    far.say_ready()
    far.wait(lambda: user32.GetWindowLongW(subject.hwnd, GWL_EXSTYLE)
             & WS_EX_LAYERED, "the window to be hidden")
    far.stop = True
    far.ch.close()                      # as if the other computer vanished
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not user32.GetWindowLongW(subject.hwnd, GWL_EXSTYLE) & WS_EX_LAYERED:
            break
        time.sleep(0.1)
    assert not user32.GetWindowLongW(subject.hwnd, GWL_EXSTYLE) & WS_EX_LAYERED, \
        "a broken connection left the window hidden"


def test_a_window_that_cannot_move_is_refused_with_a_reason(subject):
    """And no frame is promised for it."""
    far_sock, near_sock = socket.socketpair()
    near = winmove.plain_channel(near_sock)
    user32.ShowWindow(subject.hwnd, 6)              # minimized: it stops drawing
    time.sleep(0.8)
    try:
        why = window_service.attach(near, "lend", str(subject.hwnd), "aio")
        assert "minimized" in why, why
    finally:
        user32.ShowWindow(subject.hwnd, 9)
        far_sock.close()
        near.close()


# -------------------------------------------------- the helper on its own
def test_the_helper_lists_the_windows_that_could_move(subject):
    """What the tray's menu is built from, asked of this session."""
    out = subprocess.run(
        window_service.self_command() + ["--list-windows"],
        capture_output=True, text=True, timeout=60,
        creationflags=0x08000000)
    assert out.returncode == 0, out.stderr[-400:]
    assert TITLE in out.stdout, f"our window was not listed: {out.stdout[:400]}"


def test_the_helper_says_what_it_cannot_do_rather_than_failing(subject):
    """Asked for a window that does not exist: a reason, and nothing hidden."""
    far_sock, near_sock = socket.socketpair()
    near = winmove.plain_channel(near_sock)
    try:
        why = window_service.attach(near, "lend", "999999999", "aio")
        assert why and "gone" in why, why
    finally:
        far_sock.close()
        near.close()
