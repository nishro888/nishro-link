"""The pointer's movement across the desk, and when it leaves this machine.

desk.py says where every display is and compiles where each border leads; this
module moves a pointer over them the way an operating system moves its own
across its monitors:

  - the pointer moves in the pixels of the machine it is on, always. However
    big a machine's box is drawn, its pointer moves at the same speed; the
    size matters only at a border, where the doorway carries the pointer to
    the matching point of the next machine's border;
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

MAX_STEPS = 256          # doors one move may go through: a fence, never reached


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
        """Advance by a mouse delta, crossing machines through the doorways.

        The delta is pixels of the machine the cursor is on, and it moves the
        cursor that many pixels - however big anyone drew that machine's box.
        Only at a border does the arrangement matter: there the doorway
        carries the pointer across to the matching point of the other
        machine's border, and what is left of the delta carries on there.

        Tries the horizontal part then the vertical, and the other way round,
        and keeps whichever threw less of the movement away against walls
        (the horizontal-first one if equal). That is what makes a diagonal
        push along a wall slide instead of stick, and what stops a diagonal
        move squeezing through a corner.

        `allowed` is the machines the pointer may enter - the ones connected
        right now. Any other machine is a wall, however it is arranged. The
        machine the cursor is on is always allowed, so a cursor left on one
        that has just disconnected can still come home.
        """
        desk = self.layout
        dx, dy = int(dx), int(dy)
        here = (self._screen, self._x, self._y)
        a = _walk(desk, here, dx, dy, allowed, "xy")
        b = _walk(desk, here, dx, dy, allowed, "yx")
        end = a if a[4] <= b[4] else b
        self._screen, self._x, self._y = end[0], end[1], end[2]
        return self.spot(end[3])

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


def _walk(desk, here, dx, dy, allowed, order):
    """One way of carrying out a move: axis by axis in `order`. Returns
    (machine, x, y, crossed, pixels lost against walls).

    Terminates: every pass round the loop either uses up the delta or goes
    through a door, and going through a door uses one pixel of it. The step
    limit is a second fence, for arithmetic no one has thought of yet."""
    screen, x, y = here
    crossed, lost = False, 0
    start = screen
    for axis in order:
        n = dx if axis == "x" else dy
        steps = 0
        while n:
            steps += 1
            if steps > MAX_STEPS:
                lost += abs(n)
                break
            rects = desk.get(screen).displays()
            if axis == "x":
                nx = _slide(rects, x, y, n, "x")
                n -= nx - x
                x = nx
            else:
                ny = _slide(rects, x, y, n, "y")
                n -= ny - y
                y = ny
            if not n:
                break
            fwd = n > 0
            if axis == "x":
                side, edge, t = ("right" if fwd else "left"), (x + 1 if fwd else x), y
            else:
                side, edge, t = ("bottom" if fwd else "top"), (y + 1 if fwd else y), x
            door = desk.door_at(screen, side, edge, t)
            if door is None or (allowed is not None and door.dst not in allowed
                                and door.dst != start):
                lost += abs(n)
                break
            u = door.map(t)
            if axis == "x":
                x, y = door.land, u
            else:
                x, y = u, door.land
            screen, crossed = door.dst, True
            n += -1 if fwd else 1
    return screen, x, y, crossed, lost


def _slide(rects, x, y, n, axis):
    """Move along the row (or column) as far as this machine's displays run
    unbroken - the operating system's own rule for its monitors."""
    if axis == "x":
        iv = _interval([(r.x, r.right) for r in rects if r.y <= y < r.bottom], x)
        return x if iv is None else max(iv[0], min(iv[1] - 1, x + n))
    iv = _interval([(r.y, r.bottom) for r in rects if r.x <= x < r.right], y)
    return y if iv is None else max(iv[0], min(iv[1] - 1, y + n))


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


def _onto(m, x: int, y: int) -> tuple:
    """Onto a display that actually exists - never into a hole between them."""
    if m.inside(x, y):
        return x, y
    return m.nearest(x, y)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else float(v)


def _round5(v: float) -> float:
    return round(float(v), 5)
