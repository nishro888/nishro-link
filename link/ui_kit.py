"""The window's building blocks, drawn to one theme.

Classic Tk widgets rather than ttk: ttk's themes decide colours themselves, and
on Windows they refuse to be dark. These take every colour from ui_theme, so the
pages never name a colour or a font - they ask for a primary button, a card, a
pill - and the whole look lives in two small files.

Nothing here knows about Nishro Link. That is the point: pages compose these,
and a change of look is a change here only.
"""
from __future__ import annotations

import tkinter as tk


class Kit:
    """Palette and fonts, handed to every component."""

    def __init__(self, C: dict, F: dict):
        self.C, self.F = C, F


# ---------------------------------------------------------------- buttons
class Button(tk.Label):
    """A flat button with a hover state. A Label, not a Button: a native
    Windows button ignores most colours, and this one must follow the theme.

    kind: primary (the one action that matters), secondary, ghost, danger.
    """

    def __init__(self, parent, kit: Kit, text, command=None, kind="secondary",
                 small=False, width=None):
        C, F = kit.C, kit.F
        self._kit, self._kind = kit, kind
        self._colours = {
            "primary": (C["accent"], C["accent_ink"], "#5ee3f5"),
            "secondary": (C["card_hi"], C["ink"], C["line_hi"]),
            "ghost": (parent.cget("bg"), C["dim"], C["card_hi"]),
            "danger": (C["bad_bg"], C["bad"], "#4a1c28"),
        }[kind]
        bg, fg, _ = self._colours
        super().__init__(parent, text=text, bg=bg, fg=fg, cursor="hand2",
                         font=F["small"] if small else F["h3"],
                         padx=10 if small else 14, pady=4 if small else 7,
                         width=width or 0)
        self.command = command
        self._enabled = True
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)

    def _hover(self, on):
        if not self._enabled:
            return
        bg, fg, hi = self._colours
        self.configure(bg=hi if on else bg)

    def _click(self, e):
        # Only if released over the button - dragging off it cancels, as with
        # any real button.
        if not self._enabled or self.command is None:
            return
        if 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self.command()

    def invoke(self):
        if self._enabled and self.command:
            self.command()

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        bg, fg, _ = self._colours
        self.configure(bg=bg, fg=fg if on else self._kit.C["faint"],
                       cursor="hand2" if on else "arrow")

    def set_text(self, text: str) -> None:
        if self.cget("text") != text:
            self.configure(text=text)

    @property
    def enabled(self) -> bool:
        return self._enabled


# ------------------------------------------------------------ small things
class Pill(tk.Label):
    """A short status word on a tinted background."""

    def __init__(self, parent, kit: Kit, text="", tone="dim"):
        self._kit = kit
        super().__init__(parent, text=text, font=kit.F["caps"], padx=8, pady=2)
        self.set(text, tone)

    def set(self, text, tone="dim"):
        C = self._kit.C
        fg = {"ok": C["ok"], "warn": C["warn"], "bad": C["bad"],
              "accent": C["accent"], "accent2": C["accent2"]}.get(tone, C["dim"])
        bg = {"bad": C["bad_bg"], "warn": C["warn_bg"]}.get(tone, C["card_hi"])
        self.configure(text=text, fg=fg, bg=bg)


class Dot(tk.Canvas):
    """A status light."""

    def __init__(self, parent, kit: Kit, size=10, colour=None):
        super().__init__(parent, width=size, height=size, highlightthickness=0,
                         bg=parent.cget("bg"))
        self._id = self.create_oval(1, 1, size - 1, size - 1, outline="",
                                    fill=colour or kit.C["faint"])

    def set(self, colour):
        self.itemconfigure(self._id, fill=colour)


class Toggle(tk.Canvas):
    """An on/off switch - clearer than a checkbox about what state is in force."""

    W, H = 38, 20

    def __init__(self, parent, kit: Kit, variable: tk.BooleanVar, command=None):
        super().__init__(parent, width=self.W, height=self.H, highlightthickness=0,
                         bg=parent.cget("bg"), cursor="hand2")
        self._kit, self.var, self.command = kit, variable, command
        self.bind("<Button-1>", self._flip)
        variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _flip(self, _e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self):
        C, on = self._kit.C, bool(self.var.get())
        self.delete("all")
        track = C["accent"] if on else C["line_hi"]
        r = self.H / 2
        self.create_oval(0, 0, self.H, self.H, fill=track, outline="")
        self.create_oval(self.W - self.H, 0, self.W, self.H, fill=track, outline="")
        self.create_rectangle(r, 0, self.W - r, self.H, fill=track, outline="")
        x = self.W - self.H + 3 if on else 3
        self.create_oval(x, 3, x + self.H - 6, self.H - 3,
                         fill=C["accent_ink"] if on else C["ink"], outline="")


class Segmented(tk.Frame):
    """Pick one of a few - a row of buttons with the chosen one lit."""

    def __init__(self, parent, kit: Kit, options, variable: tk.StringVar,
                 command=None):
        super().__init__(parent, bg=kit.C["card_hi"])
        self._kit, self.var, self.command = kit, variable, command
        self._items = {}
        for value, label in options:
            b = tk.Label(self, text=label, font=kit.F["small"], padx=12, pady=5,
                         cursor="hand2")
            b.pack(side="left", padx=1, pady=1)
            b.bind("<Button-1>", lambda _e, v=value: self._pick(v))
            self._items[value] = b
        variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _pick(self, value):
        self.var.set(value)
        if self.command:
            self.command()

    def _draw(self):
        C = self._kit.C
        for value, b in self._items.items():
            on = value == self.var.get()
            b.configure(bg=C["accent"] if on else C["card_hi"],
                        fg=C["accent_ink"] if on else C["dim"])


def field(parent, kit: Kit, show=None, width=None):
    """A text box on the theme, with the accent ring when focused."""
    C = kit.C
    e = tk.Entry(parent, bg=C["card_hi"], fg=C["ink"], insertbackground=C["ink"],
                 disabledbackground=C["card"], readonlybackground=C["card_hi"],
                 relief="flat", highlightthickness=1, highlightbackground=C["line"],
                 highlightcolor=C["accent"], font=kit.F["body"], show=show or "")
    if width:
        e.configure(width=width)
    return e


def label(parent, kit: Kit, text="", role="body", tone="ink", wrap=None, **kw):
    C = kit.C
    lb = tk.Label(parent, text=text, font=kit.F[role], bg=parent.cget("bg"),
                  fg=C.get(tone, tone), justify="left", anchor="w", **kw)
    if wrap:
        lb.configure(wraplength=wrap)
    return lb


# ------------------------------------------------------------------ cards
class Card(tk.Frame):
    """A raised panel: a hairline border, a small caps title, and a body."""

    def __init__(self, parent, kit: Kit, title=None, pad=14):
        C = kit.C
        super().__init__(parent, bg=C["card"], highlightthickness=1,
                         highlightbackground=C["line"])
        self.body = tk.Frame(self, bg=C["card"])
        if title:
            head = tk.Frame(self, bg=C["card"])
            head.pack(fill="x", padx=pad, pady=(pad - 2, 0))
            tk.Label(head, text=title.upper(), font=kit.F["caps"], bg=C["card"],
                     fg=C["dim"]).pack(side="left")
            self.head = head
        self.body.pack(fill="both", expand=True, padx=pad, pady=pad)


class Metric(tk.Frame):
    """A number worth glancing at, with what it is under it."""

    def __init__(self, parent, kit: Kit, caption):
        C = kit.C
        super().__init__(parent, bg=C["card"], highlightthickness=1,
                         highlightbackground=C["line"])
        self._kit = kit
        self.value = tk.Label(self, text="—", font=kit.F["metric"], bg=C["card"],
                              fg=C["ink"], anchor="w")
        self.value.pack(anchor="w", padx=14, pady=(12, 0))
        self.caption = tk.Label(self, text=caption, font=kit.F["small"],
                                bg=C["card"], fg=C["dim"], anchor="w")
        self.caption.pack(anchor="w", padx=14, pady=(0, 12))

    def set(self, value, tone="ink"):
        C = self._kit.C
        self.value.configure(text=value, fg=C.get(tone, C["ink"]))


class Monitors(tk.Canvas):
    """A little picture of a machine's displays, as they sit on its desk."""

    def __init__(self, parent, kit: Kit, w=92, h=54):
        super().__init__(parent, width=w, height=h, highlightthickness=0,
                         bg=parent.cget("bg"))
        # NOT self._w: Tkinter keeps the widget's own Tcl name there. Storing
        # the width in it made every later call on the canvas a call to a Tcl
        # command named "92".
        self._kit, self._pw, self._ph = kit, w, h

    def draw(self, parts, colour, fill):
        """`parts`: [(x, y, w, h)] in the machine's own coordinates."""
        self.delete("all")
        if not parts:
            return
        x0 = min(p[0] for p in parts)
        y0 = min(p[1] for p in parts)
        x1 = max(p[0] + p[2] for p in parts)
        y1 = max(p[1] + p[3] for p in parts)
        s = min((self._pw - 6) / max(1, x1 - x0), (self._ph - 6) / max(1, y1 - y0))
        ox = (self._pw - (x1 - x0) * s) / 2
        oy = (self._ph - (y1 - y0) * s) / 2
        for x, y, w, h in parts:
            ax, ay = ox + (x - x0) * s, oy + (y - y0) * s
            self.create_rectangle(ax + 1, ay + 1, ax + w * s - 1, ay + h * s - 1,
                                  fill=fill, outline=colour, width=1.5)


# ----------------------------------------------------------------- scroll
class Scroll(tk.Frame):
    """A page that grows a scrollbar only when it needs one.

    On a 768-pixel laptop screen a fixed layout hides whatever is at the
    bottom; a scrollbar that is always there wastes width when nothing is.
    """

    def __init__(self, parent, kit: Kit, bg=None):
        bg = bg or kit.C["panel"]
        super().__init__(parent, bg=bg)
        self._canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, bg=bg)
        self._bar = tk.Scrollbar(self, orient="vertical", command=self._canvas.yview,
                                 bg=kit.C["card"], troughcolor=bg,
                                 activebackground=kit.C["line_hi"], relief="flat",
                                 borderwidth=0, width=10)
        self._canvas.configure(yscrollcommand=self._on_scroll)
        self._canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self._canvas, bg=bg)
        self._win = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda _e: self._canvas.configure(
                            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfigure(self._win, width=e.width))
        self._canvas.bind("<Enter>", lambda _e: self._wheel(True))
        self._canvas.bind("<Leave>", lambda _e: self._wheel(False))

    def _on_scroll(self, lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self._bar.pack_forget()
        else:
            self._bar.pack(side="right", fill="y")
        self._bar.set(lo, hi)

    def _wheel(self, on):
        # MouseWheel is Windows; Button-4/5 is X11. Bound only while the pointer
        # is inside, so two scrollable pages never fight over the wheel.
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            if on:
                self._canvas.bind_all(seq, self._scroll)
            else:
                self._canvas.unbind_all(seq)

    def _scroll(self, e):
        num = getattr(e, "num", None)
        step = -1 if num == 4 else 1 if num == 5 else 0
        if not step and getattr(e, "delta", 0):
            step = -1 if e.delta > 0 else 1
        if step:
            self._canvas.yview_scroll(step, "units")


def ago(ts) -> str:
    """'just now', '5 min ago', '3 h ago', '2 days ago'."""
    import time
    if not ts:
        return "never"
    d = max(0, int(time.time() - float(ts)))
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{d // 60} min ago"
    if d < 86400:
        return f"{d // 3600} h ago"
    return f"{d // 86400} day{'s' if d >= 172800 else ''} ago"
