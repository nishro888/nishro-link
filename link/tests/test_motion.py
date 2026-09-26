"""The pointer moving across the desk. See motion.py.

The desks are from a real setup: a laptop whose desktop is a 1920x1080 external monitor
with the 1366x768 panel to its right, 148 px lower; and a 1920x1080 AIO.
"""
import pytest

from link.desk import Desk
from link.motion import Cursor, exits, to_pixels

EXTERNAL = (0, 0, 1920, 1080)
PANEL = (1920, 148, 1366, 768)


def desk(aio_at):
    d = Desk("laptop")
    d.add("laptop", 3286, 1080, parts=(EXTERNAL, PANEL))
    d.add("aio", 1920, 1080, owner="aio", x=aio_at[0], y=aio_at[1])
    return d


def at(c):
    return c.screen, c.x, c.y


# ------------------------------------------------------------ crossing over
def test_off_the_external_monitors_left_edge_onto_the_aio():
    c = Cursor(desk((-1920, 0)), "laptop", 3, 500)
    s = c.move(-10, 0)
    assert s.crossed and at(c) == ("aio", 1913, 500)


def test_the_overshoot_carries_on_rather_than_landing_flush():
    """A pointer landing exactly on the edge would bounce straight back."""
    c = Cursor(desk((-1920, 0)), "laptop", 0, 500)
    c.move(-40, 0)
    assert c.x == 1920 - 40


def test_crossing_keeps_the_physical_height_as_drawn():
    """Not stretched proportionally: the picture is the truth."""
    c = Cursor(desk((3286, 0)), "laptop", 3285, 400)     # panel, 400 into the desktop
    c.move(5, 0)
    assert at(c) == ("aio", 4, 400)


def test_moving_between_the_laptops_own_monitors_is_not_crossing():
    c = Cursor(desk((-1920, 0)), "laptop", 1925, 500)   # on the panel
    s = c.move(-50, 0)
    assert not s.crossed and at(c) == ("laptop", 1875, 500)   # on the external


# ------------------------------------------------ the example from the original request
def test_aio_above_the_second_monitor_is_reached_going_up_from_it():
    c = Cursor(desk((0, -1080)), "laptop", 800, 2)
    s = c.move(0, -10)
    assert s.crossed and at(c) == ("aio", 800, 1072)


def test_going_up_from_the_panel_beside_it_is_a_wall():
    c = Cursor(desk((0, -1080)), "laptop", 2500, 150)
    s = c.move(0, -500)
    assert not s.crossed and at(c) == ("laptop", 2500, 148)


def test_and_back_down_from_the_aio_lands_on_the_second_monitor():
    c = Cursor(desk((0, -1080)), "aio", 100, 1075)
    c.move(0, 20)
    assert at(c) == ("laptop", 100, 15)


# --------------------------------------------------------------- walls
def test_where_the_aio_sticks_out_past_the_panel_there_is_a_wall():
    """AIO on the right is 1080 tall; the panel beside it only 768. From the
    part of the AIO beside the hole above the panel, left is a wall."""
    c = Cursor(desk((3286, 0)), "aio", 5, 50)
    s = c.move(-30, 0)
    assert not s.crossed and at(c) == ("aio", 0, 50)


def test_a_diagonal_push_along_a_wall_slides():
    c = Cursor(desk((3286, 0)), "aio", 5, 50)
    c.move(-30, 20)
    assert at(c) == ("aio", 0, 70), "stopped on x, carried on along y"


def test_a_corner_is_not_a_doorway():
    c = Cursor(desk((-1920, -1080)), "laptop", 2, 2)       # corner-to-corner only
    s = c.move(-10, -10)
    assert not s.crossed and c.screen == "laptop"


def test_a_gap_is_a_wall():
    c = Cursor(desk((-1930, 0)), "laptop", 2, 500)
    assert not c.move(-50, 0).crossed


def test_the_far_edge_of_everything_is_a_wall():
    c = Cursor(desk((-1920, 0)), "laptop", 3280, 500)
    c.move(100, 0)
    assert at(c) == ("laptop", 3285, 500)


# ------------------------------------------------------------ big moves
def test_one_big_flick_can_cross_several_displays():
    c = Cursor(desk((3286, 0)), "laptop", 100, 500)
    s = c.move(3500, 0)
    assert s.crossed and at(c) == ("aio", 314, 500)


# ------------------------------------------------ the arrangement changing
def test_the_cursor_stays_put_on_its_machine_when_the_arrangement_changes():
    d = desk((-1920, 0))
    c = Cursor(d, "aio", 100, 200)
    d.move("aio", 3286, 0)
    assert at(c) == ("aio", 100, 200)
    c.move(-150, 0)                               # and moves from where it IS
    assert at(c) == ("laptop", 3286 - 50, 200)


def test_a_warp_into_a_hole_lands_on_a_display():
    c = Cursor(desk((-1920, 0)), "laptop", 0, 0)
    c.warp("laptop", 2500, 20)
    assert at(c) == ("laptop", 2500, 148)


# ---------------------------------------------- leaving this machine by hand
@pytest.mark.parametrize("x,y,dx,dy,eaten,why", [
    (1920, 500, -8, 0, (0, 0), "panel -> own external monitor: Windows' job"),
    (0, 500, -8, 0, (-8, 0), "the external monitor's outer left edge"),
    (3285, 500, 8, 0, (8, 0), "the panel's right edge"),
    (2500, 148, 0, -8, (0, -8), "the panel's top, with a hole above it"),
    (500, 0, 0, -8, (0, -8), "the external monitor's top"),
    (500, 1079, 0, 8, (0, 8), "the external monitor's bottom"),
    (2500, 915, 0, 8, (0, 8), "the panel's bottom"),
    (2500, 500, 5, 5, (0, 0), "the middle of the panel"),
    (3300, 500, 8, 0, (8, 0), "a hook position already past the edge"),
])
def test_what_counts_as_pushing_off_this_machine(x, y, dx, dy, eaten, why):
    lap = desk((-1920, 0)).get("laptop")
    assert exits(lap, x, y, dx, dy) == eaten, why


# ------------------------------------------------------------------ wire
def test_positions_go_on_the_wire_as_fractions_of_the_desktop():
    c = Cursor(desk((-1920, 0)), "aio", 1919, 0)
    assert c.norm() == (1.0, 0.0)
    assert to_pixels(1.0, 0.0, 1920, 1080) == (1919, 0)
    assert to_pixels(-3, 9, 100, 100) == (0, 99), "clamped, never off-screen"
