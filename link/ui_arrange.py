"""The arrangement screen: drag machines to say where they sit.

Everything geometric is asked of desk.py - the same module the pointer moves by
(motion.py) - so what this draws is exactly what the pointer will do. This file
only draws, and turns mouse and keyboard into desk operations.

WHAT IT SHOWS
  - each machine as its real displays, grouped: a laptop with an external
    monitor is two rectangles that move together, in the arrangement its own
    operating system gives them (that part is Windows' business, not ours)
  - every stretch of edge where the pointer crosses between machines, as a
    bright line - the answer to "if I push the mouse there, where does it go?"
  - problems in words: a machine that touches nothing, two that overlap
  - the selected machine's details: its displays and what it crosses to

WHAT YOU CAN DO
  - drag a machine; it snaps to other machines' edges and lines up with their
    tops, bottoms and centres, and dashed guides show what it snapped to
  - drop it on top of another and it is pushed clear the short way
  - arrow keys nudge the selected machine (Shift for bigger steps)
  - In a row / In a column lay everything out side by side or stacked

A change is kept until Apply or Revert - the window refreshes every 700ms, and a
screen that jumped back the moment it was let go was the bug that started this.

The view is frozen while a drag is in progress. Refitting it on every movement
changed the scale under the hand, and the box drifted away from the pointer.
"""
from __future__ import annotations

import tkinter as tk

from . import desk as _desk

SNAP_PX = 14                 # on-screen distance within which an edge grabs
NUDGE, NUDGE_BIG = 10, 100   # desktop pixels per arrow key press (Shift: big)
PAD = 28                     # on-screen margin round the whole desk

FALLBACK = {"surface": "#0f1524", "ink": "#e7edf7", "dim": "#8c9ab3",
            "mine": "#60a5fa", "theirs": "#34d399", "mine_fill": "#15223a",
            "theirs_fill": "#12281f", "cross": "#22d3ee", "bad": "#f87171",
            "offline": "#5a6886", "offline_fill": "#151b29", "grid": "#161f33",
            "accent_dim": "#0e4a5a"}
GRID_PX = 28                 # on-screen spacing of the background grid


class Arranger:
    """A canvas of draggable machines. `boxes` is the arrangement to Apply."""

    def __init__(self, parent, boxes, node: str, on_change=None, height=250,
                 palette=None, on_select=None, readonly=False):
        self.node = node
        self.on_change = on_change
        self.on_select = on_select
        self.C = dict(FALLBACK, **(palette or {}))
        self.desk = _desk.place(node, [dict(b) for b in boxes])
        self.dirty = False           # moved by hand and not applied yet
        self.selected = None         # machine name
        self.online = None           # names connected now; None = not known
        self._held = None            # (name, start x, start y, grab world x, y)
        self._guides = []
        self._view = None            # (scale, ox, oy) - frozen during a drag

        self.readonly = readonly
        self.canvas = tk.Canvas(parent, height=height, highlightthickness=0,
                                background=self.C["surface"],
                                takefocus=0 if readonly else 1)
        self.canvas.pack(fill="both", expand=True)
        c = self.canvas
        c.bind("<Configure>", lambda _e: self.redraw())
        if readonly:
            # A picture of the arrangement (the Overview page): nothing moves
            # here, so nothing is bound that could move it.
            self.redraw()
            return
        c.bind("<ButtonPress-1>", self._grab)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<ButtonRelease-1>", self._drop)
        c.bind("<Motion>", self._hover)
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0),
                            ("Up", 0, -1), ("Down", 0, 1)):
            c.bind(f"<{key}>", lambda _e, dx=dx, dy=dy: self.nudge(dx * NUDGE, dy * NUDGE))
            c.bind(f"<Shift-{key}>",
                   lambda _e, dx=dx, dy=dy: self.nudge(dx * NUDGE_BIG, dy * NUDGE_BIG))
        self.redraw()

    # ------------------------------------------------------------- the data
    @property
    def boxes(self) -> list:
        return self.desk.boxes()

    def problems(self) -> list:
        return self.desk.problems()

    def describe(self) -> str:
        if self.selected and self.selected in self.desk.names():
            return self.desk.describe(self.selected)
        return "Click a machine to see what it touches; drag it to move it."

    def set_boxes(self, boxes) -> None:
        """Adopt the arrangement in use - the window's 700ms refresh.

        The hand wins while dragging, and afterwards until Apply or Revert
        clears `dirty`: then sizes, monitors and new machines still arrive
        (the other machine can reconnect with a different screen) but nothing
        the user placed moves.
        """
        if self._held is not None:
            return
        fresh = _desk.place(self.node, [dict(b) for b in boxes])
        if self.dirty:
            for m in self.desk.machines():
                if m.name in fresh.names():
                    fresh.move(m.name, m.x, m.y)
        if fresh.boxes() != self.desk.boxes():
            self.desk = fresh
            if self.selected not in fresh.names():
                self.selected = None
            self.redraw()

    def set_online(self, names) -> None:
        """Which machines are connected now, so the rest can be drawn dimmed."""
        names = None if names is None else set(names)
        if names != self.online:
            self.online = names
            self.redraw()

    # ------------------------------------------------------------ changes
    def _changed(self) -> None:
        self.dirty = True
        self.redraw()
        if self.on_change:
            self.on_change(self.boxes)

    def tidy(self) -> None:
        """Everything side by side, centres lined up."""
        self.desk.row()
        self.desk.normalise()
        self._changed()

    def stack(self) -> None:
        """Everything one above another, centres lined up."""
        self.desk.column()
        self.desk.normalise()
        self._changed()

    def nudge(self, dx: int, dy: int) -> None:
        """Move the selected machine a little - refused if it would overlap."""
        name = self.selected
        if not name or name not in self.desk.names():
            return
        m = self.desk.get(name)
        self.desk.move(name, m.x + dx, m.y + dy)
        if any(name in pair for pair in self.desk.overlaps()):
            self.desk.move(name, m.x, m.y)
            return
        self._changed()

    def select(self, name) -> None:
        if name != self.selected:
            self.selected = name
            self.redraw()
            if self.on_select:
                self.on_select(name)

    # ------------------------------------------------------------ geometry
    def _fit(self):
        """Scale and offset so the whole desk fits with a margin."""
        c = self.canvas
        cw = c.winfo_width() if c.winfo_width() > 1 else c.winfo_reqwidth()
        ch = c.winfo_height() if c.winfo_height() > 1 else c.winfo_reqheight()
        cw, ch = max(cw, 80), max(ch, 80)
        b = self.desk.bounds()
        scale = min((cw - 2 * PAD) / max(1, b.w), (ch - 2 * PAD) / max(1, b.h))
        scale = max(1e-4, min(scale, 0.25))
        ox = (cw - b.w * scale) / 2 - b.x * scale
        oy = (ch - b.h * scale) / 2 - b.y * scale
        return scale, ox, oy

    def _to_screen(self, x, y):
        s, ox, oy = self._view
        return x * s + ox, y * s + oy

    def _to_desk(self, px, py):
        s, ox, oy = self._view
        return (px - ox) / s, (py - oy) / s

    def _hit(self, px, py):
        """The machine under an on-screen point - the last drawn wins."""
        wx, wy = self._to_desk(px, py)
        hit = None
        for name, _, r in self.desk.rects():
            if r.x <= wx < r.right and r.y <= wy < r.bottom:
                hit = name
        return hit

    # ---------------------------------------------------------------- draw
    def _colours(self, name):
        m = self.desk.get(name)
        if self.online is not None and name not in self.online:
            return self.C["offline"], self.C["offline_fill"]
        if m.owner == self.node:
            return self.C["mine"], self.C["mine_fill"]
        return self.C["theirs"], self.C["theirs_fill"]

    def redraw(self) -> None:
        if self._held is None or self._view is None:
            self._view = self._fit()
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), c.winfo_reqwidth())
        h = max(c.winfo_height(), c.winfo_reqheight())
        for gx in range(GRID_PX, int(w), GRID_PX):
            c.create_line(gx, 0, gx, h, fill=self.C["grid"])
        for gy in range(GRID_PX, int(h), GRID_PX):
            c.create_line(0, gy, w, gy, fill=self.C["grid"])
        overlapping = {n for pair in self.desk.overlaps() for n in pair}

        for m in self.desk.machines():
            line, fill = self._colours(m.name)
            chosen = m.name == self.selected or (
                self._held is not None and self._held[0] == m.name)
            rects = m.world()
            if len(rects) > 1:
                # the whole desktop, dashed: what moves together
                x0, y0 = self._to_screen(m.x, m.y)
                x1, y1 = self._to_screen(m.x + m.w, m.y + m.h)
                c.create_rectangle(x0 - 3, y0 - 3, x1 + 3, y1 + 3, outline=line,
                                   dash=(3, 3), width=1)
            for i, r in enumerate(rects):
                x0, y0 = self._to_screen(r.x, r.y)
                x1, y1 = self._to_screen(r.right, r.bottom)
                c.create_rectangle(x0, y0, x1, y1, fill=fill,
                                   outline=self.C["bad"] if m.name in overlapping
                                   else line,
                                   width=3 if chosen else 1.5)
                # sizes only where they fit without crowding the name
                if x1 - x0 >= 110 and y1 - y0 >= 70:
                    c.create_text(x1 - 4, y1 - 3, anchor="se", fill=self.C["dim"],
                                  text=f"{r.w}×{r.h}", font=("", 7))
                if len(rects) > 1 and x1 - x0 >= 24:
                    c.create_text(x0 + 4, y0 + 3, anchor="nw", fill=line,
                                  text=str(i + 1), font=("", 7, "bold"))
            # the name, on its largest display
            big = max(rects, key=lambda r: r.w * r.h)
            cx, cy = self._to_screen(big.x + big.w / 2, big.y + big.h / 2)
            c.create_text(cx, cy - (6 if m.owner == self.node else 0),
                          text=m.name, fill=line, font=("", 10, "bold"))
            by0, by1 = self._to_screen(big.x, big.y)[1], self._to_screen(big.x, big.bottom)[1]
            if m.owner == self.node and by1 - by0 >= 56:
                c.create_text(cx, cy + 9, text="this machine", fill=self.C["dim"],
                              font=("", 7))

        # where the pointer crosses: on top, so nothing hides them
        for x in self.desk.crossings():
            x0, y0 = self._to_screen(x.x1, x.y1)
            x1, y1 = self._to_screen(x.x2, x.y2)
            if self.online is not None and not {x.a, x.b} <= self.online:
                # Arranged, but one side is not connected: the pointer cannot
                # cross here now, so it must not look as if it can. Drawn bright,
                # it said the opposite of what the pointer would do.
                c.create_line(x0, y0, x1, y1, fill=self.C["offline"], width=2,
                              dash=(4, 3), tags=("crossing_off",))
                continue
            # a glow under the line - Tk has no transparency, so a wider line in
            # a deeper shade of the same colour does the job
            c.create_line(x0, y0, x1, y1, fill=self.C["accent_dim"], width=11,
                          capstyle="round")
            c.create_line(x0, y0, x1, y1, fill=self.C["cross"], width=4,
                          capstyle="round", tags=("crossing",))

        for g in self._guides:
            x0, y0 = self._to_screen(g[0], g[1])
            x1, y1 = self._to_screen(g[2], g[3])
            c.create_line(x0, y0, x1, y1, fill=self.C["mine"], dash=(4, 3),
                          tags=("guide",))

        if len(self.desk.names()) < 2:
            w = max(c.winfo_width(), c.winfo_reqwidth())
            c.create_text(w / 2, 14, fill=self.C["dim"], font=("", 8),
                          text="other machines appear here once they connect")

    # ---------------------------------------------------------------- drag
    def _grab(self, e) -> None:
        self.canvas.focus_set()          # so the arrow keys reach us
        name = self._hit(e.x, e.y)
        self.select(name)
        if name is None:
            return
        m = self.desk.get(name)
        wx, wy = self._to_desk(e.x, e.y)
        self._held = (name, m.x, m.y, wx, wy)
        self.redraw()

    def _drag(self, e) -> None:
        if self._held is None:
            return
        name, sx, sy, gx, gy = self._held
        wx, wy = self._to_desk(e.x, e.y)
        want_x, want_y = round(sx + wx - gx), round(sy + wy - gy)
        tol = SNAP_PX / self._view[0]
        x, y, self._guides = self.desk.snap(name, want_x, want_y, tol)
        self.desk.move(name, x, y)
        self.redraw()

    def _drop(self, e) -> None:
        if self._held is None:
            return
        name, sx, sy = self._held[:3]
        self._held = None
        self._guides = []
        self.desk.resolve(name)           # never leave it on top of another
        m = self.desk.get(name)
        if (m.x, m.y) == (sx, sy):
            self.redraw()                 # a click, not a move
            return
        # Only after deciding it moved: normalising shifts everything, and
        # would make a plain click look like a change.
        self.desk.normalise()
        self._changed()

    def _hover(self, e) -> None:
        if self.readonly:
            return
        if self._held is None:
            self.canvas.configure(cursor="fleur" if self._hit(e.x, e.y) else "")
