"""The arrangement screen: drag machines to say where they sit.

Everything geometric is asked of desk.py - the same module the pointer moves by
(motion.py) - so what this draws is exactly what the pointer will do. This file
only draws, and turns mouse and keyboard into desk operations.

WHAT IT SHOWS
  - each machine as its real displays, grouped: a laptop with an external
    monitor is two rectangles that move together, in the arrangement its own
    operating system gives them (that part is Windows' business, not ours)
  - copies of machines, dashed: doorways back to the machine itself
  - every stretch of border where the pointer crosses, as a bright line - the
    answer to "if I push the mouse there, where does it go?"
  - problems in words: a machine that touches nothing, two that overlap, a
    border that would lead to two places
  - the selected box's details: its displays, its size, what it crosses to

WHAT YOU CAN DO
  - drag a box; it snaps to other boxes' edges and lines up with their tops,
    bottoms and centres, and dashed guides show what it snapped to
  - resize the selected box by its handles - corners, or the middle of a side
    to move one border alone. Its borders snap to other boxes' borders. The
    pointer's speed never changes: a box's size only says where borders meet
  - drop a box on top of another and it is pushed clear the short way
  - arrow keys nudge the selected box (Shift for bigger steps); Delete removes
    a selected copy
  - place a copy of a machine, give a box back its aspect ratio or its actual
    size - from the page's buttons or the right-click menu (on_menu)
  - In a row / In a column lay everything out side by side or stacked

A box is named by what it shows: a machine by its name, a copy by (name, n).

The view is frozen while a drag is in progress. Refitting it on every movement
changed the scale under the hand, and the box drifted away from the pointer.
"""
from __future__ import annotations

import tkinter as tk

from . import desk as _desk

SNAP_PX = 14                 # on-screen distance within which an edge grabs
HANDLE_PX = 5                # half the size of a resize handle, on screen
NUDGE, NUDGE_BIG = 10, 100   # desktop pixels per arrow key press (Shift: big)
PAD = 28                     # on-screen margin round the whole desk

FALLBACK = {"surface": "#0f1524", "ink": "#e7edf7", "dim": "#8c9ab3",
            "mine": "#60a5fa", "theirs": "#34d399", "mine_fill": "#15223a",
            "theirs_fill": "#12281f", "cross": "#22d3ee", "bad": "#f87171",
            "offline": "#5a6886", "offline_fill": "#151b29", "grid": "#161f33",
            "accent_dim": "#0e4a5a", "accent": "#22d3ee"}
GRID_PX = 28                 # on-screen spacing of the background grid

# Which borders each handle moves, and the pointer shape over it.
HANDLES = {"nw": ({"left", "top"}, "top_left_corner"),
           "n": ({"top"}, "top_side"),
           "ne": ({"right", "top"}, "top_right_corner"),
           "e": ({"right"}, "right_side"),
           "se": ({"right", "bottom"}, "bottom_right_corner"),
           "s": ({"bottom"}, "bottom_side"),
           "sw": ({"left", "bottom"}, "bottom_left_corner"),
           "w": ({"left"}, "left_side")}


def _sel(key):
    """How a box is named here: a machine by name, a copy by (name, n)."""
    name, n = _desk._key(key)
    return name if n == 0 else (name, n)


class Arranger:
    """A canvas of draggable boxes. `boxes` is the arrangement in force."""

    def __init__(self, parent, boxes, node: str, on_change=None, height=250,
                 palette=None, on_select=None, readonly=False, on_menu=None):
        self.node = node
        self.on_change = on_change
        self.on_select = on_select
        self.on_menu = on_menu
        self.C = dict(FALLBACK, **(palette or {}))
        self.desk = _desk.place(node, [dict(b) for b in boxes])
        self.dirty = False           # moved by hand and not applied yet
        self.selected = None         # a machine name, or (name, n) for a copy
        self.online = None           # names connected now; None = not known
        self._held = None            # what the hand is doing - see _grab
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
        c.bind("<Button-3>", self._menu)
        c.bind("<Delete>", lambda _e: self.remove_copy())
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
        if self._exists(self.selected):
            return self.desk.describe(self.selected)
        return "Select a screen to see where the pointer crosses"

    def _exists(self, sel) -> bool:
        if sel is None:
            return False
        try:
            self.desk.instance(sel)
            return True
        except KeyError:
            return False

    def set_boxes(self, boxes) -> None:
        """Adopt the arrangement in use - the window's 700ms refresh.

        The hand wins while dragging, and afterwards until the change is in
        force and `dirty` is cleared: then sizes, monitors and new machines
        still arrive (the other machine can reconnect with a different
        screen) but nothing the user placed or sized moves.
        """
        if self._held is not None:
            return
        fresh = _desk.place(self.node, [dict(b) for b in boxes])
        if self.dirty:
            for p in self.desk.instances():
                if p.name not in fresh.names():
                    continue
                try:
                    if p.n and p.key not in [q.key for q in fresh.copies()]:
                        fresh.add_copy(p.name, p.x, p.y, p.ww, p.wh, p.n)
                    else:
                        fresh.place_box(p.key, p.x, p.y, p.ww, p.wh)
                except (KeyError, ValueError):
                    continue
        if fresh.boxes() != self.desk.boxes():
            self.desk = fresh
            if not self._exists(self.selected):
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
        """Move the selected box a little - refused if it would overlap."""
        if not self._exists(self.selected):
            return
        p = self.desk.instance(self.selected)
        self.desk.move(p.key, p.x + dx, p.y + dy)
        if p.key in self.desk.overlapping():
            self.desk.move(p.key, p.x, p.y)
            return
        self._changed()

    def select(self, sel) -> None:
        if sel != self.selected:
            self.selected = sel
            self.redraw()
            if self.on_select:
                self.on_select(sel)

    # ---- what the page's buttons and the right-click menu offer ----
    def can(self, what: str) -> bool:
        """Whether an action applies to the selected box right now."""
        if not self._exists(self.selected):
            return False
        p = self.desk.instance(self.selected)
        m = self.desk.get(p.name)
        if what == "copy":
            return len(self.desk.copies(p.name)) < _desk.MAX_COPIES
        if what == "remove_copy":
            return p.n > 0
        if what == "aspect":
            # To the nearest whole unit: exact ratios rarely fit in integers.
            return abs(p.wh - (2 * p.ww * m.h + m.w) // (2 * m.w)) > 1
        if what == "actual":
            return (p.ww, p.wh) != (m.w, m.h)
        return False

    def add_copy(self) -> None:
        """Place a copy of the selected box's machine beside everything."""
        if not self.can("copy"):
            return
        p = self.desk.instance(self.selected)
        c = self.desk.add_copy(p.name, ww=p.ww, wh=p.wh)
        self.desk.resolve(c.key)
        self.desk.normalise()
        self.selected = _sel(c.key)
        self._changed()
        if self.on_select:
            self.on_select(self.selected)

    def remove_copy(self) -> None:
        if not self.can("remove_copy"):
            return
        self.desk.remove_copy(self.selected)
        self.selected = None
        self.desk.normalise()
        self._changed()
        if self.on_select:
            self.on_select(None)

    def keep_aspect(self) -> None:
        if self.can("aspect"):
            self._resized(lambda k: self.desk.keep_aspect(k))

    def actual_size(self) -> None:
        if self.can("actual"):
            self._resized(lambda k: self.desk.actual_size(k))

    def _resized(self, how) -> None:
        key = _desk._key(self.selected)
        how(key)
        self.desk.resolve(key)
        self.desk.normalise()
        self._changed()

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
        """The box under an on-screen point - the last drawn wins."""
        wx, wy = self._to_desk(px, py)
        hit = None
        for key, _, r in self.desk.placed_rects():
            if r.x <= wx < r.right and r.y <= wy < r.bottom:
                hit = _sel(key)
        return hit

    def _handles(self):
        """{handle: (screen x, screen y)} of the selected box, or {}."""
        if self.readonly or not self._exists(self.selected):
            return {}
        p = self.desk.instance(self.selected)
        x0, y0 = self._to_screen(p.x, p.y)
        x1, y1 = self._to_screen(p.x + p.ww, p.y + p.wh)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return {"nw": (x0, y0), "n": (mx, y0), "ne": (x1, y0), "e": (x1, my),
                "se": (x1, y1), "s": (mx, y1), "sw": (x0, y1), "w": (x0, my)}

    def _handle_at(self, px, py):
        for name, (hx, hy) in self._handles().items():
            if abs(px - hx) <= HANDLE_PX + 2 and abs(py - hy) <= HANDLE_PX + 2:
                return name
        return None

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
        overlapping = self.desk.overlapping()
        held = _desk._key(self._held["key"]) if self._held else None
        chosen_key = _desk._key(self.selected) if self._exists(self.selected) else None

        for p in self.desk.instances():
            m = self.desk.get(p.name)
            line, fill = self._colours(p.name)
            chosen = p.key in (chosen_key, held)
            dash = (5, 3) if p.n else None
            rects = self.desk.world_of(p.key)
            if len(rects) > 1 or p.n:
                # the whole box, dashed: what moves together
                x0, y0 = self._to_screen(p.x, p.y)
                x1, y1 = self._to_screen(p.x + p.ww, p.y + p.wh)
                c.create_rectangle(x0 - 3, y0 - 3, x1 + 3, y1 + 3, outline=line,
                                   dash=(3, 3), width=1)
            for i, r in enumerate(rects):
                x0, y0 = self._to_screen(r.x, r.y)
                x1, y1 = self._to_screen(r.right, r.bottom)
                c.create_rectangle(
                    x0, y0, x1, y1, fill=self.C["surface"] if p.n else fill,
                    outline=self.C["bad"] if p.key in overlapping else line,
                    width=3 if chosen else 1.5, dash=dash)
                # sizes only where they fit without crowding the name
                if x1 - x0 >= 110 and y1 - y0 >= 70 and not p.n:
                    d = m.displays()[i]
                    c.create_text(x1 - 4, y1 - 3, anchor="se", fill=self.C["dim"],
                                  text=f"{d.w}×{d.h}", font=("", 7))
                if len(rects) > 1 and x1 - x0 >= 24:
                    c.create_text(x0 + 4, y0 + 3, anchor="nw", fill=line,
                                  text=str(i + 1), font=("", 7, "bold"))
            # the name, on its largest display
            big = max(rects, key=lambda r: r.w * r.h)
            cx, cy = self._to_screen(big.x + big.w / 2, big.y + big.h / 2)
            sub = ("copy - leads to " + p.name if p.n else
                   "this machine" if m.owner == self.node else "")
            c.create_text(cx, cy - (6 if sub else 0),
                          text=("↺ " if p.n else "") + p.name, fill=line,
                          font=("", 10, "bold"))
            by0, by1 = self._to_screen(big.x, big.y)[1], self._to_screen(big.x, big.bottom)[1]
            if sub and by1 - by0 >= 56:
                c.create_text(cx, cy + 9, text=sub, fill=self.C["dim"],
                              font=("", 7))

        # where the pointer crosses: on top, so nothing hides them. A door is
        # compiled both ways round; each border is drawn once.
        drawn = set()
        for d in self.desk.doors():
            seg = d.segment
            if seg in drawn:
                continue
            drawn.add(seg)
            x0, y0 = self._to_screen(seg[0], seg[1])
            x1, y1 = self._to_screen(seg[2], seg[3])
            if self.online is not None and not {d.src, d.dst} <= self.online:
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

        for hx, hy in self._handles().values():
            c.create_rectangle(hx - HANDLE_PX, hy - HANDLE_PX, hx + HANDLE_PX,
                               hy + HANDLE_PX, fill=self.C["surface"],
                               outline=self.C["accent"], width=1.5,
                               tags=("handle",))

        if len(self.desk.names()) < 2:
            w = max(c.winfo_width(), c.winfo_reqwidth())
            c.create_text(w / 2, 14, fill=self.C["dim"], font=("", 8),
                          text="other machines appear here once they connect")

    # ---------------------------------------------------------------- drag
    def _grab(self, e) -> None:
        self.canvas.focus_set()          # so the arrow keys reach us
        handle = self._handle_at(e.x, e.y)
        if handle:
            p = self.desk.instance(self.selected)
            self._held = {"key": p.key, "handle": handle, "start": p,
                          "at": self._to_desk(e.x, e.y)}
            self.redraw()
            return
        sel = self._hit(e.x, e.y)
        self.select(sel)
        if sel is None:
            return
        p = self.desk.instance(sel)
        self._held = {"key": p.key, "handle": None, "start": p,
                      "at": self._to_desk(e.x, e.y)}
        self.redraw()

    def _drag(self, e) -> None:
        if self._held is None:
            return
        key, start = self._held["key"], self._held["start"]
        gx, gy = self._held["at"]
        wx, wy = self._to_desk(e.x, e.y)
        tol = SNAP_PX / self._view[0]
        if self._held["handle"]:
            moving = HANDLES[self._held["handle"]][0]
            x0, y0 = start.x, start.y
            x1, y1 = start.x + start.ww, start.y + start.wh
            dx, dy = round(wx - gx), round(wy - gy)
            if "left" in moving:
                x0 = min(x0 + dx, x1 - 1)
            if "right" in moving:
                x1 = max(x1 + dx, x0 + 1)
            if "top" in moving:
                y0 = min(y0 + dy, y1 - 1)
            if "bottom" in moving:
                y1 = max(y1 + dy, y0 + 1)
            r, self._guides = self.desk.snap_edges(
                key, _desk.Rect(x0, y0, x1 - x0, y1 - y0), moving, tol)
            self._place_resized(key, r, moving)
        else:
            want_x, want_y = round(start.x + wx - gx), round(start.y + wy - gy)
            x, y, self._guides = self.desk.snap(key, want_x, want_y, tol)
            self.desk.move(key, x, y)
        self.redraw()

    def _place_resized(self, key, r, moving) -> None:
        """Size the box to r - within what a box may be - keeping the borders
        that are not being dragged exactly where they were."""
        self.desk.set_size(key, r.w, r.h)
        p = self.desk.instance(key)
        x = r.x + r.w - p.ww if "left" in moving else r.x
        y = r.y + r.h - p.wh if "top" in moving else r.y
        self.desk.move(key, x, y)

    def _drop(self, e) -> None:
        if self._held is None:
            return
        key, start = self._held["key"], self._held["start"]
        self._held = None
        self._guides = []
        self.desk.resolve(key)           # never leave it on top of another
        p = self.desk.instance(key)
        if (p.x, p.y, p.ww, p.wh) == (start.x, start.y, start.ww, start.wh):
            self.redraw()                 # a click, not a move
            return
        # Only after deciding it moved: normalising shifts everything, and
        # would make a plain click look like a change.
        self.desk.normalise()
        self._changed()

    def _hover(self, e) -> None:
        if self.readonly or self._held is not None:
            return
        handle = self._handle_at(e.x, e.y)
        if handle:
            self.canvas.configure(cursor=HANDLES[handle][1])
        else:
            self.canvas.configure(cursor="fleur" if self._hit(e.x, e.y) else "")

    def _menu(self, e) -> None:
        sel = self._hit(e.x, e.y)
        self.select(sel)
        if sel is not None and self.on_menu:
            self.on_menu(e)
