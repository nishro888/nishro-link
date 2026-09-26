"""The arrangement: every machine's displays, placed on one shared plane.

This module is the one place that knows where things are. The pointer's movement
(motion.py) and the arrangement screen (ui_arrange.py) both ask it, so what the
screen draws is exactly what the pointer does. Pure data and arithmetic - no OS
calls, no sockets - so every rule here is provable in a test.

THE MODEL. A desk is a plane of integer pixels. On it sit machines; a machine is
a rigid group of displays. Each display is a rectangle in its machine's own
desktop coordinates, exactly as that machine's operating system arranges them -
a laptop with an external monitor to its left is one machine with two displays,
and their relative position is Windows' business, never changed here. What is
arranged is where each MACHINE sits: one (x, y) per machine.

The pointer crosses from one machine to another wherever a display of one shares
an edge with a display of the other, and keeps its physical position across the
edge. Where no display is adjacent, the edge is a wall. That is how an operating
system treats its own monitors, and it is what makes arrangements like "the AIO
above the laptop's second monitor" mean what they look like: up from the second
monitor reaches the AIO, up from the laptop's panel beside it is a wall.

WHAT THIS REPLACED. Screens joined by declared links that mapped one edge onto
another proportionally. That model knew whole machines and their four sides,
so it could not say "above the second monitor", and proportional mapping meant
the pointer did not come out where the picture said it would.

TWO RULES THE WHOLE THING RESTS ON:
  - machines must TOUCH to connect. A gap is a wall. Dragging snaps, and
    problems() names any machine the pointer cannot reach.
  - machines never OVERLAP. A point would belong to two machines at once.
    resolve() pushes a dropped machine clear; check() refuses what is left.

Rectangles are half-open: a display at x=0 with w=1920 covers 0..1919, and the
one to its right starts at 1920. Touching means one's right == the other's left.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

SIDES = ("left", "right", "top", "bottom")


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

    `w`, `h` are its whole desktop (the bounding box of every display) and
    `parts` the displays inside it as (x, y, w, h). Empty `parts` means one
    display filling the desktop. `owner` is the node that drives it.

    Local coordinates - what the wire and the OS speak - count from the
    desktop's corner. World coordinates add the machine's (x, y).
    """
    name: str
    w: int
    h: int
    owner: str
    parts: tuple = ()
    x: int = 0
    y: int = 0

    def displays(self) -> list:
        """Each display as a Rect in LOCAL coordinates."""
        if not self.parts:
            return [Rect(0, 0, self.w, self.h)]
        return [Rect(*p) for p in self.parts]

    def world(self) -> list:
        """Each display as a Rect in WORLD coordinates."""
        return [r.moved(self.x, self.y) for r in self.displays()]

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
        return d


@dataclass(frozen=True)
class Crossing:
    """A stretch of edge where the pointer passes from one machine to another.

    Seen from `a`: `side` is which of a's edges it is. The segment runs from
    (x1, y1) to (x2, y2) in world coordinates - vertical for left/right, so
    x1 == x2, horizontal for top/bottom.
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

    @property
    def length(self) -> int:
        return abs(self.x2 - self.x1) + abs(self.y2 - self.y1)


# -------------------------------------------------------------------- desk
class Desk:
    """Machines on a plane. `node` is the machine asking - it answers is_local()."""

    def __init__(self, node: str):
        if not node:
            raise ValueError("a desk must know which node it belongs to")
        self.node = node
        self._m: dict[str, Machine] = {}

    # ---- building ----
    def add(self, name: str, w: int, h: int, owner: str = None, parts=(),
            x: int = 0, y: int = 0) -> Machine:
        if not name:
            raise ValueError("a machine needs a name")
        if name in self._m:
            raise ValueError(f"duplicate machine {name!r}")
        w, h = int(w), int(h)
        if w < 1 or h < 1:
            raise ValueError(f"machine {name!r} has a non-positive size {w}x{h}")
        m = Machine(name, w, h, owner or self.node, normal_parts(parts, w, h),
                    int(x), int(y))
        self._m[name] = m
        return m

    def remove(self, name: str) -> None:
        self._require(name)
        del self._m[name]

    def move(self, name: str, x: int, y: int) -> Machine:
        self._require(name)
        self._m[name] = replace(self._m[name], x=int(x), y=int(y))
        return self._m[name]

    def rename(self, old: str, new: str, owner: str = None) -> Machine:
        """Give a machine its real name - the placeholder "peer" becomes "aio"
        once the AIO says who it is. A machine that owned itself under the old
        name owns itself under the new one."""
        self._require(old)
        if new != old and new in self._m:
            raise ValueError(f"duplicate machine {new!r}")
        m = self._m.pop(old)
        if owner is None:
            owner = new if m.owner == old else m.owner
        self._m[new] = replace(m, name=new, owner=owner)
        return self._m[new]

    def resize(self, name: str, w: int, h: int, parts=()) -> Machine:
        """Change a machine's desktop without breaking the arrangement.

        A machine can grow a lot - a monitor plugged in, or a laptop first
        described by its panel and now by its whole desktop - and growing it in
        place would lay it over its neighbours. So anything wholly to its right
        moves right by the growth and anything wholly below moves down: every
        adjacency survives. Shrinking pulls them back the same way.
        """
        self._require(name)
        me = self._m[name]
        w, h = int(w), int(h)
        dw, dh = w - me.w, h - me.h
        right, below = me.x + me.w, me.y + me.h
        for other in list(self._m.values()):
            if other.name == name:
                continue
            nx = other.x + dw if other.x >= right else other.x
            ny = other.y + dh if other.y >= below else other.y
            if (nx, ny) != (other.x, other.y):
                self._m[other.name] = replace(other, x=nx, y=ny)
        self._m[name] = replace(me, w=w, h=h, parts=normal_parts(parts, w, h))
        return self._m[name]

    # ---- asking ----
    def get(self, name: str) -> Machine:
        self._require(name)
        return self._m[name]

    def names(self) -> list:
        return list(self._m)

    def machines(self) -> list:
        return list(self._m.values())

    def is_local(self, name: str) -> bool:
        return self.get(name).owner == self.node

    def mine(self) -> list:
        return [m.name for m in self._m.values() if m.owner == self.node]

    def owners(self) -> set:
        return {m.owner for m in self._m.values()}

    def to_world(self, name: str, x: int, y: int) -> tuple:
        m = self.get(name)
        return m.x + int(x), m.y + int(y)

    def at(self, wx: int, wy: int):
        """(machine name, local x, local y) for the display under a world
        point, or None if the point is on no display."""
        for m in self._m.values():
            if m.inside(wx - m.x, wy - m.y):
                return m.name, wx - m.x, wy - m.y
        return None

    def rects(self) -> list:
        """Every display in world coordinates, as (machine name, index, Rect)."""
        return [(m.name, i, r) for m in self._m.values()
                for i, r in enumerate(m.world())]

    def bounds(self) -> Rect:
        rs = [r for _, _, r in self.rects()]
        if not rs:
            return Rect(0, 0, 1, 1)
        x0, y0 = min(r.x for r in rs), min(r.y for r in rs)
        x1, y1 = max(r.right for r in rs), max(r.bottom for r in rs)
        return Rect(x0, y0, x1 - x0, y1 - y0)

    # ---- the rules ----
    def crossings(self) -> list:
        """Every stretch of edge where the pointer passes between two machines.

        Each touching pair is reported once, from the machine listed first.
        Displays of the SAME machine are not crossings: moving between them is
        the operating system's job.
        """
        out = []
        rects = self.rects()
        for i, (an, ai, a) in enumerate(rects):
            for bn, bi, b in rects[i + 1:]:
                if an == bn:
                    continue
                c = _touch(an, ai, a, bn, bi, b)
                if c:
                    out.append(c)
        return out

    def touching(self, name: str) -> dict:
        """{other machine: total length of shared edge} for one machine."""
        out = {}
        for c in self.crossings():
            if name in (c.a, c.b):
                other = c.b if c.a == name else c.a
                out[other] = out.get(other, 0) + c.length
        return out

    def overlaps(self) -> list:
        """Pairs of machines whose displays share area. Always a mistake."""
        out = []
        rects = self.rects()
        for i, (an, _, a) in enumerate(rects):
            for bn, _, b in rects[i + 1:]:
                if an != bn and a.overlaps(b):
                    pair = tuple(sorted((an, bn)))
                    if pair not in out:
                        out.append(pair)
        return out

    def unreachable(self) -> list:
        """Machines the pointer cannot get to from this node's machine."""
        start = self.mine()
        if not start:
            return []
        links = {n: set() for n in self._m}
        for c in self.crossings():
            links[c.a].add(c.b)
            links[c.b].add(c.a)
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
        for n in self.unreachable():
            out.append(f"{n} does not touch anything the pointer can reach - "
                       f"drag it against another screen")
        return out

    def check(self) -> None:
        """Raise ValueError for an arrangement that cannot be used at all.

        Overlap is fatal; an unreachable machine is not - the arrangement still
        works for everything else, and problems() says so on screen.
        """
        if not self.mine():
            raise ValueError("the arrangement has no screen belonging to this machine")
        ov = self.overlaps()
        if ov:
            raise ValueError(f"{ov[0][0]} and {ov[0][1]} overlap")

    # ---- helping a hand that is dragging ----
    def snap(self, name: str, x: int, y: int, tolerance: int):
        """Where to put `name` if it is dropped near (x, y), and the guide lines
        that explain why.

        Each axis snaps on its own, to the nearest candidate within
        `tolerance`: an edge of ours against an edge of another machine's
        display (touching), or lined up with it (tops, bottoms, lefts, rights,
        centres). Returns (x, y, guides), guides as world-coordinate lines
        (x1, y1, x2, y2) to draw while dragging.
        """
        me = self.get(name)
        mine = me.displays()
        others = [r for n, _, r in self.rects() if n != name]
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
        b = self.bounds()
        guides = []
        if best_x:
            guides.append((best_x[1], b.y - 200, best_x[1], b.bottom + 200))
        if best_y:
            guides.append((b.x - 200, best_y[1], b.right + 200, best_y[1]))
        return sx, sy, guides

    def resolve(self, name: str) -> Machine:
        """Push `name` clear of anything it overlaps, the shortest way.

        Tries every way out of every overlap - left, right, up, down - smallest
        first, and takes the first that leaves no overlap anywhere. Falls back
        to the right of everything, which is always free.
        """
        me = self.get(name)
        if not any(name in pair for pair in self.overlaps()):
            return me
        mine = me.world()
        others = [r for n, _, r in self.rects() if n != name]
        moves = set()
        for d in mine:
            for o in others:
                if d.overlaps(o):
                    moves.update({(o.right - d.x, 0), (o.x - d.right, 0),
                                  (0, o.bottom - d.y), (0, o.y - d.bottom)})
        for dx, dy in sorted(moves, key=lambda m: abs(m[0]) + abs(m[1])):
            moved = [d.moved(dx, dy) for d in mine]
            if not any(d.overlaps(o) for d in moved for o in others):
                return self.move(name, me.x + dx, me.y + dy)
        right = max((o.right for o in others), default=0)
        return self.move(name, right - min(d.x for d in me.displays()), me.y)

    def row(self) -> None:
        """Side by side, left to right in their current order, centres lined up."""
        ms = sorted(self._m.values(), key=lambda m: (m.x, m.y))
        tallest = max((m.h for m in ms), default=0)
        x = 0
        for m in ms:
            self.move(m.name, x, (tallest - m.h) // 2)
            x += m.w

    def column(self) -> None:
        """One above another, top to bottom in their current order, centred."""
        ms = sorted(self._m.values(), key=lambda m: (m.y, m.x))
        widest = max((m.w for m in ms), default=0)
        y = 0
        for m in ms:
            self.move(m.name, (widest - m.w) // 2, y)
            y += m.h

    def normalise(self) -> None:
        """Shift everything so the arrangement starts at (0, 0). Positions are
        only ever relative; this keeps saved numbers small and readable."""
        b = self.bounds()
        if (b.x, b.y) == (0, 0):
            return
        for m in list(self._m.values()):
            self._m[m.name] = replace(m, x=m.x - b.x, y=m.y - b.y)

    def describe(self, name: str) -> str:
        """One line for the arrangement screen about the selected machine."""
        m = self.get(name)
        n = len(m.displays())
        sizes = ", ".join(f"{r.w}×{r.h}" for r in m.displays())
        what = f"{name}: {n} display{'s' if n != 1 else ''} ({sizes})"
        t = self.touching(name)
        if not t:
            return what + " - touches nothing yet, so the pointer cannot reach it"
        return what + " - the pointer crosses to " + ", ".join(
            f"{o} along {length} px" for o, length in sorted(t.items()))

    # ---- to and from the wire and the config ----
    def boxes(self) -> list:
        return [m.box() for m in self._m.values()]

    def to_dict(self) -> dict:
        return {"screens": self.boxes()}

    @classmethod
    def from_dict(cls, d: dict, node: str) -> "Desk":
        return place(node, (d or {}).get("screens") or [])

    def _require(self, name: str) -> None:
        if name not in self._m:
            raise KeyError(f"no such machine: {name!r}")


# ------------------------------------------------------------ constructors
def place(node: str, boxes) -> Desk:
    """A desk from saved boxes: [{name, w, h, x, y, owner, parts}]."""
    desk = Desk(node)
    for b in boxes:
        desk.add(b["name"], b["w"], b["h"], b.get("owner") or node,
                 b.get("parts") or (), b.get("x", 0), b.get("y", 0))
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


def normal_parts(parts, w: int, h: int) -> tuple:
    """Displays as a tuple of int 4-tuples, dropping any that are malformed or
    lie outside the desktop. One display covering everything is the same as
    none."""
    out = []
    for p in parts or ():
        try:
            x, y, pw, ph = (int(v) for v in p)
        except (TypeError, ValueError):
            continue
        if pw > 0 and ph > 0 and x >= 0 and y >= 0 and x + pw <= w and y + ph <= h:
            out.append((x, y, pw, ph))
    if out == [(0, 0, int(w), int(h))]:
        return ()
    return tuple(sorted(out))
