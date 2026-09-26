"""The application window.

Tkinter, because this has to run on a low-spec laptop and pack into the same
single-file exe: it is in the standard library and adds nothing to install on
Linux either. The look comes from ui_theme (colours, fonts) and ui_kit (buttons,
cards, pills, switches) - this file only lays pages out and says what they show.

It calls ControlAPI's status()/command() in-process, NOT over HTTP: this window
and the web page drive exactly the same code.

LAYOUT. A sidebar of pages - Overview, Devices, Arrangement, Activity, Settings
- and a header that always says the one thing worth knowing: whether linking is
on, and how many machines are here. The sidebar folds down to icons on a narrow
window; every page scrolls when it has to; text wraps to the window. The minimum
fits a netbook, and the opening size is clamped to the actual display - the
first version asked for 780 pixels of height on a 768-pixel laptop.

THREADS. Everything here runs on the Tk main thread; the link runs on its own.
Nothing here blocks - state is polled with after(), never waited for, and the
two slow actions (the firewall prompt, the network search) run on threads.
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from . import autostart, ui_arrange, ui_pair, ui_theme
from .ui_kit import (Button, Card, Dot, Kit, Metric, Monitors, Pill, Scroll,
                     Segmented, Toggle, ago, field, label)

POLL_MS = 700
WM_CLASS = "nishro-link"      # Tk shows the class as "Nishro-link"
MIN_W, MIN_H = 560, 440            # small enough for a netbook
WANT_W, WANT_H = 1040, 700         # clamped to the display before use
NARROW = 820                       # below this the sidebar folds to icons

PAGES = (("overview", "Overview", "◈"),
         ("devices", "Devices", "▣"),
         ("arrange", "Arrangement", "⊞"),
         ("activity", "Activity", "≡"),
         ("settings", "Settings", "⚙"))


class App:
    def __init__(self, api, on_quit=None, root=None):
        self.api = api
        self.on_quit = on_quit
        self.touched: set = set()
        self._last = None
        self._alive = True
        self._poll_id = None
        self._msg_id = None
        self._wrapped = []            # labels whose wraplength follows the window
        self._dev_sig = None          # what the device cards were built from
        self._dev_rows = {}           # name -> widgets updated in place
        self.page = None

        # `root` lets the tests hand in a Toplevel: a fresh Tk() per test is a
        # fresh Tcl interpreter, and on Windows starting those one after another
        # fails now and then ("couldn't read file init.tcl: No error").
        #
        # The class names the window to the desktop: GNOME's dock matches it to
        # nishro-link.desktop and shows the app's name and icon. Tk's default
        # class is "Tk", which the dock shows as an unknown program.
        self.root = root if root is not None else tk.Tk(className=WM_CLASS)
        self.root.title("Nishro Link")
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.minsize(MIN_W, MIN_H)
        self._size_to_fit()

        self.C = ui_theme.palette(self.root)
        self.F = ui_theme.fonts(self.root)
        self.kit = Kit(self.C, self.F)
        self.root.configure(bg=self.C["bg"])

        self._build()
        self.show_page("overview")
        self.root.bind("<Configure>", self._reflow)
        for i, (name, _, _) in enumerate(PAGES, start=1):
            self.root.bind(f"<Control-Key-{i}>", lambda _e, n=name: self.show_page(n))
        self._reflow()
        self._poll()

    def _size_to_fit(self) -> None:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = max(MIN_W, min(WANT_W, sw - 80))
        h = max(MIN_H, min(WANT_H, sh - 120))
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

    def _wrap(self, lb):
        self._wrapped.append(lb)
        return lb

    # ================================================================ frame
    def _build(self) -> None:
        C, F, kit = self.C, self.F, self.kit
        # ---- sidebar
        self.sidebar = tk.Frame(self.root, bg=C["sidebar"], width=200)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        brand = tk.Frame(self.sidebar, bg=C["sidebar"])
        brand.pack(fill="x", padx=16, pady=(18, 22))
        tk.Label(brand, text="◆", font=F["h2"], bg=C["sidebar"],
                 fg=C["accent"]).pack(side="left")
        self.brand_text = tk.Label(brand, text=" NISHRO LINK", font=F["h3"],
                                   bg=C["sidebar"], fg=C["ink"])
        self.brand_text.pack(side="left")

        self.nav = {}
        for name, text, glyph in PAGES:
            self.nav[name] = self._nav_item(name, text, glyph)

        foot = tk.Frame(self.sidebar, bg=C["sidebar"])
        foot.pack(side="bottom", fill="x", padx=12, pady=14)
        st = tk.Frame(foot, bg=C["sidebar"])
        st.pack(fill="x", pady=(0, 10))
        self.dot = Dot(st, kit, size=10)
        self.dot.pack(side="left", padx=(4, 8))
        self.side_status = tk.Label(st, text="starting…", font=F["small"],
                                    bg=C["sidebar"], fg=C["dim"], anchor="w")
        self.side_status.pack(side="left", fill="x", expand=True)
        self.btn_release = Button(foot, kit, "Release input", self._release,
                                  kind="secondary", small=True)
        self.btn_release.pack(fill="x")
        row = tk.Frame(foot, bg=C["sidebar"])
        row.pack(fill="x", pady=(8, 0))
        Button(row, kit, "Hide", self.hide, kind="ghost", small=True).pack(side="left")
        Button(row, kit, "Quit", self._quit, kind="ghost", small=True).pack(side="right")

        # ---- main
        self.main = tk.Frame(self.root, bg=C["panel"])
        self.main.pack(side="left", fill="both", expand=True)
        head = tk.Frame(self.main, bg=C["panel"])
        head.pack(fill="x", padx=26, pady=(20, 6))
        left = tk.Frame(head, bg=C["panel"])
        left.pack(side="left", fill="x", expand=True)
        self.title = tk.Label(left, text="", font=F["h1"], bg=C["panel"],
                              fg=C["ink"], anchor="w")
        self.title.pack(anchor="w")
        self.subtitle = tk.Label(left, text="starting…", font=F["small"],
                                 bg=C["panel"], fg=C["dim"], anchor="w")
        self.subtitle.pack(anchor="w", pady=(2, 0))
        self.chip = Pill(head, kit, "…")
        self.chip.pack(side="left", padx=(8, 12))
        self.btn_toggle = Button(head, kit, "…", self._toggle, kind="primary")
        self.btn_toggle.pack(side="left")

        # the message line, and the firewall banner - on every page
        self.msg = self._wrap(tk.Label(self.main, text="", font=F["small"],
                                       bg=C["panel"], fg=C["dim"], anchor="w",
                                       justify="left"))
        self.msg.pack(fill="x", padx=26)
        self.fw_box = tk.Frame(self.main, bg=C["bad_bg"], highlightthickness=1,
                               highlightbackground=C["bad"])
        self.fw_text = self._wrap(tk.Label(
            self.fw_box, bg=C["bad_bg"], fg=C["ink"], font=F["small"],
            justify="left", anchor="w",
            text="Windows Firewall is blocking Nishro Link, so other devices "
                 "cannot find or reach this one. That happens when a Windows "
                 "Security prompt about it is dismissed."))
        self.fw_text.pack(side="left", fill="x", expand=True, padx=12, pady=10)
        self.fw_btn = Button(self.fw_box, kit, "Allow through the firewall",
                             self._fix_firewall, kind="danger", small=True)
        self.fw_btn.pack(side="right", padx=12)

        # Linux, first run: the keyboard and mouse are not ours to use yet.
        self.setup_box = tk.Frame(self.main, bg=C["warn_bg"], highlightthickness=1,
                                  highlightbackground=C["warn"])
        self.setup_text = self._wrap(tk.Label(
            self.setup_box, bg=C["warn_bg"], fg=C["ink"], font=F["small"],
            justify="left", anchor="w", text=""))
        self.setup_text.pack(side="left", fill="x", expand=True, padx=12, pady=10)
        self.setup_btn = Button(self.setup_box, kit, "Set up permissions",
                                self._fix_setup, kind="primary", small=True)
        self.setup_btn.pack(side="right", padx=12)

        self.stack = tk.Frame(self.main, bg=C["panel"])
        self.stack.pack(fill="both", expand=True, pady=(6, 0))
        self.pages = {"overview": self._overview(), "devices": self._devices(),
                      "arrange": self._arrange(), "activity": self._activity(),
                      "settings": self._settings()}

    def _nav_item(self, name, text, glyph):
        C, F = self.C, self.F
        row = tk.Frame(self.sidebar, bg=C["sidebar"], cursor="hand2")
        row.pack(fill="x", padx=10, pady=1)
        bar = tk.Frame(row, bg=C["sidebar"], width=3)
        bar.pack(side="left", fill="y")
        g = tk.Label(row, text=glyph, font=F["h2"], bg=C["sidebar"], fg=C["dim"],
                     width=2)
        g.pack(side="left", padx=(8, 4), pady=7)
        t = tk.Label(row, text=text, font=F["nav"], bg=C["sidebar"], fg=C["dim"],
                     anchor="w")
        t.pack(side="left", fill="x", expand=True)
        for w in (row, g, t, bar):
            w.bind("<Button-1>", lambda _e, n=name: self.show_page(n))
            w.bind("<Enter>", lambda _e, n=name: self._nav_hover(n, True))
            w.bind("<Leave>", lambda _e, n=name: self._nav_hover(n, False))
        return {"row": row, "bar": bar, "glyph": g, "text": t}

    def _nav_hover(self, name, on):
        if name == self.page:
            return
        bg = self.C["card"] if on else self.C["sidebar"]
        for k in ("row", "glyph", "text"):
            self.nav[name][k].configure(bg=bg)

    def show_page(self, name) -> None:
        if name == self.page:
            return
        if self.page:
            self.pages[self.page].pack_forget()
        self.page = name
        self.pages[name].pack(fill="both", expand=True)
        C = self.C
        for n, item in self.nav.items():
            on = n == name
            bg = C["card"] if on else C["sidebar"]
            for k in ("row", "glyph", "text"):
                item[k].configure(bg=bg)
            item["bar"].configure(bg=C["accent"] if on else C["sidebar"])
            item["glyph"].configure(fg=C["accent"] if on else C["dim"])
            item["text"].configure(fg=C["ink"] if on else C["dim"])
        self.title.configure(text=dict((n, t) for n, t, _ in PAGES)[name])

    def _reflow(self, _e=None) -> None:
        """Fold the sidebar on a narrow window, and wrap text to the space."""
        w = self.root.winfo_width()
        narrow = 1 < w < NARROW
        self.sidebar.configure(width=64 if narrow else 200)
        for item in self.nav.values():
            if narrow:
                item["text"].pack_forget()
            elif not item["text"].winfo_manager():
                item["text"].pack(side="left", fill="x", expand=True)
        if narrow:
            self.brand_text.pack_forget()
            self.side_status.pack_forget()
        else:
            if not self.brand_text.winfo_manager():
                self.brand_text.pack(side="left")
            if not self.side_status.winfo_manager():
                self.side_status.pack(side="left", fill="x", expand=True)
        width = max(200, self.main.winfo_width() - 90)
        for lb in self._wrapped:
            try:
                current = int(lb.cget("wraplength"))
            except (TypeError, ValueError, tk.TclError):
                current = -1
            if current != width:
                try:
                    lb.configure(wraplength=width)
                except tk.TclError:
                    pass
        cols = 2 if self.main.winfo_width() >= 760 else 1
        if getattr(self, "_dev_cols", None) != cols:
            self._dev_cols = cols
            self._dev_sig = None          # rebuild the grid at the new width

    def _page(self):
        s = Scroll(self.stack, self.kit)
        s.inner.configure(padx=26, pady=10)
        return s, s.inner

    # ============================================================= Overview
    def _overview(self):
        C, F, kit = self.C, self.F, self.kit
        page, box = self._page()

        hero = Card(box, kit, "Control")
        hero.pack(fill="x")
        top = tk.Frame(hero.body, bg=C["card"])
        top.pack(fill="x")
        self.hero_name = tk.Label(top, text="—", font=F["hero"], bg=C["card"],
                                  fg=C["accent"], anchor="w")
        self.hero_name.pack(side="left")
        self.hero_pill = Pill(top, kit, "")
        self.hero_pill.pack(side="left", padx=12)
        self.hero_sub = self._wrap(label(hero.body, kit, "", "body", "dim"))
        self.hero_sub.configure(bg=C["card"])
        self.hero_sub.pack(anchor="w", pady=(4, 0))

        grid = tk.Frame(box, bg=C["panel"])
        grid.pack(fill="x", pady=12)
        self.m_online = Metric(grid, kit, "devices online")
        self.m_rtt = Metric(grid, kit, "round trip")
        self.m_mouse = Metric(grid, kit, "your mouse")
        self.m_kbd = Metric(grid, kit, "your keyboard")
        for i, m in enumerate((self.m_online, self.m_rtt, self.m_mouse, self.m_kbd)):
            m.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            grid.columnconfigure(i, weight=1, uniform="m")

        prev = Card(box, kit, "Arrangement")
        prev.pack(fill="x")
        Button(prev.head, kit, "Open  →", lambda: self.show_page("arrange"),
               kind="ghost", small=True).pack(side="right")
        self.preview = ui_arrange.Arranger(prev.body, [], "?", palette=C,
                                           height=150, readonly=True)

        now = Card(box, kit, "What is happening")
        now.pack(fill="x", pady=(12, 0))
        self.explain = self._wrap(label(now.body, kit, "", "body", "ink"))
        self.explain.configure(bg=C["card"])
        self.explain.pack(anchor="w", fill="x")
        self.trust = self._wrap(label(now.body, kit, "", "small", "dim"))
        self.trust.configure(bg=C["card"])
        self.trust.pack(anchor="w", fill="x", pady=(8, 0))
        return page

    # ============================================================== Devices
    def _devices(self):
        C, kit = self.C, self.kit
        page, box = self._page()
        bar = tk.Frame(box, bg=C["panel"])
        bar.pack(fill="x", pady=(0, 12))
        self.dev_intro = self._wrap(label(bar, kit, "", "body", "dim"))
        self.dev_intro.configure(bg=C["panel"])
        self.dev_intro.pack(side="left", fill="x", expand=True)
        self.btn_add = Button(bar, kit, "+  Add a device", self._add_device,
                              kind="primary")
        self.btn_add.pack(side="right")
        self.dev_list = tk.Frame(box, bg=C["panel"])
        self.dev_list.pack(fill="x")
        foot = tk.Frame(box, bg=C["panel"])
        foot.pack(fill="x", pady=(14, 0))
        self.btn_forget = Button(foot, kit, "Leave this group", self._forget,
                                 kind="danger", small=True)
        self.btn_forget.pack(side="left")
        self.my_addr = self._wrap(label(foot, kit, "", "small", "faint"))
        self.my_addr.configure(bg=C["panel"])
        self.my_addr.pack(side="left", padx=12)
        return page

    def _device_cards(self, s) -> None:
        """Rebuilt only when WHO is there changes; round trips and 'last seen'
        are updated in place, so the page does not flicker every poll."""
        devs = s.get("devices") or []
        sig = tuple((d["name"], d["online"], d["me"], d["hub"],
                     tuple(map(tuple, d["displays"]))) for d in devs) + \
            (s["hub"], getattr(self, "_dev_cols", 2))
        if sig != self._dev_sig:
            self._dev_sig = sig
            for w in self.dev_list.winfo_children():
                w.destroy()
            self._dev_rows = {}
            cols = getattr(self, "_dev_cols", 2)
            for c in range(cols):
                self.dev_list.columnconfigure(c, weight=1, uniform="dev")
            for i, d in enumerate(devs):
                card = self._device_card(d, s)
                card.grid(row=i // cols, column=i % cols, sticky="nsew",
                          padx=(0 if i % cols == 0 else 12, 0), pady=(0, 12))
        for d in devs:
            row = self._dev_rows.get(d["name"])
            if row:
                row["state"].configure(text=_device_state(d))

    def _device_card(self, d, s):
        C, F, kit = self.C, self.F, self.kit
        card = tk.Frame(self.dev_list, bg=C["card"], highlightthickness=1,
                        highlightbackground=C["accent"] if d["me"] else C["line"])
        inner = tk.Frame(card, bg=C["card"])
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        pic = Monitors(inner, kit)
        pic.configure(bg=C["card"])
        pic.pack(side="left", padx=(0, 14))
        colour = (C["offline"] if not d["online"] else
                  C["mine"] if d["me"] else C["theirs"])
        fill = (C["offline_fill"] if not d["online"] else
                C["mine_fill"] if d["me"] else C["theirs_fill"])
        parts = [tuple(r) for r in d.get("rects") or []]
        if not parts:                     # an older status without positions
            x = 0
            for w, h in d["displays"]:
                parts.append((x, 0, w, h))
                x += w
        pic.draw(parts, colour, fill)
        text = tk.Frame(inner, bg=C["card"])
        text.pack(side="left", fill="both", expand=True)
        top = tk.Frame(text, bg=C["card"])
        top.pack(fill="x")
        tk.Label(top, text=d["name"], font=F["h2"], bg=C["card"], fg=C["ink"],
                 anchor="w").pack(side="left")
        if d["me"]:
            Pill(top, kit, "THIS DEVICE", "accent").pack(side="left", padx=(8, 0))
        if d["hub"]:
            Pill(top, kit, "HUB", "accent2").pack(side="left", padx=(6, 0))
        n = len(d["displays"])
        sizes = ", ".join(f"{w}×{h}" for w, h in d["displays"])
        tk.Label(text, text=f"{n} display{'s' if n != 1 else ''}  ·  {sizes}",
                 font=F["small"], bg=C["card"], fg=C["dim"],
                 anchor="w").pack(fill="x", pady=(3, 0))
        state = tk.Label(text, text=_device_state(d), font=F["small"], bg=C["card"],
                         fg=C["ok"] if d["online"] else C["faint"], anchor="w")
        state.pack(fill="x")
        self._dev_rows[d["name"]] = {"state": state}
        if s["hub"] and not d["me"] and not d["online"]:
            Button(text, kit, "Forget", lambda n=d["name"]: self._forget_device(n),
                   kind="ghost", small=True).pack(anchor="w", pady=(6, 0))
        return card

    # ========================================================== Arrangement
    def _arrange(self):
        C, kit = self.C, self.kit
        page = tk.Frame(self.stack, bg=C["panel"])
        box = tk.Frame(page, bg=C["panel"])
        box.pack(fill="both", expand=True, padx=26, pady=10)
        bar = tk.Frame(box, bg=C["panel"])
        bar.pack(fill="x", pady=(0, 10))
        Button(bar, kit, "In a row", lambda: self.arranger.tidy(),
               small=True).pack(side="left")
        Button(bar, kit, "In a column", lambda: self.arranger.stack(),
               small=True).pack(side="left", padx=6)
        Button(bar, kit, "Apply", self._apply_arrangement,
               kind="primary").pack(side="right")
        Button(bar, kit, "Revert", self._revert_arrangement).pack(side="right", padx=8)
        holder = tk.Frame(box, bg=C["surface"], highlightthickness=1,
                          highlightbackground=C["line"])
        holder.pack(fill="both", expand=True)
        self.arranger = ui_arrange.Arranger(holder, [], "?", palette=C, height=320,
                                            on_change=self._arranged,
                                            on_select=lambda _n: self._arr_text())
        info = tk.Frame(box, bg=C["panel"])
        info.pack(fill="x", pady=(10, 0))
        self.arr_info = self._wrap(label(info, kit, "", "body", "ink"))
        self.arr_info.configure(bg=C["panel"])
        self.arr_info.pack(anchor="w", fill="x")
        self.arr_problems = self._wrap(label(info, kit, "", "small", "bad"))
        self.arr_problems.configure(bg=C["panel"])
        self.arr_problems.pack(anchor="w", fill="x", pady=(4, 0))
        self._wrap(label(
            info, kit, "Drag a machine to where it sits - it snaps to edges and "
                       "lines up with them. The pointer crosses only along the "
                       "bright lines, and a machine that is not connected is a "
                       "wall. Arrow keys nudge the selected one (Shift for bigger "
                       "steps). Nothing changes until you press Apply.",
            "small", "faint")).pack(anchor="w", fill="x", pady=(8, 0))
        for w in info.winfo_children():
            w.configure(bg=C["panel"])
        return page

    # ============================================================= Activity
    def _activity(self):
        C, F, kit = self.C, self.F, self.kit
        page = tk.Frame(self.stack, bg=C["panel"])
        box = tk.Frame(page, bg=C["panel"])
        box.pack(fill="both", expand=True, padx=26, pady=10)
        wrap = tk.Frame(box, bg=C["surface"], highlightthickness=1,
                        highlightbackground=C["line"])
        wrap.pack(fill="both", expand=True)
        self.log = tk.Text(wrap, wrap="word", font=F["mono"], relief="flat",
                           borderwidth=0, bg=C["surface"], fg=C["dim"],
                           insertbackground=C["ink"], padx=12, pady=10,
                           state="disabled", height=6,
                           selectbackground=C["line_hi"])
        bar = tk.Scrollbar(wrap, orient="vertical", command=self.log.yview,
                           bg=C["card"], troughcolor=C["surface"], relief="flat",
                           borderwidth=0, width=10)
        self.log.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for tag, colour in (("bad", C["bad"]), ("warn", C["warn"]),
                            ("ok", C["ok"]), ("ts", C["faint"])):
            self.log.tag_configure(tag, foreground=colour)
        foot = tk.Frame(box, bg=C["panel"])
        foot.pack(fill="x", pady=(10, 0))
        self.logpath = self._wrap(label(foot, kit, "", "small", "faint"))
        self.logpath.configure(bg=C["panel"])
        self.logpath.pack(side="left", fill="x", expand=True)
        Button(foot, kit, "Open the log folder", self._open_log,
               small=True).pack(side="right")
        return page

    # ============================================================= Settings
    def _settings(self):
        C, F, kit = self.C, self.F, self.kit
        page, box = self._page()

        me = Card(box, kit, "This device")
        me.pack(fill="x")
        g = tk.Frame(me.body, bg=C["card"])
        g.pack(fill="x")
        self.node_name = self._row_field(g, "Device name", 0, "node")
        self.port = self._row_field(g, "Port", 1, "port")
        g.columnconfigure(1, weight=1)

        ctl = Card(box, kit, "Control")
        ctl.pack(fill="x", pady=(12, 0))
        tk.Label(ctl.body, text="What takes control", font=F["h3"], bg=C["card"],
                 fg=C["ink"]).pack(anchor="w")
        self.claim = tk.StringVar(value="motion")
        Segmented(ctl.body, kit, (("motion", "Moving the mouse"),
                                  ("click", "A click"),
                                  ("hotkey", "A hotkey")),
                  self.claim, command=lambda: self.touched.add("claim")
                  ).pack(anchor="w", pady=(6, 12))
        self.may_drive = tk.BooleanVar(value=True)
        self.may_be_driven = tk.BooleanVar(value=True)
        for var, text, key in (
                (self.may_drive, "This device may take control of the others",
                 "may_drive"),
                (self.may_be_driven, "The others may take control of this device",
                 "may_be_driven")):
            row = tk.Frame(ctl.body, bg=C["card"])
            row.pack(fill="x", pady=3)
            Toggle(row, kit, var, command=lambda k=key: self.touched.add(k)
                   ).pack(side="left")
            tk.Label(row, text=text, font=F["body"], bg=C["card"],
                     fg=C["ink"]).pack(side="left", padx=10)

        # Applied as soon as it is flipped, not by Save: it changes nothing in
        # the running program, and a switch that waits for a button reads as
        # broken.
        self.autostart = self.autostart_note = None
        if autostart.available():
            st = Card(box, kit, "Startup")
            st.pack(fill="x", pady=(12, 0))
            row = tk.Frame(st.body, bg=C["card"])
            row.pack(fill="x", pady=3)
            self.autostart = tk.BooleanVar(value=False)
            Toggle(row, kit, self.autostart, command=self._set_autostart
                   ).pack(side="left")
            tk.Label(row, text="Start Nishro Link when I log in", font=F["body"],
                     bg=C["card"], fg=C["ink"]).pack(side="left", padx=10)
            self.autostart_note = self._wrap(label(st.body, kit, "", "small", "dim"))
            self.autostart_note.configure(bg=C["card"])
            self.autostart_note.pack(anchor="w", fill="x", pady=(8, 0))

        sec = Card(box, kit, "Security")
        sec.pack(fill="x", pady=(12, 0))
        g = tk.Frame(sec.body, bg=C["card"])
        g.pack(fill="x")
        self.password = self._row_field(g, "Password", 0, "pin", show="•")
        g.columnconfigure(1, weight=1)
        self._wrap(label(
            sec.body, kit, "Every device in the group uses the same password. It is "
                           "never sent over the network: each side proves it knows "
                           "it by answering the other's challenge. Leave it empty "
                           "to keep the current one.", "small", "dim")
        ).pack(anchor="w", fill="x", pady=(8, 0))
        for w in sec.body.winfo_children():
            if isinstance(w, tk.Label):
                w.configure(bg=C["card"])

        bar = tk.Frame(box, bg=C["panel"])
        bar.pack(fill="x", pady=14)
        Button(bar, kit, "Save", self._save, kind="primary").pack(side="left")
        return page

    def _row_field(self, parent, text, row, key, show=None):
        C, F = self.C, self.F
        tk.Label(parent, text=text, font=F["body"], bg=C["card"],
                 fg=C["dim"]).grid(row=row, column=0, sticky="w", pady=5,
                                   padx=(0, 14))
        e = field(parent, self.kit, show=show)
        e.grid(row=row, column=1, sticky="ew", pady=5, ipady=4)
        e.bind("<KeyRelease>", lambda _e, k=key: self.touched.add(k))
        return e

    # ================================================================ state
    def _poll(self) -> None:
        # after() keeps firing once rescheduled, and a callback that lands after
        # the window is gone prints 'invalid command name ..._poll' from inside
        # Tcl - noise that looks like a crash and is not one.
        if not self._alive:
            return
        try:
            self._render(self.api.status())
        except Exception as e:
            try:
                self.subtitle.configure(text=f"cannot read status: {e!r}")
            except tk.TclError:
                return
        self._poll_id = self.root.after(POLL_MS, self._poll)

    def _render(self, s: dict) -> None:
        C = self.C
        self._last = s
        devs = s.get("devices") or []
        online = sum(1 for d in devs if d["online"])
        role = "the hub - others connect to it" if s["hub"] else \
            f"connected through {_peer(s)}" if s["connected"] else \
            f"looking for {_peer(s)}" if s.get("paired") else "not paired yet"
        self.subtitle.configure(text=f"{s['node']}  ·  {role}  ·  "
                                     f"{online} of {len(devs)} online")
        if s.get("setup"):
            colour = C["warn"]            # the setup banner below says why
        elif not s["enabled"]:
            self.chip.set("LINKING OFF", "bad")
            colour = C["bad"]
        elif s["connected"]:
            self.chip.set("LINKED", "ok")
            colour = C["ok"]
        else:
            self.chip.set("WAITING" if s["hub"] else "SEARCHING", "warn")
            colour = C["warn"]
        self.dot.set(colour)
        self.side_status.configure(
            text=("off" if not s["enabled"] else
                  f"{online} of {len(devs)} online"))
        self.btn_toggle.set_text("Stop linking" if s["enabled"] else "Start linking")

        setup = s.get("setup")
        shown = self.setup_box.winfo_manager() == "pack"
        if setup and not shown:
            self.setup_box.pack(fill="x", padx=26, pady=(6, 0), before=self.stack)
        elif not setup and shown:
            self.setup_box.pack_forget()
        if setup:
            head = ("Almost ready." if setup.get("relogin") else
                    "Nishro Link needs permission to use this computer's "
                    "keyboard and mouse.")
            self.setup_text.configure(text=head + " " + " ".join(setup["problems"]))
            if setup.get("fixable"):
                if not self.setup_btn.winfo_manager():
                    self.setup_btn.pack(side="right", padx=12)
            elif self.setup_btn.winfo_manager():
                self.setup_btn.pack_forget()
            self.chip.set("SETUP NEEDED", "warn")

        blocked = bool(s.get("firewall_blocked"))
        shown = self.fw_box.winfo_manager() == "pack"
        if blocked and not shown:
            self.fw_box.pack(fill="x", padx=26, pady=(6, 0), before=self.stack)
        elif not blocked and shown:
            self.fw_box.pack_forget()

        # ---- overview
        holder = "this device" if s["holds"] else (s["holder"] or "nobody")
        self.hero_name.configure(text=s["node"] if s["holds"] else
                                 (s["holder"] or "nobody"),
                                 fg=C["accent"] if s["holder"] else C["faint"])
        self.hero_pill.set("YOU" if s["holds"] else
                           ("DRIVING" if s["holder"] else "IDLE"),
                           "accent" if s["holds"] else "dim")
        cur = s["cursor"]
        self.hero_sub.configure(text=(
            f"{holder.capitalize()} has control. The pointer is on "
            f"{cur['screen']}" + ("." if not cur["remote"] else
                                  " - your keyboard types there.")))
        self.m_online.set(f"{online} / {len(devs)}",
                          "ok" if online > 1 else "dim")
        rtts = [p["rtt_ms"] for p in s.get("peers") or [] if p.get("rtt_ms")]
        self.m_rtt.set(f"{min(rtts):.1f} ms" if rtts else "—")
        self.m_mouse.set("forwarded" if s["suppress"]["mouse"] else "local",
                         "accent" if s["suppress"]["mouse"] else "ink")
        self.m_kbd.set("forwarded" if s["suppress"]["keyboard"] else "local",
                       "accent" if s["suppress"]["keyboard"] else "ink")
        self.explain.configure(text=_explain(s))
        text, tone = _trust(s)
        self.trust.configure(text=text, fg=C[tone])
        live = [d["name"] for d in devs if d["online"]] or [s["node"]]
        for arr in (self.preview, self.arranger):
            arr.node = s["node"]
            arr.set_boxes(s.get("placement") or [])
            arr.set_online(live)
        self._arr_text()

        # ---- devices
        self.dev_intro.configure(text=(
            "Every machine in this group. Machines that are switched off stay "
            "here, dimmed, until they are forgotten." if len(devs) > 1 else
            "Only this device so far. Choose “Add a device” here and on the "
            "other machine."))
        self._device_cards(s)
        self.btn_forget.configure(text="Leave this group" if not s["hub"]
                                  else "Stop being the hub")
        self.btn_forget.set_enabled(bool(s.get("paired")))
        addrs = s.get("addresses") or _addresses()
        self.my_addr.configure(text=("This device is at " + ", ".join(addrs))
                               if addrs else "This device has no network address.")

        # ---- settings
        if "claim" not in self.touched:
            self.claim.set(s["policy"].get("claim", "motion"))
        if "node" not in self.touched:
            self._replace(self.node_name, s["node"])
        if "port" not in self.touched:
            self._replace(self.port, str(s["port"]))
        if "may_drive" not in self.touched:
            self.may_drive.set(s["policy"].get("may_drive", True))
        if "may_be_driven" not in self.touched:
            self.may_be_driven.set(s["policy"].get("may_be_driven", True))
        if self.autostart is not None:
            a = s.get("autostart") or {}
            if self.autostart.get() != bool(a.get("on")):
                self.autostart.set(bool(a.get("on")))
            note = _autostart_note(a)
            if self.autostart_note.cget("text") != note:
                self.autostart_note.configure(text=note)

        # ---- activity
        lines = s.get("log", [])
        body = "\n".join(lines)
        if body != self.log.get("1.0", "end-1c"):
            at_end = self.log.yview()[1] > 0.99
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            for i, line in enumerate(lines):
                if i:
                    self.log.insert("end", "\n")
                self.log.insert("end", line, _tone(line))
            self.log.configure(state="disabled")
            if at_end:
                self.log.see("end")
        self.logpath.configure(text=f"Also written to {_log_path()}")

    @staticmethod
    def _replace(entry, value):
        if entry.get() != value:
            entry.delete(0, "end")
            entry.insert(0, value)

    # ============================================================== actions
    def _toggle(self) -> None:
        on = bool(self._last and self._last["enabled"])
        self.api.command("/api/disable" if on else "/api/enable", {})
        self._poll_now()

    def _set_autostart(self) -> None:
        r = self.api.command("/api/autostart", {"on": bool(self.autostart.get())})
        if not r.get("ok"):
            self._say(f"Could not change starting at login: {r.get('error')}",
                      self.C["bad"])
        self._poll_now()

    def _fix_firewall(self) -> None:
        """Hand over to Windows' own elevation prompt, off the Tk thread - it
        waits for a person to answer, and the window must not freeze meanwhile."""
        self.fw_btn.set_enabled(False)
        self._say("Windows will ask for permission - answer Yes to allow "
                  "Nishro Link on private networks.", self.C["warn"])
        done = []
        threading.Thread(target=lambda: done.append(
            self.api.command("/api/firewall", {}) or {}), daemon=True).start()

        def wait():
            if not self._alive:
                return
            if not done:
                self.root.after(200, wait)
                return
            r = done[0]
            try:
                self.fw_btn.set_enabled(True)
            except tk.TclError:
                return
            if r.get("ok"):
                self._say("Allowed. Other devices can find and reach this one now.",
                          self.C["ok"])
            elif r.get("declined"):
                self._say("Nothing was changed - the permission prompt was declined.",
                          self.C["dim"])
            else:
                self._say("Windows still reports a block: "
                          + "; ".join(r.get("blocked") or []), self.C["bad"])
            self._poll_now()
        wait()

    def _fix_setup(self) -> None:
        """The desktop's password prompt, off the Tk thread - it waits for a
        person, and the window must not freeze meanwhile."""
        self.setup_btn.set_enabled(False)
        self._say("Your computer will ask for your password.", self.C["warn"])
        done = []
        threading.Thread(target=lambda: done.append(
            self.api.command("/api/setup", {}) or {}), daemon=True).start()

        def wait():
            if not self._alive:
                return
            if not done:
                self.root.after(200, wait)
                return
            r = done[0]
            try:
                self.setup_btn.set_enabled(True)
            except tk.TclError:
                return
            if r.get("ok"):
                self._say("Done. Log out and back in once, then open Nishro Link "
                          "again.", self.C["ok"])
            elif r.get("declined"):
                self._say("Nothing was changed - the password prompt was closed.",
                          self.C["dim"])
            else:
                self._say(f"Setup did not finish: {r.get('error', 'unknown error')}",
                          self.C["bad"])
            self._poll_now()
        wait()

    def _release(self) -> None:
        self.api.command("/api/release", {})
        self._say("Released - this machine's mouse and keyboard are yours again.",
                  self.C["ok"])
        self._poll_now()

    def _add_device(self) -> None:
        ui_pair.AddDevice(self.root, self.api, self.C,
                          on_done=lambda r: self._poll_now())

    def _forget(self) -> None:
        if not messagebox.askokcancel(
                "Leave the group",
                "Stop sharing with the other devices and forget how to reach "
                "them? You can pair again at any time."):
            return
        r = self.api.command("/api/forget", {}) or {}
        self._say(r.get("error") or "Done - nothing is paired now.",
                  self.C["bad"] if r.get("error") else self.C["dim"])
        self._poll_now()

    def _forget_device(self, name) -> None:
        r = self.api.command("/api/device/forget", {"name": name}) or {}
        self._say(r.get("error") or f"{name} forgotten.",
                  self.C["bad"] if r.get("error") else self.C["dim"])
        self._dev_sig = None
        self._poll_now()

    def _arranged(self, boxes) -> None:
        self._say("Arrangement changed - press Apply to keep it, or Revert.",
                  self.C["warn"])
        self._arr_text()

    def _arr_text(self) -> None:
        """What the arrangement means, in words: the selected machine, and
        anything wrong - so nobody has to work it out from the picture."""
        self.arr_info.configure(text=self.arranger.describe())
        problems = self.arranger.problems()
        self.arr_problems.configure(text="\n".join("⚠  " + p for p in problems))

    def _revert_arrangement(self) -> None:
        self.arranger.dirty = False
        self._say("Back to the arrangement in use.", self.C["dim"])
        self._poll_now()

    def _apply_arrangement(self) -> None:
        r = self.api.command("/api/placement", {"boxes": self.arranger.boxes}) or {}
        if r.get("error"):
            return self._say(r["error"], self.C["bad"])
        self.arranger.dirty = False          # follow the program again
        self._say({
            "both": "Arrangement applied on every connected device.",
            "here": "Arrangement applied. The others get it as soon as they connect.",
            "sent": "Arrangement sent - the hub applies it on every device.",
        }.get(r.get("applied"), "Arrangement applied."), self.C["ok"])
        self._poll_now()

    def _save(self) -> None:
        body = {"claim": self.claim.get(),
                "node": self.node_name.get(), "port": self.port.get(),
                "may_drive": self.may_drive.get(),
                "may_be_driven": self.may_be_driven.get()}
        if self.password.get():
            body["pin"] = self.password.get()
        r = self.api.command("/api/config", body) or {}
        if r.get("error"):
            return self._say(r["error"], self.C["bad"])
        self.touched.clear()
        self.password.delete(0, "end")
        pending = r.get("needs_reconnect") or []
        if pending:
            self._say("Saved. Still to take effect: " + ", ".join(pending),
                      self.C["warn"])
        else:
            self._say("Saved and applied.", self.C["ok"])
        self._poll_now()

    def _open_log(self) -> None:
        folder = os.path.dirname(_log_path())
        try:
            if sys.platform == "win32":
                os.startfile(folder)               # noqa: S606 - a folder we own
            else:
                import subprocess
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            self._say(f"Could not open {folder}: {e}", self.C["bad"])

    def hide(self) -> None:
        """Keep linking, put the window away. Launching it again brings it back.

        Explicit rather than hijacking the X button: someone who closes a window
        expects the program to stop, and a thing that silently keeps capturing
        your keyboard after you closed it would be worse than a button that says
        what it does."""
        self.root.withdraw()

    def show(self) -> None:
        """Called when a second launch signals us. Safe from any thread."""
        self.root.after(0, self._raise)

    def _raise(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def _quit(self) -> None:
        if not messagebox.askokcancel(
                "Quit Nishro Link",
                "Stop sharing and quit?\n\n"
                "Every machine goes back to its own mouse and keyboard."):
            return
        self.close()

    def close(self) -> None:
        """Stop polling, then tear the window down - in that order."""
        self._alive = False
        for attr in ("_poll_id", "_msg_id"):
            after = getattr(self, attr)
            if after is not None:
                try:
                    self.root.after_cancel(after)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        if self.on_quit:
            self.on_quit()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _say(self, text, colour=None) -> None:
        self.msg.configure(text=text, fg=colour or self.C["dim"])
        if self._msg_id is not None:
            try:
                self.root.after_cancel(self._msg_id)
            except tk.TclError:
                pass
        self._msg_id = self.root.after(8000, self._clear_msg)

    def _clear_msg(self) -> None:
        self._msg_id = None
        if self._alive:
            self.msg.configure(text="")

    def _poll_now(self) -> None:
        try:
            self._render(self.api.status())
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


# ------------------------------------------------------------------ helpers
def _addresses() -> list:
    from .control_api import my_addresses
    return my_addresses()


def _log_path() -> str:
    from .runtime import log_path
    return str(log_path())


def _tone(line: str) -> str:
    low = line.lower()
    if "disconnected" in low:             # contains "connected" - check it first
        return "warn"
    if any(w in low for w in ("rejected", "error", "failed", "lost", "blocking",
                              "refused", "cannot")):
        return "bad"
    if any(w in low for w in ("warning", "retrying", "timed out", "disabled")):
        return "warn"
    if any(w in low for w in ("connected", "verified", "found", "allowed",
                              "enabled")):
        return "ok"
    return ""


def _autostart_note(a: dict) -> str:
    if not a.get("on"):
        return "Off. Nishro Link starts only when you open it."
    if not a.get("current"):
        return ("On, but set to start a different copy of Nishro Link - one that "
                "has since moved or been replaced. Switch this off and on again "
                "to start this one.")
    return ("It starts in the background with this window hidden. Open Nishro "
            "Link again to bring the window back.")


def _device_state(d) -> str:
    if d["me"]:
        return "this device"
    if d["online"]:
        rtt = d.get("rtt_ms")
        where = f"  ·  {d['addr']}" if d.get("addr") else ""
        return f"● online{f'  ·  {rtt:.1f} ms' if rtt else ''}{where}"
    return f"○ offline  ·  last seen {ago(d.get('last_seen'))}"


def _peer(s: dict) -> str:
    """The other device, by name - by address only if that is all there is."""
    return s.get("peer") or s.get("peer_addr") or "the other device"


def _trust(s: dict):
    """What to say about how far the connections can be relied on - blunt
    about the half that is not done: devices verify each other, but the stream
    itself is not encrypted yet."""
    live = [d["name"] for d in s.get("devices") or [] if d["online"] and not d["me"]]
    if not s["connected"] or not live:
        return "Not connected.", "dim"
    if not s["pin_set"]:
        return ("Connected, but NO PASSWORD IS SET - anything on this network that "
                "speaks the protocol can connect and type here.", "bad")
    return (f"Verified both ways with {', '.join(live)}. The password was never "
            f"sent - each side answered the other's challenge. Keystrokes are not "
            f"encrypted yet, so use this on a network you trust.", "ok")


def _explain(s: dict) -> str:
    """Plain English for the state. "suppress(mouse=1,kbd=0)" is correct and
    unreadable, and this window exists so nobody has to read it."""
    if not s["enabled"]:
        return "Linking is off. Nothing is captured and nothing is forwarded."
    if not s["connected"]:
        if s["hub"]:
            return ("Waiting for other devices to connect. If one reports a "
                    "timeout, a firewall is blocking this device.")
        return (f"Looking for {_peer(s)} on the network. Retries back off on "
                f"their own; nothing needs doing.")
    m, k = s["suppress"]["mouse"], s["suppress"]["keyboard"]
    if m and k:
        return ("Another device is driving and the pointer is on another screen, "
                "so this mouse and keyboard are forwarded there. Move this mouse "
                "to take control back.")
    if m:
        return ("Another device is driving, but the pointer is on this screen - "
                "typing here stays here, and this mouse only takes control back.")
    return ("This device is driving. Push the pointer off an edge where a bright "
            "line joins another screen to cross.")


def available() -> bool:
    """Is there a display and a working Tk? On a headless box there is not, and
    the answer must be False rather than an exception at startup."""
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False
