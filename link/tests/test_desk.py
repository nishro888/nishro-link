"""The arrangement engine. See desk.py.

Most of these use a real desk: a laptop whose desktop is two displays - a
1920x1080 external monitor, and to its right the 1366x768 panel, 148 px lower -
and an AIO with one 1920x1080 display.
"""
import pytest

from link.desk import Desk, Rect, beside, place, simple

EXTERNAL = (0, 0, 1920, 1080)
PANEL = (1920, 148, 1366, 768)


def desk(aio_at):
    """the test laptop at the origin and the AIO at `aio_at`."""
    d = Desk("laptop")
    d.add("laptop", 3286, 1080, parts=(EXTERNAL, PANEL))
    d.add("aio", 1920, 1080, owner="aio", x=aio_at[0], y=aio_at[1])
    return d


def segments(d):
    return sorted((c.a, c.b, c.side, c.x1, c.y1, c.x2, c.y2) for c in d.crossings())


# ------------------------------------------------------------------ basics
def test_a_machine_knows_its_displays_locally_and_in_the_world():
    d = desk((-1920, 0))
    lap = d.get("laptop")
    assert lap.displays() == [Rect(*EXTERNAL), Rect(*PANEL)]
    aio = d.get("aio")
    assert aio.world() == [Rect(-1920, 0, 1920, 1080)]


def test_holes_between_displays_are_not_inside():
    lap = desk((-1920, 0)).get("laptop")
    assert lap.inside(2500, 500)            # on the panel
    assert not lap.inside(2500, 50)         # above the panel: a hole
    assert lap.nearest(2500, 50) == (2500, 148)


def test_who_is_where():
    d = desk((-1920, 0))
    assert d.mine() == ["laptop"] and d.is_local("laptop")
    assert not d.is_local("aio") and d.owners() == {"laptop", "aio"}
    assert d.at(-5, 10) == ("aio", 1915, 10)
    assert d.at(2500, 50) is None, "a hole belongs to nobody"


@pytest.mark.parametrize("bad", [("", 10, 10), ("x", 0, 10), ("x", 10, -1)])
def test_nonsense_machines_are_refused(bad):
    with pytest.raises(ValueError):
        Desk("laptop").add(*bad)


def test_names_are_unique():
    d = Desk("laptop")
    d.add("laptop", 10, 10)
    with pytest.raises(ValueError):
        d.add("laptop", 10, 10)


# --------------------------------------------------------------- crossings
def test_aio_on_the_left_meets_the_external_display_along_its_full_height():
    assert segments(desk((-1920, 0))) == [
        ("laptop", "aio", "left", 0, 0, 0, 1080)]


def test_aio_on_the_right_meets_only_the_panel():
    """The panel is 768 tall and 148 down; only that stretch connects."""
    assert segments(desk((3286, 0))) == [
        ("laptop", "aio", "right", 3286, 148, 3286, 916)]


def test_aio_above_the_second_monitor():
    """The motivating example. Up from the external monitor reaches the AIO; the panel
    beside it has nothing above it at all."""
    assert segments(desk((0, -1080))) == [
        ("laptop", "aio", "top", 0, 0, 1920, 0)]


def test_aio_above_the_panel_also_touches_the_external_monitors_side():
    """Above the panel, the AIO's lower-left corner hangs beside the taller
    external monitor - a second, short crossing that is really there."""
    got = segments(desk((1920, 148 - 1080)))
    assert ("laptop", "aio", "right", 1920, 0, 1920, 148) in got
    assert ("laptop", "aio", "top", 1920, 148, 3286, 148) in got
    assert len(got) == 2


def test_a_corner_is_not_a_crossing():
    assert segments(desk((-1920, -1080))) == []


def test_a_gap_is_not_a_crossing():
    assert segments(desk((-1930, 0))) == []


def test_a_machines_own_displays_are_not_crossings():
    d = Desk("laptop")
    d.add("laptop", 3286, 1080, parts=(EXTERNAL, PANEL))
    assert d.crossings() == []


def test_touching_totals_per_neighbour():
    assert desk((3286, 0)).touching("aio") == {"laptop": 768}


# ---------------------------------------------------------------- problems
def test_overlap_is_found_and_refused():
    d = desk((1000, 0))
    assert d.overlaps() == [("aio", "laptop")]
    with pytest.raises(ValueError, match="overlap"):
        d.check()


def test_sitting_in_the_panels_hole_is_not_overlap():
    """The region above the panel is empty; a small machine may sit there."""
    d = desk((0, 0))
    d.remove("aio")
    d.add("tablet", 1200, 148, owner="tablet", x=2000, y=0)
    assert d.overlaps() == []
    assert d.crossings(), "and it touches the panel below and the monitor beside"


def test_a_machine_touching_nothing_is_named():
    d = desk((-3000, 0))
    assert d.unreachable() == ["aio"]
    assert any("aio does not touch anything" in p for p in d.problems())
    d.check()                                   # still usable for the rest


def test_reachable_through_another_machine():
    d = desk((-1920, 0))
    d.add("tv", 1000, 500, owner="tv", x=-1920 - 1000, y=0)   # left of the AIO
    assert d.unreachable() == []


def test_an_arrangement_without_this_machine_is_refused():
    d = Desk("laptop")
    d.add("aio", 100, 100, owner="aio")
    with pytest.raises(ValueError, match="no screen belonging"):
        d.check()


# ----------------------------------------------------------- dragging help
def test_snapping_pulls_a_near_miss_against_the_edge():
    d = desk((-1920, 0))
    x, y, guides = d.snap("aio", -1935, 7, tolerance=40)
    assert (x, y) == (-1920, 0)
    assert guides, "and says why, so the screen can show it"


def test_snapping_lines_up_tops():
    """Dropped beside the panel a little below its top: the AIO's top lines up
    with the panel's (148), the nearest line within reach of 160."""
    d = desk((3286, 400))
    _, y, guides = d.snap("aio", 3286, 160, tolerance=40)
    assert y == 148
    assert any(g[1] == g[3] == 148 for g in guides), "a guide along that line"


def test_snapping_lines_up_centres():
    """The panel's centre is at 148 + 384 = 532; the AIO's is 540 below its
    top, so centring puts its top at -8."""
    d = desk((3286, 400))
    _, y, _ = d.snap("aio", 3286, -20, tolerance=15)
    assert y == -8


def test_nothing_near_means_no_snap():
    d = desk((5000, 3000))
    assert d.snap("aio", 5000, 3000, tolerance=10)[:2] == (5000, 3000)


def test_a_drop_on_top_of_another_machine_is_pushed_clear_the_short_way():
    d = desk((3000, 100))                       # overlapping the panel's right end
    d.resolve("aio")
    assert d.overlaps() == []
    assert d.get("aio").x == 3286, "out to the right, the nearest way clear"


def test_resolving_a_clear_machine_changes_nothing():
    d = desk((-1920, 0))
    d.resolve("aio")
    assert (d.get("aio").x, d.get("aio").y) == (-1920, 0)


def test_in_a_row_and_in_a_column():
    d = desk((5000, 5000))
    d.row()
    assert d.overlaps() == [] and d.unreachable() == []
    assert d.get("aio").x == 3286
    d.column()
    assert d.overlaps() == [] and d.unreachable() == []
    assert d.get("aio").y >= 1080, "one above the other"


def test_normalise_starts_the_arrangement_at_the_origin():
    d = desk((-1920, -50))
    d.normalise()
    assert d.bounds().x == 0 and d.bounds().y == 0
    assert segments(d)[0][2] == "left", "and changes nothing about what touches"


# ------------------------------------------------------ a machine that grew
def test_growing_pushes_neighbours_along_so_nothing_breaks():
    """A laptop first known by its panel, then by its whole desktop."""
    d = Desk("laptop")
    d.add("laptop", 1366, 768)
    d.add("aio", 1920, 1080, owner="aio", x=1366, y=0)
    d.resize("laptop", 3286, 1080, (EXTERNAL, PANEL))
    assert d.get("aio").x == 3286
    assert d.overlaps() == []


def test_shrinking_pulls_them_back():
    d = desk((3286, 0))
    d.resize("laptop", 1920, 1080)
    assert d.get("aio").x == 1920


def test_a_placeholder_takes_the_real_name():
    d = simple("laptop", (1366, 768), "peer", (1920, 1080), "right")
    d.rename("peer", "aio")
    assert d.get("aio").owner == "aio" and "peer" not in d.names()


# ------------------------------------------------------- building a desk
@pytest.mark.parametrize("side", ["left", "right", "top", "bottom"])
def test_simple_puts_the_peer_touching_on_that_side(side):
    d = simple("laptop", (1366, 768), "aio", (1920, 1080), side)
    assert d.overlaps() == [] and d.unreachable() == []
    (c,) = d.crossings()
    assert {c.a, c.b} == {"laptop", "aio"}


def test_a_new_machine_goes_against_the_edge_of_everything():
    d = desk((-1920, 0))
    beside(d, "tv", 1280, 720, "tv", "right")
    assert d.overlaps() == [] and d.unreachable() == []


def test_the_arrangement_survives_the_wire():
    d = desk((1920, 148 - 1080))
    back = Desk.from_dict(d.to_dict(), node="aio")
    assert back.boxes() == d.boxes()
    assert back.mine() == ["aio"], "the same desk, seen by the other machine"
    assert segments(back) == segments(d)


def test_saved_boxes_become_a_desk():
    d = place("laptop", [{"name": "laptop", "w": 100, "h": 50, "x": 0, "y": 0},
                         {"name": "aio", "w": 80, "h": 50, "x": 100, "y": 0,
                          "owner": "aio"}])
    assert d.get("laptop").owner == "laptop"
    assert [c.side for c in d.crossings()] == ["right"]


def test_a_description_says_what_touches_what():
    text = desk((3286, 0)).describe("aio")
    assert "1920×1080" in text and "crosses to laptop (768 px)" in text
    assert "touches nothing" in desk((-3000, 0)).describe("aio")
