"""The look: one dark, deliberate theme, the same on Windows and Linux.

The window used to borrow the desktop's colours and hardcode the rest, which
looked like a settings dialog - fine on Windows, mismatched on a dark GNOME
desktop, and never like a product. It now has its own palette, as most modern
desktop software does: a deep blue-black base, raised panels, one cyan accent for
what matters (the crossing lines, the primary action) and a violet second accent
used sparingly.

Every colour the program draws comes from here, so the whole window can be
restyled in one place, and nothing is hardcoded in the pages.

luminance() stays: it is how the tests - and anyone checking contrast - can ask
Tk what a colour actually resolves to.
"""
from __future__ import annotations

BRAND = {
    "dark": True,
    # surfaces, from the back forward
    "bg": "#0a0e16",          # the window
    "sidebar": "#0d1220",
    "panel": "#101626",       # a page's own background
    "card": "#141b2d",        # a card on a page
    "card_hi": "#1a2338",     # a card under the pointer; inputs
    "line": "#233050",        # hairlines and borders
    "line_hi": "#2f3f66",
    # text
    "ink": "#e7edf7",
    "dim": "#8c9ab3",
    "faint": "#5a6886",
    # meaning
    "accent": "#22d3ee",      # cyan: primary actions, crossings, focus
    "accent_ink": "#04121a",  # text on the accent
    "accent_dim": "#0e4a5a",  # the glow under a crossing line
    "accent2": "#a78bfa",     # violet: the hub, sparingly
    "ok": "#34d399",
    "warn": "#fbbf24",
    "bad": "#f87171",
    "bad_bg": "#3a1620",
    "warn_bg": "#3a2c0f",
    # the arrangement
    "surface": "#0f1524",     # canvas background
    "grid": "#161f33",
    "mine": "#60a5fa",
    "theirs": "#34d399",
    "mine_fill": "#15223a",
    "theirs_fill": "#12281f",
    "cross": "#22d3ee",
    "offline": "#5a6886",
    "offline_fill": "#151b29",
}

SANS = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Ubuntu", "Cantarell",
        "Noto Sans", "DejaVu Sans", "Helvetica")
MONO = ("Cascadia Mono", "Consolas", "JetBrains Mono", "Ubuntu Mono",
        "DejaVu Sans Mono", "Courier New")


def luminance(root, colour: str) -> float:
    """0.0 (black) to 1.0 (white) for any colour Tk can name."""
    try:
        r, g, b = root.winfo_rgb(colour)          # 16-bit channels
    except Exception:
        return 1.0                                # assume light, the safer guess
    return (0.299 * r + 0.587 * g + 0.114 * b) / 65535.0


def palette(root=None) -> dict:
    """The theme's colours. `root` is accepted for callers that pass one."""
    return dict(BRAND)


def fonts(root) -> dict:
    """Font tuples for each role, from whatever this machine actually has."""
    try:
        from tkinter import font as tkfont
        have = set(tkfont.families(root))
    except Exception:
        have = set()
    sans = next((f for f in SANS if f in have), "")
    mono = next((f for f in MONO if f in have), "Courier")
    return {
        "h1": (sans, 17, "bold"),
        "h2": (sans, 12, "bold"),
        "h3": (sans, 10, "bold"),
        "body": (sans, 10),
        "small": (sans, 9),
        "tiny": (sans, 8),
        "caps": (sans, 8, "bold"),
        "metric": (sans, 18, "bold"),
        "hero": (sans, 22, "bold"),
        "nav": (sans, 10),
        "mono": (mono, 9),
        "mono_big": (mono, 13, "bold"),
    }
