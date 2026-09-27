"""Resizable boxes, copies of machines, and the doorways they compile to.

Asked for: boxes that can be resized so borders line up however the screens
really sit, machines placed again so the pointer wraps round - "if I swipe
right of the AIO, it goes to the laptop's additional monitor, left" - and:
"resizing should not affect mouse sensitivity anywhere ... the calculation
should be 100% accurate so no bug, exploit or crash occurs".

So besides the examples, three kinds of proof:
  - SAME AS BEFORE: with no resizing and no copies, the doorway engine must do
    exactly what the old one did. The old one is kept below and both are run
    over thousands of random arrangements and moves.
  - INVARIANTS: over random resized arrangements with copies, whatever the
    move - the pointer ends on a real display, moves by exactly its delta
    inside a machine, and comes straight back across a border.
  - JUNK: anything that arrives as an arrangement is either a desk or a
    ValueError. Never another exception.
"""
import random

import pytest

from link import desk as D
from link.desk import Desk, place
from link.motion import Cursor

EXTERNAL = (0, 0, 1920, 1080)
PANEL = (1920, 148, 1366, 768)


def laptop_desk():
    """The real one: the laptop's external monitor, its panel to the right,
    the AIO to the right of the panel."""
    d = Desk("laptop")
    d.add("laptop", 3286, 1080, parts=(EXTERNAL, PANEL))
    d.add("aio", 1920, 1080, owner="aio", x=3286, y=0)
    return d


def at(c):
    return c.screen, c.x, c.y


# =============================================================== copies
def test_the_wrap_round_that_was_asked_for():
    """laptop external <> laptop panel <> aio, and right from the AIO comes
    back to the external monitor's left."""
    d = laptop_desk()
    d.add_copy("laptop", x=3286 + 1920, y=0)
    d.check()
    c = Cursor(d, "aio", 1915, 600)
    s = c.move(10, 0)
    assert s.crossed and at(c) == ("laptop", 5, 600)
    c.move(-10, 0)                                # and straight back
    assert at(c) == ("aio", 1915, 600)


def test_a_copy_is_a_doorway_not_a_place():
    d = laptop_desk()
    d.add_copy("laptop", x=3286 + 1920, y=0)
    c = Cursor(d, "aio", 1919, 300)
    c.move(3000, 0)                   # far past where the copy is drawn
    assert c.screen == "laptop" and 0 <= c.x < 3286


def test_copies_are_saved_and_come_back():
    d = laptop_desk()
    d.add_copy("laptop", x=5206, y=0, ww=1643, wh=540)
    again = place("laptop", d.boxes())
    assert again.boxes() == d.boxes()
    assert again.copies()[0].key == ("laptop", 1)


def test_a_border_leading_to_two_places_is_refused():
    """The laptop's left edge touches a copy of the AIO; the laptop's own
    copy has something else against its left edge too. Left from the laptop
    would have two answers."""
    d = laptop_desk()
    d.add("desk2", 1920, 1080, owner="desk2", x=-1920, y=0)
    d.add_copy("laptop", x=0, y=1080)
    d.add_copy("aio", x=-1920, y=1080)
    with pytest.raises(ValueError, match="leads to both"):
        d.check()
    assert any("leads to both" in p for p in d.problems())


def test_removing_a_machine_removes_its_copies():
    d = laptop_desk()
    d.add_copy("aio", x=-1920, y=0)
    d.remove("aio")
    assert d.copies() == []


def test_a_renamed_machine_keeps_its_copies():
    d = laptop_desk()
    d.add_copy("aio", x=-1920, y=0)
    d.rename("aio", "desktop")
    assert [p.key for p in d.copies()] == [("desktop", 1)]


def test_there_is_a_limit_to_copies():
    d = laptop_desk()
    for i in range(D.MAX_COPIES):
        d.add_copy("aio", x=10_000 * (i + 1), y=5000)
    with pytest.raises(ValueError):
        d.add_copy("aio")


def test_a_machine_can_wrap_round_onto_itself():
    d = Desk("laptop")
    d.add("laptop", 1920, 1080)
    d.add("aio", 1920, 1080, owner="aio", x=0, y=1080)
    d.add_copy("laptop", x=1920, y=0)
    c = Cursor(d, "laptop", 1919, 500)
    s = c.move(3, 0)
    assert s.crossed and at(c) == ("laptop", 2, 500)


def test_wrapping_onto_this_machine_moves_the_real_pointer():
    from link.node import NodeCore
    d = Desk("laptop")
    d.add("laptop", 1920, 1080)
    d.add("aio", 1920, 1080, owner="aio", x=0, y=1080)
    d.add_copy("laptop", x=1920, y=0)
    core = NodeCore("laptop", d, {}, is_hub=True, side="right")
    a = core.local_pointer("laptop", 1919, 500, 6, 0)
    assert ("move_abs", 5, 500) in a.inject


# ================================================================ sizes
def test_a_resized_box_maps_its_whole_border_onto_the_other():
    """The panel is 768 tall and the AIO 1080: drawn the same height, the
    panel's top pixel meets the AIO's top, its bottom the AIO's bottom."""
    d = Desk("laptop")
    d.add("laptop", 1366, 768)
    d.add("aio", 1920, 1080, owner="aio", x=1920, y=0)
    d.set_size("laptop", 1920, 1080)
    for y, want in ((0, 0), (767, 1079), (384, 540)):
        c = Cursor(d, "laptop", 1365, y)
        c.move(1, 0)
        assert at(c) == ("aio", 0, want), y


def test_resizing_never_changes_the_speed():
    d = Desk("laptop")
    d.add("laptop", 1366, 768)
    d.add("aio", 1920, 1080, owner="aio", x=1366, y=0)
    for size in ((300, 200), (1366, 768), (5000, 90)):
        d.set_size("laptop", *size)
        c = Cursor(d, "laptop", 100, 100)
        c.move(37, 23)
        assert at(c) == ("laptop", 137, 123), size


def test_the_rest_of_a_move_carries_on_in_the_other_machines_pixels():
    d = Desk("laptop")
    d.add("laptop", 1366, 768, sw=683, sh=384)          # drawn at half size
    d.add("aio", 1920, 1080, owner="aio", x=683, y=0)
    c = Cursor(d, "laptop", 1360, 200)
    c.move(20, 0)                     # 5 to the border, 1 across, 14 beyond
    assert c.screen == "aio" and c.x == 14


def test_back_to_the_aspect_ratio_and_to_actual_size():
    d = laptop_desk()
    d.set_size("aio", 1000, 900)
    assert (d.instance("aio").ww, d.instance("aio").wh) == (1000, 900)
    d.keep_aspect("aio")
    assert (d.instance("aio").ww, d.instance("aio").wh) == (1000, 563)
    d.actual_size("aio")
    assert d.get("aio").scaled is False and "sw" not in d.get("aio").box()


def test_a_box_cannot_be_made_absurd():
    d = laptop_desk()
    d.set_size("aio", 1, 10 ** 9)
    p = d.instance("aio")
    assert p.ww >= D.MIN_BOX and p.wh <= 1080 * D.MAX_SCALE


def test_a_new_monitor_keeps_a_resized_box_in_proportion():
    d = Desk("laptop")
    d.add("laptop", 1920, 1080, sw=960, sh=540)
    d.add("aio", 1920, 1080, owner="aio", x=960, y=0)
    d.resize("laptop", 3840, 1080, parts=((0, 0, 1920, 1080), (1920, 0, 1920, 1080)))
    p = d.instance("laptop")
    assert (p.ww, p.wh) == (1920, 540)
    assert d.instance("aio").x == 1920, "pushed along by the growth"


def test_a_border_snaps_to_a_border_while_resizing():
    d = laptop_desk()
    r, guides = d.snap_edges("aio", D.Rect(3286, 0, 1920, 1071), {"bottom"}, 20)
    assert r.bottom == 1080 and guides


# ========================================================= same as before
def _old_move(desk, screen, x, y, dx, dy, allowed=None):
    """The engine before doorways, verbatim in effect: one plane of pixels,
    sliding along rows and columns of every display of every machine."""
    def interval(spans, at):
        run = None
        for lo, hi in sorted(spans):
            if run and lo <= run[1]:
                run = (run[0], max(run[1], hi))
                continue
            if run and run[0] <= at < run[1]:
                return run
            run = (lo, hi)
        if run and run[0] <= at < run[1]:
            return run
        return None

    def sx(rects, p, d):
        if not d:
            return p
        iv = interval([(r.x, r.right) for r in rects if r.y <= p[1] < r.bottom], p[0])
        return p if iv is None else (max(iv[0], min(iv[1] - 1, p[0] + d)), p[1])

    def sy(rects, p, d):
        if not d:
            return p
        iv = interval([(r.y, r.bottom) for r in rects if r.x <= p[0] < r.right], p[1])
        return p if iv is None else (p[0], max(iv[0], min(iv[1] - 1, p[1] + d)))

    m = desk.get(screen)
    start = (m.x + x, m.y + y)
    want = (start[0] + dx, start[1] + dy)
    rects = [r for n, _, r in desk.rects()
             if allowed is None or n in allowed or n == screen]
    a = sy(rects, sx(rects, start, dx), dy)
    b = sx(rects, sy(rects, start, dy), dx)
    dist = lambda p: abs(p[0] - want[0]) + abs(p[1] - want[1])   # noqa: E731
    end = a if dist(a) <= dist(b) else b
    for mm in desk.machines():
        if allowed is not None and mm.name not in allowed and mm.name != screen:
            continue
        if mm.inside(end[0] - mm.x, end[1] - mm.y):
            return mm.name, end[0] - mm.x, end[1] - mm.y
    return screen, x, y


def random_desk(rng, copies=False, scaled=False):
    """Machines laid against each other at random - some with two displays,
    some drawn at another size, some placed again as copies."""
    d = Desk("m0")
    sizes = [(1920, 1080), (1366, 768), (2560, 1440), (1280, 1024), (800, 600)]
    for i in range(rng.randint(2, 5)):
        w, h = rng.choice(sizes)
        parts = ()
        if rng.random() < 0.3:
            w2, h2 = rng.choice(sizes)
            off = rng.randint(0, max(0, abs(h - h2)))
            H = max(h, h2)
            parts = ((0, 0 if h >= h2 else off, w, h), (w, off if h >= h2 else 0, w2, h2))
            w, h = w + w2, H
        name = f"m{i}"
        sw = sh = 0
        if scaled and rng.random() < 0.6:
            sw = max(D.MIN_BOX, int(w * rng.uniform(0.3, 2.5)))
            sh = max(D.MIN_BOX, int(h * rng.uniform(0.3, 2.5)))
        if i == 0:
            d.add(name, w, h, owner=name, parts=parts, sw=sw, sh=sh)
            continue
        for _ in range(50):
            ref = rng.choice(d.instances())
            ww, wh = sw or w, sh or h
            side = rng.choice(D.SIDES)
            if side == "right":
                x, y = ref.x + ref.ww, ref.y + rng.randint(-wh + 1, ref.wh - 1)
            elif side == "left":
                x, y = ref.x - ww, ref.y + rng.randint(-wh + 1, ref.wh - 1)
            elif side == "bottom":
                x, y = ref.x + rng.randint(-ww + 1, ref.ww - 1), ref.y + ref.wh
            else:
                x, y = ref.x + rng.randint(-ww + 1, ref.ww - 1), ref.y - wh
            d.add(name, w, h, owner=name, parts=parts, x=x, y=y, sw=sw, sh=sh)
            if not d.overlaps():
                break
            d.remove(name)
    if copies:
        for _ in range(rng.randint(1, 3)):
            name = rng.choice(d.names())
            m = d.get(name)
            ref = rng.choice(d.instances())
            b = d.bounds()
            x, y = rng.choice([(b.right, ref.y), (b.x - m.ww, ref.y),
                               (ref.x, b.bottom), (ref.x, b.y - m.wh)])
            try:
                d.add_copy(name, x=x, y=y)
            except ValueError:
                continue
            if d.overlaps():
                d.remove_copy(d.copies()[-1].key)
    return d


def random_point(rng, desk, name):
    r = rng.choice(desk.get(name).displays())
    edge = rng.random() < 0.6          # near a border, where things happen
    if edge:
        x = rng.choice([r.x, r.right - 1, rng.randrange(r.x, r.right)])
        y = rng.choice([r.y, r.bottom - 1, rng.randrange(r.y, r.bottom)])
    else:
        x, y = rng.randrange(r.x, r.right), rng.randrange(r.y, r.bottom)
    return x, y


def random_delta(rng):
    return rng.choice([
        (rng.randint(-3, 3), rng.randint(-3, 3)),
        (rng.randint(-40, 40), rng.randint(-40, 40)),
        (rng.randint(-4000, 4000), 0), (0, rng.randint(-4000, 4000)),
        (rng.randint(-900, 900), rng.randint(-900, 900))])


@pytest.mark.parametrize("seed", range(40))
def test_with_no_resizing_and_no_copies_nothing_has_changed(seed):
    rng = random.Random(seed)
    d = random_desk(rng)
    names = d.names()
    for _ in range(250):
        name = rng.choice(names)
        x, y = random_point(rng, d, name)
        dx, dy = random_delta(rng)
        allowed = None if rng.random() < 0.7 else set(rng.sample(names, rng.randint(1, len(names))))
        c = Cursor(d, name, x, y)
        c.move(dx, dy, allowed=allowed)
        assert at(c) == _old_move(d, name, x, y, dx, dy, allowed), \
            (seed, name, x, y, dx, dy, allowed, d.boxes())


# ============================================================ invariants
@pytest.mark.parametrize("seed", range(60))
def test_whatever_the_move_the_pointer_ends_on_a_real_display(seed):
    rng = random.Random(1000 + seed)
    d = random_desk(rng, copies=True, scaled=True)
    for _ in range(300):
        name = rng.choice(d.names())
        x, y = random_point(rng, d, name)
        c = Cursor(d, name, x, y)
        dx, dy = random_delta(rng)
        c.move(dx, dy)
        assert d.get(c.screen).inside(c.x, c.y), (seed, name, x, y, dx, dy)


@pytest.mark.parametrize("seed", range(40))
def test_inside_a_machine_the_pointer_moves_by_exactly_its_delta(seed):
    rng = random.Random(2000 + seed)
    d = random_desk(rng, copies=True, scaled=True)
    for _ in range(300):
        name = rng.choice(d.names())
        r = rng.choice(d.get(name).displays())
        x, y = rng.randrange(r.x, r.right), rng.randrange(r.y, r.bottom)
        dx = rng.randint(r.x - x, r.right - 1 - x)
        dy = rng.randint(r.y - y, r.bottom - 1 - y)
        c = Cursor(d, name, x, y)
        s = c.move(dx, dy)
        assert not s.crossed and at(c) == (name, x + dx, y + dy)


@pytest.mark.parametrize("seed", range(40))
def test_one_pixel_across_a_border_and_one_back_comes_home(seed):
    """Tolerance: where one border is longer in pixels than the other, a
    pixel on the short side covers several on the long one."""
    rng = random.Random(3000 + seed)
    d = random_desk(rng, copies=True, scaled=True)
    if d.conflicts():
        return
    for door in d.doors():
        for t in {door.a, door.b - 1, rng.randrange(door.a, door.b)}:
            if door.side in ("left", "right"):
                x, y = (door.edge - 1 if door.side == "right" else door.edge), t
                if not d.get(door.src).inside(x, y):
                    continue
                c = Cursor(d, door.src, x, y)
                step = 1 if door.side == "right" else -1
                c.move(step, 0)
                if c.screen != door.dst:
                    continue          # another door of the same border won
                c.move(-step, 0)
                back = c.y - t
            else:
                x, y = t, (door.edge - 1 if door.side == "bottom" else door.edge)
                if not d.get(door.src).inside(x, y):
                    continue
                c = Cursor(d, door.src, x, y)
                step = 1 if door.side == "bottom" else -1
                c.move(0, step)
                if c.screen != door.dst:
                    continue
                c.move(0, -step)
                back = c.x - t
            slack = -(-(door.b - door.a) // (door.d - door.c))
            assert c.screen == door.src and abs(back) <= slack, (seed, door)


@pytest.mark.parametrize("seed", range(30))
def test_every_door_maps_in_order_and_inside_its_border(seed):
    rng = random.Random(4000 + seed)
    d = random_desk(rng, copies=True, scaled=True)
    for door in d.doors():
        prev = None
        for t in range(door.a, door.b, max(1, (door.b - door.a) // 97)):
            u = door.map(t)
            assert door.c <= u < door.d
            assert prev is None or u >= prev
            prev = u
        m = d.get(door.dst)
        if door.side in ("left", "right"):
            assert m.inside(door.land, door.map(door.a))
        else:
            assert m.inside(door.map(door.a), door.land)


def test_the_same_length_maps_pixel_for_pixel():
    d = laptop_desk()
    for door in d.doors():
        if door.b - door.a == door.d - door.c:
            assert all(door.map(t) == door.c + t - door.a
                       for t in range(door.a, door.b))


@pytest.mark.parametrize("seed", range(30))
def test_saved_and_loaded_it_is_the_same_arrangement(seed):
    d = random_desk(random.Random(5000 + seed), copies=True, scaled=True)
    again = place("m0", d.boxes())
    assert again.boxes() == d.boxes()
    assert [(x.src, x.side, x.edge, x.a, x.b, x.dst, x.c, x.d) for x in again.doors()] \
        == [(x.src, x.side, x.edge, x.a, x.b, x.dst, x.c, x.d) for x in d.doors()]


# ================================================================== junk
JUNK = [None, "", "x", 0, -1, 1.5, 10 ** 30, -(10 ** 30), True, [], {}, [1, 2],
        {"a": 1}, float("nan"), float("inf")]


@pytest.mark.parametrize("seed", range(40))
def test_anything_that_arrives_is_a_desk_or_a_value_error(seed):
    rng = random.Random(6000 + seed)
    good = random_desk(rng, copies=True, scaled=True).boxes()
    for _ in range(80):
        boxes = [dict(b) for b in good]
        for _ in range(rng.randint(1, 4)):
            b = rng.choice(boxes) if boxes else {}
            key = rng.choice(["name", "w", "h", "x", "y", "sw", "sh", "owner",
                              "parts", "copy_of", "n"])
            b[key] = rng.choice(JUNK + [rng.randint(-10 ** 7, 10 ** 7)])
        if rng.random() < 0.2:
            boxes.append(rng.choice(JUNK))
        try:
            d = place("m0", boxes)
            d.check()
            d.doors()
        except ValueError:
            continue


@pytest.mark.parametrize("boxes", [
    None, "screens", 7, [None], [[]], [{"name": "m0"}],
    [{"name": "m0", "w": 0, "h": 1}],
    [{"name": "m0", "w": 10, "h": 10, "sw": 10 ** 9, "sh": 10}],
    [{"name": "m0", "w": 10, "h": 10, "x": 10 ** 12}],
    [{"name": "m0", "w": 10, "h": 10}, {"copy_of": "m0", "n": 99}],
    [{"name": "m0", "w": 10, "h": 10}, {"copy_of": "m0", "n": 1},
     {"copy_of": "m0", "n": 1, "x": 500}],
])
def test_nonsense_is_refused_in_words(boxes):
    with pytest.raises(ValueError):
        d = place("m0", boxes)
        d.check()


def test_a_copy_of_a_machine_that_left_is_dropped():
    d = place("m0", [{"name": "m0", "w": 10, "h": 10},
                     {"copy_of": "gone", "n": 1, "x": 10, "y": 0}])
    assert d.copies() == []
