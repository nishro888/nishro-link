"""Windows' half of a moved window: choose one, hide it, capture it, drive it.

docs/moving-windows.md is the design; this is section 4.1. What a window has
to offer winmove.HomeSession is here, over Windows' own APIs through ctypes -
nothing new to install.

The three things that were not obvious, each measured on a real laptop before
being built on (TESTLOG, 2026-10-13):

* **Hiding.** A window moved off the screen, or minimized, STOPS DRAWING, so
  neither can be how a window leaves home - it would freeze the picture the
  moment it left. A window made layered at alpha 1 and click-through is gone
  from the screen, out of the way of this computer's own mouse, and still
  draws. That is what hiding means here.
* **Capturing.** PrintWindow with PW_RENDERFULLCONTENT gets the picture of a
  window that is covered, hidden, or drawn by DirectComposition (anything
  Chromium-based). 15-35 ms for a windowful.
* **Driving.** Posted window messages, not SendInput: home is a computer the
  person is still using, and SendInput would need the window in the
  foreground, taking their focus every time they typed on the other screen.
  Posted messages reached a hidden Notepad and a hidden Edge - text and
  clicks both, inside the web page - and never moved the foreground window.

This module only ever touches a window the person chose. It is run by the
desk agent (agent.py), in the signed-in session, because that is where the
desktop is.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

from . import keymap

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
HWND = wt.HWND

# Window styles and the bits of them we care about.
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CHILD, WS_VISIBLE = 0x40000000, 0x10000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x80000, 0x20, 0x80
WS_EX_NOACTIVATE = 0x08000000
LWA_ALPHA = 0x2
SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOMOVE = 0x4, 0x10, 0x2
PW_RENDERFULLCONTENT = 2
SRCCOPY = 0x00CC0020

WM_CLOSE = 0x0010
WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x0200, 0x020A, 0x020E
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
_BUTTON_MSGS = {                     # name -> (down, up, which button is held)
    "left": (0x0201, 0x0202, 0x0001),
    "right": (0x0204, 0x0205, 0x0002),
    "middle": (0x0207, 0x0208, 0x0010),
}
WHEEL_NOTCH = 120


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", HWND),
                ("hwndFocus", HWND), ("hwndCapture", HWND), ("hwndMenuOwner", HWND),
                ("hwndMoveSize", HWND), ("hwndCaret", HWND), ("rcCaret", RECT)]


# Every call declared: a handle is 64-bit, and ctypes cuts undeclared
# arguments down to an int. Left undeclared, SelectObject raised
# "int too long to convert" as soon as a real bitmap handle was passed - and
# a cut-down handle that happens to fit would be worse, acting on nothing.
def _declare():
    for name, args, res in [
        ("GetWindowLongW", [HWND, ctypes.c_int], ctypes.c_long),
        ("SetWindowLongW", [HWND, ctypes.c_int, ctypes.c_long], ctypes.c_long),
        ("GetLayeredWindowAttributes",
         [HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], wt.BOOL),
        ("SetLayeredWindowAttributes", [HWND, wt.DWORD, wt.BYTE, wt.DWORD], wt.BOOL),
        ("RedrawWindow", [HWND, ctypes.c_void_p, ctypes.c_void_p, wt.UINT], wt.BOOL),
        ("SetWindowPos", [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, wt.UINT], wt.BOOL),
        ("GetWindowRect", [HWND, ctypes.c_void_p], wt.BOOL),
        ("GetClientRect", [HWND, ctypes.c_void_p], wt.BOOL),
        ("ScreenToClient", [HWND, ctypes.c_void_p], wt.BOOL),
        ("RealChildWindowFromPoint", [HWND, POINT], HWND),
        ("GetGUIThreadInfo", [wt.DWORD, ctypes.c_void_p], wt.BOOL),
        ("GetWindowThreadProcessId", [HWND, ctypes.c_void_p], wt.DWORD),
        ("IsWindow", [HWND], wt.BOOL),
        ("IsWindowVisible", [HWND], wt.BOOL),
        ("IsIconic", [HWND], wt.BOOL),
        ("IsZoomed", [HWND], wt.BOOL),
        ("GetWindowTextLengthW", [HWND], ctypes.c_int),
        ("GetWindowTextW", [HWND, ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int),
        ("GetClassNameW", [HWND, ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int),
        ("GetForegroundWindow", [], HWND),
        ("GetDesktopWindow", [], HWND),
        ("GetShellWindow", [], HWND),
        ("GetWindow", [HWND, wt.UINT], HWND),
        ("PostMessageW", [HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL),
        ("PrintWindow", [HWND, wt.HDC, wt.UINT], wt.BOOL),
        ("GetDC", [HWND], wt.HDC),
        ("ReleaseDC", [HWND, wt.HDC], ctypes.c_int),
        ("ShowWindow", [HWND, ctypes.c_int], wt.BOOL),
    ]:
        fn = getattr(user32, name)
        fn.argtypes, fn.restype = args, res
    for name, args, res in [
        ("CreateCompatibleDC", [wt.HDC], wt.HDC),
        ("CreateCompatibleBitmap", [wt.HDC, ctypes.c_int, ctypes.c_int], wt.HBITMAP),
        ("SelectObject", [wt.HDC, wt.HGDIOBJ], wt.HGDIOBJ),
        ("DeleteObject", [wt.HGDIOBJ], wt.BOOL),
        ("DeleteDC", [wt.HDC], wt.BOOL),
        ("BitBlt", [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD], wt.BOOL),
        ("GetDIBits", [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                       ctypes.c_void_p, wt.UINT], ctypes.c_int),
    ]:
        fn = getattr(gdi32, name)
        fn.argtypes, fn.restype = args, res


_declare()


# ------------------------------------------------------------------ looking
def handle(hwnd) -> int:
    """A window handle as a plain number, whichever form it arrived in.

    EnumWindows and the rest hand back plain ints, but a caller may well
    have a ctypes HWND instead - and `int()` on one of those raises, while
    comparing one to an int quietly says False. Both were real bugs here, so
    every way into this module goes through this.
    """
    if hwnd is None:
        return 0
    if isinstance(hwnd, int):
        return hwnd
    value = getattr(hwnd, "value", None)
    return int(value) if value is not None else 0


def title_of(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def rect_of(hwnd):
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def pid_of(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _per_pixel_alpha(hwnd) -> bool:
    """A window already drawing its own transparency. Making such a window
    layered at alpha 1 would not hide it reliably and would disturb how it
    draws, so it is refused (R9)."""
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if not ex & WS_EX_LAYERED:
        return False
    # A layered window set up with a colour key or an alpha has attributes to
    # read; one drawing per-pixel alpha (UpdateLayeredWindow) has none.
    return not user32.GetLayeredWindowAttributes(hwnd, None, None, None)


def why_not(hwnd, own_pid: int = None) -> str:
    """Why this window cannot be moved, or "" if it can (R9)."""
    hwnd = handle(hwnd)
    if not hwnd or not user32.IsWindow(hwnd):
        return "that window has gone"
    if not user32.IsWindowVisible(hwnd):
        return "that window is not on the screen"
    if user32.IsIconic(hwnd):
        return "that window is minimized - a minimized window stops drawing"
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    if style & WS_CHILD:
        return "that is part of another window, not a window of its own"
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if ex & WS_EX_TOOLWINDOW:
        return "that is a toolbar or a panel, not a window that can move"
    if hwnd in (user32.GetDesktopWindow(), user32.GetShellWindow()):
        return "that is the desktop itself"
    if class_of(hwnd) in ("Shell_TrayWnd", "Progman", "WorkerW", "Windows.UI.Core.CoreWindow"):
        return "that belongs to Windows itself"
    if not title_of(hwnd).strip():
        return "that window has no title, so it is probably not a real window"
    if own_pid and pid_of(hwnd) == own_pid:
        return "that is one of Nishro Link's own windows"
    if _per_pixel_alpha(hwnd):
        return "that window draws its own transparency, which cannot be hidden safely"
    return ""


def movable(own_pid: int = None):
    """Every window the person could move, newest in the stack first, as
    [(handle, title, class)]. The tray's menu and the shortcut use this."""
    out = []
    cb_t = ctypes.WINFUNCTYPE(wt.BOOL, HWND, wt.LPARAM)

    def each(hwnd, _):
        if not why_not(hwnd, own_pid):
            out.append((hwnd, title_of(hwnd), class_of(hwnd)))
        return True
    user32.EnumWindows(cb_t(each), 0)
    return out


def active(own_pid: int = None):
    """The window in front, if it can be moved; else (None, why not)."""
    hwnd = user32.GetForegroundWindow()
    why = why_not(hwnd, own_pid)
    return (None, why) if why else (hwnd, "")


# ------------------------------------------------------------------ the window
class Window:
    """One window of someone else's program, as winmove.HomeSession needs it.

    `rescue` is a winmove.Rescue: how to un-hide this window is written there
    before it is hidden, so a crash cannot leave it invisible (R6).
    """

    def __init__(self, hwnd, rescue=None, log=None):
        self.hwnd = handle(hwnd)
        self.rescue = rescue
        self.log = log or (lambda *_: None)
        self.hidden = False
        self._held = 0                 # which mouse buttons the far side holds
        self._down = set()             # which keys, so they can be let go

    # --------------------------------------------------------------- looking
    def alive(self) -> bool:
        return bool(user32.IsWindow(self.hwnd))

    def title(self) -> str:
        return title_of(self.hwnd)

    def size(self):
        r = rect_of(self.hwnd)
        return (r[2], r[3]) if r else (0, 0)

    def capture(self):
        """The window's picture as BGRA, top row first - or None if it has
        gone. PW_RENDERFULLCONTENT is what gets a hidden or covered window,
        and a DirectComposition one (anything Chromium-based)."""
        r = rect_of(self.hwnd)
        if not r or r[2] <= 0 or r[3] <= 0 or not self.alive():
            return None
        w, h = r[2], r[3]
        screen = user32.GetDC(None)
        dc = gdi32.CreateCompatibleDC(screen)
        bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
        old = gdi32.SelectObject(dc, bmp)
        try:
            if not user32.PrintWindow(self.hwnd, dc, PW_RENDERFULLCONTENT):
                return None
            info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32,
                                    0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)
            if not gdi32.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(info), 0):
                return None
            return buf.raw
        finally:
            gdi32.SelectObject(dc, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(dc)
            user32.ReleaseDC(None, screen)

    # ------------------------------------------------------- hiding and back
    def hide(self) -> bool:
        """Take the window off this screen, still drawing. The undo is
        written down first."""
        if self.hidden:
            return True
        ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        alpha = wt.BYTE(255)
        flags = wt.DWORD(0)
        had = bool(user32.GetLayeredWindowAttributes(
            self.hwnd, None, ctypes.byref(alpha), ctypes.byref(flags)))
        if self.rescue is not None:
            self.rescue.remember(int(self.hwnd), {
                "exstyle": int(ex),
                "alpha": int(alpha.value) if had else None,
                "flags": int(flags.value) if had else None,
                "title": self.title()[:120],
            })
        # NOACTIVATE as well: hidden, it must not be what Windows hands the
        # focus to when the person alt-tabs or clicks on the desktop.
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                              ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        if not user32.SetLayeredWindowAttributes(self.hwnd, 0, 1, LWA_ALPHA):
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex)
            if self.rescue is not None:
                self.rescue.forget(int(self.hwnd))
            return False
        self.hidden = True
        return True

    def show(self) -> None:
        """Put it back exactly as it was, and let go of anything the other
        computer was still holding down."""
        self._let_go()
        if self.alive():
            undo = (self.rescue.pending().get(str(int(self.hwnd)))
                    if self.rescue is not None else None)
            _unhide(self.hwnd, undo)
        if self.rescue is not None:
            self.rescue.forget(int(self.hwnd))
        self.hidden = False

    # --------------------------------------------------------------- driving
    def resize(self, w, h) -> None:
        user32.SetWindowPos(self.hwnd, None, 0, 0, int(w), int(h),
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOMOVE)

    def close(self) -> None:
        """Ask the program's window to close, as its own close button would -
        it may put up "save changes?" first, which is the point."""
        user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def _aim(self, x, y):
        """The window a real click at (x, y) - in this window's own
        coordinates, title bar included - would land on, and that point in
        ITS client coordinates.

        Windows delivers a mouse message to the deepest child at the point,
        so a click posted to the top-level window of a program that has
        child controls reaches none of them. RealChildWindowFromPoint is
        what finds that child: ChildWindowFromPointEx was tried first and
        stops descending the moment the window is hidden, because its
        children then count as invisible - which is exactly when a moved
        window needs driving (seen on the laptop: clicks reached a shown Tk
        canvas and nothing at all once it was hidden).
        """
        r = rect_of(self.hwnd)
        if not r:
            return self.hwnd, x, y
        sx, sy = r[0] + int(x), r[1] + int(y)
        hwnd = self.hwnd
        for _ in range(8):               # down the tree; a few levels is plenty
            p = POINT(sx, sy)
            user32.ScreenToClient(hwnd, ctypes.byref(p))
            child = user32.RealChildWindowFromPoint(hwnd, p)
            if not child or child == hwnd:
                break
            hwnd = child
        p = POINT(sx, sy)
        user32.ScreenToClient(hwnd, ctypes.byref(p))
        return hwnd, p.x, p.y

    def _focus(self):
        """The control holding the keyboard focus inside this program."""
        tid = user32.GetWindowThreadProcessId(self.hwnd, None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
            return info.hwndFocus
        return self.hwnd

    def move(self, x, y) -> None:
        hwnd, cx, cy = self._aim(x, y)
        self._at = (x, y)
        user32.PostMessageW(hwnd, WM_MOUSEMOVE, self._held, _lp(cx, cy))

    def button(self, name, down) -> None:
        msgs = _BUTTON_MSGS.get(name)
        if not msgs:
            return
        x, y = getattr(self, "_at", (0, 0))
        hwnd, cx, cy = self._aim(x, y)
        if down:
            self._held |= msgs[2]
        else:
            self._held &= ~msgs[2]
        user32.PostMessageW(hwnd, msgs[0] if down else msgs[1],
                            self._held, _lp(cx, cy))

    def wheel(self, dx, dy) -> None:
        x, y = getattr(self, "_at", (0, 0))
        hwnd, _, _ = self._aim(x, y)
        r = rect_of(self.hwnd) or (0, 0, 0, 0)
        # The wheel is the one message whose position is in SCREEN
        # coordinates, not the window's.
        screen = _lp(r[0] + int(x), r[1] + int(y))
        if dy:
            user32.PostMessageW(hwnd, WM_MOUSEWHEEL,
                                (int(dy) * WHEEL_NOTCH) << 16 | self._held, screen)
        if dx:
            user32.PostMessageW(hwnd, WM_MOUSEHWHEEL,
                                (int(dx) * WHEEL_NOTCH) << 16 | self._held, screen)

    def key(self, code, down) -> None:
        """A key, as the evdev code Nishro Link speaks everywhere."""
        vk = keymap.evdev_to_vk(code)
        if not vk:
            return
        target = self._focus()
        if down:
            self._down.add(vk)
        else:
            self._down.discard(vk)
        user32.PostMessageW(target, WM_KEYDOWN if down else WM_KEYUP, vk,
                            0x00000001 if down else 0xC0000001)

    def _let_go(self) -> None:
        """Nothing may be left held down when a window comes home - a stuck
        Ctrl or mouse button is the kind of thing the person cannot undo."""
        for vk in list(self._down):
            try:
                user32.PostMessageW(self._focus(), WM_KEYUP, vk, 0xC0000001)
            except Exception:
                pass
        self._down.clear()
        for name, (_, up, bit) in _BUTTON_MSGS.items():
            if self._held & bit:
                x, y = getattr(self, "_at", (0, 0))
                hwnd, cx, cy = self._aim(x, y)
                self._held &= ~bit
                user32.PostMessageW(hwnd, up, self._held, _lp(cx, cy))


def _lp(x, y) -> int:
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def _unhide(hwnd, undo) -> None:
    """Put a window's look back from what Rescue wrote down."""
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    was = (undo or {}).get("exstyle")
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, int(was) if was is not None
                          else ex & ~(WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE))
    alpha = (undo or {}).get("alpha")
    if alpha is not None:
        user32.SetLayeredWindowAttributes(hwnd, 0, int(alpha),
                                          int((undo or {}).get("flags") or LWA_ALPHA))
    user32.RedrawWindow(hwnd, None, None, 0x1 | 0x4 | 0x80 | 0x100)


def restore_everything(rescue, log=None) -> int:
    """Put back every window left hidden when Nishro Link last stopped (R6).

    Called at start-up, before anything else may hide a window. A handle that
    belongs to some other window by now is left alone: handles are reused,
    and the record keeps the title so an obvious mismatch can be seen.
    """
    log = log or (lambda *_: None)
    left = rescue.pending()
    if not left:
        return 0
    done = 0
    for key, undo in list(left.items()):
        try:
            hwnd = HWND(int(key))
        except (TypeError, ValueError):
            continue
        if not user32.IsWindow(hwnd):
            continue                     # the program has gone; nothing to put back
        want = (undo or {}).get("title") or ""
        now = title_of(hwnd)
        if want and now and want[:40] != now[:40]:
            log(f"a window left hidden is now called {now!r}, not {want!r} - "
                f"leaving it alone")
            continue
        _unhide(hwnd, undo)
        log(f"put back a window left hidden when Nishro Link stopped: {now!r}")
        done += 1
    rescue.clear()
    return done
