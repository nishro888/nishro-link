"""Dragging one of this computer's windows across the edge (windrag.py).

Nothing of Windows here: the watcher is fed where the window is, as the
system-specific half would, so every decision is checked with no window on
any screen. The real hook and a real dragged window are in
test_windrag_win.py.
"""
from link import windrag, winmove

# This computer's own screen runs to 1920 across and 1080 down, in local
# pixels - which is where the border to the other computer is.
SCREEN = (1920, 1080)
RIGHT = [{"side": "right", "edge": 1920, "device": "aio"}]


class Watcher:
    """A Drags with everything round it written down."""

    def __init__(self, doors=None, why=""):
        self.doors = RIGHT if doors is None else doors
        self.why = why
        self.asked = []
        self.ended = []
        self.said = []
        self.drags = windrag.Drags(
            doorways=lambda: self.doors, ask=self._ask,
            end_drag=self.ended.append, log=self.said.append)

    def _ask(self, hwnd, device):
        self.asked.append((hwnd, device))
        return self.why

    def drag(self, *places, hwnd=77):
        """A whole drag: picked up, moved through each place, let go."""
        out = []
        self.drags.began(hwnd)
        for place in places:
            out.append(self.drags.moved(place))
        self.drags.ended()
        return out


def inside(x=800):
    return (x, 400, 400, 300)


def over_right(showing=100):
    return (SCREEN[0] - showing, 400, 400, 300)


# ------------------------------------------------------------- the gesture
def test_a_window_dragged_over_the_edge_goes_to_that_computer():
    w = Watcher()
    assert w.drag(inside(), inside(1400), over_right())[-1] == "aio"
    assert w.asked == [(77, "aio")]


def test_the_window_is_taken_out_of_the_move_loop_first():
    """Left in it, the window stays under the hand while its picture appears
    on the other computer, and letting go would drop it half off this
    screen."""
    w = Watcher()
    w.drag(inside(), over_right())
    assert w.ended == [77]


def test_a_window_moved_about_well_inside_the_screen_stays():
    w = Watcher()
    assert w.drag(inside(300), inside(900), inside(1200)) == ["", "", ""]
    assert w.asked == []


def test_a_nudge_over_the_edge_is_not_the_gesture():
    w = Watcher()
    assert w.drag(inside(), (SCREEN[0] - 380, 400, 400, 300)) == ["", ""]
    assert w.asked == []


def test_a_window_too_wide_to_fit_is_never_thrown_by_being_touched():
    """It hangs over the border wherever it is put, so there is no drag that
    began inside - and START_INSIDE is what stops it going on a touch."""
    w = Watcher()
    wide = 3000
    assert w.drag((300, 400, wide, 300),
                  (100, 400, wide, 300)) == ["", ""]
    assert w.asked == []


def test_the_pixels_decide_for_a_window_whose_fraction_is_out_of_reach():
    """40% of a wide window is more than Windows lets anyone drag off the
    edge of the desktop, so the smaller of the two is what counts."""
    assert winmove.far_enough("right", (1920 - 3000 + 100, 0, 3000, 300),
                              1920)
    assert not winmove.far_enough("right", (1920 - 3000 + 40, 0, 3000, 300),
                                  1920)


def test_it_goes_only_once_however_far_it_is_dragged_on():
    w = Watcher()
    out = w.drag(inside(), over_right(), over_right(40), over_right(10))
    assert out == ["", "aio", "", ""]
    assert w.asked == [(77, "aio")]


def test_a_window_already_hanging_over_the_edge_is_not_sent_on_a_touch():
    """Picked up where it already was, nobody made a gesture."""
    w = Watcher()
    assert w.drag(over_right(), over_right(90)) == ["", ""]
    assert w.asked == []


def test_but_dragged_back_in_and_out_again_it_does_go():
    w = Watcher()
    out = w.drag(over_right(), inside(600), over_right())
    assert out[-1] == "aio"


def test_each_drag_starts_afresh():
    w = Watcher()
    w.drag(inside(), over_right())
    assert w.drag(inside(), over_right())[-1] == "aio"
    assert w.asked == [(77, "aio"), (77, "aio")]


# --------------------------------------------------------- which edges count
def test_only_a_border_that_leads_somewhere_counts():
    w = Watcher()
    # over the LEFT edge, where nothing is
    assert w.drag(inside(), (-300, 400, 400, 300)) == ["", ""]
    assert w.asked == []


def test_with_nothing_connected_a_drag_is_not_even_watched():
    w = Watcher(doors=[])
    assert w.drags.began(77) is False
    assert w.drag(inside(), over_right()) == ["", ""]
    assert w.asked == []


def test_each_side_leads_to_its_own_computer():
    for side, edge, place in (("left", 0, (-300, 400, 400, 300)),
                              ("right", 1920, over_right()),
                              ("top", 0, (800, -200, 400, 300)),
                              ("bottom", 1080, (800, 1080 - 100, 400, 300))):
        w = Watcher(doors=[{"side": side, "edge": edge, "device": "aio"}])
        assert w.drag(inside(), (700, 400, 400, 300), place)[-1] == "aio", side


def test_a_border_between_two_monitors_is_measured_not_the_far_side():
    """The laptop has a monitor to its left, so the whole desktop runs from
    0 to 3286 in local pixels and the laptop's own screen starts at 1920. A
    window dragged left off the laptop's screen crosses THAT border, not the
    far side of everything (measured: the virtual desktop is 3286x1080 and
    starts at (-1920, -148) in Windows' own numbers)."""
    w = Watcher(doors=[{"side": "left", "edge": 1920, "device": "aio"}])
    # at local x=1870: 50 of a 400-wide window past 1920 - a nudge
    assert w.drag((2400, 300, 400, 300), (1870, 300, 400, 300)) == ["", ""]
    # at local x=1700: 220 of it past - the gesture
    assert w.drag((2400, 300, 400, 300), (1700, 300, 400, 300))[-1] == "aio"


def test_the_same_threshold_as_dragging_a_frame_back():
    """The gesture must feel the same both ways round."""
    w = Watcher()
    needs = min(400 * winmove.THROW_BACK, winmove.THROW_PIXELS)
    assert w.drag(inside(), over_right(400 - needs))[-1] == "aio"
    assert w.drag(inside(), over_right(400 - needs + 10))[-1] == ""


def test_the_threshold_can_actually_be_REACHED_at_the_desktops_edge():
    """Measured on the laptop, where the border to the other computer IS the
    outer edge of the desktop: Windows CUTS a drag once a window hangs too
    far off it and snaps the window back to where it was picked up. A
    500-wide window got 180 px past and a 300-wide one 115 - both short of
    40% of themselves, so a fraction alone was a threshold nobody could
    reach, and the gesture simply never fired.
    """
    for width, reachable in ((300, 115), (500, 180), (1000, 180)):
        w = Watcher()
        place = (1920 - width + reachable, 400, width, 300)
        assert w.drag((600, 400, width, 300), place)[-1] == "aio",             f"a {width}-wide window cannot be thrown even {reachable} px past"


# --------------------------------------------------------- when it cannot go
def test_a_refusal_is_said_plainly_and_the_window_stays():
    w = Watcher(why="aio is not connected")
    assert w.drag(inside(), over_right()) == ["", ""]
    assert any("cannot go" in s and "not connected" in s for s in w.said), \
        w.said


def test_asking_blowing_up_does_not_take_the_drag_watcher_with_it():
    w = Watcher()

    def boom(hwnd, device):
        raise RuntimeError("the node went away")
    w.drags.ask = boom
    assert w.drag(inside(), over_right()) == ["", ""]
    assert any("could not be sent" in s for s in w.said), w.said
    # and the next drag still works
    w.drags.ask = w._ask
    assert w.drag(inside(), over_right())[-1] == "aio"


def test_doorways_blowing_up_is_not_an_error_a_window_moves_for():
    w = Watcher()

    def boom():
        raise RuntimeError("no answer from the node")
    w.drags.doorways = boom
    assert w.drags.began(77) is False
    assert w.drags.moved(over_right()) == ""


def test_a_drag_ending_before_anything_crossed_leaves_nothing_behind():
    w = Watcher()
    w.drags.began(77)
    w.drags.moved(inside())
    w.drags.ended()
    assert w.drags.hwnd is None
    assert w.drags.moved(over_right()) == ""
    assert w.asked == []
