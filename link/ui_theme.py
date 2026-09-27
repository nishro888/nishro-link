"""The look: Windows 11's, in light and dark, the same on Windows and Linux.

The controls - buttons, switches, text boxes, scrollbars - are real ttk widgets
drawn by the Sun Valley theme (link/theme, MIT, by rdbende), which reproduces
Windows 11's own. Everything around them - pages, cards, the sidebar - is drawn
here, in colours taken from the same theme, so the two never disagree.

An earlier look was a dark dashboard of flat, coloured labels posing as
buttons. It read as a web page; this reads as a Windows program.

MODE is System, Light or Dark. System follows the computer - Windows' app
mode, or GNOME's colour scheme - and is where a new install starts. The choice
is the person's, so it is kept in their own folder, not in the settings the
service shares.

Every colour the program draws comes from palette(), so nothing is hardcoded in
the pages. luminance() is how the tests - and anyone checking contrast - ask Tk
what a colour actually resolves to.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

THEME_DIR = Path(__file__).resolve().with_name("theme")
MODES = ("system", "light", "dark")

DARK = {
    "dark": True,
    # surfaces, from the back forward
    "bg": "#1c1c1c",          # the window, and what the controls are drawn on
    "sidebar": "#202020",
    "panel": "#1c1c1c",       # a page's own background
    "card": "#242424",        # a card on a page
    "card_hi": "#2d2d2d",     # under the pointer; the page being shown
    "line": "#303030",        # hairlines and borders
    "line_hi": "#454545",
    "menu": "#2c2c2c",        # an open menu
    "menu_hi": "#3a3a3a",     # the item under the pointer
    "field": "#292929",       # a text box at rest, under the pointer, typing
    "field_hover": "#2f2f2f",
    "field_focus": "#1c1c1c",
    # text
    "ink": "#fafafa",
    "dim": "#a3a3a3",
    "faint": "#6e6e6e",
    # meaning
    "accent": "#57c8ff",      # the theme's: primary actions, crossings, focus
    "accent_ink": "#000000",  # text on the accent
    "accent_dim": "#1b3f52",
    "accent2": "#c3a6ff",     # the hub, sparingly
    "ok": "#6ccb5f",
    "warn": "#fce100",
    "bad": "#ff99a4",
    "bad_bg": "#442726",
    "warn_bg": "#433519",
    # the arrangement
    "surface": "#171717",     # canvas background
    "grid": "#232323",
    "mine": "#60a5fa",
    "theirs": "#6ccb5f",
    "mine_fill": "#16243a",
    "theirs_fill": "#18291a",
    "cross": "#57c8ff",
    "offline": "#6e6e6e",
    "offline_fill": "#232323",
}

LIGHT = {
    "dark": False,
    "bg": "#fafafa",
    "sidebar": "#f0f0f0",
    "panel": "#fafafa",
    "card": "#ffffff",
    "card_hi": "#e9e9e9",
    "line": "#e1e1e1",
    "line_hi": "#c8c8c8",
    "menu": "#f9f9f9",
    "menu_hi": "#e8e8e8",
    "field": "#fdfdfd",
    "field_hover": "#f9f9f9",
    "field_focus": "#ffffff",
    "ink": "#1c1c1c",
    "dim": "#5c5c5c",
    "faint": "#8f8f8f",
    "accent": "#005fb8",
    "accent_ink": "#ffffff",
    "accent_dim": "#cfe3f6",
    "accent2": "#7a3fc4",
    "ok": "#0f7b0f",
    "warn": "#9d5d00",
    "bad": "#c42b1c",
    "bad_bg": "#fde7e9",
    "warn_bg": "#fff4ce",
    "surface": "#f3f3f3",
    "grid": "#e5e5e5",
    "mine": "#0067c0",
    "theirs": "#0f7b0f",
    "mine_fill": "#dcebfa",
    "theirs_fill": "#dff6dd",
    "cross": "#005fb8",
    "offline": "#8f8f8f",
    "offline_fill": "#ececec",
}

SANS = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Ubuntu", "Cantarell",
        "Noto Sans", "DejaVu Sans", "Helvetica")
MONO = ("Cascadia Mono", "Consolas", "JetBrains Mono", "Ubuntu Mono",
        "DejaVu Sans Mono", "Courier New")

_current = "dark"         # the mode in force: "light" or "dark"
PREF_PATH = None          # where the choice is kept; the tests point it away


def luminance(root, colour: str) -> float:
    """0.0 (black) to 1.0 (white) for any colour Tk can name."""
    try:
        r, g, b = root.winfo_rgb(colour)          # 16-bit channels
    except Exception:
        return 1.0                                # assume light, the safer guess
    return (0.299 * r + 0.587 * g + 0.114 * b) / 65535.0


def current() -> str:
    return _current


def palette(root=None) -> dict:
    """The colours of the mode in force. `root` is accepted for old callers."""
    return dict(DARK if _current == "dark" else LIGHT)


# ------------------------------------------------------------------ mode
def system_mode() -> str:
    """What the computer is set to: 'light' or 'dark'."""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion"
                                r"\Themes\Personalize") as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] \
                    else "dark"
        except OSError:
            return "light"
    # GNOME's colour scheme says it outright - where it has one. Not every
    # desktop does: the AIO's Ubuntu has no such key, and is dark only by its
    # theme's name (Yaru-sage-dark). So the theme's name is asked too.
    scheme = _gsetting("color-scheme")
    if "dark" in scheme:
        return "dark"
    if "light" in scheme:
        return "light"
    theme = _gsetting("gtk-theme") or os.environ.get("GTK_THEME", "")
    return "dark" if "dark" in theme.lower() else "light"


def _gsetting(key: str) -> str:
    """GNOME's org.gnome.desktop.interface `key`, or '' if it has none."""
    try:
        r = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface",
                            key], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip().strip("'") if r.returncode == 0 else ""


def resolve(mode: str) -> str:
    return system_mode() if mode not in ("light", "dark") else mode


def _pref_file() -> Path:
    if PREF_PATH:
        return Path(PREF_PATH)
    from . import config
    return config.path().parent / "ui.json"


def load_pref() -> str:
    try:
        mode = json.loads(_pref_file().read_text(encoding="utf-8")).get("mode")
    except (OSError, ValueError, AttributeError):
        mode = None
    return mode if mode in MODES else "system"


def save_pref(mode: str) -> None:
    p = _pref_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    except OSError:
        pass                  # a preference that cannot be kept is no failure


def apply(root, mode: str) -> str:
    """Put `mode` in force on this Tk: the controls' theme, the palette, the
    fonts. Returns what it resolved to. Call before building widgets."""
    global _current
    _current = resolve(mode)
    from tkinter import ttk
    try:
        style = ttk.Style(root)
        if "sun-valley-dark" not in style.theme_names():
            root.tk.call("source", str(THEME_DIR / "sv.tcl"))
        style.theme_use(f"sun-valley-{_current}")
        root.tk.call("configure_colors")      # now, not when the event lands
        _styles(root, style)
    except Exception:
        pass                  # without the theme: plain ttk, still usable
    _made.clear()
    return _current


def _styles(root, style) -> None:
    C, F = palette(), fonts(root)
    family = F["body"][0]
    for name, size in (("SunValleyCaptionFont", 9), ("SunValleyBodyFont", 10),
                       ("SunValleyBodyLargeFont", 12)):
        try:
            root.tk.call("font", "configure", name, "-family", family,
                         "-size", size)
        except Exception:
            pass
    try:
        root.tk.call("font", "configure", "SunValleyBodyStrongFont",
                     "-family", F["h3"][0], "-size", 10,
                     "-weight", "bold" if len(F["h3"]) > 2 else "normal")
    except Exception:
        pass
    style.configure("Danger.TButton", foreground=C["bad"])
    style.map("Danger.TButton", foreground=[("disabled", C["faint"])])
    style.configure("Toolbutton", foreground=C["dim"])
    style.map("Toolbutton", foreground=[("disabled", C["faint"]),
                                        ("active", C["ink"])])


_made = set()


def on(root, base: str, bg: str) -> str:
    """The ttk style `base`, for a control sitting on `bg`. The theme draws its
    controls for its own background; anywhere else the corners would show it."""
    bg = (bg or "").lower()
    if not bg or bg == palette()["bg"]:
        return base
    name = f"on{bg.lstrip('#')}.{base}"
    if name not in _made:
        from tkinter import ttk
        try:
            ttk.Style(root).configure(name, background=bg)
        except Exception:
            return base
        _made.add(name)
    return name


def fonts(root) -> dict:
    """Font tuples for each role, from whatever this machine actually has.
    Headings use Segoe UI Semibold where it exists - Windows' own weight."""
    try:
        from tkinter import font as tkfont
        have = set(tkfont.families(root))
    except Exception:
        have = set()
    sans = next((f for f in SANS if f in have), "")
    mono = next((f for f in MONO if f in have), "Courier")
    if "Segoe UI Semibold" in have:
        def strong(size):
            return ("Segoe UI Semibold", size)
    else:
        def strong(size):
            return (sans, size, "bold")
    return {
        "h1": strong(18),
        "h2": strong(13),
        "h3": strong(10),
        "body": (sans, 10),
        "small": (sans, 9),
        "tiny": (sans, 8),
        "caps": strong(9),
        "metric": strong(18),
        "hero": strong(22),
        "nav": (sans, 10),
        "mono": (mono, 9),
        "mono_big": (mono, 13, "bold"),
    }


def title_bar(win) -> None:
    """Match Windows' title bar to the mode: Windows 10 (2004 and later) and 11
    draw a white one unless asked, the one bright strip on a dark window.
    Elsewhere, or on an older Windows, nothing happens."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        dwm = ctypes.WinDLL("dwmapi")
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              ctypes.c_void_p, wintypes.DWORD]
        win.update_idletasks()
        hwnd = user32.GetParent(win.winfo_id())
        on_ = ctypes.c_int(1 if _current == "dark" else 0)
        # 20 is DWMWA_USE_IMMERSIVE_DARK_MODE; builds before 20H1 used 19.
        for attr in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on_),
                                         ctypes.sizeof(on_)) == 0:
                break
        # Windows 10 repaints the title bar only when the frame changes.
        win.attributes("-alpha", 0.99)
        win.attributes("-alpha", 1.0)
    except Exception:
        pass


dark_title_bar = title_bar        # the name it had while there was one mode
