"""The arrangement screen, driven headless against a real Tk. See ui_arrange.py.

The desk is a real setup: a laptop with two displays (a 1920x1080 external monitor
and, to its right and 148 px lower, the 1366x768 panel) and a 1920x1080 AIO.
"""
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" and not (os.environ.get("DISPLAY")
                                     or os.environ.get("WAYLAND_DISPLAY")),
    reason="no display for Tk")

import tkinter as tk                                    # noqa: E402

from link.ui_arrange import Arranger                    # noqa: E402

LAPTOP = {"name": "laptop", "w": 3286, "h": 1080, "x": 0, "y": 0,
          "owner": "laptop", "parts": [[0, 0, 1920, 1080], [1920, 148, 1366, 768]]}


@pytest.fixture(scope="module")
def root(tk_session):
    """A window of the one shared interpreter - see conftest.py."""
    r = tk.Toplevel(tk_session)
    r.withdraw()
    yield r
    r.destroy()


def boxes(aio_at=(3286, 0)):
    return [dict(LAPTOP), {"name": "aio", "w": 1920, "h": 1080, "x": aio_at[0],
                           "y": aio_at[1], "owner": "aio"}]


def arranger(root, bx=None, **kw):
    for child in root.winfo_children():
        child.destroy()
    a = Arranger(root, bx if bx is not None else boxes(), "laptop", **kw)
    a.canvas.configure(width=640, height=260)
    a.canvas.update_idletasks()
    a.redraw()
    return a


def ev(x, y):
    return type("E", (), {"x": x, "y": y})


def drag(a, name, to_world):
    """Pick up `name` by the middle of its first display and drop it so that
    its top-left corner lands at `to_world` (before snapping)."""
    m = a.desk.get(name)
    r = m.world()[0]
    gx, gy = r.x + r.w // 2, r.y + r.h // 2
    px, py = a._to_screen(gx, gy)
    a._grab(ev(px, py))
    tx, ty = a._to_screen(to_world[0] + (gx - m.x), to_world[1] + (gy - m.y))
    a._drag(ev(tx, ty))
    a._drop(ev(tx, ty))


def crossing_lines(a):
    return a.canvas.find_withtag("crossing")


def texts(a):
    return [a.canvas.itemcget(i, "text") for i in a.canvas.find_all()
            if a.canvas.type(i) == "text"]


# ------------------------------------------------------------------ basics
def test_it_does_not_mutate_what_it_was_given(root):
    given = boxes()
    before = [dict(b) for b in given]
    a = arranger(root, given)
    drag(a, "aio", (-1920, 0))
    assert given == before


def test_every_display_is_drawn_and_every_crossing_marked(root):
    a = arranger(root)
    # the panel meets the AIO along 768 px: one bright line
    assert len(crossing_lines(a)) == 1
    t = texts(a)
    assert "laptop" in t and "aio" in t and "this machine" in t
    assert "1366×768" in t and "1920×1080" in t, "each display's size"


def test_one_machine_alone_draws_and_says_others_will_appear(root):
    a = arranger(root, [dict(LAPTOP)])
    assert any("appear here" in t for t in texts(a))


# ---------------------------------------------------------------- dragging
def test_clicking_a_machine_selects_it_and_empty_desk_selects_nothing(root):
    a = arranger(root)
    r = a.desk.get("aio").world()[0]
    p = a._to_screen(r.x + 10, r.y + 10)
    a._grab(ev(*p))
    a._drop(ev(*p))
    assert a.selected == "aio"
    a._grab(ev(2, 2))
    a._drop(ev(2, 2))
    assert a.selected is None


def test_a_click_is_not_a_change(root):
    a = arranger(root, boxes((3286 + 5000, 3000)))   # an arrangement not at 0,0
    r = a.desk.get("aio").world()[0]
    p = a._to_screen(r.x + 10, r.y + 10)
    a._grab(ev(*p))
    a._drop(ev(*p))
    assert a.dirty is False


def test_dropped_near_an_edge_it_goes_flush(root):
    a = arranger(root)
    drag(a, "aio", (-1920 - 60, 30))            # a little short of the left edge
    got, lap = a.desk.get("aio"), a.desk.get("laptop")
    assert got.x + got.w == lap.x, "flush against the laptop's left edge"
    assert a.desk.crossings(), "and the pointer can cross there"


def test_dropped_on_top_of_another_it_is_pushed_clear(root):
    a = arranger(root)
    drag(a, "aio", (2600, 100))                  # onto the panel
    assert a.desk.overlaps() == []


def test_only_the_held_machine_moves(root):
    a = arranger(root)
    lap = a.desk.get("laptop").displays()
    drag(a, "aio", (-1920, 0))
    assert a.desk.get("laptop").displays() == lap


def test_the_view_holds_still_while_dragging(root):
    """Refitting on every movement changed the scale under the hand."""
    a = arranger(root)
    r = a.desk.get("aio").world()[0]
    a._grab(ev(*a._to_screen(r.x + 50, r.y + 50)))
    view = a._view
    a._drag(ev(630, 250))                        # far out: the desk grows
    assert a._view == view
    a._drop(ev(630, 250))


def test_guides_show_while_dragging_and_go_after(root):
    a = arranger(root)
    r = a.desk.get("aio").world()[0]
    a._grab(ev(*a._to_screen(r.x + 50, r.y + 50)))
    a._drag(ev(*a._to_screen(r.x + 50 + 3, r.y + 50 + 3)))
    assert a.canvas.find_withtag("guide")
    a._drop(ev(0, 0))
    assert not a.canvas.find_withtag("guide")


def test_after_a_drop_the_arrangement_starts_at_the_origin(root):
    a = arranger(root)
    drag(a, "aio", (-1920, 0))
    b = a.desk.bounds()
    assert (b.x, b.y) == (0, 0)


# ------------------------------------------- the arrangement the user asked for
def test_the_aio_can_be_put_above_the_laptops_second_monitor(root):
    a = arranger(root)
    drag(a, "aio", (0, -1080 - 25))              # just above the external monitor
    (c,) = a.desk.crossings()
    lap, aio = a.desk.get("laptop"), a.desk.get("aio")
    assert aio.y + aio.h == lap.y, "flush on top"
    assert {c.a, c.b} == {"laptop", "aio"} and c.length == 1920
    assert "laptop along 1920 px" in a.desk.describe("aio")


# ---------------------------------------------------------------- keyboard
def test_arrow_keys_nudge_the_selected_machine(root):
    a = arranger(root, boxes((3286, 0)))
    a.select("aio")
    a.nudge(10, 0)
    assert a.desk.get("aio").x == 3296 and a.dirty


def test_a_nudge_into_another_machine_is_refused(root):
    a = arranger(root, boxes((3286, 0)))
    a.select("aio")
    a.nudge(-100, 0)                             # into the panel
    assert a.desk.get("aio").x == 3286


def test_nothing_selected_means_nothing_nudged(root):
    a = arranger(root)
    a.nudge(50, 0)
    assert a.dirty is False


# ----------------------------------------------------------- whole layouts
def test_in_a_row_and_in_a_column(root):
    a = arranger(root, boxes((9000, 9000)))
    a.tidy()
    assert a.desk.unreachable() == [] and a.desk.overlaps() == []
    a.stack()
    assert a.desk.unreachable() == [] and a.desk.overlaps() == []
    assert a.dirty


# ---------------------------------------------------------------- telling
def test_a_machine_touching_nothing_is_reported(root):
    a = arranger(root, boxes((9000, 0)))
    assert any("aio does not touch anything" in p for p in a.problems())


def test_the_selected_machine_is_described(root):
    a = arranger(root)
    assert "Click a machine" in a.describe()
    a.select("aio")
    assert "aio" in a.describe() and "laptop" in a.describe()


def test_a_machine_that_is_not_connected_is_drawn_dimmed(root):
    a = arranger(root)
    a.set_online(["laptop"])
    fills = {a.canvas.itemcget(i, "fill") for i in a.canvas.find_all()
             if a.canvas.type(i) == "rectangle"}
    assert a.C["offline_fill"] in fills


# ------------------------------------------- a change waiting for Apply
# Reported: "this time the arrangement i can not change". The window refreshes the
# desk every 700ms, and a moved screen was protected only WHILE held.
def test_a_move_survives_the_next_refresh(root):
    a = arranger(root)
    saved = boxes()
    drag(a, "aio", (-1920, 0))
    moved = a.boxes
    a.set_boxes(saved)                           # the 700ms poll
    assert a.boxes == moved, "the refresh undid the move before Apply"


def test_a_pending_move_still_takes_in_new_sizes(root):
    a = arranger(root)
    drag(a, "aio", (-1920, 0))
    where = {b["name"]: (b["x"], b["y"]) for b in a.boxes}
    grown = boxes()
    grown[1]["w"], grown[1]["h"] = 2560, 1440
    a.set_boxes(grown)
    got = {b["name"]: b for b in a.boxes}
    assert (got["aio"]["w"], got["aio"]["h"]) == (2560, 1440)
    assert {n: (b["x"], b["y"]) for n, b in got.items()} == where


def test_after_apply_or_revert_the_program_is_followed_again(root):
    a = arranger(root)
    saved = boxes()
    drag(a, "aio", (-1920, 0))
    a.dirty = False                              # what Apply and Revert do
    a.set_boxes(saved)
    assert a.boxes == saved


def test_a_refresh_during_a_drag_is_ignored(root):
    a = arranger(root)
    r = a.desk.get("aio").world()[0]
    a._grab(ev(*a._to_screen(r.x + 10, r.y + 10)))
    a.set_boxes(boxes((0, 5000)))
    assert a.desk.get("aio").y == 0
    a._drop(ev(0, 0))


def test_a_crossing_to_a_machine_that_is_offline_does_not_look_live(root):
    """Drawn bright, it said the pointer could go there - it cannot."""
    a = arranger(root)
    assert len(a.canvas.find_withtag("crossing")) == 1
    a.set_online(["laptop"])
    assert not a.canvas.find_withtag("crossing")
    assert a.canvas.find_withtag("crossing_off")
