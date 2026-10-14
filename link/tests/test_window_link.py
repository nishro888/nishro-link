"""A window moved over the REAL link: two nodes, the handshake, the lot.

Everything but the system's own half - the window at home and the frame away
are fakes, as in test_winmove_live - so this is about the wiring: the
connection dialled and proved like a link and never mistaken for one, the
switch that has to be on at both ends, the hub asking a member to come and
be shown a window, and bringing everything home.
"""
import time

import pytest

from link import winmove
from test_node_live import Pair
from test_winmove_live import FakeFrame, FakeWindow, raw_encode

PASSWORD = "window-moves-across-nicely"


class Moving:
    """A connected pair, both willing to move windows."""

    def __init__(self, hub_can=True, member_can=True):
        self.pair = Pair(pin=PASSWORD)
        self.hub, self.aio = self.pair.hub, self.pair.aio
        self.windows = {}            # which -> the FakeWindow it stands for
        self.frames = {}             # (node name, which) -> the FakeFrame
        self.sessions = []
        self.sides = []              # what each end was told about the edge
        for node, can in ((self.hub, hub_can), (self.aio, member_can)):
            node.move_windows = can
            if can:
                node.window_sides = ("home", "away")
                node.serve_window = self._server(node)

    def _log_for(self, node):
        which = "hub" if node is self.hub else "aio"
        return self.pair.logs[which].append

    def _server(self, node):
        """Stands in for the part of Nishro Link that has a screen: it runs
        the real sessions, against a fake window and a fake frame."""
        def serve(ch, kind, which, whose, side=""):
            self.sides.append((node.core.node, kind, whose, side))
            if kind == "lend":
                win = self.windows.get(which)
                if win is None:
                    return f"no window called {which}"
                s = winmove.HomeSession(ch, win, raw_encode,
                                        log=self._log_for(node))
                self.sessions.append(s)
                s.run()
                return ""
            frame = FakeFrame()
            self.frames[(node.core.node, which)] = frame
            s = winmove.AwaySession(ch, frame, log=self._log_for(node))
            self.sessions.append(s)
            s.run()
            return ""
        return serve

    def start(self):
        """Connected, patiently. Pair.connected() allows five seconds, which
        is plenty on its own and not always enough with sixteen hundred other
        tests running beside it - and a connection that takes six seconds is
        nothing to do with what these tests are checking."""
        self.pair.start()
        self.pair.wait(lambda: self.hub.ch is not None and self.aio.ch is not None,
                       timeout=PATIENCE, what="both ends to connect")
        return self

    def stop(self):
        for s in self.sessions:
            s.stop()
        for f in self.frames.values():
            f.did(None)
        self.pair.stop()


@pytest.fixture
def moving():
    m = Moving()
    try:
        yield m
    finally:
        m.stop()


# Generous, and on purpose: showing a window on the other computer means a
# whole second connection and its handshake, and these run alongside fifteen
# hundred other tests on a laptop. A short wait here fails for reasons that
# have nothing to do with what is being checked (seen twice in full runs).
PATIENCE = 30.0


def wait(cond, what, timeout=PATIENCE, moving=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    tail = ""
    if moving is not None:
        tail = "  logs: " + " | ".join(
            x for v in moving.pair.logs.values() for x in v[-6:])
    pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}\n{tail}")


# --------------------------------------------------- each side knows the other
def test_each_computer_learns_whether_the_other_will_move_windows(moving):
    moving.start()
    wait(lambda: moving.hub.window_peers.get("aio") is True, "the hub to learn it")
    wait(lambda: moving.aio.window_peers.get("laptop") is True,
         "the member to learn it")


def test_a_computer_with_the_switch_off_says_so():
    m = Moving(member_can=False)
    try:
        m.start()
        wait(lambda: "aio" in m.hub.window_peers, "the hub to learn about it")
        assert m.hub.window_peers["aio"] is False
        why = m.hub.move_window_to("aio", "w1")
        assert "cannot show a window from here" in why
    finally:
        m.stop()


def test_with_the_switch_off_here_nothing_moves(moving):
    """The switch is looked at before anything about the link, so this needs
    no connection - and must not wait for one, or a busy machine makes it
    flaky for a reason that has nothing to do with what it is checking."""
    moving.hub.move_windows = False
    assert "switched off on this computer" in moving.hub.move_window_to("aio", "w1")


# -------------------------------------------- the hub lends a member a window
def test_a_window_on_the_hub_is_shown_on_the_member(moving):
    """The hub cannot dial a member, so it asks over the link and the member
    dials in to be shown it - as a file copy does."""
    moving.windows["w1"] = FakeWindow(title="Notepad")
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    assert moving.hub.move_window_to("aio", "w1") == ""
    wait(lambda: ("aio", "w1") in moving.frames, "the member's frame")
    frame = moving.frames[("aio", "w1")]
    wait(lambda: frame.opened, "the frame to open")
    assert frame.opened[0] == "Notepad"
    wait(lambda: frame.drawn, "the first picture")


def test_what_the_person_does_on_the_member_drives_the_hubs_window(moving):
    win = moving.windows.setdefault("w2", FakeWindow())
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    moving.hub.move_window_to("aio", "w2")
    wait(lambda: ("aio", "w2") in moving.frames, "the frame")
    frame = moving.frames[("aio", "w2")]
    wait(lambda: frame.opened, "the frame to open")
    frame.did({"t": "in", "how": "move", "x": 7, "y": 9})
    frame.did({"t": "in", "how": "btn", "name": "left", "down": True})
    wait(lambda: ("btn", "left", True) in win.played, "the click to reach the window")
    assert ("move", 7, 9) in win.played


def test_the_link_is_untouched_by_a_moved_window(moving):
    """The window's connection is proved and sealed exactly like a link, so
    the one thing it must never do is become one."""
    moving.windows["w3"] = FakeWindow()
    moving.start()
    link = moving.hub.links["aio"].ch
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    moving.hub.move_window_to("aio", "w3")
    wait(lambda: ("aio", "w3") in moving.frames, "the frame")
    wait(lambda: moving.frames[("aio", "w3")].drawn, "a picture")
    time.sleep(0.3)
    assert list(moving.hub.links) == ["aio"], "a second link appeared"
    assert moving.hub.links["aio"].ch is link, "the link was replaced"


# ------------------------------------------- a member lends the hub a window
def test_a_window_on_the_member_is_shown_on_the_hub(moving):
    moving.windows["m1"] = FakeWindow(title="Files")
    moving.start()
    wait(lambda: moving.hub.window_peers.get("aio"), "the capability")
    assert moving.aio.move_window_to("laptop", "m1") == ""
    wait(lambda: ("laptop", "m1") in moving.frames, "the hub's frame")
    frame = moving.frames[("laptop", "m1")]
    wait(lambda: frame.opened, "the frame to open")
    assert frame.opened[0] == "Files"
    wait(lambda: frame.drawn, "the first picture")


# ------------------------------------------------------------- bringing home
def test_bringing_windows_home_closes_the_frame(moving):
    moving.windows["w4"] = FakeWindow()
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    moving.hub.move_window_to("aio", "w4")
    wait(lambda: ("aio", "w4") in moving.frames, "the frame")
    frame = moving.frames[("aio", "w4")]
    wait(lambda: frame.drawn, "a picture")
    # The hub registers the window when the member's connection reaches it,
    # which is its own moment - wait for it rather than assume it has
    # happened, or a busy machine fails this for no good reason.
    wait(lambda: moving.hub.windows_away, "the hub to have it on its list")
    assert moving.hub.bring_windows_home() == 1
    wait(lambda: frame.closed, "the frame to be taken away")


def test_the_switch_going_off_brings_every_window_home(moving):
    """R1 and R6: turning it off on either computer brings them all back."""
    moving.windows["w5"] = FakeWindow()
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    moving.hub.move_window_to("aio", "w5")
    wait(lambda: ("aio", "w5") in moving.frames, "the frame")
    frame = moving.frames[("aio", "w5")]
    wait(lambda: frame.drawn, "a picture")
    # the member switches it off, and says so over the link
    moving.aio.move_windows = False
    moving.aio.bring_windows_home(None)
    wait(lambda: frame.closed, "the frame to be taken away")
    wait(lambda: not moving.hub.windows_away, "the hub to have it back")


def test_a_window_whose_program_has_gone_takes_its_frame_with_it(moving):
    win = moving.windows.setdefault("w6", FakeWindow())
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    moving.hub.move_window_to("aio", "w6")
    wait(lambda: ("aio", "w6") in moving.frames, "the frame")
    frame = moving.frames[("aio", "w6")]
    wait(lambda: frame.drawn, "a picture")
    win.gone = True
    wait(lambda: frame.closed == winmove.GONE, "the frame to say why it went")
    wait(lambda: not moving.hub.windows_away, "the hub to forget it")


def test_no_more_than_four_windows_go_at_once(moving):
    """R8. The fifth is refused with a reason, not quietly dropped."""
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    for i in range(winmove.MOST_AWAY):
        moving.windows[f"many{i}"] = FakeWindow()
        moving.hub.move_window_to("aio", f"many{i}")
    wait(lambda: len(moving.hub.windows_away) == winmove.MOST_AWAY,
         "four windows away")
    moving.windows["one-too-many"] = FakeWindow()
    why = moving.hub.move_window_to("aio", "one-too-many")
    assert "as many as Nishro Link will move at once" in why


def test_a_window_nobody_offers_is_refused_without_a_frame(moving):
    """The far side asks for a window that is not there: say so and show
    nothing, rather than an empty frame."""
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    moving.hub.move_window_to("aio", "no-such-window")
    time.sleep(1.5)
    frame = moving.frames.get(("aio", "no-such-window"))
    assert frame is None or not frame.drawn, "something was drawn for no window"
    assert not moving.hub.windows_away


def test_a_wrong_password_opens_no_window_channel(moving):
    moving.windows["w7"] = FakeWindow()
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    moving.aio.pin = "quite-the-wrong-words-here"
    with pytest.raises(winmove.ChannelError, match="wrong password|refused"):
        moving.aio._window_channel("w7", mine=False)
    assert list(moving.hub.links) == ["aio"], "the link itself is untouched"


def test_the_hub_refuses_a_window_channel_when_its_switch_is_off(moving):
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    moving.hub.move_windows = False
    with pytest.raises(winmove.ChannelError, match="switched off"):
        moving.aio._window_channel("w8", mine=False)


def test_several_windows_at_once_each_get_their_own_frame(moving):
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    for i in (1, 2):
        moving.windows[f"two{i}"] = FakeWindow(title=f"Window {i}")
        assert moving.hub.move_window_to("aio", f"two{i}") == ""
    wait(lambda: all(("aio", f"two{i}") in moving.frames for i in (1, 2)),
         "both frames")
    for i in (1, 2):
        frame = moving.frames[("aio", f"two{i}")]
        wait(lambda f=frame: f.opened, f"frame {i} to open")
        assert frame.opened[0] == f"Window {i}"


def test_a_window_moving_does_not_stop_the_pointer_crossing(moving):
    """N4: the pictures travel on a connection of their own, so the pointer
    carries on while a window is being shown."""
    moving.windows["w9"] = FakeWindow()
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    moving.hub.move_window_to("aio", "w9")
    wait(lambda: ("aio", "w9") in moving.frames, "the frame")
    wait(lambda: moving.frames[("aio", "w9")].drawn, "a picture")
    moving.pair.hub_cap.sink.on_pointer(0, 384, -5, 0)
    moving.pair.wait(lambda: moving.pair.aio_cap.suppress[0] is True,
                     what="the pointer to cross while a window is away")


def test_a_moved_window_is_not_given_up_on_while_nothing_happens(moving):
    """A window nobody is touching sends nothing for a long time. The five
    seconds passed to create_connection are for CONNECTING; left on the
    socket they became a read timeout, and every window came home after
    exactly five seconds as though the other computer had let go."""
    win = moving.windows.setdefault("quiet", FakeWindow())
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    moving.hub.move_window_to("aio", "quiet")
    wait(lambda: ("aio", "quiet") in moving.frames, "the frame")
    frame = moving.frames[("aio", "quiet")]
    wait(lambda: frame.drawn, "the first picture")
    # Nothing at all for longer than that timeout, then check it is still away
    time.sleep(7)
    assert frame.closed is None, \
        f"the window came home by itself after a quiet spell ({frame.closed})"
    assert moving.hub.windows_away, "the hub no longer thinks it is away"
    # and it still works afterwards
    frame.did({"t": "in", "how": "btn", "name": "left", "down": True})
    wait(lambda: ("btn", "left", True) in win.played,
         "input after the quiet spell")


def test_the_dialled_window_channel_has_no_read_timeout(moving):
    """Directly: the socket must not be left with the connect timeout on it."""
    moving.windows["sock"] = FakeWindow()
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    ch, hub = moving.aio._window_channel("sock", mine=False)
    try:
        assert ch.sock.gettimeout() is None, \
            f"the window channel would give up after {ch.sock.gettimeout()}s"
    finally:
        ch.close()


# ------------------------- which way home lies, for dragging a frame back
def test_the_computer_showing_a_window_is_told_which_edge_leads_home(moving):
    """So the frame can be dragged back across the very edge the pointer
    crosses to drive that computer. In this pair the aio has the laptop
    arranged on its right (test_node_live.Pair), so from the aio the way
    home is right - and the laptop, whose own window it is, is told nothing,
    because a window at home is not dragged anywhere."""
    moving.windows["w1"] = FakeWindow(title="Notepad")
    moving.start()
    wait(lambda: moving.aio.window_peers.get("laptop"), "the capability")
    assert moving.hub.move_window_to("aio", "w1") == ""
    wait(lambda: ("aio", "w1") in moving.frames, "the member's frame")
    wait(lambda: len(moving.sides) >= 2, f"both ends ({moving.sides})")
    told = {(who, kind): side for who, kind, _whose, side in moving.sides}
    assert told[("aio", "show")] == "right", moving.sides
    assert told[("laptop", "lend")] == "", moving.sides


def test_the_side_comes_from_the_arrangement_not_a_guess(moving):
    """Rearranged, the way home changes with it."""
    aio = moving.aio
    assert aio.side_toward("laptop") == "right"
    assert moving.hub.side_toward("aio") == "left"
    # Nothing is invented for a machine the desk has never heard of, or for
    # this computer itself.
    assert aio.side_toward("some-other-pc") == ""
    assert aio.side_toward("aio") == ""
    assert aio.side_toward("") == ""


def test_the_borders_that_lead_somewhere_are_the_desks_own(moving):
    """The hub is the laptop with the aio on its left, so the laptop's left
    border is the one a window can be thrown across - and the line is where
    the desk says it is, not the far side of everything."""
    moving.start()
    wait(lambda: moving.hub.window_peers.get("aio"), "the capability")
    doors = moving.hub.window_doorways()
    assert [d["side"] for d in doors] == ["left"], doors
    assert doors[0]["device"] == "aio"
    assert isinstance(doors[0]["edge"], int)
    back = moving.aio.window_doorways()
    assert [d["side"] for d in back] == ["right"], back


def test_a_computer_that_cannot_lend_a_window_offers_no_border(moving):
    """Nothing to throw, so nothing to throw it across."""
    moving.start()
    wait(lambda: moving.hub.window_peers.get("aio"), "the capability")
    moving.hub.window_sides = ("away",)
    assert moving.hub.window_doorways() == []


def test_with_the_switch_off_there_are_no_borders(moving):
    moving.start()
    wait(lambda: moving.hub.window_peers.get("aio"), "the capability")
    moving.hub.move_windows = False
    assert moving.hub.window_doorways() == []


def test_a_peer_that_will_not_show_windows_offers_no_border(moving):
    moving.start()
    wait(lambda: moving.hub.window_peers.get("aio"), "the capability")
    moving.hub.window_peers.pop("aio", None)
    assert moving.hub.window_doorways() == []
