"""A machine's desktop: every monitor it has, as one screen with parts.

The link used to describe a machine by its PRIMARY monitor. On the test laptop that
is the 1366x768 panel, with a 1920x1080 display to its left - so the panel's
left edge, which Windows treats as the way onto the external display, was also
the link's left edge. Put the AIO on the left and every move onto the external
display went to the AIO instead.

So a machine's screen is its whole desktop: the bounding box of all its
monitors, with each monitor recorded as a part. Edges between monitors belong to
the operating system; the link only crosses at the outside of the box.

Coordinates: the OS numbers pixels from the primary monitor's corner, so a
display to the left has negative x. The link numbers them from the box's corner,
so everything is >= 0. to_os / from_os convert.
"""
from __future__ import annotations

import glob
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Desktop:
    x: int                  # the bounding box, in the OS's own coordinates
    y: int
    w: int
    h: int
    parts: tuple = ()       # monitors as (x, y, w, h) inside the box; () = one

    @property
    def size(self) -> tuple:
        return self.w, self.h

    def to_os(self, x: int, y: int) -> tuple:
        return int(x) + self.x, int(y) + self.y

    def from_os(self, x: int, y: int) -> tuple:
        return int(x) - self.x, int(y) - self.y


def from_monitors(rects) -> Desktop:
    """The desktop spanned by these monitors, each (x, y, w, h) in OS terms."""
    rects = [(int(x), int(y), int(w), int(h)) for x, y, w, h in rects
             if int(w) > 0 and int(h) > 0]
    if not rects:
        return Desktop(0, 0, 1920, 1080)
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    parts = tuple(sorted((x - x0, y - y0, w, h) for x, y, w, h in rects))
    if len(parts) == 1:
        parts = ()                      # one monitor filling the box
    return Desktop(x0, y0, x1 - x0, y1 - y0, parts)


def detect() -> Desktop:
    if sys.platform == "win32":
        return _windows()
    return _linux()


def _windows() -> Desktop:
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    try:
        # Before any metric: on a scaled display GetSystemMetrics returns
        # logical pixels while the mouse hook reports physical ones.
        u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    rects = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HMONITOR, wt.HDC,
                              ctypes.POINTER(wt.RECT), wt.LPARAM)

    def found(_mon, _dc, rc, _data):
        r = rc.contents
        rects.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    try:
        u.EnumDisplayMonitors(None, None, proc(found), 0)
    except Exception:
        rects = []
    if not rects:
        rects = [(0, 0, u.GetSystemMetrics(0) or 1920, u.GetSystemMetrics(1) or 1080)]
    return from_monitors(rects)


def parse_xrandr(text: str) -> list:
    """Monitor rectangles from `xrandr --listmonitors`, whose lines look like

        Monitors: 2
         0: +*DP-1 1920/527x1080/296+0+0  DP-1
         1: +HDMI-1 1366/344x768/193+1920+148  HDMI-1
    """
    import re
    out = []
    for line in (text or "").splitlines():
        m = re.search(r"(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)", line)
        if m:
            w, h, x, y = (int(v) for v in m.groups())
            out.append((x, y, w, h))
    return out


def _linux() -> Desktop:
    # xrandr lists every monitor with its position, under X11 and under GNOME's
    # Wayland session (through XWayland), which is as much of the compositor's
    # arrangement as an ordinary program can see. Failing that, one monitor
    # from the kernel's mode list.
    try:
        import subprocess
        r = subprocess.run(["xrandr", "--listmonitors"], capture_output=True,
                           text=True, timeout=3)
        rects = parse_xrandr(r.stdout) if r.returncode == 0 else []
        if rects:
            return from_monitors(rects)
    except (OSError, subprocess.SubprocessError):
        pass
    for f in sorted(glob.glob("/sys/class/drm/*/modes")):
        try:
            line = open(f).readline().strip().lower()
            if "x" in line:
                w, h = line.split("x")[:2]
                return Desktop(0, 0, int(w), int(h.split()[0]))
        except Exception:
            continue
    return Desktop(0, 0, 1920, 1080)
