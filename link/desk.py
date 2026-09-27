"""The arrangement: every machine's displays, placed on one shared plane.

This module is the one place that knows where things are. The pointer's movement
(motion.py) and the arrangement screen (ui_arrange.py) both ask it, so what the
screen draws is exactly what the pointer does. Pure data and integer arithmetic
- no OS calls, no sockets, no floating point - so every rule here is provable in
a test.

THE MODEL. A desk is a plane of integer units. On it sit machines; a machine is
a rigid group of displays. Each display is a rectangle in its machine's own
desktop coordinates, in pixels, exactly as that machine's operating system
arranges them - a laptop with an external monitor to its left is one machine
with two displays, and their relative position is Windows' business, never
changed here. What is arranged is where each MACHINE sits, and how big its box
is drawn.

A BOX'S SIZE is the machine's pixels unless it is resized (sw, sh). Resizing
says only where borders meet, never how fast the pointer moves: the pointer
always moves in the pixels of the machine it is on, and only where it crosses
from one machine to another is the size used - the crossing point is carried
across in proportion along the shared border. A box need not keep its aspect
ratio; a border is a line, and each of the four is placed on its own.

COPIES. A machine may be placed again, as a copy: the same machine, its whole
set of displays, somewhere else on the plane, at its own size. A copy is a
doorway, never a place - where another machine's border touches a copy, the
pointer crosses to (and from) the REAL machine, at the same point of its
border. That is how the pointer wraps round: a copy of the laptop's desktop to
the right of the AIO makes right from the AIO arrive at the laptop's left, and
left from the laptop arrive at the AIO's right.

DOORWAYS. The arrangement is compiled into doors(): for each machine, each side
and each border line of its desktop, the stretches of that border that lead
somewhere - which machine, which of its borders, which stretch - with the
mapping between the two in exact integer arithmetic. The pointer consults only
that map. Compiling is where every rule is checked, so what reaches the pointer
is always consistent.

THE RULES:
  - boxes must TOUCH to connect. A gap is a wall; touching only at a corner is
    no doorway, as with real monitors.
  - boxes never OVERLAP - no machine, no copy. A point would belong to two.
  - no stretch of a border may lead to two places. With copies it could: the
    laptop's left border touching one thing, and its copy's left border
    another. check() refuses that and says where.

Rectangles are half-open: a display at x=0 with w=1920 covers 0..1919, and the
one to its right starts at 1920. Touching means one's right == the other's left.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace

SIDES = ("left", "right", "top", "bottom")
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}

# What an arrangement may hold. Checked wherever one comes in - from the
# window, from the hub over the network, from a saved file - so no arithmetic
# here ever meets an absurd number.
MAX_COORD = 10_000_000       # |x|, |y| of any box
MAX_PIXELS = 100_000         # a desktop's width or height, in pixels
MAX_SCALE = 16               # a box may be drawn up to 16 times its pixels,
MIN_BOX = 8                  # and down to 1/16 - but never under 8 units
MAX_COPIES = 8               # copies of any one machine
MAX_MACHINES = 64
SLIVER = 2                   # border pixels two doors may share by rounding


# ------------------------------------------------------------------ shapes
@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom

    def overlaps(self, o: "Rect") -> bool:
        """Share any AREA. Touching along an edge is not overlapping."""
        return (self.x < o.right and o.x < self.right
                and self.y < o.bottom and o.y < self.bottom)

    def moved(self, dx: int, dy: int) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)


@dataclass(frozen=True)
class Machine:
    """One machine: where it sits, its desktop size, and its displays.

    `w`, `h` are its whole desktop in pixels (the bounding box of every
    display) and `parts` the displays inside it as (x, y, w, h). Empty `parts`
    means one display filling the desktop. `owner` is the node that drives it.
    `sw`, `sh` are the size its box is drawn at, 0 meaning its pixels.

    Local coordinates - what the wire and the OS speak - count pixels from the
    desktop's corner. World coordinates are the plane's.
    """
    name: str
    w: int
    h: int
    owner: str
    parts: tuple = ()
    x: int = 0
    y: int = 0
    sw: int = 0
    sh: int = 0

    @property
    def ww(self) -> int:
        """The box's width on the plane."""
        return self.sw or self.w

    @property
    def wh(self) -> int:
        return self.sh or self.h

    @property
    def scaled(self) -> bool:
        return (self.ww, self.wh) != (self.w, self.h)

    def displays(self) -> list:
        """Each display as a Rect in LOCAL coordinates."""
        if not self.parts:
            return [Rect(0, 0, self.w, self.h)]
        return [Rect(*p) for p in self.parts]

    def world(self) -> list:
        """Each display as a Rect in WORLD coordinates."""
        return _world(self, self.x, self.y, self.ww, self.wh)

    def inside(self, x: int, y: int) -> bool:
        """Is this local point on one of the displays - not in a hole between?"""
        return any(r.contains(x, y) for r in self.displays())

    def nearest(self, x: int, y: int) -> tuple:
        """The closest local point where the pointer can actually be."""
        best = None
        for r in self.displays():
            cx = max(r.x, min(r.right - 1, x))
            cy = max(r.y, min(r.bottom - 1, y))
            d = (cx - x) ** 2 + (cy - y) ** 2
            if best is None or d < best[0]:
                best = (d, cx, cy)
        return best[1], best[2]

    def box(self) -> dict:
        """As saved in the config and sent on the wire."""
        d = {"name": self.name, "owner": self.owner, "x": self.x, "y": self.y,
             "w": self.w, "h": self.h}
        if self.parts:
            d["parts"] = [list(p) for p in self.parts]
        if self.scaled:
            d["sw"], d["sh"] = self.ww, self.wh
        return d


@dataclass(frozen=True)
class Copy:
    """Machine `of` placed again: copy number `n`, at (x, y), drawn ww x wh."""
    of: str
    n: int
    x: int
    y: int
    ww: int
    wh: int

    def box(self) -> dict:
        return {"copy_of": self.of, "n": self.n, "x": self.x, "y": self.y,
                "sw": self.ww, "sh": self.wh}


@dataclass(frozen=True)
class Placed:
    """One box on the plane: a machine (n=0) or one of its copies (n>=1)."""
    name: str
    n: int
    x: int
    y: int
    ww: int
    wh: int

    @property
    def key(self) -> tuple:
        return (self.name, self.n)

    @property
    def label(self) -> str:
        return self.name if not self.n else f"{self.name} (copy {self.n})"

    @property
    def rect(self) -> Rect:
        return Rect(self.x, self.y, self.ww, self.wh)


@dataclass(frozen=True)
class Crossing:
    """A stretch of edge where the pointer passes from one box to another.

    Seen from `a`: `side` is which of a's edges it is. The segment runs from
    (x1, y1) to (x2, y2) in world coordinates - vertical for left/right, so
    x1 == x2, horizontal for top/bottom. `a_n`, `b_n` say which box of each
    machine: 0 the machine itself, 1 and up its copies.
    """
    a: str
    a_display: int
    b: str
    b_display: int
    side: str
    x1: int
    y1: int
    x2: int
    y2: int
    a_n: int = 0
    b_n: int = 0

    @property
    def length(self) -> int:
        return abs(self.x2 - self.x1) + abs(self.y2 - self.y1)


@dataclass(frozen=True)
class Door:
    """Where a stretch of one machine's border leads, in pixels on both sides.

    Leaving machine `src` through `side` across the border line `edge` (the
    local x of a left/right border, the local y of a top/bottom one), at a
    position t along it with a <= t < b, the pointer arrives on `dst` at
    `land` across the border and map(t) along it, within [c, d).
    """
    src: str
    src_n: int
    side: str
    edge: int
    a: int
    b: int
    dst: str
    dst_n: int
    land: int
    c: int
    d: int
    segment: tuple           # the shared border on the plane, for drawing

    def map(self, t: int) -> int:
        """t on this border -> the matching pixel on the other. Pixel centre
        to pixel centre, so the same length maps one to one, and back again."""
        t = min(max(int(t), self.a), self.b - 1)
        return self.c + ((2 * (t - self.a) + 1) * (self.d - self.c)) // (
            2 * (self.b - self.a))


# -------------------------------------------------------------------- desk
class Desk:
    """Machines on a plane. `node` is the machine asking - it answers is_local()."""

    def __init__(self, node: str):
        if not node:
            raise ValueError("a desk must know which node it belongs to")
        self.node = node
        self._m: dict[str, Machine] = {}
        self._c: dict[tuple, Copy] = {}
        self._compiled = None

    def _changed(self) -> None:
        self._compiled = None

    # ---- building ----
    def add(self, name: str, w: int, h: int, owner: str = None, parts=(),
            x: int = 0, y: int = 0, sw: int = 0, sh: int = 0) -> Machine:
        if not name or not isinstance(name, str):
            raise ValueError("a machine needs a name")
        if name in self._m:
            raise ValueError(f"duplicate machine {name!r}")
        if len(self._m) >= MAX_MACHINES:
            raise ValueError("too many machines")
        if owner is not None and not isinstance(owner, str):
            raise ValueError(f"machine {name!r} has no proper owner")
        w, h = _int(w, "width"), _int(h, "height")
        if not (1 <= w <= MAX_PIXELS and 1 <= h <= MAX_PIXELS):
            raise ValueError(f"machine {name!r} has an impossible size {w}x{h}")
        x, y = _coord(x), _coord(y)
        sw, sh = _int(sw or 0, "width"), _int(sh or 0, "height")
        if (sw or sh) and not (_size_ok(sw or w, w) and _size_ok(sh or h, h)):
            raise ValueError(f"{name!r} is drawn at an impossible size {sw}x{sh}")
        m = Machine(name, w, h, owner or self.node, normal_parts(parts, w, h),
                    x, y, *_store(sw or w, sh or h, w, h))
        self._m[name] = m
        self._changed()
        return m

    def add_copy(self, name: str, x: int = None, y: int = None, ww: int = None,
                 wh: int = None, n: int = None) -> Placed:
        """Place machine `name` again. Unless told where: clear of everything
        on the right, level with the machine, at the machine's size - touching
        nothing, so it changes nothing until it is dragged where it belongs.
        Placed flush it could make a border lead to two places at once."""
        m = self.get(name)
        taken = {k[1] for k in self._c if k[0] == name}
        if n is None:
            free = [i for i in range(1, MAX_COPIES + 1) if i not in taken]
            if not free:
                raise ValueError(f"{name} already has {MAX_COPIES} copies")
            n = free[0]
        n = _int(n, "copy number")
        if not 1 <= n <= MAX_COPIES or n in taken:
            raise ValueError(f"no room for copy {n} of {name}")
        ww = m.ww if ww is None else _int(ww, "width")
        wh = m.wh if wh is None else _int(wh, "height")
        if not (_size_ok(ww, m.w) and _size_ok(wh, m.h)):
            raise ValueError(f"a copy of {name} at an impossible size {ww}x{wh}")
        if x is None or y is None:
            b = self.bounds()
            x, y = b.right + max(MIN_BOX, ww // 8), m.y
        self._c[(name, n)] = Copy(name, n, _coord(x), _coord(y), ww, wh)
        self._changed()
        return self.instance((name, n))

    def remove_copy(self, key) -> None:
        key = _key(key)
        if key not in self._c:
            raise KeyError(f"no such copy: {key!r}")
        del self._c[key]
        self._changed()

    def remove(self, name: str) -> None:
        self._require(name)
        del self._m[name]
        for k in [k for k in self._c if k[0] == name]:
            del self._c[k]
        self._changed()

    def move(self, key, x: int, y: int):
        """Put a box - a machine, by name, or a copy, by (name, n) - at (x, y)."""
        name, n = _key(key)
        x, y = _coord(x), _coord(y)
        if n == 0:
            self._require(name)
            self._m[name] = replace(self._m[name], x=x, y=y)
            self._changed()
            return self._m[name]
        c = self._copy((name, n))
        self._c[(name, n)] = replace(c, x=x, y=y)
        self._changed()
        return self._c[(name, n)]

    def set_size(self, key, ww: int, wh: int) -> Placed:
        """Draw a box at ww x wh - clamped to what a box may be. The machine's
        pixels do not change; only where its borders meet others' does."""
        name, n = _key(key)
        m = self.get(name)
        ww, wh = _clamp_size(int(ww), m.w), _clamp_size(int(wh), m.h)
        if n == 0:
            sw, sh = _store(ww, wh, m.w, m.h)
            self._m[name] = replace(m, sw=sw, sh=sh)
        else:
            self._c[(name, n)] = replace(self._copy((name, n)), ww=ww, wh=wh)
        self._changed()
        return self.instance((name, n))

    def place_box(self, key, x: int, y: int, ww: int, wh: int) -> Placed:
        """Move and size a box at once - what a resize handle does."""
        self.set_size(key, ww, wh)
        self.move(key, x, y)
        return self.instance(key)

    def keep_aspect(self, key) -> Placed:
        """Back to the machine's own proportions, keeping the box's width."""
        p = self.instance(key)
        m = self.get(p.name)
        return self.set_size(key, p.ww, (2 * p.ww * m.h + m.w) // (2 * m.w))

    def actual_size(self, key) -> Placed:
        """Back to one unit per pixel."""
        m = self.get(_key(key)[0])
        return self.set_size(key, m.w, m.h)

    def rename(self, old: str, new: str, owner: str = None) -> Machine:
        """Give a machine its real name - the placeholder "peer" becomes "aio"
        once the AIO says who it is. A machine that owned itself under the old
        name owns itself under the new one. Its copies follow."""
        self._require(old)
        if new != old and new in self._m:
            raise ValueError(f"duplicate machine {new!r}")
        m = self._m.pop(old)
        if owner is None:
            owner = new if m.owner == old else m.owner
        self._m[new] = replace(m, name=new, owner=owner)
        if new != old:
            for k in [k for k in self._c if k[0] == old]:
                c = self._c.pop(k)
                self._c[(new, k[1])] = replace(c, of=new)
        self._changed()
        return self._m[new]

    def resize(self, name: str, w: int, h: int, parts=()) -> Machine:
        """Change a machine's desktop without breaking the arrangement.

        A machine can grow a lot - a monitor plugged in, or a laptop first
        described by its panel and now by its whole desktop - and growing it in
        place would lay it over its neighbours. So anything wholly to the right
        of one of its boxes moves right by that box's growth, and anything
        wholly below moves down: every adjacency survives. Shrinking pulls
        them back the same way. A resized box keeps its scale.
        """
        self._require(name)
        me = self._m[name]
        w, h = _int(w, "width"), _int(h, "height")
        if not (1 <= w <= MAX_PIXELS and 1 <= h <= MAX_PIXELS):
            raise ValueError(f"machine {name!r} has an impossible size {w}x{h}")
        for p in [p for p in self.instances() if p.name == name]:
            nw = _clamp_size(_rescale(p.ww, me.w, w), w)
            nh = _clamp_size(_rescale(p.wh, me.h, h), h)
            dw, dh = nw - p.ww, nh - p.wh
            right, below = p.x + p.ww, p.y + p.wh
            for o in self.instances():
                if o.name == name:
                    continue
                nx = o.x + dw if o.x >= right else o.x
                ny = o.y + dh if o.y >= below else o.y
                if (nx, ny) != (o.x, o.y):
                    self.move(o.key, nx, ny)
            if p.n:
                self._c[p.key] = replace(self._c[p.key], ww=nw, wh=nh)
            else:
                sw, sh = _store(nw, nh, w, h) if me.scaled else (0, 0)
                me = replace(me, sw=sw, sh=sh)
        self._m[name] = replace(me, w=w, h=h, parts=normal_parts(parts, w, h))
        self._changed()
        return self._m[name]

    # ---- asking ----
    def get(self, name: str) -> Machine:
        self._require(name)
        return self._m[name]

    def names(self) -> list:
        return list(self._m)

    def machines(self) -> list:
        return list(self._m.values())

    def copies(self, name: str = None) -> list:
        return [self.instance(k) for k in sorted(self._c)
                if name is None or k[0] == name]

    def instances(self) -> list:
        """Every box on the plane: the machines, then their copies."""
        out = [Placed(m.name, 0, m.x, m.y, m.ww, m.wh) for m in self._m.values()]
        order = {n: i for i, n in enumerate(self._m)}
        for k in sorted(self._c, key=lambda k: (order.get(k[0], 0), k[1])):
            c = self._c[k]
            out.append(Placed(c.of, c.n, c.x, c.y, c.ww, c.wh))
        return out

    def instance(self, key) -> Placed:
        name, n = _key(key)
        if n == 0:
            m = self.get(name)
            return Placed(name, 0, m.x, m.y, m.ww, m.wh)
        c = self._copy((name, n))
        return Placed(name, n, c.x, c.y, c.ww, c.wh)

    def world_of(self, key) -> list:
        """A box's displays, as Rects on the plane."""
        p = self.instance(key)
        return _world(self.get(p.name), p.x, p.y, p.ww, p.wh)

    def is_local(self, name: str) -> bool:
        return self.get(name).owner == self.node

    def mine(self) -> list:
        return [m.name for m in self._m.values() if m.owner == self.node]

    def owners(self) -> set:
        return {m.owner for m in self._m.values()}

    def to_world(self, name: str, x: int, y: int) -> tuple:
        """A local pixel of a machine, on its own box, as a plane point."""
        m = self.get(name)
        return (m.x + _scale(int(x), m.ww, m.w), m.y + _scale(int(y), m.wh, m.h))

    def at(self, wx: int, wy: int):
        """(machine name, local x, local y) for the display under a plane
        point - on the machine or on any copy of it - or None."""
        for p in self.instances():
            m = self.get(p.name)
            for i, r in enumerate(self.world_of(p.key)):
                if r.contains(wx, wy):
                    d = m.displays()[i]
                    return (p.name, d.x + ((wx - r.x) * d.w) // max(1, r.w),
                            d.y + ((wy - r.y) * d.h) // max(1, r.h))
        return None

    def placed_rects(self) -> list:
        """Every display of every box, as ((name, n), index, Rect)."""
        return [(p.key, i, r) for p in self.instances()
                for i, r in enumerate(self.world_of(p.key))]

    def rects(self) -> list:
        """Every display of every box, as (machine name, index, Rect)."""
        return [(k[0], i, r) for k, i, r in self.placed_rects()]

    def bounds(self) -> Rect:
        rs = [r for _, _, r in self.placed_rects()]
        if not rs:
            return Rect(0, 0, 1, 1)
        x0, y0 = min(r.x for r in rs), min(r.y for r in rs)
        x1, y1 = max(r.right for r in rs), max(r.bottom for r in rs)
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def crossings(self) -> list:
        """Every stretch of edge where the pointer passes between two boxes.

        Each touching pair is reported once, from the box listed first.
        Displays of the SAME box are not crossings: moving between them is
        the operating system's job.
        """
        out = []
        rects = self.placed_rects()
        for i, (ak, ai, a) in enumerate(rects):
            for bk, bi, b in rects[i + 1:]:
                if ak == bk:
                    continue
                c = _touch(ak[0], ai, a, bk[0], bi, b)
                if c:
                    out.append(replace(c, a_n=ak[1], b_n=bk[1]))
        return out

    def touching(self, name: str) -> dict:
        """{other machine: total length of shared edge} for one machine, on
        any of its boxes."""
        out = {}
        for c in self.crossings():
            for me, other in ((c.a, c.b), (c.b, c.a)):
                if me == name:
                    out[other] = out.get(other, 0) + c.length
                    break
        return out

    def overlaps(self) -> list:
        """Pairs of boxes whose displays share area. Always a mistake. Named as
        a person would: "aio", "laptop (copy 1)"."""
        out = []
        for a, b in self._overlapping_pairs():
            pair = tuple(sorted((self.instance(a).label, self.instance(b).label)))
            if pair not in out:
                out.append(pair)
        return out

    def overlapping(self) -> set:
        """The boxes in any overlap, as (name, n) keys - for drawing them red."""
        return {k for pair in self._overlapping_pairs() for k in pair}

    def _overlapping_pairs(self) -> list:
        out = []
        rects = self.placed_rects()
        for i, (ak, _, a) in enumerate(rects):
            for bk, _, b in rects[i + 1:]:
                if ak != bk and a.overlaps(b) and (ak, bk) not in out:
                    out.append((ak, bk))
        return out

    # ---- the doorways ----
    def doors(self) -> list:
        """Every doorway, both ways round. Compiled once per change."""
        return self._compile()[0]

    def door_at(self, name: str, side: str, edge: int, t: int):
        """The door out of `name` through `side` across border line `edge`,
        at t along it - or None: a wall."""
        spans = self._compile()[1].get((name, side, edge))
        if not spans:
            return None
        starts, doors = spans
        i = bisect_right(starts, t) - 1
        # Two doors may share a sliver where rounding met; the first wins.
        for j in (i - 1, i):
            if 0 <= j < len(doors) and doors[j].a <= t < doors[j].b:
                return doors[j]
        return None

    def conflicts(self) -> list:
        """Stretches of a border that would lead to two places, as
        (machine, side, first door, second door)."""
        return self._compile()[2]

    def _compile(self):
        if self._compiled is not None:
            return self._compiled
        rects = self.placed_rects()
        doors = []
        for i, (ak, ai, a) in enumerate(rects):
            for bk, bi, b in rects[i + 1:]:
                if ak == bk:
                    continue
                c = _touch(ak[0], ai, a, bk[0], bi, b)
                if not c:
                    continue
                la = self._m[ak[0]].displays()[ai]
                lb = self._m[bk[0]].displays()[bi]
                for d in (_door(ak, la, a, bk, lb, b, c),
                          _door(bk, lb, b, ak, la, a, c, flip=True)):
                    if d:
                        doors.append(d)
        index, conflicts = {}, []
        groups = {}
        for d in doors:
            groups.setdefault((d.src, d.side, d.edge), []).append(d)
        for key, ds in groups.items():
            ds.sort(key=lambda d: (d.a, d.b, d.dst, d.dst_n))
            for prev, cur in zip(ds, ds[1:]):
                if prev.b - cur.a > SLIVER:
                    conflicts.append((key[0], key[1], prev, cur))
            index[key] = ([d.a for d in ds], ds)
        self._compiled = (doors, index, conflicts)
        return self._compiled

    def unreachable(self) -> list:
        """Machines the pointer cannot get to from this node's machine."""
        start = self.mine()
        if not start:
            return []
        links = {n: set() for n in self._m}
        for d in self.doors():
            links[d.src].add(d.dst)
        seen, todo = set(start), list(start)
        while todo:
            for n in links[todo.pop()]:
                if n not in seen:
                    seen.add(n)
                    todo.append(n)
        return [n for n in self._m if n not in seen]

    def problems(self) -> list:
        """Everything wrong with this arrangement, in words a person can act on."""
        out = [f"{a} and {b} overlap - drag one of them clear"
               for a, b in self.overlaps()]
        for name, side, one, two in self.conflicts():
            out.append(f"{name}'s {side} edge leads to both "
                       f"{self._where(one)} and {self._where(two)} - "
                       f"move one of them")
        for n in self.unreachable():
            out.append(f"{n} does not touch anything the pointer can reach - "
                       f"drag it against another screen")
        return out

    def _where(self, door: Door) -> str:
        """The other side of a door, named by the box the border touched."""
        near = self.instance((door.dst, door.dst_n)).label
        via = self.instance((door.src, door.src_n))
        return f"{near} (from {via.label})" if via.n else near

    def check(self) -> None:
        """Raise ValueError for an arrangement that cannot be used at all.

        Overlap and a border leading to two places are fatal; an unreachable
        machine is not - the arrangement still works for everything else, and
        problems() says so on screen.
        """
        if not self.mine():
            raise ValueError("the arrangement has no screen belonging to this machine")
        ov = self.overlaps()
        if ov:
            raise ValueError(f"{ov[0][0]} and {ov[0][1]} overlap")
        cf = self.conflicts()
        if cf:
            name, side, one, two = cf[0]
            raise ValueError(f"{name}'s {side} edge leads to both "
                             f"{self._where(one)} and {self._where(two)}")

    # ---- helping a hand that is dragging ----
    def snap(self, key, x: int, y: int, tolerance: int):
        """Where to put a box if it is dropped near (x, y), and the guide lines
        that explain why.

        Each axis snaps on its own, to the nearest candidate within
        `tolerance`: an edge of ours against an edge of another box's display
        (touching), or lined up with it (tops, bottoms, lefts, rights,
        centres). Returns (x, y, guides), guides as world-coordinate lines
        (x1, y1, x2, y2) to draw while dragging.
        """
        p = self.instance(key)
        mine = [r.moved(-p.x, -p.y) for r in self.world_of(p.key)]
        others = [r for k, _, r in self.placed_rects() if k != p.key]
        best_x = _best(x, tolerance, (
            (o.right - d.x, o.right) for d in mine for o in others), (
            (o.x - d.right, o.x) for d in mine for o in others), (
            (o.x - d.x, o.x) for d in mine for o in others), (
            (o.right - d.right, o.right) for d in mine for o in others), (
            (o.x + o.w // 2 - (d.x + d.w // 2), o.x + o.w // 2)
            for d in mine for o in others))
        best_y = _best(y, tolerance, (
            (o.bottom - d.y, o.bottom) for d in mine for o in others), (
            (o.y - d.bottom, o.y) for d in mine for o in others), (
            (o.y - d.y, o.y) for d in mine for o in others), (
            (o.bottom - d.bottom, o.bottom) for d in mine for o in others), (
            (o.y + o.h // 2 - (d.y + d.h // 2), o.y + o.h // 2)
            for d in mine for o in others))
        sx = best_x[0] if best_x else x
        sy = best_y[0] if best_y else y
        return sx, sy, self._guides(best_x, best_y)

    def snap_edges(self, key, box: Rect, moving: set, tolerance: int):
        """A box being resized: each border in `moving` ("left", "right",
        "top", "bottom") snaps to the nearest border line of another box
        within `tolerance`, so two borders can be made to meet exactly.
        Returns (Rect, guides)."""
        p = self.instance(key)
        others = [r for k, _, r in self.placed_rects() if k != p.key]
        xs = [v for o in others for v in (o.x, o.right)]
        ys = [v for o in others for v in (o.y, o.bottom)]
        x0, y0, x1, y1 = box.x, box.y, box.right, box.bottom
        gx = gy = None
        for side, lines in (("left", xs), ("right", xs), ("top", ys),
                            ("bottom", ys)):
            if side not in moving:
                continue
            now = {"left": x0, "right": x1, "top": y0, "bottom": y1}[side]
            hit = _best(now, tolerance, ((v, v) for v in lines))
            if not hit:
                continue
            if side == "left":
                x0, gx = hit[0], hit[0]
            elif side == "right":
                x1, gx = hit[0], hit[0]
            elif side == "top":
                y0, gy = hit[0], hit[0]
            else:
                y1, gy = hit[0], hit[0]
        guides = self._guides((gx, gx) if gx is not None else None,
                              (gy, gy) if gy is not None else None)
        return Rect(x0, y0, max(1, x1 - x0), max(1, y1 - y0)), guides

    def _guides(self, best_x, best_y) -> list:
        b = self.bounds()
        guides = []
        if best_x:
            guides.append((best_x[1], b.y - 200, best_x[1], b.bottom + 200))
        if best_y:
            guides.append((b.x - 200, best_y[1], b.right + 200, best_y[1]))
        return guides

    def resolve(self, key):
        """Push a box clear of anything it overlaps, the shortest way.

        Tries every way out of every overlap - left, right, up, down - smallest
        first, and takes the first that leaves no overlap anywhere. Falls back
        to the right of everything, which is always free.
        """
        p = self.instance(key)
        if not any(p.key in pair for pair in self._overlapping_pairs()):
            return p
        mine = self.world_of(p.key)
        others = [r for k, _, r in self.placed_rects() if k != p.key]
        moves = set()
        for d in mine:
            for o in others:
                if d.overlaps(o):
                    moves.update({(o.right - d.x, 0), (o.x - d.right, 0),
                                  (0, o.bottom - d.y), (0, o.y - d.bottom)})
        for dx, dy in sorted(moves, key=lambda m: (abs(m[0]) + abs(m[1]), m)):
            moved = [d.moved(dx, dy) for d in mine]
            if not any(d.overlaps(o) for d in moved for o in others):
                self.move(p.key, p.x + dx, p.y + dy)
                return self.instance(p.key)
        right = max((o.right for o in others), default=0)
        self.move(p.key, right - min(d.x - p.x for d in mine), p.y)
        return self.instance(p.key)

    def row(self) -> None:
        """Side by side, left to right in their current order, centres lined
        up - copies too."""
        ps = sorted(self.instances(), key=lambda p: (p.x, p.y, p.n))
        tallest = max((p.wh for p in ps), default=0)
        x = 0
        for p in ps:
            self.move(p.key, x, (tallest - p.wh) // 2)
            x += p.ww

    def column(self) -> None:
        """One above another, top to bottom in their current order, centred."""
        ps = sorted(self.instances(), key=lambda p: (p.y, p.x, p.n))
        widest = max((p.ww for p in ps), default=0)
        y = 0
        for p in ps:
            self.move(p.key, (widest - p.ww) // 2, y)
            y += p.wh

    def normalise(self) -> None:
        """Shift everything so the arrangement starts at (0, 0). Positions are
        only ever relative; this keeps saved numbers small and readable."""
        b = self.bounds()
        if (b.x, b.y) == (0, 0):
            return
        for p in self.instances():
            self.move(p.key, p.x - b.x, p.y - b.y)

    def describe(self, key) -> str:
        """One line for the arrangement screen about the selected box."""
        p = self.instance(key)
        m = self.get(p.name)
        n = len(m.displays())
        sizes = ", ".join(f"{r.w}×{r.h}" for r in m.displays())
        what = f"{p.label}  ·  {n} display{'s' if n != 1 else ''} ({sizes})"
        if (p.ww, p.wh) != (m.w, m.h):
            what += f"  ·  drawn {p.ww}×{p.wh}"
        out = {}
        for d in self.doors():
            if (d.src, d.src_n) == p.key:
                other = self.instance((d.dst, d.dst_n)).label
                out[other] = out.get(other, 0) + (d.b - d.a)
        if not out:
            return what + "  ·  touches nothing"
        return what + "  ·  crosses to " + ", ".join(
            f"{o} ({length} px)" for o, length in sorted(out.items()))

    # ---- to and from the wire and the config ----
    def boxes(self) -> list:
        return ([m.box() for m in self._m.values()]
                + [self._c[k].box() for k in sorted(self._c)])

    def to_dict(self) -> dict:
        return {"screens": self.boxes()}

    @classmethod
    def from_dict(cls, d: dict, node: str) -> "Desk":
        return place(node, (d or {}).get("screens") or [])

    def _require(self, name: str) -> None:
        if name not in self._m:
            raise KeyError(f"no such machine: {name!r}")

    def _copy(self, key) -> Copy:
        if key not in self._c:
            raise KeyError(f"no such copy: {key!r}")
        return self._c[key]


# ------------------------------------------------------------ constructors
def place(node: str, boxes) -> Desk:
    """A desk from saved boxes: machines [{name, w, h, x, y, owner, parts,
    sw, sh}] and copies [{copy_of, n, x, y, sw, sh}]. Anything malformed is a
    ValueError - never a crash, whatever arrives over the network. A copy of
    a machine that is not there is dropped: that machine has left."""
    desk = Desk(node)
    try:
        items = list(boxes or ())
    except TypeError as e:
        raise ValueError("the arrangement is not a list") from e
    for b in items:
        if not isinstance(b, dict):
            raise ValueError("a screen is not described properly")
        if "copy_of" in b:
            continue
        missing = {"name", "w", "h"} - set(b)
        if missing:
            raise ValueError(f"a screen is missing {sorted(missing)}")
        desk.add(b["name"], b["w"], b["h"], b.get("owner") or node,
                 b.get("parts") or (), b.get("x", 0), b.get("y", 0),
                 b.get("sw") or 0, b.get("sh") or 0)
    for b in items:
        if "copy_of" not in b or b["copy_of"] not in desk.names():
            continue
        desk.add_copy(b["copy_of"], b.get("x", 0), b.get("y", 0),
                      b.get("sw"), b.get("sh"), b.get("n"))
    return desk


def simple(node: str, size, peer: str, peer_size, side: str,
           parts=(), peer_parts=()) -> Desk:
    """This machine and one other, the other on `side` of it, centres lined up.

    `side` is where the PEER sits, matching what --side has always meant.
    """
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")
    desk = Desk(node)
    me = desk.add(node, size[0], size[1], parts=parts)
    pw, ph = int(peer_size[0]), int(peer_size[1])
    if side == "left":
        at = (-pw, (me.h - ph) // 2)
    elif side == "right":
        at = (me.w, (me.h - ph) // 2)
    elif side == "top":
        at = ((me.w - pw) // 2, -ph)
    else:
        at = ((me.w - pw) // 2, me.h)
    desk.add(peer, pw, ph, owner=peer, parts=peer_parts, x=at[0], y=at[1])
    return desk


def beside(desk: Desk, name: str, w: int, h: int, owner: str, side: str,
           parts=()) -> Machine:
    """Add a machine against the edge of everything already there, on `side`,
    centred - where a newly connected machine goes until someone moves it."""
    b = desk.bounds()
    if side == "left":
        at = (b.x - w, b.y + (b.h - h) // 2)
    elif side == "top":
        at = (b.x + (b.w - w) // 2, b.y - h)
    elif side == "bottom":
        at = (b.x + (b.w - w) // 2, b.bottom)
    else:
        at = (b.right, b.y + (b.h - h) // 2)
    return desk.add(name, w, h, owner, parts, *at)


# ----------------------------------------------------------------- helpers
def _scale(v: int, world: int, px: int) -> int:
    """A pixel offset v (0..px) as a plane offset on a box `world` long.
    Exact integers, rounded half up, so 0 -> 0 and px -> world."""
    return (2 * v * world + px) // (2 * px)


def _world(m: Machine, x: int, y: int, ww: int, wh: int) -> list:
    """Machine m's displays on a box at (x, y) drawn ww x wh. A display's
    borders land on the same plane line from either side, so displays that
    touch in pixels touch on the plane."""
    out = []
    for r in m.displays():
        x0, x1 = _scale(r.x, ww, m.w), _scale(r.right, ww, m.w)
        y0, y1 = _scale(r.y, wh, m.h), _scale(r.bottom, wh, m.h)
        out.append(Rect(x + x0, y + y0, x1 - x0, y1 - y0))
    return out


def _local(v: int, px: int, world: int) -> int:
    """A plane offset v (0..world) along a border as a pixel offset (0..px)."""
    return (2 * v * px + world) // (2 * world)


def _door(sk, ls: Rect, ws: Rect, dk, ld: Rect, wd: Rect, c: Crossing,
          flip: bool = False):
    """The door out of display ls (drawn ws, of box sk) into display ld (drawn
    wd, of box dk), across the border that Crossing c found between them."""
    side = OPPOSITE[c.side] if flip else c.side
    if side in ("left", "right"):
        lo, hi = c.y1, c.y2
        a = ls.y + _local(lo - ws.y, ls.h, ws.h)
        b = ls.y + _local(hi - ws.y, ls.h, ws.h)
        cc = ld.y + _local(lo - wd.y, ld.h, wd.h)
        dd = ld.y + _local(hi - wd.y, ld.h, wd.h)
        edge = ls.right if side == "right" else ls.x
        land = ld.x if side == "right" else ld.right - 1
    else:
        lo, hi = c.x1, c.x2
        a = ls.x + _local(lo - ws.x, ls.w, ws.w)
        b = ls.x + _local(hi - ws.x, ls.w, ws.w)
        cc = ld.x + _local(lo - wd.x, ld.w, wd.w)
        dd = ld.x + _local(hi - wd.x, ld.w, wd.w)
        edge = ls.bottom if side == "bottom" else ls.y
        land = ld.y if side == "bottom" else ld.bottom - 1
    if b <= a or dd <= cc:
        return None                    # narrower than a pixel on one side
    return Door(sk[0], sk[1], side, edge, a, b, dk[0], dk[1], land, cc, dd,
                (c.x1, c.y1, c.x2, c.y2))


def _touch(an, ai, a: Rect, bn, bi, b: Rect):
    """The shared edge between two displays, as a Crossing seen from a."""
    lo, hi = max(a.y, b.y), min(a.bottom, b.bottom)
    if hi > lo:
        if a.right == b.x:
            return Crossing(an, ai, bn, bi, "right", a.right, lo, a.right, hi)
        if b.right == a.x:
            return Crossing(an, ai, bn, bi, "left", a.x, lo, a.x, hi)
    lo, hi = max(a.x, b.x), min(a.right, b.right)
    if hi > lo:
        if a.bottom == b.y:
            return Crossing(an, ai, bn, bi, "bottom", lo, a.bottom, hi, a.bottom)
        if b.bottom == a.y:
            return Crossing(an, ai, bn, bi, "top", lo, a.y, hi, a.y)
    return None


def _best(want: int, tolerance: int, *groups):
    """The candidate (position, guide) closest to `want`, within tolerance."""
    best = None
    for group in groups:
        for pos, guide in group:
            d = abs(pos - want)
            if d <= tolerance and (best is None or d < best[0]):
                best = (d, pos, guide)
    return (best[1], best[2]) if best else None


def _key(key) -> tuple:
    """A box's key: a machine name, or (name, copy number)."""
    if isinstance(key, str):
        return (key, 0)
    try:
        name, n = key
    except (TypeError, ValueError) as e:
        raise KeyError(f"not a box: {key!r}") from e
    return (name, int(n))


def _int(v, what: str) -> int:
    if isinstance(v, bool):
        raise ValueError(f"{what} is not a number")
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError) as e:
        raise ValueError(f"{what} is not a number") from e


def _coord(v) -> int:
    v = _int(v, "a position")
    if abs(v) > MAX_COORD:
        raise ValueError("a screen is placed impossibly far away")
    return v


def _size_bounds(px: int) -> tuple:
    lo = min(px, max(MIN_BOX, -(-px // MAX_SCALE)))
    return lo, px * MAX_SCALE


def _size_ok(world: int, px: int) -> bool:
    lo, hi = _size_bounds(px)
    return lo <= world <= hi


def _clamp_size(world: int, px: int) -> int:
    lo, hi = _size_bounds(px)
    return max(lo, min(hi, world))


def _store(ww: int, wh: int, w: int, h: int) -> tuple:
    """How a machine keeps its box size: 0, 0 while it is its pixels."""
    return (0, 0) if (ww, wh) == (w, h) else (ww, wh)


def _rescale(world: int, old_px: int, new_px: int) -> int:
    """A box size kept in proportion when its machine's pixels change."""
    return (2 * world * new_px + old_px) // (2 * old_px)


def normal_parts(parts, w: int, h: int) -> tuple:
    """Displays as a tuple of int 4-tuples, dropping any that are malformed or
    lie outside the desktop. One display covering everything is the same as
    none."""
    out = []
    try:
        items = list(parts or ())
    except TypeError:
        return ()
    for p in items:
        try:
            x, y, pw, ph = (int(v) for v in p)
        except (TypeError, ValueError, OverflowError):
            continue
        if pw > 0 and ph > 0 and x >= 0 and y >= 0 and x + pw <= w and y + ph <= h:
            out.append((x, y, pw, ph))
    if out == [(0, 0, int(w), int(h))]:
        return ()
    return tuple(sorted(out))
