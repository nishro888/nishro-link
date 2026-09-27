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

import tkinter as tk


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
    until something is chosen or it is dismissed."""

    def __init__(self, master, kit, items: Items, x, y, keyboard=False,
                 on_close=None, over=None, step=None):
        C, F = kit.C, kit.F
        self.kit, self.items = kit, items
        self.on_close, self.over, self.step = on_close, over, step
        self.rows = []                # (entry, [widgets]) for each choosable row
        self.active = None
        self.top = top = tk.Toplevel(master, bg=C["line_hi"])
        top.withdraw()
        top.overrideredirect(True)
        try:
            top.attributes("-topmost", True)     # over everything, as a menu is
        except tk.TclError:
            pass
        body = tk.Frame(top, bg=C["menu"], pady=5)
        body.pack(padx=1, pady=1, fill="both", expand=True)
        for e in items.entries:
            if e["kind"] == "separator":
                tk.Frame(body, bg=C["line"], height=1).pack(fill="x", padx=10,
                                                           pady=5)
                continue
            on = e["state"] != "disabled"
            row = tk.Frame(body, bg=C["menu"])
            row.pack(fill="x")
            mark = "✓" if e["kind"] == "check" else "•"
            tick = tk.Label(row, text=mark if Items.ticked(e) else "", width=2,
                            font=F["body"], bg=C["menu"], fg=C["accent"])
            tick.pack(side="left", padx=(6, 0), pady=3)
            text = tk.Label(row, text=e["label"], font=F["body"], bg=C["menu"],
                            fg=C["ink"] if on else C["faint"], anchor="w")
            text.pack(side="left", fill="x", expand=True, padx=(2, 0))
            acc = tk.Label(row, text=e["accelerator"], font=F["small"],
                           bg=C["menu"], fg=C["dim"] if on else C["faint"])
            acc.pack(side="right", padx=(28, 14))
            parts = [row, tick, text, acc]
            if on:
                i = len(self.rows)
                self.rows.append((e, parts))
                for w in parts:
                    w.bind("<Enter>", lambda _e, i=i: self.light(i))
                    w.bind("<ButtonRelease-1>", lambda _e, i=i: self.choose(i))
        body.configure(width=210)
        top.update_idletasks()
        w, h = max(210, top.winfo_reqwidth()), top.winfo_reqheight()
        sw, sh = master.winfo_screenwidth(), master.winfo_screenheight()
        if 0 <= x < sw and x + w > sw:
            x = sw - w - 4
        if 0 <= y < sh and y + h > sh:
            y = max(0, y - h)
        top.geometry(f"{w}x{h}+{x}+{y}")
        top.deiconify()
        top.lift()

        for seq, fn in (("<Escape>", lambda _e: self.close()),
                        ("<Up>", lambda _e: self.move(-1)),
                        ("<Down>", lambda _e: self.move(1)),
                        ("<Return>", lambda _e: self.choose()),
                        ("<KP_Enter>", lambda _e: self.choose()),
                        ("<space>", lambda _e: self.choose()),
                        ("<Left>", lambda _e: self._step(-1)),
                        ("<Right>", lambda _e: self._step(1)),
                        ("<ButtonPress>", self._press),
                        ("<Motion>", self._motion),
                        ("<FocusOut>", self._focus_out)):
            top.bind(seq, fn)
        self._hold()
        if keyboard:
            self.move(1)

    def _hold(self, tries=10) -> None:
        """Take the pointer and the keys. X refuses a grab until the window is
        on screen, which is a moment after it is asked for."""
        if not self.alive():
            return
        try:
            self.top.grab_set()
            self.top.focus_force()
        except tk.TclError:
            if tries:
                self.top.after(30, lambda: self._hold(tries - 1))

    # ------------------------------------------------------------ choosing
    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def light(self, i) -> None:
        C = self.kit.C
        for j, (_, parts) in enumerate(self.rows):
            bg = C["menu_hi"] if j == i else C["menu"]
            for w in parts:
                w.configure(bg=bg)
        self.active = i

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

    def _focus_out(self, _e=None) -> None:
        # Another program was clicked: the app lost the focus altogether.
        def check():
            try:
                if self.alive() and self.top.focus_get() is None:
                    self.close()
            except (tk.TclError, KeyError):
                self.close()
        try:
            self.top.after(80, check)
        except tk.TclError:
            pass


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
