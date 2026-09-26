"""The pointer's movement across the desk, and when it leaves this machine.

desk.py says where every display is; this module moves a pointer over them the
way an operating system moves its own across its monitors:

  - a move that stays on displays happens as asked, whichever machines those
    displays belong to - crossing where two machines' displays share an edge
    needs no special case, it is just more display;
  - a move that would leave every display slides along the wall it hit: the
    axis that is blocked stops, the other carries on;
  - a corner is not a doorway - displays touching only at a corner do not
    connect, exactly as with real monitors.

Exactly one node at a time owns the virtual cursor - the one holding the baton
(DESIGN.md section 3) - and every other node is told where to put its pointer.

The cursor remembers WHICH MACHINE it is on and where on that machine's desktop,
not a point on the plane: if the arrangement changes while it is on the AIO, it
stays at the same place on the AIO's screen rather than wherever that plane
coordinate now happens to fall.

    cur = Cursor(desk, "laptop", 0, 400)
    spot = cur.move(-5, 0)        # off the laptop's left edge, onto the AIO
    spot.crossed, spot.screen     -> True, "aio"
    cur.norm()                    -> fractions of the AIO's desktop, for the wire
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Spot:
    """Where the cursor ended up, and whether it changed machines getting there."""
    screen: str
    x: int
    y: int
    crossed: bool


class Cursor:
    def __init__(self, desk, screen: str, x: int = 0, y: int = 0):
        self.layout = desk                 # the name the rest of the code uses
        m = desk.get(screen)
        self._screen = screen
        self._x, self._y = _onto(m, int(x), int(y))

    # ---- state ----
    @property
    def screen(self) -> str:
        return self._screen

    @property
    def x(self) -> int:
        return self._x

    @property
    def y(self) -> int:
        return self._y

    @property
    def owner(self) -> str:
        """Which node drives the machine the cursor is on - i.e. who injects."""
        return self.layout.get(self._screen).owner

    @property
    def remote(self) -> bool:
        return not self.layout.is_local(self._screen)

    def spot(self, crossed: bool = False) -> Spot:
        return Spot(self._screen, self._x, self._y, crossed)

    # ---- movement ----
    def move(self, dx: int, dy: int, allowed=None) -> Spot:
        """Advance by a mouse delta, crossing machines where displays touch.

        Tries the horizontal part then the vertical, and the other way round,
        and keeps whichever ends nearer where the delta pointed. That is what
        makes a diagonal push along a wall slide instead of stick, and what
        stops a diagonal move squeezing through a corner.

        `allowed` is the machines the pointer may enter - the ones connected
        right now. Any other machine is a wall, however it is arranged. The
        machine the cursor is on is always allowed, so a cursor left on one
        that has just disconnected can still come home.
        """
        desk = self.layout
        dx, dy = int(dx), int(dy)
        start = desk.to_world(self._screen, self._x, self._y)
        want = (start[0] + dx, start[1] + dy)
        rects = [r for n, _, r in desk.rects()
                 if allowed is None or n in allowed or n == self._screen]
        a = _slide_y(rects, _slide_x(rects, start, dx), dy)
        b = _slide_x(rects, _slide_y(rects, start, dy), dx)
        end = a if _dist(a, want) <= _dist(b, want) else b
        hit = _at(desk, end, allowed, self._screen)
        if hit is None:                      # cannot happen from a real display
            return self.spot()
        name, lx, ly = hit
        crossed = name != self._screen
        self._screen, self._x, self._y = name, lx, ly
        return self.spot(crossed)

    def warp(self, screen: str, x: int, y: int) -> Spot:
        """Put the cursor somewhere directly (a claim, the failsafe, a restore)."""
        m = self.layout.get(screen)
        self._screen = screen
        self._x, self._y = _onto(m, int(x), int(y))
        return self.spot(crossed=True)

    # ---- the wire ----
    def norm(self) -> tuple:
        """Position as fractions of the current machine's desktop, for `p`.

        Fractions rather than pixels, so a machine whose resolution changed
        since the arrangement was made still lands the pointer on its screen.
        """
        m = self.layout.get(self._screen)
        return (_round5(self._x / (m.w - 1) if m.w > 1 else 0.0),
                _round5(self._y / (m.h - 1) if m.h > 1 else 0.0))


def to_pixels(nx: float, ny: float, w: int, h: int) -> tuple:
    """The receiving side of norm(): fractions -> pixels on this desktop."""
    x = int(round(_clamp01(nx) * (w - 1))) if w > 1 else 0
    y = int(round(_clamp01(ny) * (h - 1))) if h > 1 else 0
    return x, y


def exits(machine, x: int, y: int, dx: int, dy: int) -> tuple:
    """How much of a move did the OS throw away at the edge of this machine?

    While this machine drives and its pointer is on its own desktop, the OS
    moves the pointer - across its own displays too, which is none of the
    link's business. Only a push against the OUTSIDE of the desktop is ours: the
    pointer is on its last pixel in that direction, with no display of this
    machine beyond it. That discarded motion is what may carry on to another
    machine. Checked per display, not on the bounding box, so moving from a
    laptop panel onto its own external monitor is never mistaken for leaving.
    """
    lx, ly = (x, y) if machine.inside(x, y) else machine.nearest(x, y)
    eat_x = dx if dx and not machine.inside(lx + (1 if dx > 0 else -1), ly) else 0
    eat_y = dy if dy and not machine.inside(lx, ly + (1 if dy > 0 else -1)) else 0
    return eat_x, eat_y


def _at(desk, p, allowed, current):
    """The machine under a world point, among those the pointer may enter."""
    for m in desk.machines():
        if allowed is not None and m.name not in allowed and m.name != current:
            continue
        if m.inside(p[0] - m.x, p[1] - m.y):
            return m.name, p[0] - m.x, p[1] - m.y
    return None


# ------------------------------------------------------------------ sliding
def _slide_x(rects, p, dx):
    """Move along the row, as far as displays continue unbroken."""
    if not dx:
        return p
    iv = _interval([(r.x, r.right) for r in rects if r.y <= p[1] < r.bottom], p[0])
    if iv is None:
        return p
    return max(iv[0], min(iv[1] - 1, p[0] + dx)), p[1]


def _slide_y(rects, p, dy):
    """Move along the column, as far as displays continue unbroken."""
    if not dy:
        return p
    iv = _interval([(r.y, r.bottom) for r in rects if r.x <= p[0] < r.right], p[1])
    if iv is None:
        return p
    return p[0], max(iv[0], min(iv[1] - 1, p[1] + dy))


def _interval(spans, at):
    """The run of touching or overlapping spans that contains `at`."""
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


def _dist(a, b) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _onto(m, x: int, y: int) -> tuple:
    """Onto a display that actually exists - never into a hole between them."""
    if m.inside(x, y):
        return x, y
    return m.nearest(x, y)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else float(v)


def _round5(v: float) -> float:
    return round(float(v), 5)
