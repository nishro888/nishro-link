"""One window back where it belongs (docs/moving-windows.md 9.3, steps 2.1-2.2).

The maintainer: "calling back I want the same exact method" - choose the
window and use the tray - and back always means to the computer the window
belongs to. These cover the parts of that on every system: the relay keeping
a frame's focus for its own service and noting each window's title, the away
session saying which frame has the keyboard, the node bringing ONE window
back and knowing which frame is in front, and the tray offering both.
"""
import queue
import threading
import time
import types

import pytest

from link import node as node_mod
from link import tray, winmove
from test_window_link import Moving, wait
from test_winmove import _pair


# ------------------------------------------------------------------- relay
def _relaying(notes):
    """relay(a, b): a is the NETWORK channel, b the part with the screen."""
    screen, screen_far = _pair()
    net, net_far = _pair()
    threading.Thread(target=lambda: winmove.relay(net, screen, notes=notes),
                     daemon=True).start()
    return screen_far, net_far, (screen, screen_far, net, net_far)


def test_a_frames_focus_is_kept_for_its_own_service_not_passed_on():
    noted = []
    screen_far, net_far, all_four = _relaying(noted.append)
    try:
        screen_far.send(winmove.host_focus(True))
        end = time.monotonic() + 5
        while time.monotonic() < end and not noted:
            time.sleep(0.02)
        assert noted == [{"focus": True}]
        net_far.sock.settimeout(0.4)
        with pytest.raises(winmove.ChannelError):
            net_far.recv()                       # the far side never sees it
    finally:
        for ch in all_four:
            ch.close()


def test_a_windows_title_is_noted_as_it_passes_and_still_arrives():
    noted = []
    screen_far, net_far, all_four = _relaying(noted.append)
    try:
        net_far.send(winmove.pack({"t": "meta", "title": "Notepad", "w": 10, "h": 8}))
        header, _ = winmove.unpack(screen_far.recv())
        assert header["t"] == "meta" and header["title"] == "Notepad"
        assert {"title": "Notepad"} in noted
    finally:
        for ch in all_four:
            ch.close()


def test_a_note_that_fails_never_stops_the_frames():
    def broken(_what):
        raise RuntimeError("the service's bookkeeping broke")
    screen_far, net_far, all_four = _relaying(broken)
    try:
        net_far.send(winmove.pack({"t": "meta", "title": "x", "w": 4, "h": 4}))
        assert winmove.unpack(screen_far.recv())[0]["t"] == "meta"
    finally:
        for ch in all_four:
            ch.close()


# ------------------------------------------------------------ the away side
class _Sent:
    def __init__(self):
        self.frames = []

    def send(self, frame):
        self.frames.append(frame)


def test_the_away_session_says_which_frame_has_the_keyboard():
    ch = _Sent()
    frame = types.SimpleNamespace(events=queue.Queue())
    s = winmove.AwaySession(ch, frame)
    frame.events.put({"t": "focus", "on": True})
    frame.events.put(None)                       # the frame has gone
    s._send_events()
    assert ch.frames == [winmove.host_focus(True)]


# --------------------------------------------------------------------- node
def _node(**away):
    n = types.SimpleNamespace(windows_away=dict(away),
                              FRONT_GRACE_S=node_mod.Node.FRONT_GRACE_S)
    for name in ("frame_in_front", "bring_window_home", "_window_note"):
        setattr(n, name, types.MethodType(getattr(node_mod.Node, name), n))
    return n


def test_the_frame_in_front_is_the_one_that_had_the_keyboard_last():
    now = time.monotonic()
    n = _node(**{"laptop:1": {"who": "laptop", "mine": False, "focused": now - 9},
                 "laptop:2": {"who": "laptop", "mine": False, "focused": now - 1}})
    assert n.frame_in_front() == "laptop:2"


def test_a_frame_that_lost_the_keyboard_a_moment_ago_still_counts():
    """A click on the tray takes the keyboard from the frame before the
    menu opens; the menu must still offer to send THAT window back."""
    now = time.monotonic()
    n = _node(**{"laptop:1": {"who": "laptop", "mine": False, "focused": now - 3,
                              "blurred": now - 1}})
    assert n.frame_in_front() == "laptop:1"
    n.windows_away["laptop:1"]["blurred"] = now - 60
    assert n.frame_in_front() is None


def test_our_own_windows_away_are_never_the_frame_in_front():
    n = _node(**{"me:1": {"who": "aio", "mine": True, "focused": time.monotonic()}})
    assert n.frame_in_front() is None


def test_notes_from_an_older_run_of_the_same_window_are_ignored():
    mark, old = object(), object()
    n = _node(**{"laptop:1": {"who": "laptop", "mine": False, "mark": mark}})
    n._window_note("laptop:1", old, {"title": "stale"})
    assert "title" not in n.windows_away["laptop:1"]
    n._window_note("laptop:1", mark, {"title": "Notepad"})
    n._window_note("laptop:1", mark, {"focus": True})
    assert n.windows_away["laptop:1"]["title"] == "Notepad"
    assert n.frame_in_front() == "laptop:1"


def test_bringing_one_back_leaves_the_others_where_they_are():
    closed = []
    ch = types.SimpleNamespace(close=lambda: closed.append(1))
    n = _node(**{"laptop:1": {"who": "laptop", "ch": ch},
                 "laptop:2": {"who": "laptop", "ch": None}})
    assert n.bring_window_home("laptop:1") is True
    assert closed == [1] and list(n.windows_away) == ["laptop:2"]
    assert n.bring_window_home("laptop:9") is False


def test_one_window_goes_back_for_real_and_the_other_stays():
    """Two real nodes: two windows on the member, one sent back."""
    m = Moving()
    try:
        from test_winmove_live import FakeWindow
        m.windows["s1"] = FakeWindow(title="one")
        m.windows["s2"] = FakeWindow(title="two")
        m.start()
        wait(lambda: m.aio.window_peers.get("laptop"), "the capability")
        assert m.hub.move_window_to("aio", "s1") == ""
        assert m.hub.move_window_to("aio", "s2") == ""
        wait(lambda: ("aio", "s1") in m.frames and ("aio", "s2") in m.frames,
             "both frames")
        one, two = m.frames[("aio", "s1")], m.frames[("aio", "s2")]
        wait(lambda: one.drawn and two.drawn, "both pictures")
        assert m.aio.bring_window_home("laptop:s1") is True
        wait(lambda: one.closed, "the first frame to go")
        wait(lambda: "laptop:s1" not in m.hub.windows_away, "the first to be home")
        assert two.closed is None and "laptop:s2" in m.hub.windows_away
    finally:
        m.stop()


# --------------------------------------------------------------------- tray
def _state(**extra):
    s = {"enabled": True, "connected": True, "role": "hub", "move_windows": True,
         "windows_here": ["home", "away"],
         "devices": [{"name": "aio", "me": True, "hub": False, "online": True},
                     {"name": "laptop", "me": False, "hub": True, "online": True}]}
    s.update(extra)
    return tray.state(s)


def test_with_a_frame_in_front_the_tray_offers_to_send_it_back():
    st = _state(frame_in_front={"which": "laptop:7", "device": "laptop",
                                "title": "Notepad"})
    items = tray.window_items(st)
    labels = [i.get("label") for i in items]
    assert "In front: Notepad - from laptop" in labels
    back = [i for i in items if i.get("label") == "Send back to laptop"]
    assert back and back[0]["action"] == "sendback:laptop:7"
    assert not any(i.get("kind") == "sub" and "Move" in i.get("label", "")
                   for i in items), "no list of devices: back means to its owner"


def test_a_real_window_in_front_comes_before_a_frame_that_had_the_keyboard():
    """On Windows the tray sees the real window in front; a frame that lost
    the keyboard a moment ago must not hide it."""
    st = _state(current_window={"which": "42", "title": "Paint"},
                frame_in_front={"which": "laptop:7", "device": "laptop",
                                "title": "Notepad"})
    labels = [i.get("label") for i in tray.window_items(st)]
    assert "In front: Paint" in labels and "Send back to laptop" not in labels


def test_every_window_away_can_be_brought_back_on_its_own():
    st = _state(windows_away=[
        {"which": "aio:1", "device": "laptop", "ours": True, "title": "Notepad"},
        {"which": "laptop:2", "device": "laptop", "ours": False, "title": "Edge"}])
    sub = [i for i in tray.window_items(st) if i.get("label") == "Bring one back"]
    assert sub, "no list to bring one back from"
    got = [(i["label"], i["action"]) for i in sub[0]["items"]]
    assert got == [("Notepad - on laptop", "sendback:aio:1"),
                   ("Edge - from laptop", "sendback:laptop:2")]
    assert any(i.get("action") == "windows-home" for i in tray.window_items(st)), \
        "and all of them at once, as before"


class _API:
    def __init__(self, back=1):
        self.asked, self.back = [], back

    def status(self):
        return {}

    def command(self, path, body):
        self.asked.append((path, body))
        return {"ok": True, "brought_home": self.back}


class _UI:
    def __init__(self):
        self.said = []

    def notify(self, title, text):
        self.said.append(text)

    def show(self, *_):
        pass


def test_choosing_send_back_asks_for_that_one_window():
    api, ui = _API(), _UI()
    t = tray.Tray(lambda: api, ui, ["nishro-link"])
    t.api = api
    t.act("sendback:laptop:7")
    assert ("/api/window/home", {"which": "laptop:7"}) in api.asked
    assert ui.said == [], "it goes back where it can be seen going: nothing to say"


def test_a_window_already_gone_back_is_said_so():
    api, ui = _API(back=0), _UI()
    t = tray.Tray(lambda: api, ui, ["nishro-link"])
    t.api = api
    t.act("sendback:laptop:7")
    assert any("no longer on another screen" in s for s in ui.said), ui.said
