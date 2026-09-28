"""Arranging the desk: that it takes effect on BOTH machines, and that a machine
with several monitors is one screen whose edges are its outer edges.

Both halves come from one report. The user dragged the AIO to the left of the laptop
and pressed Apply, and pushing right off the laptop still went to the AIO. The
AIO held the baton, so its own copy of the layout decided - and the arrangement
had only reached the laptop. Fixing that alone would have exposed the next
fault: the laptop has an external display on its LEFT, and the link measured
edges on the laptop's panel, so with the AIO on the left every move onto the
external display would have jumped to the AIO instead.
"""
import sys
import time
import types

import pytest

from link import desktop
from link.desk import Desk, place, simple
from link.motion import Cursor
from link.node import Node, NodeCore

from test_node_live import Pair

# the test laptop, as Windows reports it: the panel is primary at (0, 0) and the
# external display sits to its left, 148 pixels higher.
PANEL = (0, 0, 1366, 768)
EXTERNAL = (-1920, -148, 1920, 1080)


# =============================================================== the desktop
def test_the_desktop_spans_every_monitor():
    d = desktop.from_monitors([PANEL, EXTERNAL])
    assert (d.x, d.y, d.w, d.h) == (-1920, -148, 3286, 1080)
    assert d.parts == ((0, 0, 1920, 1080), (1920, 148, 1366, 768))


def test_one_monitor_has_no_parts():
    d = desktop.from_monitors([PANEL])
    assert (d.x, d.y, d.w, d.h, d.parts) == (0, 0, 1366, 768, ())


def test_coordinates_convert_both_ways():
    d = desktop.from_monitors([PANEL, EXTERNAL])
    assert d.to_os(1920, 148) == (0, 0)               # the panel's corner
    assert d.from_os(-1920, -148) == (0, 0)           # the desktop's corner
    assert d.from_os(*d.to_os(2500, 400)) == (2500, 400)


# ================================================================== edges
def laptop_on_the_right():
    """The AIO to the left of the whole laptop desktop."""
    d = desktop.from_monitors([PANEL, EXTERNAL])
    return place("laptop", [
        {"name": "aio", "w": 1920, "h": 1080, "x": 0, "y": 0, "owner": "aio"},
        {"name": "laptop", "w": d.w, "h": d.h, "x": 1920, "y": 0,
         "owner": "laptop", "parts": d.parts},
    ])


def hub_core(lay):
    c = NodeCore("laptop", lay, {}, is_hub=True)
    c.peer_online("aio")                      # as if the AIO were connected
    return c


def test_moving_onto_the_external_display_is_not_a_crossing():
    """The bug that would have followed the first fix. The panel's left edge is
    at x=1920 in desktop terms; stepping past it lands on the external display,
    which Windows handles, not the link."""
    core = hub_core(laptop_on_the_right())
    core.local_pointer("laptop", 1919, 500, -5, 0)
    assert core.cursor.screen == "laptop"
    assert not core.cursor_is_remote()


def test_the_outer_left_edge_leads_to_the_aio():
    core = hub_core(laptop_on_the_right())
    core.local_pointer("laptop", 0, 500, -5, 0)
    assert core.cursor.screen == "aio"


def test_the_panels_right_edge_is_a_wall_with_the_aio_on_the_left():
    core = hub_core(laptop_on_the_right())
    core.local_pointer("laptop", 3285, 400, 8, 0)
    assert core.cursor.screen == "laptop"


def test_the_virtual_cursor_never_drifts_into_a_hole():
    """Above the short panel, beside the tall external display, is a region
    with no monitor. A cursor driven there from the AIO would sit invisibly
    above the real pointer and leave a dead zone on the way back."""
    lay = laptop_on_the_right()
    c = Cursor(lay, "laptop", 2500, 148)              # top edge of the panel
    c.move(0, -60)
    assert (c.x, c.y) == (2500, 148), "went up into the hole"
    c.move(0, 1)
    assert c.y == 149, "a dead zone on the way back down"


def test_a_warp_into_a_hole_lands_on_the_nearest_monitor():
    lay = laptop_on_the_right()
    c = Cursor(lay, "laptop", 2500, 10)
    assert lay.get("laptop").inside(c.x, c.y)


def test_monitors_survive_the_trip_through_the_wire():
    lay = laptop_on_the_right()
    back = Desk.from_dict(lay.to_dict(), node="aio")
    assert back.get("laptop").parts == lay.get("laptop").parts


# ========================================================= a screen that grew
def test_a_screen_that_grows_pushes_its_neighbours_along():
    """A saved arrangement knows the laptop as its 1366-wide panel. Measured
    as a whole desktop it is 3286 wide, and growing it in place would lay it
    over the AIO beside it."""
    saved = [{"name": "laptop", "w": 1366, "h": 768, "x": 0, "y": 8},
             {"name": "aio", "w": 1920, "h": 1080, "x": 1366, "y": 0}]
    lay = place("laptop", saved)
    lay.resize("laptop", 3286, 1080, ((0, 0, 1920, 1080), (1920, 148, 1366, 768)))
    boxes = {b["name"]: b for b in lay.boxes()}
    assert boxes["aio"]["x"] == 3286, "the AIO should still be flush on the right"
    assert (boxes["laptop"]["w"], boxes["laptop"]["h"]) == (3286, 1080)
    assert lay.overlaps() == []
    # the panel (8 + 148 down, 768 tall) still meets the AIO along its whole side
    assert lay.touching("laptop") == {"aio": 768}


def test_things_to_the_left_stay_put():
    saved = [{"name": "aio", "w": 1920, "h": 1080, "x": 0, "y": 0},
             {"name": "laptop", "w": 1366, "h": 768, "x": 1920, "y": 0}]
    lay = place("laptop", saved)
    lay.resize("laptop", 3286, 1080)
    assert {b["name"]: b["x"] for b in lay.boxes()} == {"aio": 0, "laptop": 1920}


# ====================================================== Windows coordinates
@pytest.mark.skipif(sys.platform != "win32", reason="Windows capture")
def test_capture_reports_positions_from_the_desktop_corner():
    from link.capture_win import WM_MOUSEMOVE, WinCapture

    class U:
        def GetSystemMetrics(self, i):
            return 1366 if i == 0 else 768

        def SetCursorPos(self, x, y):
            self.at = (x, y)

    c = WinCapture(u=U(), origin=(-1920, -148))
    got = []
    c.sink = types.SimpleNamespace(on_pointer=lambda *a: got.append(a))
    ev = types.SimpleNamespace(pt=types.SimpleNamespace(x=-1900, y=0), mouseData=0)
    c._handle_mouse(WM_MOUSEMOVE, ev)
    assert got[0][:2] == (20, 148)          # on the external display, near its edge


@pytest.mark.skipif(sys.platform != "win32", reason="Windows capture")
def test_an_injected_position_becomes_an_anchor_in_os_terms():
    from link.capture_win import WinCapture
    c = WinCapture(u=types.SimpleNamespace(GetSystemMetrics=lambda i: 800),
                   origin=(-1920, -148))
    c.note_injected(1920, 148)
    assert c._anchor == (0, 0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows injection")
def test_injection_moves_the_os_pointer_to_the_right_place():
    from link.inject import WindowsInjector
    inj = WindowsInjector(origin=(-1920, -148))
    seen = []
    inj.u = types.SimpleNamespace(SetCursorPos=lambda x, y: seen.append((x, y)))
    inj.move_abs(1920 + 683, 148 + 384)
    assert seen == [(683, 384)]             # the middle of the panel


# ============================================================ both machines
def boxes_aio_on(side):
    """The Pair fixture's screens, with the AIO on `side` of the laptop."""
    if side == "right":
        return [{"name": "laptop", "w": 1366, "h": 768, "x": 0, "y": 0,
                 "owner": "laptop"},
                {"name": "aio", "w": 1920, "h": 1080, "x": 1366, "y": 0,
                 "owner": "aio"}]
    return [{"name": "aio", "w": 1920, "h": 1080, "x": 0, "y": 0, "owner": "aio"},
            {"name": "laptop", "w": 1366, "h": 768, "x": 1920, "y": 0,
             "owner": "laptop"}]


def wait(cond, what, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    pytest.fail(f"timed out waiting for {what}")


def right_of(core, screen):
    """The machine across `screen`'s right edge, if any - asked of the desk."""
    for c in core.layout.crossings():
        if c.a == screen and c.side == "right":
            return c.b
        if c.b == screen and c.side == "left":
            return c.a
    return None


def test_an_arrangement_applied_on_the_hub_reaches_the_peer_at_once():
    """The original report, reproduced. The AIO holds the baton and drives on the
    laptop's screen, so the AIO's copy of the layout decides where it crosses.
    Before, the new arrangement reached only the laptop."""
    p = Pair()            # the fixture starts with the AIO on the laptop's left
    saved = []
    p.hub.on_placement = saved.append
    try:
        p.start().connected()
        assert right_of(p.aio_core, "laptop") is None

        assert p.hub.arrange(boxes_aio_on("right")) is None
        wait(lambda: right_of(p.aio_core, "laptop") == "aio",
             "the AIO to adopt the new arrangement")
        assert p.aio_core.placement, "the AIO's window has nothing to draw"
        assert saved, "the hub did not persist it"

        # and it is the AIO's copy that now decides, as the AIO drives
        p.aio_cap.sink.on_pointer(500, 500, 20, 0)
        wait(lambda: p.aio_core.holds(), "the AIO to take the baton")
        with p.aio._lock:
            p.aio_core.cursor.warp("laptop", 1300, 300)
        p.aio_cap.sink.on_motion(200, 0)
        assert p.aio_core.cursor.screen == "aio"
    finally:
        p.stop()


def test_the_peer_can_rearrange_from_its_own_window():
    p = Pair()
    saved = []
    p.hub.on_placement = saved.append
    try:
        p.start().connected()
        assert p.aio.arrange(boxes_aio_on("right")) is None
        wait(lambda: right_of(p.hub_core, "laptop") == "aio",
             "the hub to apply the peer's arrangement")
        wait(lambda: right_of(p.aio_core, "laptop") == "aio",
             "the peer to get it back")
        assert saved and {b["name"] for b in saved[-1]} == {"laptop", "aio"}
    finally:
        p.stop()


def test_a_broken_arrangement_is_refused_and_changes_nothing():
    p = Pair()
    try:
        p.start().connected()
        before = p.hub_core.layout.to_dict()
        err = p.hub.arrange([{"name": "aio", "w": 1920, "h": 1080, "x": 0, "y": 0,
                              "owner": "aio"}])
        assert err and "no screen belonging to this machine" in err
        assert p.hub_core.layout.to_dict() == before
    finally:
        p.stop()


def test_rearranging_does_not_yank_the_cursor():
    """A box dragged in a window is no reason for the pointer to jump home."""
    p = Pair()
    try:
        p.start().connected()
        p.hub_cap.sink.on_pointer(0, 384, -5, 0)       # the laptop drives onto the AIO
        wait(lambda: p.hub_core.cursor.screen == "aio", "the crossing")
        where = (p.hub_core.cursor.screen, p.hub_core.cursor.x, p.hub_core.cursor.y)
        p.hub.arrange(boxes_aio_on("right"))
        assert (p.hub_core.cursor.screen, p.hub_core.cursor.x,
                p.hub_core.cursor.y) == where
    finally:
        p.stop()


def test_a_peer_that_is_not_connected_is_told_so():
    lay = simple("aio", (1920, 1080), "laptop", (1366, 768), "right")
    from test_node_live import FakeCapture, FakeInjector
    n = Node(NodeCore("aio", lay, {}, is_hub=False), FakeCapture(),
             FakeInjector(), port=1, peer_addr="127.0.0.1")
    err = n.arrange([{"name": "aio", "w": 1920, "h": 1080, "x": 0, "y": 0,
                      "owner": "aio"},
                     {"name": "laptop", "w": 1366, "h": 768, "x": 1920, "y": 0,
                      "owner": "laptop"}])
    assert err and "connect first" in err


# ================================================== monitors on Linux
def test_xrandr_monitors_are_read_with_their_positions():
    text = ("Monitors: 2\n"
            " 0: +*DP-1 1920/527x1080/296+0+0  DP-1\n"
            " 1: +HDMI-1 1366/344x768/193+1920+148  HDMI-1\n")
    assert desktop.parse_xrandr(text) == [(0, 0, 1920, 1080), (1920, 148, 1366, 768)]
    d = desktop.from_monitors(desktop.parse_xrandr(text))
    assert (d.w, d.h, len(d.parts)) == (3286, 1080, 2)


def test_xrandr_output_with_nothing_useful_gives_nothing():
    assert desktop.parse_xrandr("") == []
    assert desktop.parse_xrandr("Can't open display") == []
