"""Help: About, Quick start and Keyboard shortcuts - the Help menu's windows.

About says which Nishro Link this is and where it runs, and copies the same as
text for a bug report. The copy leaves out addresses and the password: it is
made to be pasted somewhere public.
"""
from __future__ import annotations

import os
import platform
import sys
import tkinter as tk

from . import __stage__, __version__, protocol, ui_theme
from .ui_kit import Button, Kit, Pill

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
    """(label, value) for the About window - and, with more, for the copy."""
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


# ================================================================ windows
class Sheet:
    """A small window of the app's own: themed, centred on the app, closed by
    Escape. One of each kind at a time - asking again brings it forward."""

    title = "Nishro Link"

    def __init__(self, parent, kit: Kit):
        self.parent, self.kit = parent, kit
        C = kit.C
        self.top = tk.Toplevel(parent, bg=C["panel"])
        self.top.title(self.title)
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _e: self.close())
        self.box = tk.Frame(self.top, bg=C["panel"], padx=26, pady=22)
        self.box.pack(fill="both", expand=True)
        self.fill(self.box)
        self.top.update_idletasks()
        try:
            x = parent.winfo_rootx() + (parent.winfo_width()
                                        - self.top.winfo_reqwidth()) // 2
            y = parent.winfo_rooty() + max(40, (parent.winfo_height()
                                                - self.top.winfo_reqheight()) // 3)
            self.top.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass
        ui_theme.dark_title_bar(self.top)
        self.top.focus_set()

    def fill(self, box) -> None:
        raise NotImplementedError

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def raise_(self) -> None:
        self.top.deiconify()
        self.top.lift()
        self.top.focus_set()

    def close(self) -> None:
        try:
            self.top.destroy()
        except tk.TclError:
            pass

    def _buttons(self, box):
        bar = tk.Frame(box, bg=self.kit.C["panel"])
        bar.pack(fill="x", pady=(20, 0))
        Button(bar, self.kit, "Close", self.close).pack(side="right")
        return bar


class About(Sheet):
    title = "About Nishro Link"

    def __init__(self, parent, kit, status, copy=None, browse=None):
        self.status = status or {}
        self.copy = copy
        self.browse = browse
        super().__init__(parent, kit)

    def fill(self, box) -> None:
        C, F, kit = self.kit.C, self.kit.F, self.kit
        head = tk.Frame(box, bg=C["panel"])
        head.pack(fill="x")
        try:
            from .icon import PNG_64
            self._icon = tk.PhotoImage(master=box, data=PNG_64)
            tk.Label(head, image=self._icon, bg=C["panel"]).pack(side="left",
                                                                 padx=(0, 16))
        except (ImportError, tk.TclError):
            pass
        words = tk.Frame(head, bg=C["panel"])
        words.pack(side="left", fill="x")
        tk.Label(words, text="Nishro Link", font=F["hero"], bg=C["panel"],
                 fg=C["ink"]).pack(anchor="w")
        tk.Label(words, text="One mouse and keyboard across your computers",
                 font=F["small"], bg=C["panel"], fg=C["dim"]).pack(anchor="w")
        v = tk.Frame(words, bg=C["panel"])
        v.pack(anchor="w", pady=(8, 0))
        self.version = tk.Label(v, text=f"Version {__version__}", font=F["h3"],
                                bg=C["panel"], fg=C["ink"])
        self.version.pack(side="left")
        if __stage__:
            Pill(v, kit, __stage__.upper(), "accent").pack(side="left", padx=8)

        tk.Frame(box, bg=C["line"], height=1).pack(fill="x", pady=16)
        grid = tk.Frame(box, bg=C["panel"])
        grid.pack(fill="x")
        self.rows = {}
        for i, (k, val) in enumerate(details(self.status)):
            tk.Label(grid, text=k, font=F["small"], bg=C["panel"], fg=C["dim"]
                     ).grid(row=i, column=0, sticky="w", padx=(0, 18), pady=2)
            mono = k == "Device ID"
            self.rows[k] = tk.Label(grid, text=val, bg=C["panel"], fg=C["ink"],
                                    font=F["mono"] if mono else F["body"])
            self.rows[k].grid(row=i, column=1, sticky="w", pady=2)

        tk.Frame(box, bg=C["line"], height=1).pack(fill="x", pady=16)
        for text in (f"{COPYRIGHT} · MIT License",
                     "Password words from the EFF list · CC BY 3.0 US",
                     "Controls: Sun Valley theme by rdbende · MIT"):
            tk.Label(box, text=text, font=F["small"], bg=C["panel"],
                     fg=C["faint"]).pack(anchor="w")

        bar = self._buttons(box)
        self.btn_copy = Button(bar, kit, "Copy details", self._copy)
        self.btn_copy.pack(side="left")
        Button(bar, kit, "Website", lambda: self.browse and self.browse(HOME),
               kind="ghost").pack(side="left", padx=(8, 0))

    def _copy(self) -> None:
        text = copy_text(self.status)
        if self.copy:
            self.copy(text)
        else:
            self.top.clipboard_clear()
            self.top.clipboard_append(text)
        self.btn_copy.set_text("Copied")
        self.top.after(1500, lambda: self.alive() and
                       self.btn_copy.set_text("Copy details"))


STEPS = (
    ("Install on each computer", "Every computer that shares needs Nishro Link."),
    ("Add a device", "Enter the other computer's name and password."),
    ("Arrange the screens", "Drag them to match your desk."),
    ("Move across", "Push the pointer off the edge. The keyboard follows."),
)


class QuickStart(Sheet):
    title = "Quick start"

    def __init__(self, parent, kit, add=None):
        self.add = add
        super().__init__(parent, kit)

    def fill(self, box) -> None:
        C, F, kit = self.kit.C, self.kit.F, self.kit
        tk.Label(box, text="Quick start", font=F["h1"], bg=C["panel"],
                 fg=C["ink"]).pack(anchor="w", pady=(0, 14))
        for i, (head, line) in enumerate(STEPS, start=1):
            row = tk.Frame(box, bg=C["panel"])
            row.pack(fill="x", pady=6)
            tk.Label(row, text=str(i), font=F["h3"], width=2, bg=C["accent"],
                     fg=C["accent_ink"]).pack(side="left", anchor="n", padx=(0, 14))
            words = tk.Frame(row, bg=C["panel"])
            words.pack(side="left", fill="x")
            tk.Label(words, text=head, font=F["h3"], bg=C["panel"], fg=C["ink"]
                     ).pack(anchor="w")
            tk.Label(words, text=line, font=F["small"], bg=C["panel"], fg=C["dim"]
                     ).pack(anchor="w")
        tip = tk.Frame(box, bg=C["card"], highlightthickness=1,
                       highlightbackground=C["line"])
        tip.pack(fill="x", pady=(16, 0))
        tk.Label(tip, text="Stuck?", font=F["h3"], bg=C["card"], fg=C["warn"]
                 ).pack(side="left", padx=(12, 8), pady=10)
        tk.Label(tip, text="Press both Ctrl keys to get your mouse back.",
                 font=F["small"], bg=C["card"], fg=C["ink"]).pack(side="left")
        bar = self._buttons(box)
        if self.add:
            Button(bar, kit, "Add a device", lambda: (self.close(), self.add()),
                   kind="primary").pack(side="left")


SHORTCUTS = (
    ("Both Ctrl keys", "Release input, on every computer"),
    ("Ctrl+N", "Add a device"),
    ("Ctrl+1 … Ctrl+5", "Go to a page"),
    ("Arrow keys", "Move the selected screen · Shift for more"),
    ("Ctrl+Z", "Undo an arrangement change"),
    ("Alt+F, V, S, H", "Open a menu"),
    ("F1", "Quick start"),
    ("Ctrl+Q", "Quit"),
)


class Shortcuts(Sheet):
    title = "Keyboard shortcuts"

    def fill(self, box) -> None:
        C, F = self.kit.C, self.kit.F
        tk.Label(box, text="Keyboard shortcuts", font=F["h1"], bg=C["panel"],
                 fg=C["ink"]).pack(anchor="w", pady=(0, 14))
        grid = tk.Frame(box, bg=C["panel"])
        grid.pack(fill="x")
        for i, (key, what) in enumerate(SHORTCUTS):
            tk.Label(grid, text=key, font=F["caps"], bg=C["card_hi"], fg=C["ink"],
                     padx=8, pady=2).grid(row=i, column=0, sticky="w", pady=4)
            tk.Label(grid, text=what, font=F["body"], bg=C["panel"], fg=C["dim"]
                     ).grid(row=i, column=1, sticky="w", padx=(16, 0), pady=4)
        self._buttons(box)
