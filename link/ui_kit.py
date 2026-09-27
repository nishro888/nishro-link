"""The window's building blocks, drawn to one theme.

The controls are ttk widgets in the Sun Valley theme - Windows 11's buttons,
switches, text boxes and scrollbars, with their focus rings, hover and pressed
states, and keyboard handling. The first version drew them as coloured labels,
which looked like a web page and behaved like one: no focus, no Tab, no Space.

Everything else - cards, pills, the little pictures - is classic Tk in colours
from ui_theme, so the pages never name a colour or a font. They ask for a
primary button, a card, a pill, and the whole look lives in two small files.

A ttk control is drawn for the theme's own background; on a card or in the
sidebar it is given a variant for that background (ui_theme.on), or its corners
would show the page colour.

Nothing here knows about Nishro Link. That is the point: pages compose these,
and a change of look is a change here only.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import ui_theme


class Kit:
    """Palette and fonts, handed to every component."""

    def __init__(self, C: dict, F: dict):
        self.C, self.F = C, F


def _bg(w, kit: Kit) -> str:
    """The colour `w` is painted - a ttk frame has none of its own."""
    try:
        return str(w.cget("bg"))
    except tk.TclError:
        return kit.C["bg"]


# ---------------------------------------------------------------- buttons
class Button(ttk.Button):
    """A push button.

    kind: primary (the one action that matters - the accent colour), secondary
    (the ordinary button), ghost (flat until the pointer is over it), danger.
    """

    STYLES = {"primary": "Accent.TButton", "secondary": "TButton",
              "ghost": "Toolbutton", "danger": "Danger.TButton"}

    def __init__(self, parent, kit: Kit, text, command=None, kind="secondary",
                 small=False, width=None):
        self._kit, self._kind = kit, kind
        super().__init__(parent, text=text, command=self._run,
                         style=ui_theme.on(parent, self.STYLES[kind],
                                           _bg(parent, kit)))
        if width:
            self.configure(width=width)
        self.command = command
        self._enabled = True

    def _run(self):
        if self._enabled and self.command:
            self.command()

    def invoke(self):
        self._run()

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        self.state(["!disabled"] if on else ["disabled"])

    def set_text(self, text: str) -> None:
        if str(self.cget("text")) != text:
            self.configure(text=text)

    @property
    def enabled(self) -> bool:
        return self._enabled


# ------------------------------------------------------------ small things
class Pill(tk.Label):
    """A short status word on a tinted background - in sentence case, as
    Windows writes one, not shouted in capitals."""

    def __init__(self, parent, kit: Kit, text="", tone="dim"):
        self._kit = kit
        super().__init__(parent, text=text, font=kit.F["caps"], padx=8, pady=1)
        self.set(text, tone)

    def set(self, text, tone="dim"):
        C = self._kit.C
        if text.isupper():
            text = text[:1] + text[1:].lower()
        fg = {"ok": C["ok"], "warn": C["warn"], "bad": C["bad"],
              "accent": C["accent"], "accent2": C["accent2"]}.get(tone, C["dim"])
        bg = {"bad": C["bad_bg"], "warn": C["warn_bg"]}.get(tone, C["card_hi"])
        self.configure(text=text, fg=fg, bg=bg)


class Dot(tk.Canvas):
    """A status light."""

    def __init__(self, parent, kit: Kit, size=10, colour=None):
        super().__init__(parent, width=size, height=size, highlightthickness=0,
                         bg=_bg(parent, kit))
        self._id = self.create_oval(1, 1, size - 1, size - 1, outline="",
                                    fill=colour or kit.C["faint"])

    def set(self, colour):
        self.itemconfigure(self._id, fill=colour)


class Toggle(ttk.Checkbutton):
    """An on/off switch - clearer than a checkbox about what state is in force."""

    def __init__(self, parent, kit: Kit, variable: tk.BooleanVar, command=None):
        super().__init__(parent, variable=variable, command=command,
                         style=ui_theme.on(parent, "Switch.TCheckbutton",
                                           _bg(parent, kit)))
        self._kit, self.var, self.command = kit, variable, command

    def _flip(self, _e=None):
        self.invoke()


class Segmented(tk.Frame):
    """Pick one of a few - a row of buttons with the chosen one lit."""

    def __init__(self, parent, kit: Kit, options, variable: tk.StringVar,
                 command=None):
        bg = _bg(parent, kit)
        super().__init__(parent, bg=bg)
        self._kit, self.var, self.command = kit, variable, command
        self._items = {}
        style = ui_theme.on(parent, "Toggle.TButton", bg)
        for value, text in options:
            b = ttk.Radiobutton(self, text=text, value=value, variable=variable,
                                command=self._picked, style=style)
            b.pack(side="left", padx=(0, 4))
            self._items[value] = b

    def _pick(self, value):
        self.var.set(value)
        self._picked()

    def _picked(self):
        if self.command:
            self.command()


def field(parent, kit: Kit, show=None, width=None):
    """A text box: the theme's, with its accent underline while typing."""
    e = ttk.Entry(parent, show=show or "", font=kit.F["body"],
                  style=ui_theme.on(parent, "TEntry", _bg(parent, kit)))
    if width:
        e.configure(width=width)
    return e


def label(parent, kit: Kit, text="", role="body", tone="ink", wrap=None, **kw):
    C = kit.C
    lb = tk.Label(parent, text=text, font=kit.F[role], bg=_bg(parent, kit),
                  fg=C.get(tone, tone), justify="left", anchor="w", **kw)
    if wrap:
        lb.configure(wraplength=wrap)
    return lb


# ------------------------------------------------------------------ cards
class Card(tk.Frame):
    """A panel on a page: a hairline border, a title, and a body."""

    def __init__(self, parent, kit: Kit, title=None, pad=14):
        C = kit.C
        super().__init__(parent, bg=C["card"], highlightthickness=1,
                         highlightbackground=C["line"])
        self.body = tk.Frame(self, bg=C["card"])
        if title:
            head = tk.Frame(self, bg=C["card"])
            head.pack(fill="x", padx=pad, pady=(pad - 2, 0))
            tk.Label(head, text=title, font=kit.F["h3"], bg=C["card"],
                     fg=C["ink"]).pack(side="left")
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
                         bg=_bg(parent, kit))
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
        self._bar = ttk.Scrollbar(self, orient="vertical",
                                  command=self._canvas.yview,
                                  style=ui_theme.on(parent, "Vertical.TScrollbar",
                                                    bg))
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


# ----------------------------------------------------------- quiet detail
class Tooltip:
    """The detail a label does not need to carry, shown on hover.

    Explanations belong here rather than in sentences on the page: the page
    stays a set of short labels, and the reason is one hover away.
    """

    DELAY_MS = 350

    def __init__(self, widget, kit: Kit, text: str = ""):
        self.widget, self.kit, self.text = widget, kit, text
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._arm, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def set(self, text: str) -> None:
        self.text = text

    def _arm(self, _e=None) -> None:
        self._cancel()
        if self.text:
            self._after = self.widget.after(self.DELAY_MS, self.show)

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def show(self) -> None:
        self._after = None
        if self._tip is not None or not self.text:
            return
        C, F = self.kit.C, self.kit.F
        try:
            x = self.widget.winfo_rootx() + 6
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            tip = tk.Toplevel(self.widget)
        except tk.TclError:
            return
        tip.withdraw()                    # shown once, drawn: ui_theme.reveal
        tip.wm_overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        edge = tk.Frame(tip, bg=C["line_hi"], padx=1, pady=1)
        edge.pack()
        tk.Label(edge, text=self.text, font=F["small"], bg=C["menu"], fg=C["ink"],
                 justify="left", wraplength=300, padx=10, pady=7).pack()
        tip.geometry(f"+{x}+{y}")
        self._tip = tip
        ui_theme.reveal(tip)

    def hide(self, _e=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


def info(parent, kit: Kit, text: str) -> tk.Label:
    """A small (i) that explains on hover."""
    lb = tk.Label(parent, text="ⓘ", font=kit.F["small"], bg=_bg(parent, kit),
                  fg=kit.C["faint"], cursor="question_arrow")
    lb.tip = Tooltip(lb, kit, text)
    return lb


def placeholder(entry, kit: Kit, text: str) -> tk.Label:
    """Grey example text over an empty field. An overlay, not text in the
    field: what the field holds - entry.get() - is only ever what was typed.
    It takes the box's colour as that changes: at rest, under the pointer,
    and while typing."""
    C = kit.C
    var = tk.StringVar(master=entry, value=entry.get())
    entry.configure(textvariable=var)
    hint = tk.Label(entry, text=text, font=entry.cget("font"),
                    bg=C["field"], fg=C["faint"], cursor="xterm")

    def paint(_e=None):
        try:
            focused = entry.focus_get() is entry
        except (tk.TclError, KeyError):
            focused = False
        hovered = "hover" in entry.state()
        hint.configure(bg=C["field_focus"] if focused else
                       C["field_hover"] if hovered else C["field"])
    for seq in ("<FocusIn>", "<FocusOut>", "<Enter>", "<Leave>"):
        entry.bind(seq, lambda _e: entry.after_idle(paint), add="+")
    hint.bind("<Enter>", lambda _e: hint.configure(bg=C["field_hover"]), add="+")

    def update(*_):
        try:
            if var.get():
                hint.place_forget()
            elif not hint.winfo_manager():
                justify = str(entry.cget("justify"))
                if justify == "center":
                    hint.place(relx=0.5, rely=0.5, anchor="center")
                else:
                    hint.place(x=7, rely=0.5, anchor="w")
        except tk.TclError:
            pass
    var.trace_add("write", update)
    hint.bind("<Button-1>", lambda _e: entry.focus_set())
    entry._placeholder = hint
    entry._var = var
    update()
    return hint


def keys(parent, kit: Kit, pairs) -> tk.Frame:
    """A row of key hints: [("Drag", "move"), ("Ctrl+Z", "undo")]."""
    C, F = kit.C, kit.F
    bg = _bg(parent, kit)
    row = tk.Frame(parent, bg=bg)
    for key, what in pairs:
        tk.Label(row, text=key, font=F["caps"], bg=C["card_hi"], fg=C["ink"],
                 padx=6, pady=1).pack(side="left")
        tk.Label(row, text=what, font=F["small"], bg=bg, fg=C["dim"]
                 ).pack(side="left", padx=(6, 16))
    return row
