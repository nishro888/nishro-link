"""Help: the page at the bottom of the navigation - About, Get started,
Keyboard shortcuts, Support.

It was a menu of small windows, beside a navigation pane that did most of what
the menu bar did too - two ways to everything, which read as clutter. Now
there is one navigation, as in Windows' own Settings: pages at the top, and
Settings and Help at the bottom.

About says which Nishro Link this is and where it runs, and Copy details puts
the same on the clipboard for a bug report. The copy leaves out addresses and
the password: it is made to be pasted somewhere public.
"""
from __future__ import annotations

import os
import platform
import sys
import tkinter as tk

from . import __stage__, __version__, protocol
from .ui_kit import Button, Card, Kit, Pill

HOME = "https://github.com/nishro888/nishro-link"
DOCS = HOME + "#readme"
ISSUES = HOME + "/issues"
RELEASES = HOME + "/releases"
COPYRIGHT = "© 2026 nishro888"


def version() -> str:
    return f"{__version__} {__stage__}".strip()


def system_name() -> str:
    """'Windows 10 (build 19045)', 'Ubuntu 26.04 LTS · Wayland'."""
    if sys.platform == "win32":
        build = platform.version().split(".")[-1]
        name = "11" if build.isdigit() and int(build) >= 22000 else platform.release()
        return f"Windows {name} (build {build})"
    name = ""
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    name = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    name = name or f"{platform.system()} {platform.release()}"
    session = os.environ.get("XDG_SESSION_TYPE", "")
    return f"{name} · {session.capitalize()}" if session in ("wayland", "x11") \
        else name


def group_line(s: dict) -> str:
    """How many devices, and whether they all run this version."""
    devs = s.get("devices") or []
    if s.get("role") == "alone" or len(devs) < 2:
        return "On its own"
    others = [f"{d['name']} {d['version']}" for d in devs
              if not d.get("me") and d.get("version")
              and d["version"] != __version__]
    text = f"{len(devs)} devices"
    if others:
        return text + " · " + ", ".join(others)
    if all(d.get("version") for d in devs):
        return text + f" · all on {__version__}"
    return text


def details(s: dict) -> list:
    """(label, value) for About - and, with more, for the copy."""
    return [
        ("This device", s.get("node") or "—"),
        ("Device ID", s.get("device_id") or "—"),
        ("Runs as", "Background service" if s.get("service") else "App"),
        ("Group", group_line(s)),
        ("System", system_name()),
        ("Protocol", f"{protocol.VERSION} · encrypted"),
    ]


def copy_text(s: dict) -> str:
    rows = [f"Nishro Link {version()}"]
    rows += [f"{k}: {v}" for k, v in details(s)]
    rows.append("Devices: " + (", ".join(
        f"{d['name']} {d.get('version') or '?'}" for d in s.get("devices") or [])
        or "none"))
    rows.append(f"Python {platform.python_version()} · "
                f"Tk {tk.TkVersion} · {platform.machine() or '?'}")
    return "\n".join(rows)


STEPS = (
    ("Install on each computer", "Every computer that shares needs Nishro Link."),
    ("Add a device", "Enter the other computer's name and password."),
    ("Arrange the screens", "Drag them to match your desk."),
    ("Move across", "Push the pointer off the edge. The keyboard follows."),
)

SHORTCUTS = (
    ("Both Ctrl keys", "Release input, on every computer"),
    ("Shake the mouse", "Find the pointer"),
    ("Ctrl+1 … Ctrl+6", "Go to a page"),
    ("Ctrl+N", "Add a device"),
    ("Arrow keys", "Move the selected screen · Shift for more"),
    ("Ctrl+Z", "Undo an arrangement change"),
    ("F1", "Help"),
    ("Ctrl+Q", "Quit"),
)


class HelpPage:
    """The Help page's cards, built into `box`. `on` holds what the buttons
    do: add, copy, browse, log."""

    def __init__(self, box, kit: Kit, icon=None, **on):
        C, F = kit.C, kit.F
        self.kit, self.on = kit, on
        self.rows = {}

        # ---- About
        about = Card(box, kit, "About")
        about.pack(fill="x")
        head = tk.Frame(about.body, bg=C["card"])
        head.pack(fill="x")
        if icon is not None:
            tk.Label(head, image=icon, bg=C["card"]).pack(side="left", padx=(0, 16))
        words = tk.Frame(head, bg=C["card"])
        words.pack(side="left", fill="x")
        tk.Label(words, text="Nishro Link", font=F["h2"], bg=C["card"],
                 fg=C["ink"]).pack(anchor="w")
        tk.Label(words, text="One mouse and keyboard across your computers",
                 font=F["small"], bg=C["card"], fg=C["dim"]).pack(anchor="w")
        v = tk.Frame(words, bg=C["card"])
        v.pack(anchor="w", pady=(6, 0))
        self.version = tk.Label(v, text=f"Version {__version__}", font=F["h3"],
                                bg=C["card"], fg=C["ink"])
        self.version.pack(side="left")
        if __stage__:
            Pill(v, kit, __stage__.upper(), "accent").pack(side="left", padx=8)
        grid = tk.Frame(about.body, bg=C["card"])
        grid.pack(fill="x", pady=(14, 0))
        for i, (k, val) in enumerate(details({})):
            tk.Label(grid, text=k, font=F["small"], bg=C["card"], fg=C["dim"]
                     ).grid(row=i, column=0, sticky="w", padx=(0, 18), pady=2)
            self.rows[k] = tk.Label(grid, text=val, bg=C["card"], fg=C["ink"],
                                    font=F["mono"] if k == "Device ID" else F["body"])
            self.rows[k].grid(row=i, column=1, sticky="w", pady=2)
        for text in (f"{COPYRIGHT} · MIT License",
                     "Password words from the EFF list · CC BY 3.0 US",
                     "Controls: Sun Valley theme by rdbende · MIT"):
            tk.Label(about.body, text=text, font=F["small"], bg=C["card"],
                     fg=C["faint"]).pack(anchor="w")
        bar = tk.Frame(about.body, bg=C["card"])
        bar.pack(fill="x", pady=(12, 0))
        self.btn_copy = Button(bar, kit, "Copy details", self._copy, small=True)
        self.btn_copy.pack(side="left")
        Button(bar, kit, "Website", lambda: self._do("browse", HOME), kind="ghost",
               small=True).pack(side="left", padx=(6, 0))

        # ---- Get started
        start = Card(box, kit, "Get started")
        start.pack(fill="x", pady=(12, 0))
        for i, (headline, line) in enumerate(STEPS, start=1):
            row = tk.Frame(start.body, bg=C["card"])
            row.pack(fill="x", pady=4)
            tk.Label(row, text=str(i), font=F["h3"], width=2, bg=C["accent"],
                     fg=C["accent_ink"]).pack(side="left", anchor="n", padx=(0, 14))
            words = tk.Frame(row, bg=C["card"])
            words.pack(side="left", fill="x")
            tk.Label(words, text=headline, font=F["h3"], bg=C["card"],
                     fg=C["ink"]).pack(anchor="w")
            tk.Label(words, text=line, font=F["small"], bg=C["card"],
                     fg=C["dim"]).pack(anchor="w")
        tip = tk.Frame(start.body, bg=C["card"])
        tip.pack(fill="x", pady=(10, 0))
        tk.Label(tip, text="Stuck?", font=F["h3"], bg=C["card"], fg=C["warn"]
                 ).pack(side="left", padx=(0, 8))
        tk.Label(tip, text="Press both Ctrl keys to get your mouse back.",
                 font=F["small"], bg=C["card"], fg=C["ink"]).pack(side="left")
        Button(start.body, kit, "Add a device", lambda: self._do("add"),
               kind="primary", small=True).pack(anchor="w", pady=(12, 0))

        # ---- Keyboard shortcuts
        keys = Card(box, kit, "Keyboard shortcuts")
        keys.pack(fill="x", pady=(12, 0))
        g = tk.Frame(keys.body, bg=C["card"])
        g.pack(fill="x")
        for i, (key, what) in enumerate(SHORTCUTS):
            tk.Label(g, text=key, font=F["caps"], bg=C["card_hi"], fg=C["ink"],
                     padx=8, pady=2).grid(row=i, column=0, sticky="w", pady=3)
            tk.Label(g, text=what, font=F["body"], bg=C["card"], fg=C["dim"]
                     ).grid(row=i, column=1, sticky="w", padx=(16, 0), pady=3)

        # ---- Support
        sup = Card(box, kit, "Support")
        sup.pack(fill="x", pady=(12, 0))
        row = tk.Frame(sup.body, bg=C["card"])
        row.pack(fill="x")
        for text, what, arg in (("Documentation", "browse", DOCS),
                                ("Report a problem", "browse", ISSUES),
                                ("Downloads", "browse", RELEASES),
                                ("Open log folder", "log", None)):
            Button(row, kit, text, lambda w=what, a=arg: self._do(w, a),
                   small=True).pack(side="left", padx=(0, 6))

    def _do(self, what, *args) -> None:
        fn = self.on.get(what)
        if fn:
            fn(*[a for a in args if a is not None])

    def show(self, s: dict) -> None:
        """Fill About from the status - on each poll, changing only what did."""
        self.status = s
        for k, val in details(s):
            lb = self.rows.get(k)
            if lb is not None and lb.cget("text") != val:
                lb.configure(text=val)

    def _copy(self) -> None:
        self._do("copy", copy_text(getattr(self, "status", {}) or {}))
        self.btn_copy.set_text("Copied")

        def back():
            try:
                self.btn_copy.set_text("Copy details")
            except tk.TclError:
                pass                          # the page was redrawn meanwhile
        self.btn_copy.after(1500, back)
