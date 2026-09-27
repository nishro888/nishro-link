"""The menu bar - File, View, Sharing, Help - and the menus it opens.

Both are drawn on the theme. Tk's own menus were tried first: on Windows the
system draws their border and separators white and embosses a disabled item
in white, which on a dark window looks broken; and its native menu bar is a
white strip that ignores every colour. So a menu here is a small borderless
window of the app's own, and it behaves as menus do:

  - click a title, or Alt and its underlined letter (F10 for the first)
  - with one open, the pointer slides onto the next title and it opens
  - Up/Down/Enter/Escape, and Left/Right to the neighbouring menu
  - a click anywhere else closes it - and does nothing else

A menu is filled when it opens, so it offers what is true now - a tick by the
page on show, Leave the group only for a member. fill(menu) is written as for
a tk.Menu: add_command, add_checkbutton, add_radiobutton, add_separator.
"""
from __future__ import annotations

import sys
import tkinter as tk

from . import ui_theme

KEY_TAG = "NishroLinkMenuKeys"


class Items:
    """What one menu holds, in the tk.Menu calls that fill it."""

    def __init__(self):
        self.entries = []

    def add_command(self, label, command=None, accelerator="", state="normal"):
        self.entries.append({"kind": "command", "label": label, "command": command,
                             "accelerator": accelerator, "state": state})

    def add_checkbutton(self, label, variable, command=None, accelerator="",
                        state="normal"):
        self.entries.append({"kind": "check", "label": label, "command": command,
                             "variable": variable, "accelerator": accelerator,
                             "state": state})

    def add_radiobutton(self, label, value, variable, command=None,
                        accelerator="", state="normal"):
        self.entries.append({"kind": "radio", "label": label, "command": command,
                             "variable": variable, "value": value,
                             "accelerator": accelerator, "state": state})

    def add_separator(self):
        self.entries.append({"kind": "separator"})

    # ---------------------------------------------------------- reading
    def labels(self) -> list:
        return [e["label"] for e in self.entries if e["kind"] != "separator"]

    def find(self, label) -> dict:
        return next(e for e in self.entries if e.get("label") == label)

    def state(self, label) -> str:
        return self.find(label)["state"]

    @staticmethod
    def ticked(e) -> bool:
        if e["kind"] == "check":
            return bool(e["variable"].get())
        if e["kind"] == "radio":
            return e["variable"].get() == e["value"]
        return False

    def invoke(self, label) -> None:
        run(self.find(label))


def run(e) -> None:
    """What choosing an entry does - as tk.Menu does it."""
    if e["kind"] == "separator" or e["state"] == "disabled":
        return
    if e["kind"] == "check":
        e["variable"].set(not e["variable"].get())
    elif e["kind"] == "radio":
        e["variable"].set(e["value"])
    if e["command"]:
        e["command"]()


class Dropdown:
    """One open menu: a borderless window that holds the pointer and the keys
    until something is chosen or it is dismissed.

    Drawn on ONE canvas. On Windows every Tk widget is a window of its own,
    and a menu of rows made of labels - forty-odd windows - took 70 ms to
    open, over 40 of them painting. Drawn as text on a canvas it is one
    window, and opens in under 20."""

    ROW_PAD, SEP_H, EDGE = 10, 11, 5

    def __init__(self, master, kit, items: Items, x, y, keyboard=False,
                 on_close=None, over=None, step=None):
        from tkinter import font as tkfont
        C, F = kit.C, kit.F
        self.kit, self.items = kit, items
        self.on_close, self.over, self.step = on_close, over, step
        self.rows = []                # (entry, canvas ids) for each choosable row
        self._bands = []              # (y0, y1, row index or None) down the menu
        self.active = None
        self.top = top = tk.Toplevel(master, bg=C["line_hi"])
        top.withdraw()
        top.overrideredirect(True)
        try:
            top.attributes("-topmost", True)     # over everything, as a menu is
        except tk.TclError:
            pass
        body, small = tkfont.Font(font=F["body"]), tkfont.Font(font=F["small"])
        row_h = body.metrics("linespace") + self.ROW_PAD
        label_w = max([body.measure(e["label"]) for e in items.entries
                       if e["kind"] != "separator"] or [0])
        acc_w = max([small.measure(e["accelerator"]) for e in items.entries
                     if e["kind"] != "separator" and e["accelerator"]] or [0])
        self.width = w = max(210, 36 + label_w + (28 + acc_w if acc_w else 0) + 16)
        h = self.EDGE * 2 + sum(self.SEP_H if e["kind"] == "separator" else row_h
                                for e in items.entries)
        self.canvas = c = tk.Canvas(top, width=w, height=h, bg=C["menu"],
                                    highlightthickness=0, borderwidth=0)
        c.pack(padx=1, pady=1)
        y0 = self.EDGE
        for e in items.entries:
            if e["kind"] == "separator":
                mid = y0 + self.SEP_H // 2
                c.create_line(10, mid, w - 10, mid, fill=C["line"])
                self._bands.append((y0, y0 + self.SEP_H, None))
                y0 += self.SEP_H
                continue
            on = e["state"] != "disabled"
            mid = y0 + row_h // 2
            band = c.create_rectangle(0, y0, w, y0 + row_h, fill=C["menu"],
                                      outline="")
            mark = "✓" if e["kind"] == "check" else "•"
            tick = c.create_text(20, mid, text=mark if Items.ticked(e) else "",
                                 fill=C["accent"], font=F["body"])
            c.create_text(36, mid, text=e["label"], anchor="w", font=F["body"],
                          fill=C["ink"] if on else C["faint"])
            if e["accelerator"]:
                c.create_text(w - 14, mid, text=e["accelerator"], anchor="e",
                              font=F["small"], fill=C["dim"] if on else C["faint"])
            index = None
            if on:
                index = len(self.rows)
                self.rows.append((e, (band, tick)))
            self._bands.append((y0, y0 + row_h, index))
            y0 += row_h
        c.bind("<Motion>", lambda ev: self._hover(ev.y), add="+")
        c.bind("<Leave>", lambda _e: self.light(None))
        c.bind("<ButtonRelease-1>", lambda ev: self._release(ev.y))
        w, h = w + 2, h + 2
        sw, sh = master.winfo_screenwidth(), master.winfo_screenheight()
        if 0 <= x < sw and x + w > sw:
            x = sw - w - 4
        if 0 <= y < sh and y + h > sh:
            y = max(0, y - h)
        top.geometry(f"{w}x{h}+{x}+{y}")
        # Shown transparent until its rows have painted: an empty grey box a
        # frame before the menu - filmed - is gone.
        fade = sys.platform == "win32"
        if fade:
            top.attributes("-alpha", 0.0)
        top.deiconify()
        top.lift()
        if fade:
            ui_theme.paint(top)             # its rows painted, unseen
            if self.alive():
                top.attributes("-alpha", 1.0)

        self._keys = {"Escape": self.close, "Up": lambda: self.move(-1),
                      "Down": lambda: self.move(1), "Return": self.choose,
                      "KP_Enter": self.choose, "space": self.choose,
                      "Left": lambda: self._step(-1), "Right": lambda: self._step(1)}
        top.bind("<Key>", self._key)
        top.bind("<ButtonPress>", self._press)
        top.bind("<Motion>", self._motion)
        self._keyed = None            # the widget whose keys come to us
        self._hold()
        self._watch()
        if keyboard:
            self.move(1)

    def _hold(self, tries=10) -> None:
        """Take the pointer; and the keys, WITHOUT the focus. A menu that took
        the focus made the main window's title bar go inactive and back each
        time one opened - a flicker, filmed. So the keys are borrowed from
        whatever has the focus: a tag put first in its bindings while the
        menu is open, and taken out when it closes. X refuses a grab until
        the window is on screen, which is a moment after it is asked for."""
        if not self.alive():
            return
        try:
            self.top.grab_set()
        except tk.TclError:
            if tries:
                self.top.after(30, lambda: self._hold(tries - 1))
            return
        try:
            w = self.top.focus_get()
        except (tk.TclError, KeyError):
            w = None
        if w is not None and w is not self.top:
            w.bind_class(KEY_TAG, "<Key>", lambda e: _MENU_KEYS[-1]._key(e)
                         if _MENU_KEYS else None)
            w.bindtags((KEY_TAG,) + tuple(t for t in w.bindtags() if t != KEY_TAG))
            self._keyed = w
            _MENU_KEYS.append(self)

    def _key(self, e):
        fn = self._keys.get(e.keysym)
        if fn is None:
            return None
        fn()
        return "break"

    def _watch(self) -> None:
        """Close when the program is left - another one clicked, Alt+Tab."""
        if not self.alive():
            return
        try:
            gone = self.top.focus_get() is None
        except (tk.TclError, KeyError):
            gone = False
        if gone:
            self.close()
            return
        self.top.after(150, self._watch)

    # ------------------------------------------------------------ choosing
    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def light(self, i) -> None:
        if i == self.active:
            return
        C = self.kit.C
        for j in (self.active, i):
            if j is not None and 0 <= j < len(self.rows):
                self.canvas.itemconfigure(self.rows[j][1][0],
                                          fill=C["menu_hi"] if j == i else C["menu"])
        self.active = i

    def tick(self, i) -> str:
        """The mark by row i: a tick, a dot, or nothing."""
        return self.canvas.itemcget(self.rows[i][1][1], "text")

    def _row_at(self, y):
        for y0, y1, index in self._bands:
            if y0 <= y < y1:
                return index
        return None

    def _hover(self, y) -> None:
        i = self._row_at(y)
        if i is not None:
            self.light(i)

    def _release(self, y) -> None:
        i = self._row_at(y)
        if i is not None:
            self.choose(i)

    def move(self, d) -> None:
        if not self.rows:
            return
        i = -1 if self.active is None and d > 0 else (
            0 if self.active is None else self.active)
        self.light((i + d) % len(self.rows))

    def choose(self, i=None) -> None:
        i = self.active if i is None else i
        if i is None:
            return
        e = self.rows[i][0]
        self.close()              # first: the choice may open a window
        run(e)

    def close(self) -> None:
        if not self.alive():
            return
        if self in _MENU_KEYS:
            _MENU_KEYS.remove(self)
        w, self._keyed = self._keyed, None
        if w is not None:
            try:
                w.bindtags(tuple(t for t in w.bindtags() if t != KEY_TAG))
            except tk.TclError:
                pass                      # it went while the menu was open
        try:
            self.top.grab_release()
            self.top.destroy()
        except tk.TclError:
            pass
        if self.on_close:
            self.on_close(self)

    # -------------------------------------------------------- the pointer
    def _inside(self, x, y) -> bool:
        t = self.top
        return (t.winfo_rootx() <= x < t.winfo_rootx() + t.winfo_width()
                and t.winfo_rooty() <= y < t.winfo_rooty() + t.winfo_height())

    def _press(self, e) -> str | None:
        if self._inside(e.x_root, e.y_root):
            return None
        # Outside: this click only closes the menu - or opens another one.
        if self.over:
            self.over(e.x_root, e.y_root, click=True)
        self.close()
        return "break"

    def _motion(self, e) -> None:
        if not self._inside(e.x_root, e.y_root) and self.over:
            self.over(e.x_root, e.y_root, click=False)

    def _step(self, d) -> None:
        if self.step:
            self.step(d)



_MENU_KEYS = []                   # open menus borrowing keys, newest last


def popup(master, kit, fill, x, y) -> Dropdown:
    """A menu at the pointer - a right-click."""
    items = Items()
    fill(items)
    return Dropdown(master, kit, items, x, y)


class MenuBar(tk.Frame):
    def __init__(self, parent, kit, menus):
        """menus: [(title, fill)] - fill(menu) adds the entries when it opens."""
        C, F = kit.C, kit.F
        super().__init__(parent, bg=C["sidebar"])
        self.kit = kit
        self.menus = dict(menus)
        self.titles = {}
        self.open = None              # the Dropdown showing, if any
        self.open_title = None
        row = tk.Frame(self, bg=C["sidebar"])
        row.pack(fill="x", padx=6)
        for title in self.menus:
            lb = tk.Label(row, text=title, font=F["body"], bg=C["sidebar"],
                          fg=C["dim"], padx=10, pady=4, underline=0)
            lb.pack(side="left")
            lb.bind("<ButtonPress-1>", lambda _e, t=title: self.post(t))
            lb.bind("<Enter>", lambda _e, t=title: self._light(t, True))
            lb.bind("<Leave>", lambda _e, t=title: self._light(t, False))
            self.titles[title] = lb
        tk.Frame(self, bg=C["line"], height=1).pack(fill="x")

        top = parent.winfo_toplevel()
        for title in self.menus:
            for key in {title[0].lower(), title[0].upper()}:
                top.bind(f"<Alt-Key-{key}>",
                         lambda _e, t=title: self.post(t, keyboard=True))
        first = next(iter(self.menus))
        top.bind("<F10>", lambda _e: self.post(first, keyboard=True))

    def build(self, title) -> Items:
        """The menu as it would open now. The tests read and invoke it."""
        items = Items()
        self.menus[title](items)
        return items

    def post(self, title, keyboard=False):
        if self.open is not None and self.open.alive():
            self.open.close()
        lb = self.titles[title]
        self.open_title = title
        self._paint()
        self.open = Dropdown(self, self.kit, self.build(title),
                             lb.winfo_rootx(), lb.winfo_rooty() + lb.winfo_height(),
                             keyboard=keyboard, on_close=self._closed,
                             over=self._over, step=self._step)
        return "break"

    def _closed(self, dd) -> None:
        if dd is self.open:
            self.open = self.open_title = None
            self._paint()

    def _title_at(self, x, y):
        for title, lb in self.titles.items():
            if (lb.winfo_rootx() <= x < lb.winfo_rootx() + lb.winfo_width()
                    and lb.winfo_rooty() <= y < lb.winfo_rooty() + lb.winfo_height()):
                return title
        return None

    def _over(self, x, y, click) -> None:
        """The pointer, outside the open menu, is over (x, y) - moving, or
        clicking. On another title either opens that menu; a click on the
        open menu's own title just closes it, as the caller then does."""
        title = self._title_at(x, y)
        if title and title != self.open_title and self.open is not None:
            self.post(title)

    def _step(self, d) -> None:
        names = list(self.menus)
        i = names.index(self.open_title) if self.open_title in names else 0
        self.post(names[(i + d) % len(names)], keyboard=True)

    def _light(self, title, on) -> None:
        if title == self.open_title:
            return
        C = self.kit.C
        try:
            self.titles[title].configure(bg=C["card_hi"] if on else C["sidebar"],
                                         fg=C["ink"] if on else C["dim"])
        except tk.TclError:
            pass

    def _paint(self) -> None:
        C = self.kit.C
        for title, lb in self.titles.items():
            on = title == self.open_title
            try:
                lb.configure(bg=C["card_hi"] if on else C["sidebar"],
                             fg=C["ink"] if on else C["dim"])
            except tk.TclError:
                pass
