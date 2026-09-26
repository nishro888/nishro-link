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
import time
import tkinter as tk
from tkinter import messagebox

from . import autostart, ui_arrange, ui_device, ui_pair, ui_theme
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
        self._seen_event = None       # notices up to here have been shown
        self._toasts = []
        self._undo = []               # arrangements to go back to, newest last
        self._in_force = None         # the arrangement the program is using
        self._pending = None          # (boxes, when): applied, not yet echoed
        self._apply_id = None
        self._pw_at = 0.0
        self._nearby = None           # the last search's devices, or None
        self._nearby_busy = False
        self._nearby_at = 0.0
        self._nearby_sig = None
        self._renaming = False
        # How to look for devices nearby. Tests swap it for a canned answer.
        self._nearby_search = lambda: self.api.command("/api/discover", {})

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
        self.root.bind("<Control-z>", lambda _e: self.page == "arrange" and self._undo_arrangement())
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
        self.chip.pack(side="left", padx=(8, 14))
        # Sharing is a SWITCH. It was a button labelled with the opposite of the
        # state ("Stop linking" while on), which reads the state backwards; the
        # switch shows the state and its label says it in words.
        sw = tk.Frame(head, bg=C["card"], highlightthickness=1,
                      highlightbackground=C["line"], cursor="hand2")
        sw.pack(side="left")
        inner = tk.Frame(sw, bg=C["card"], padx=12, pady=8)
        inner.pack()
        self.sharing = tk.BooleanVar(value=True)
        self.sharing_switch = Toggle(inner, kit, self.sharing, command=self._toggle)
        self.sharing_switch.pack(side="left")
        self.sharing_label = tk.Label(inner, text="Sharing on", font=F["h3"],
                                      bg=C["card"], fg=C["ink"], cursor="hand2",
                                      width=11, anchor="w")
        self.sharing_label.pack(side="left", padx=(10, 0))
        for w in (sw, inner, self.sharing_label):
            w.bind("<Button-1>", lambda _e: self.sharing_switch._flip())

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
        # Notices float over the bottom right of every page.
        self.toasts = tk.Frame(self.main, bg=C["panel"])

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

        # First thing on the first run: what to do. Gone once in a group.
        self.start_card = tk.Frame(box, bg=C["card"], highlightthickness=1,
                                   highlightbackground=C["accent"])
        inner = tk.Frame(self.start_card, bg=C["card"], padx=18, pady=16)
        inner.pack(fill="x")
        words = tk.Frame(inner, bg=C["card"])
        words.pack(side="left", fill="x", expand=True)
        tk.Label(words, text="Connect another computer", font=F["h2"],
                 bg=C["card"], fg=C["ink"], anchor="w").pack(anchor="w")
        hint = label(words, kit, "Open Nishro Link on it, then add it from here - "
                                 "or add this one from there. Either way it takes "
                                 "one password.", "body", "dim")
        hint.configure(bg=C["card"], justify="left")
        hint.pack(anchor="w", fill="x", pady=(4, 0))
        words.bind("<Configure>", lambda e: hint.configure(
            wraplength=max(160, e.width - 8)))
        Button(inner, kit, "+  Add a device", self._add_device,
               kind="primary").pack(side="right", padx=(14, 0))
        self.overview_box = box

        hero = Card(box, kit, "Control")
        hero.pack(fill="x")
        self.hero_card = hero
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
        C, F, kit = self.C, self.F, self.kit
        page, box = self._page()

        # ---- this device: all another device needs to add it
        me = Card(box, kit, "This device")
        me.pack(fill="x")
        top = tk.Frame(me.body, bg=C["card"])
        top.pack(fill="x")
        self.me_pic = Monitors(top, kit)
        self.me_pic.configure(bg=C["card"])
        self.me_pic.pack(side="left", padx=(0, 16))
        words = tk.Frame(top, bg=C["card"])
        words.pack(side="left", fill="x", expand=True)
        row = tk.Frame(words, bg=C["card"])
        row.pack(fill="x")
        self.me_name_row = row
        self.me_name = tk.Label(row, text="", font=F["h1"], bg=C["card"],
                                fg=C["ink"], anchor="w")
        self.me_name.pack(side="left")
        self.me_pill = Pill(row, kit, "")
        self.me_pill.pack(side="left", padx=10)
        self.btn_me_details = Button(row, kit, "Details", self._me_details,
                                     kind="ghost", small=True)
        self.btn_me_details.pack(side="right")
        self.btn_rename_me = Button(row, kit, "Rename", self._rename_me,
                                    kind="ghost", small=True)
        self.btn_rename_me.pack(side="right")
        self.me_role = label(words, kit, "", "body", "dim")
        self.me_role.configure(bg=C["card"], justify="left")
        self.me_role.pack(anchor="w", fill="x", pady=(4, 0))
        # Beside the picture, so it wraps to what is left of the card - the
        # page-wide wrap ran it off the right edge.
        words.bind("<Configure>", lambda e: self.me_role.configure(
            wraplength=max(160, e.width - 8)))

        cred = tk.Frame(me.body, bg=C["surface"], highlightthickness=1,
                        highlightbackground=C["line"])
        cred.pack(fill="x", pady=(14, 0))
        g = tk.Frame(cred, bg=C["surface"], padx=16, pady=12)
        g.pack(fill="x")
        for col, text in ((0, "NAME"), (1, "PASSWORD")):
            tk.Label(g, text=text, font=F["caps"], bg=C["surface"], fg=C["faint"]
                     ).grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else 32, 0))
        self.me_cred_name = tk.Label(g, text="", font=F["h2"], bg=C["surface"],
                                     fg=C["ink"])
        self.me_cred_name.grid(row=1, column=0, sticky="w")
        # Words read better in a plain face than in a monospaced one.
        self.me_password = tk.Label(g, text="", font=F["h1"],
                                    bg=C["surface"], fg=C["accent"])
        self.me_password.grid(row=1, column=1, sticky="w", padx=(32, 0))
        tools = tk.Frame(g, bg=C["surface"])
        tools.grid(row=0, column=2, rowspan=2, sticky="e")
        Button(tools, kit, "Copy", self._copy_password, small=True).pack(side="left")
        self.btn_new_pw = Button(tools, kit, "New password", self._new_password,
                                 kind="ghost", small=True)
        self.btn_new_pw.pack(side="left", padx=(6, 0))
        g.columnconfigure(2, weight=1)
        self.me_hint = self._wrap(label(me.body, kit, "", "small", "faint"))
        self.me_hint.configure(bg=C["card"])
        self.me_hint.pack(anchor="w", fill="x", pady=(8, 0))

        # What is wrong with this device's connection, and the way out.
        self.me_problem = tk.Frame(me.body, bg=C["bad_bg"], highlightthickness=1,
                                   highlightbackground=C["bad"])
        self.me_problem_text = self._wrap(tk.Label(
            self.me_problem, text="", font=F["small"], bg=C["bad_bg"], fg=C["ink"],
            justify="left", anchor="w"))
        self.me_problem_text.pack(side="left", fill="x", expand=True, padx=12,
                                  pady=9)
        self.me_problem_btn = Button(self.me_problem, kit, "Enter the password",
                                     self._repair, kind="primary", small=True)
        self.me_problem_btn.pack(side="right", padx=12)

        self.me_actions = tk.Frame(me.body, bg=C["card"])
        self.me_actions.pack(fill="x", pady=(12, 0))
        self.btn_leave = Button(self.me_actions, kit, "Leave this group",
                                self._leave, kind="danger", small=True)
        self.my_addr = self._wrap(label(self.me_actions, kit, "", "small", "faint"))
        self.my_addr.configure(bg=C["card"])
        self.my_addr.pack(side="right")

        # ---- the others
        bar = tk.Frame(box, bg=C["panel"])
        bar.pack(fill="x", pady=(22, 10))
        self._devices_box = box
        self.dev_head = tk.Label(bar, text="OTHER DEVICES IN THIS GROUP",
                                 font=F["caps"], bg=C["panel"], fg=C["dim"])
        self.dev_head.pack(side="left", anchor="s")
        self.btn_add = Button(bar, kit, "+  Add a device", self._add_device,
                              kind="primary")
        self.btn_add.pack(side="right")
        self.dev_list = tk.Frame(box, bg=C["panel"])
        self.dev_list.pack(fill="x")

        # ---- nearby: on the network, not in this group - one click to add
        nb = tk.Frame(box, bg=C["panel"])
        nb.pack(fill="x", pady=(22, 10))
        tk.Label(nb, text="NEARBY - NOT IN THIS GROUP", font=F["caps"],
                 bg=C["panel"], fg=C["dim"]).pack(side="left", anchor="s")
        self.btn_nearby = Button(nb, kit, "Search again",
                                 lambda: self._search_nearby(force=True),
                                 kind="ghost", small=True)
        self.btn_nearby.pack(side="right")
        self.nearby_list = tk.Frame(box, bg=C["card"], highlightthickness=1,
                                    highlightbackground=C["line"])
        self.nearby_list.pack(fill="x")
        tk.Label(self.nearby_list, text="  Looking for devices nearby…",
                 font=F["body"], bg=C["card"], fg=C["dim"]).pack(anchor="w",
                                                                 pady=10)
        return page

    def _device_cards(self, s) -> None:
        """Rebuilt only when WHO is there changes; round trips and 'last seen'
        are updated in place, so the page does not flicker every poll."""
        devs = [d for d in s.get("devices") or [] if not d["me"]]
        sig = tuple((d["name"], d["online"], d["hub"], d.get("may_drive"),
                     d.get("may_be_driven"), d.get("rename_to"),
                     tuple(map(tuple, d["displays"]))) for d in devs) + \
            (s.get("role"), s.get("group"), getattr(self, "_dev_cols", 2))
        if sig != self._dev_sig:
            self._dev_sig = sig
            for w in self.dev_list.winfo_children():
                w.destroy()
            self._dev_rows = {}
            if not devs:
                self._empty_group(s)
                return
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

    def _empty_group(self, s) -> None:
        """No other devices yet: say how to get one, both ways."""
        C, F, kit = self.C, self.F, self.kit
        for c in range(3):
            self.dev_list.columnconfigure(c, weight=0, uniform="")
        card = tk.Frame(self.dev_list, bg=C["card"], highlightthickness=1,
                        highlightbackground=C["line"])
        card.grid(row=0, column=0, sticky="ew")
        self.dev_list.columnconfigure(0, weight=1)
        inner = tk.Frame(card, bg=C["card"], padx=18, pady=16)
        inner.pack(fill="x")
        tk.Label(inner, text="No other devices yet", font=F["h2"], bg=C["card"],
                 fg=C["ink"]).pack(anchor="w")
        steps = (
            "Open Nishro Link on the other computer.",
            "Here, choose Add a device, pick it, and type the password it shows -",
            f"or there, choose Add a device, pick {s['node']}, and type the "
            f"password shown above.")
        for i, text in enumerate(steps, start=1):
            row = tk.Frame(inner, bg=C["card"])
            row.pack(fill="x", pady=(8 if i == 1 else 3, 0))
            tk.Label(row, text=str(i) if i < 3 else "", font=F["h3"], bg=C["card"],
                     fg=C["accent"], width=2, anchor="w").pack(side="left")
            self._wrap(tk.Label(row, text=text, font=F["body"], bg=C["card"],
                                fg=C["dim"], justify="left", anchor="w")
                       ).pack(side="left", fill="x", expand=True)

    def _device_card(self, d, s):
        C, F, kit = self.C, self.F, self.kit
        card = tk.Frame(self.dev_list, bg=C["card"], highlightthickness=1,
                        highlightbackground=C["line"])
        inner = tk.Frame(card, bg=C["card"])
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        pic = Monitors(inner, kit)
        pic.configure(bg=C["card"])
        pic.pack(side="left", padx=(0, 14))
        colour = C["theirs"] if d["online"] else C["offline"]
        fill = C["theirs_fill"] if d["online"] else C["offline_fill"]
        pic.draw(_parts(d), colour, fill)
        text = tk.Frame(inner, bg=C["card"])
        text.pack(side="left", fill="both", expand=True)
        top = tk.Frame(text, bg=C["card"])
        top.pack(fill="x")
        tk.Label(top, text=d["name"], font=F["h2"], bg=C["card"], fg=C["ink"],
                 anchor="w").pack(side="left")
        if d["hub"]:
            Pill(top, kit, "HUB", "accent2").pack(side="left", padx=(8, 0))
        n = len(d["displays"])
        sizes = ", ".join(f"{w}×{h}" for w, h in d["displays"])
        tk.Label(text, text=f"{n} display{'s' if n != 1 else ''}  ·  {sizes}",
                 font=F["small"], bg=C["card"], fg=C["dim"],
                 anchor="w").pack(fill="x", pady=(3, 0))
        state = tk.Label(text, text=_device_state(d), font=F["small"], bg=C["card"],
                         fg=C["ok"] if d["online"] else C["faint"], anchor="w")
        state.pack(fill="x")
        limits = _limits(d)
        if limits:
            tk.Label(text, text=limits, font=F["small"], bg=C["card"], fg=C["warn"],
                     anchor="w").pack(fill="x")
        if d.get("rename_to"):
            tk.Label(text, text="takes the new name when it is next on",
                     font=F["small"], bg=C["card"], fg=C["faint"],
                     anchor="w").pack(fill="x")
        self._dev_rows[d["name"]] = {"state": state}
        acts = tk.Frame(text, bg=C["card"])
        acts.pack(anchor="w", pady=(6, 0))
        Button(acts, kit, "Details", lambda n=d["name"]: self._details(n),
               kind="secondary", small=True).pack(side="left")
        if s.get("role") == "hub":
            Button(acts, kit, "Rename", lambda n=d["name"]: self._details(n, rename=True),
                   kind="ghost", small=True).pack(side="left", padx=(6, 0))
            Button(acts, kit, "Remove", lambda n=d["name"]: self._remove_device(n),
                   kind="ghost", small=True).pack(side="left")
        # The whole card opens the details, like any list of things.
        for w in (card, inner, text, top, pic):
            w.bind("<Button-1>", lambda _e, n=d["name"]: self._details(n))
            w.configure(cursor="hand2")
        return card

    # ========================================================== Arrangement
    def _arrange(self):
        C, F, kit = self.C, self.F, self.kit
        page = tk.Frame(self.stack, bg=C["panel"])
        box = tk.Frame(page, bg=C["panel"])
        box.pack(fill="both", expand=True, padx=26, pady=10)
        bar = tk.Frame(box, bg=C["panel"])
        bar.pack(fill="x", pady=(0, 10))
        Button(bar, kit, "In a row", lambda: self.arranger.tidy(),
               small=True).pack(side="left")
        Button(bar, kit, "In a column", lambda: self.arranger.stack(),
               small=True).pack(side="left", padx=6)
        # No Apply: a change is in force - on every device - the moment the box
        # is let go. Reported: "changing arrangement in one device instantly
        # isn't synced with peers". Undo makes that safe to do.
        self.btn_undo = Button(bar, kit, "Undo", self._undo_arrangement, small=True)
        self.btn_undo.pack(side="right")
        self.btn_undo.set_enabled(False)
        self.arr_saved = tk.Label(bar, text="", font=F["small"], bg=C["panel"],
                                  fg=C["dim"])
        self.arr_saved.pack(side="right", padx=12)
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
                       "lines up with them. Changes apply as you make them, on "
                       "every device in the group (Ctrl+Z undoes). The pointer "
                       "crosses only along the bright lines, and a machine that "
                       "is not connected is a wall. Arrow keys nudge the selected "
                       "one (Shift for bigger steps).",
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
        self.subtitle.configure(text=f"{s['node']}  ·  {_role_words(s)}  ·  "
                                     f"{online} of {len(devs)} online")
        text, tone = _chip(s)
        self.chip.set(text, tone)
        self.dot.set(C[tone] if tone in ("ok", "warn", "bad") else C["dim"])
        self.side_status.configure(
            text=("sharing off" if not s["enabled"] else
                  f"{online} of {len(devs)} online"))
        on = bool(s["enabled"])
        if self.sharing.get() != on:
            self.sharing.set(on)
        self.sharing_label.configure(text="Sharing on" if on else "Sharing off",
                                     fg=C["ink"] if on else C["dim"])
        self._notices(s)

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
        placement = s.get("placement") or []
        if self._pending is not None:
            # Applied from here: keep what was dragged on screen until the
            # program echoes it back, or it would flick to the old one between.
            boxes, when = self._pending
            if _same_boxes(placement, boxes) or time.monotonic() - when > 3:
                self._pending = None
                self.arranger.dirty = False
        if self._pending is None:
            self._in_force = placement
        for arr in (self.preview, self.arranger):
            arr.node = s["node"]
            arr.set_boxes(placement)
            arr.set_online(live)
        self._arr_text()

        alone = s.get("role") == "alone"
        shown = self.start_card.winfo_manager() == "pack"
        if alone and not shown:
            self.start_card.pack(fill="x", pady=(0, 12), before=self.hero_card)
        elif not alone and shown:
            self.start_card.pack_forget()

        # ---- devices
        self._me_card(s, devs)
        self._device_cards(s)
        if self.page == "devices":
            self._search_nearby()

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
        on = bool(self.sharing.get())
        self.api.command("/api/enable" if on else "/api/disable", {})
        self._poll_now()

    def _confirm(self, title, text) -> bool:
        """A yes/no question. One place, so the tests can answer it."""
        return messagebox.askokcancel(title, text, parent=self.root)

    # ------------------------------------------------------------ this device
    def _me_card(self, s, devs) -> None:
        C = self.C
        role, group = s.get("role"), s.get("group")
        if not self._renaming:
            self.me_name.configure(text=s["node"])
        self.me_cred_name.configure(text=s["node"])
        me = next((d for d in devs if d["me"]), None)
        if me:
            sig = tuple(map(tuple, _parts(me)))
            if getattr(self, "_me_sig", None) != sig:
                self._me_sig = sig
                self.me_pic.draw(_parts(me), C["mine"], C["mine_fill"])
        others = [d for d in devs if not d["me"]]
        if role == "alone":
            self.me_pill.set("ON ITS OWN", "dim")
            self.me_role.configure(text=(
                "Not connected to any other device yet. Other devices can add "
                "this one with the name and password below."))
        elif role == "hub":
            self.me_pill.set("HUB", "accent2")
            self.me_role.configure(text=(
                f"The hub of a group of {len(others) + 1}: the other devices "
                f"connect through this one. Anyone with the name and password "
                f"below can add a device to the group."))
        else:
            self.me_pill.set(f"IN {str(group).upper()}'S GROUP", "accent")
            self.me_role.configure(text=(
                f"In {group}'s group - "
                + ("connected." if s["connected"] else "not connected right now.")
                + " The password below is the group's: the same on every device "
                  "in it."))
        if self.page == "devices" or not self.me_password.cget("text") or \
                time.monotonic() - self._pw_at > 5:
            self._pw_at = time.monotonic()
            pw = (self.api.command("/api/password", {}) or {}).get("pin", "")
            if self.me_password.cget("text") != pw:
                self.me_password.configure(text=pw)
        self.btn_new_pw.set_enabled(role != "member")
        self.me_hint.configure(text=(
            "Capitals, spaces and dashes don't matter when it is typed. "
            + (f"Only {group} can change it." if role == "member" else
               "" if role == "alone" else
               "A new password goes straight to the devices connected now; any "
               "that are switched off will ask for it.")))
        if role == "member":
            if not self.btn_leave.winfo_manager():
                self.btn_leave.pack(side="left")
        elif self.btn_leave.winfo_manager():
            self.btn_leave.pack_forget()
        addrs = s.get("addresses") or _addresses()
        self.my_addr.configure(text=("This device is at " + ", ".join(addrs))
                               if addrs else "This device has no network address.")
        self.dev_head.configure(text="OTHER DEVICES IN THIS GROUP")

        problem, fix = _connection_problem(s)
        shown = self.me_problem.winfo_manager() == "pack"
        if problem and not shown:
            self.me_problem.pack(fill="x", pady=(12, 0), before=self.me_actions)
        elif not problem and shown:
            self.me_problem.pack_forget()
        if problem:
            self.me_problem_text.configure(text=problem)
            if fix and not self.me_problem_btn.winfo_manager():
                self.me_problem_btn.pack(side="right", padx=12)
            elif not fix and self.me_problem_btn.winfo_manager():
                self.me_problem_btn.pack_forget()

    # ---------------------------------------------------------- editing
    def _details(self, name, rename=False) -> None:
        d = ui_device.DeviceDetails(self.root, self.api, self.C, name,
                                    confirm=self._confirm,
                                    on_change=self._devices_changed)
        if rename:
            d._start_rename()
        return d

    def _me_details(self) -> None:
        self._details((self._last or {}).get("node") or self.me_name.cget("text"))

    def _devices_changed(self) -> None:
        self._dev_sig = None
        self._poll_now()

    def _rename_me(self) -> None:
        """This device's name, edited where it is shown."""
        C, kit = self.C, self.kit
        if self._renaming:
            return
        self._renaming = True
        self.me_name.pack_forget()
        self.me_pill.pack_forget()
        self.btn_rename_me.pack_forget()
        self.btn_me_details.pack_forget()
        self.f_me_name = field(self.me_name_row, kit, width=20)
        self.f_me_name.configure(font=self.F["h2"])
        self.f_me_name.insert(0, self.me_name.cget("text"))
        self.f_me_name.select_range(0, "end")
        self.f_me_name.pack(side="left", ipady=3)
        self.f_me_name.focus_set()
        self._me_save = Button(self.me_name_row, kit, "Save", self._rename_me_save,
                               kind="primary", small=True)
        self._me_save.pack(side="left", padx=(8, 0))
        self._me_cancel = Button(self.me_name_row, kit, "Cancel",
                                 self._rename_me_done, kind="ghost", small=True)
        self._me_cancel.pack(side="left", padx=(4, 0))
        self.f_me_name.bind("<Return>", lambda _e: self._rename_me_save())
        self.f_me_name.bind("<Escape>", lambda _e: self._rename_me_done())

    def _rename_me_save(self) -> None:
        r = self.api.command("/api/rename", {"new": self.f_me_name.get()}) or {}
        if r.get("error"):
            self._toast(r["error"][:1].upper() + r["error"][1:], "bad")
            self.f_me_name.focus_set()
            return
        self._rename_me_done()
        self._toast(f"This device is called {self.me_name.cget('text')} now.", "ok")

    def _rename_me_done(self) -> None:
        for w in (self.f_me_name, self._me_save, self._me_cancel):
            w.destroy()
        self._renaming = False
        self.me_name.pack(side="left")
        self.me_pill.pack(side="left", padx=10)
        self.btn_me_details.pack(side="right")
        self.btn_rename_me.pack(side="right")
        self._poll_now()

    # ----------------------------------------------------------- nearby
    def _search_nearby(self, force=False) -> None:
        """Look for devices on the network every few seconds while the Devices
        page is open - off the Tk thread, a search takes about a second."""
        if self._nearby_busy or (not force and
                                 time.monotonic() - self._nearby_at < 8):
            return
        self._nearby_busy = True
        self._nearby_at = time.monotonic()
        self.btn_nearby.set_enabled(False)
        box = []
        threading.Thread(target=lambda: box.append(self._safe_search()),
                         daemon=True).start()

        def wait():
            if not self._alive:
                return
            if not box:
                self.root.after(150, wait)
                return
            self._nearby_busy = False
            self._nearby_at = time.monotonic()
            try:
                self.btn_nearby.set_enabled(True)
            except tk.TclError:
                return
            self._nearby = box[0]
            self._show_nearby()
        wait()

    def _safe_search(self):
        try:
            return list((self._nearby_search() or {}).get("devices") or [])
        except Exception:
            return []

    def _show_nearby(self) -> None:
        C, F, kit = self.C, self.F, self.kit
        s = self._last or {}
        me = {"role": s.get("role"), "group": s.get("group"),
              "members": s.get("members") or [], "connected": s.get("connected")}
        rows = []
        for f in self._nearby or []:
            what, tone, action, mode, target = ui_pair.row_action(f, me)
            if what == "In this group":
                continue
            rows.append((f, what, tone, action, mode, target))
        sig = repr(rows)
        if sig == self._nearby_sig:
            return
        self._nearby_sig = sig
        for w in self.nearby_list.winfo_children():
            w.destroy()
        if not rows:
            tk.Label(self.nearby_list,
                     text="  No other devices nearby. Open Nishro Link on another "
                          "computer and it appears here.",
                     font=F["body"], bg=C["card"], fg=C["dim"]).pack(anchor="w",
                                                                     pady=10)
            return
        for i, (f, what, tone, action, mode, target) in enumerate(rows):
            if i:
                tk.Frame(self.nearby_list, bg=C["line"], height=1).pack(fill="x")
            row = tk.Frame(self.nearby_list, bg=C["card"], padx=12, pady=9)
            row.pack(fill="x")
            dot = tk.Canvas(row, width=10, height=10, highlightthickness=0,
                            bg=C["card"])
            dot.create_oval(1, 1, 9, 9, outline="",
                            fill=C["ok"] if f.get("waiting") else C["faint"])
            dot.pack(side="left", padx=(0, 10))
            tk.Label(row, text=f.get("name"), font=F["h3"], bg=C["card"],
                     fg=C["ink"]).pack(side="left")
            tk.Label(row, text="  " + what, font=F["small"], bg=C["card"],
                     fg=C.get(tone, C["dim"])).pack(side="left")
            if action:
                Button(row, kit, action,
                       lambda m=mode, t=target: self._add_device(start=(m, t)),
                       kind="primary" if mode == "invite" else "secondary",
                       small=True).pack(side="right")
        if me["role"] == "hub" and any(r[3] is None and r[1] != "Sharing is off"
                                       for r in rows):
            tk.Label(self.nearby_list,
                     text="  This device has a group of its own, so it can add "
                          "devices that are on their own - not join another group.",
                     font=F["small"], bg=C["card"], fg=C["faint"]
                     ).pack(anchor="w", pady=(0, 8))

    def _copy_password(self) -> None:
        pw = self.me_password.cget("text")
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(pw)
            self._toast("Password copied.", "ok")
        except tk.TclError:
            pass

    def _new_password(self) -> None:
        if not self._confirm(
                "New password",
                "Make a new password for this group?\n\nThe devices connected now "
                "get it automatically. Any that are switched off will ask for it "
                "when they come back."):
            return
        r = self.api.command("/api/password", {"new": True}) or {}
        if r.get("error"):
            return self._toast(r["error"], "bad")
        self.me_password.configure(text=r.get("pin", ""))
        self._toast("New password made and given to the connected devices.", "ok")
        self._poll_now()

    def _repair(self) -> None:
        """This device's password stopped working: type the group's new one."""
        s = self._last or {}
        ui_pair.AddDevice(self.root, self.api, self.C,
                          on_done=lambda r: self._poll_now(),
                          on_arrange=lambda: self.show_page("arrange"),
                          start=("join", s.get("group") or s.get("peer")))

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

    def _add_device(self, start=None) -> None:
        ui_pair.AddDevice(self.root, self.api, self.C,
                          on_done=lambda r: self._devices_changed(),
                          on_arrange=lambda: self.show_page("arrange"),
                          start=start)

    def _leave(self) -> None:
        group = (self._last or {}).get("group") or "the"
        if not self._confirm(
                "Leave the group",
                f"Leave {group}'s group?\n\nThis device stops sharing with the "
                f"others and gets a password of its own. To come back, add it "
                f"again."):
            return
        r = self.api.command("/api/leave", {}) or {}
        if r.get("error"):
            self._toast(r["error"], "bad")
        self._dev_sig = None
        self._poll_now()

    def _remove_device(self, name) -> None:
        online = any(d["name"] == name and d["online"]
                     for d in (self._last or {}).get("devices") or [])
        if not self._confirm(
                "Remove device",
                f"Remove {name} from the group?\n\n"
                + (f"{name} is disconnected now and forgets this group."
                   if online else
                   f"{name} is switched off; it is told when it comes back.")
                + " To add it again, you will need the password it shows."):
            return
        r = self.api.command("/api/remove", {"name": name}) or {}
        self._toast(r.get("error") or f"{name} removed from the group.",
                    "bad" if r.get("error") else "dim")
        self._dev_sig = None
        self._poll_now()

    def _arranged(self, boxes) -> None:
        """A box was dropped or nudged: in force at once, on every device. A
        short wait first, so a run of arrow-key nudges is one change."""
        self._arr_text()
        if self._apply_id is not None:
            try:
                self.root.after_cancel(self._apply_id)
            except tk.TclError:
                pass
        self._apply_id = self.root.after(250, self._apply_arrangement)

    def _arr_text(self) -> None:
        """What the arrangement means, in words: the selected machine, and
        anything wrong - so nobody has to work it out from the picture."""
        self.arr_info.configure(text=self.arranger.describe())
        problems = self.arranger.problems()
        self.arr_problems.configure(text="\n".join("⚠  " + p for p in problems))

    def _apply_arrangement(self, boxes=None, undoing=False) -> None:
        self._apply_id = None
        boxes = boxes or self.arranger.boxes
        before = self._in_force
        r = self.api.command("/api/placement", {"boxes": boxes}) or {}
        C = self.C
        if r.get("error"):
            self.arr_saved.configure(text=r["error"][:1].upper() + r["error"][1:],
                                     fg=C["bad"])
            self.arranger.dirty = False       # back to what is in force
            self._pending = None
            self._poll_now()
            return
        if before and not undoing and not _same_boxes(before, boxes):
            self._undo.append(before)
            del self._undo[:-30]
        self._pending = (boxes, time.monotonic())
        self._in_force = boxes
        self.btn_undo.set_enabled(bool(self._undo))
        s = self._last or {}
        if s.get("role") == "member":
            text = f"Sent to {s.get('group')}, which applies it on every device."
        elif s.get("connected"):
            text = "Applied on every connected device."
        else:
            text = "Applied. The others get it when they connect."
        self.arr_saved.configure(text=("Undone. " if undoing else "") + text,
                                 fg=C["ok"])

    def _undo_arrangement(self) -> None:
        if not self._undo:
            return
        boxes = self._undo.pop()
        self.arranger.dirty = True
        self.arranger.desk = ui_arrange._desk.place(self.arranger.node,
                                                    [dict(b) for b in boxes])
        self.arranger.redraw()
        self._apply_arrangement(boxes, undoing=True)
        self.btn_undo.set_enabled(bool(self._undo))

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

    def _notices(self, s) -> None:
        """Show what has happened since the last look, once each."""
        evs = s.get("events") or []
        if self._seen_event is None:
            # Not what happened before the window opened: that is old news.
            self._seen_event = max((e["id"] for e in evs), default=0)
            return
        for e in evs:
            if e["id"] > self._seen_event:
                self._seen_event = e["id"]
                self._toast(e["text"], e.get("tone", "dim"))

    def _toast(self, text, tone="dim") -> None:
        C, F = self.C, self.F
        t = tk.Frame(self.toasts, bg=C["card_hi"], highlightthickness=1,
                     highlightbackground=C["line_hi"])
        tk.Frame(t, bg=C.get(tone, C["dim"]) if tone != "dim" else C["accent"],
                 width=4).pack(side="left", fill="y")
        tk.Label(t, text=text, font=F["small"], bg=C["card_hi"], fg=C["ink"],
                 wraplength=300, justify="left", anchor="w", padx=12,
                 pady=10).pack(side="left")
        x = tk.Label(t, text="×", font=F["body"], bg=C["card_hi"], fg=C["dim"],
                     cursor="hand2", padx=8)
        x.pack(side="right", anchor="n")
        x.bind("<Button-1>", lambda _e: self._drop_toast(t))
        t.pack(side="top", anchor="e", pady=(6, 0))
        self._toasts.append(t)
        while len(self._toasts) > 3:
            self._drop_toast(self._toasts[0])
        self.toasts.place(relx=1.0, rely=1.0, anchor="se", x=-18, y=-18)
        self.toasts.lift()
        self.root.after(7000, lambda: self._drop_toast(t))

    def _drop_toast(self, t) -> None:
        if t in self._toasts:
            self._toasts.remove(t)
        try:
            t.destroy()
        except tk.TclError:
            pass
        if not self._toasts:
            try:
                self.toasts.place_forget()
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


def _parts(d) -> list:
    parts = [tuple(r) for r in d.get("rects") or []]
    if not parts:                     # an older status without positions
        x = 0
        for w, h in d["displays"]:
            parts.append((x, 0, w, h))
            x += w
    return parts


def _same_boxes(a, b) -> bool:
    def key(boxes):
        return sorted((x["name"], x["x"], x["y"], x["w"], x["h"]) for x in boxes or [])
    return key(a) == key(b)


def _role_words(s: dict) -> str:
    role = s.get("role")
    if role == "alone":
        return "on its own"
    if role == "hub":
        return "the hub of this group"
    return f"in {s.get('group')}'s group"


def _chip(s: dict):
    """The one word in the header: (text, tone)."""
    if s.get("setup"):
        return "SETUP NEEDED", "warn"
    if not s["enabled"]:
        return "PAUSED", "bad"
    role = s.get("role")
    if role == "alone":
        return "READY", "accent"
    if role == "hub":
        return ("CONNECTED", "ok") if s["connected"] else ("WAITING", "warn")
    phase = (s.get("dial") or {}).get("phase")
    if s["connected"] or phase == "connected":
        return "CONNECTED", "ok"
    if phase == "failed":
        return "NOT CONNECTED", "bad"
    return "CONNECTING", "warn"


def _connection_problem(s: dict):
    """(what is wrong, whether a password fixes it) for a member that cannot
    get in - or (None, False)."""
    if s.get("role") != "member" or s["connected"] or not s["enabled"]:
        return None, False
    d = s.get("dial") or {}
    g = s.get("group") or "the hub"
    reason, phase = d.get("reason"), d.get("phase")
    if reason == "wrong_password":
        return (f"{g} did not accept this device's password - it has probably been "
                f"changed there. Enter the new one to reconnect.", True)
    if reason == "name_taken":
        return (f"{g}'s group already has a device called {s['node']}. Rename this "
                f"device in Settings.", False)
    if phase == "retrying" and (d.get("attempts") or 0) >= 3:
        if reason == "not_waiting":
            return (f"{g} is on the network but not accepting connections - sharing "
                    f"may be switched off there.", False)
        if reason == "unreachable":
            return (f"{g} was found but cannot be reached - its firewall may be "
                    f"blocking Nishro Link.", False)
        return (f"{g} is not answering. Is it switched on, with Nishro Link "
                f"open? This device keeps trying.", False)
    return None, False


def _limits(d) -> str:
    """What a device may not do, in words - or nothing."""
    out = []
    if d.get("may_drive") is False:
        out.append("cannot take control")
    if d.get("may_be_driven") is False:
        out.append("cannot be controlled")
    return "  ·  ".join(out)


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
    return s.get("group") or s.get("peer") or s.get("peer_addr") or "the other device"


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
        return ("Sharing is off. Every machine uses its own mouse and keyboard; "
                "nothing is captured or forwarded. Switch Sharing on to connect "
                "again.")
    if s.get("role") == "alone":
        return ("This device is on its own. Add another on the Devices page - or "
                "add this one from the other device, with the name and password "
                "shown there.")
    if not s["connected"]:
        if s["hub"]:
            return ("Waiting for the other devices in the group to connect. If one "
                    "says it cannot reach this device, a firewall here is "
                    "blocking it.")
        problem, _ = _connection_problem(s)
        return problem or f"Connecting to {_peer(s)}…"
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
