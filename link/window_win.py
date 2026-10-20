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
import threading
import time

from . import keymap

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi")
except OSError:                        # no desktop composition: nothing is cloaked
    dwmapi = None
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
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
MK_SHIFT, MK_CONTROL = 0x0004, 0x0008
CWP_SKIPTRANSPARENT = 0x0004
VK_SHIFT, VK_CONTROL, VK_MENU, VK_CAPITAL = 0x10, 0x11, 0x12, 0x14
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL = 0xA0, 0xA1, 0xA2, 0xA3
VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN = 0xA4, 0xA5, 0x5B, 0x5C
# Modifiers, as the evdev codes the far side sends: which side's key it is,
# and the key Windows reports for either side (the Windows keys have none).
_SIDED = {42: VK_LSHIFT, 54: VK_RSHIFT, 29: VK_LCONTROL, 97: VK_RCONTROL,
          56: VK_LMENU, 100: VK_RMENU, 125: VK_LWIN, 126: VK_RWIN}
_GENERIC = {VK_LSHIFT: VK_SHIFT, VK_RSHIFT: VK_SHIFT, VK_LCONTROL: VK_CONTROL,
            VK_RCONTROL: VK_CONTROL, VK_LMENU: VK_MENU, VK_RMENU: VK_MENU}
_CAPSLOCK = 58
# Keys Windows marks "extended" (bit 24): which of two keys with one scan
# code it was - the arrows and not the keypad's, the right Ctrl and Alt.
_EXTENDED = {VK_RCONTROL, VK_RMENU, VK_LWIN, VK_RWIN, 0x21, 0x22, 0x23, 0x24,
             0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x6F, 0x90, 0x2C, 0x5D}
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
        ("SetPropW", [HWND, wt.LPCWSTR, wt.HANDLE], wt.BOOL),
        ("GetPropW", [HWND, wt.LPCWSTR], wt.HANDLE),
        ("ChildWindowFromPointEx", [HWND, POINT, wt.UINT], HWND),
        ("AttachThreadInput", [wt.DWORD, wt.DWORD, wt.BOOL], wt.BOOL),
        ("GetKeyboardState", [ctypes.c_void_p], wt.BOOL),
        ("SetKeyboardState", [ctypes.c_void_p], wt.BOOL),
        ("MapVirtualKeyW", [wt.UINT, wt.UINT], wt.UINT),
        ("PeekMessageW", [ctypes.c_void_p, HWND, wt.UINT, wt.UINT, wt.UINT], wt.BOOL),
    ]:
        fn = getattr(user32, name)
        fn.argtypes, fn.restype = args, res
    for name, args, res in [
        ("OpenProcess", [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        ("QueryFullProcessImageNameW", [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                        ctypes.POINTER(wt.DWORD)], wt.BOOL),
        ("CloseHandle", [wt.HANDLE], wt.BOOL),
        ("GetCurrentThreadId", [], wt.DWORD),
    ]:
        fn = getattr(kernel32, name)
        fn.argtypes, fn.restype = args, res
    if dwmapi is not None:
        dwmapi.DwmGetWindowAttribute.argtypes = [HWND, wt.DWORD, ctypes.c_void_p,
                                                 wt.DWORD]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
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


# Every frame - a window from another computer, shown here - carries this
# property on its top-level window (window_frame sets it). Properties are
# readable from any process, so the tray, in a process of its own, can tell
# a frame from a window of this computer's - and never move one: that would
# show another computer's window back to it, or on to a third, in a frame of
# a frame.
FRAME_PROP = "NishroLink.Frame"


def mark_frame(hwnd) -> bool:
    """Say of a window that it is one of Nishro Link's frames."""
    return bool(user32.SetPropW(handle(hwnd), FRAME_PROP, wt.HANDLE(1)))


def is_frame(hwnd) -> bool:
    return bool(user32.GetPropW(handle(hwnd), FRAME_PROP))


def _program_of(hwnd) -> str:
    """The program a window belongs to, as its path - "" where Windows will
    not say (a process of a higher rank than ours)."""
    proc = kernel32.OpenProcess(0x1000, False, pid_of(hwnd))  # QUERY_LIMITED_INFORMATION
    if not proc:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(proc)


def _cloaked(hwnd) -> bool:
    """On another virtual desktop, or a Store app put away: Windows keeps it
    "visible", but nobody can see it."""
    if dwmapi is None:
        return False
    value = wt.DWORD(0)
    got = dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value),  # DWMWA_CLOAKED
                                       ctypes.sizeof(value))
    return got == 0 and value.value != 0


def hidden_by_us(hwnd) -> bool:
    """Is this a window Nishro Link has hidden - one that is on another
    screen right now? Its look says so: layered at alpha 1 and never to be
    activated, which is how hide() leaves it and how no program draws itself
    (the click-through bit is not part of it: it is lifted for a moment
    around a turn of the wheel)."""
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if not (ex & WS_EX_LAYERED and ex & WS_EX_NOACTIVATE):
        return False
    alpha, flags = wt.BYTE(255), wt.DWORD(0)
    if not user32.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(alpha),
                                             ctypes.byref(flags)):
        return False
    return bool(flags.value & LWA_ALPHA) and alpha.value == 1


def why_not(hwnd, own_pid: int = None) -> str:
    """Why this window cannot be moved, or "" if it can (R9)."""
    hwnd = handle(hwnd)
    if not hwnd or not user32.IsWindow(hwnd):
        return "that window has gone"
    if not user32.IsWindowVisible(hwnd):
        return "that window is not on the screen"
    if user32.IsIconic(hwnd):
        return "that window is minimized - a minimized window stops drawing"
    if hidden_by_us(hwnd):
        # Hidden, it is still a visible window to Windows, at the top of the
        # stack - so "the window in front" found it. Moved again, its hidden
        # look would be written down as the one to put back.
        return "that window is already on another screen"
    if is_frame(hwnd):
        return ("that is a window from another computer, shown here - it can "
                "only go back there")
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
    if _program_of(hwnd).lower().endswith("\\nishrolink.exe"):
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


_NOT_IN_FRONT = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
                 "NotifyIconOverflowWindow", "#32768")


def _seen(hwnd) -> bool:
    """Could a person be looking at this window? Not if Windows hides it,
    it is minimized or cloaked, Nishro Link has hidden it (a moved window
    sits at the very top of the stack, invisible), or it is the taskbar, a
    menu, a toolbar, or something a click goes through."""
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return False
    if user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_CHILD:
        return False
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if ex & (WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE):
        return False
    if class_of(hwnd) in _NOT_IN_FRONT or not title_of(hwnd).strip():
        return False
    r = rect_of(hwnd)
    return bool(r and r[2] > 0 and r[3] > 0) and not _cloaked(hwnd)


def in_front(own_pid: int = None, order=None):
    """The window the person has in front: (handle, title) if it can be
    moved, else (None, why not) - or (None, "") if there is none at all.

    It is the first window down the stack a person can see (_seen). If THAT
    one cannot be moved - a frame showing another computer's window, one of
    Nishro Link's own - there is nothing to move: what was done before, the
    first window down the stack that COULD be moved, offered the one
    underneath, which nobody had in mind. `order` is for the tests.
    """
    if order is None:
        order = []
        cb_t = ctypes.WINFUNCTYPE(wt.BOOL, HWND, wt.LPARAM)

        def each(h, _):
            order.append(h)
            return True
        user32.EnumWindows(cb_t(each), 0)
    for hwnd in order:
        hwnd = handle(hwnd)
        if not _seen(hwnd):
            continue
        why = why_not(hwnd, own_pid)
        return (hwnd, title_of(hwnd)) if not why else (None, why)
    return None, ""


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
        self._mods = set()             # modifiers held over there, by side
        self._caps = False             # Caps Lock, as the far side has it
        self._attached = None          # (our thread, its thread) while held
        self._keys_lock = threading.Lock()
        self._told_unattached = False
        self._wheel_lock = threading.Lock()
        self._lifted = False           # the click-through bit is off, for a wheel
        self._clickable_until = 0.0

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
        if hidden_by_us(self.hwnd):
            # Another session has it: hiding it again would record its hidden
            # look as how to put it back, and it would never be seen again.
            self.log("that window is already on another screen")
            return False
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
        with self._wheel_lock:
            self._clickable_until = 0.0     # the lifting thread stands down
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
            if user32.GetWindowLongW(child, GWL_EXSTYLE) & WS_EX_TRANSPARENT:
                # Click-through, to Windows' own aim: a real click goes to
                # what is under it, and so must ours. Measured: a click on
                # Edge's tab strip was aimed at its drawing-only
                # "Intermediate D3D Window", which takes no clicks at all,
                # and no tab could be chosen in a moved browser.
                under = user32.ChildWindowFromPointEx(hwnd, p, CWP_SKIPTRANSPARENT)
                if not under or under in (hwnd, child):
                    break
                child = under
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
        user32.PostMessageW(hwnd, WM_MOUSEMOVE, self._held | self._mk(), _lp(cx, cy))

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
                            self._held | self._mk(), _lp(cx, cy))

    # How long the window stays hit-testable after a turn of the wheel.
    # Measured: 20 ms is enough for the program to act on the message; a
    # quarter of a second means a flick of the wheel is not a flurry of
    # style changes.
    WHEEL_CLICKABLE_S = 0.25

    def _clickable_for_a_moment(self) -> None:
        """Lift the click-through bit, briefly, so a wheel message lands.

        Reported: scrolling did not work in a moved window. Measured: of
        everything about the way a window is hidden, WS_EX_TRANSPARENT alone
        is what stops it - clicks and keys arrive without it, the wheel does
        not, because a program asked to scroll looks at what is under the
        pointer and a click-through window is not there to be found.

        So the bit comes off around a turn of the wheel and goes back a
        quarter of a second after the last one. The window is hit-testable
        for that moment, which means a click on THIS computer in that exact
        place would land on a window nobody can see - and the person is
        scrolling the window on the other computer just then, so their
        pointer is there and not here.
        """
        if not self.hidden:
            return
        with self._wheel_lock:
            self._clickable_until = time.monotonic() + self.WHEEL_CLICKABLE_S
            if self._lifted:
                return
            ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
            if not ex & WS_EX_TRANSPARENT:
                return                      # not hidden that way after all
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex & ~WS_EX_TRANSPARENT)
            self._lifted = True
            threading.Thread(target=self._put_the_bit_back, daemon=True,
                             name="window-clickthrough").start()

    def _put_the_bit_back(self) -> None:
        while True:
            with self._wheel_lock:
                left = self._clickable_until - time.monotonic()
                if left <= 0 or not self.hidden:
                    try:
                        ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
                        if self.hidden and user32.IsWindow(self.hwnd):
                            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                                                  ex | WS_EX_TRANSPARENT)
                    except Exception:
                        pass
                    self._lifted = False
                    return
            time.sleep(min(left, 0.1))

    def wheel(self, dx, dy) -> None:
        x, y = getattr(self, "_at", (0, 0))
        self._clickable_for_a_moment()
        hwnd, _, _ = self._aim(x, y)
        r = rect_of(self.hwnd) or (0, 0, 0, 0)
        # The wheel is the one message whose position is in SCREEN
        # coordinates, not the window's.
        screen = _lp(r[0] + int(x), r[1] + int(y))
        flags = self._held | self._mk()
        if dy:
            user32.PostMessageW(hwnd, WM_MOUSEWHEEL,
                                ((int(dy) * WHEEL_NOTCH) & 0xFFFF) << 16 | flags, screen)
        if dx:
            user32.PostMessageW(hwnd, WM_MOUSEHWHEEL,
                                ((int(dx) * WHEEL_NOTCH) & 0xFFFF) << 16 | flags, screen)

    # How long a released modifier stays held in the window's keyboard
    # state, so that the keys sent while it was held are handled with it.
    # Measured: Shift held, 'h' 'i', Shift let go, 'j' gave "HIj".
    SETTLE_S = 0.04

    def key(self, code, down) -> None:
        """A key, as the evdev code Nishro Link speaks everywhere.

        Reported: Ctrl+A in a moved Notepad typed "a", and Shift gave no
        capitals. A posted WM_KEYDOWN for Ctrl changes nothing a program can
        see: shortcuts ask GetKeyState, capitals come from TranslateMessage,
        and both read the program's KEYBOARD STATE, which only real input
        sets. So the modifiers the person holds over there are held in that
        state too (_sync) for as long as they are held - measured: Ctrl held,
        'a' selected everything; Shift held, 'h' 'i' gave "HI".
        """
        vk = keymap.evdev_to_vk(code)
        if not vk:
            return
        with self._keys_lock:
            side = _SIDED.get(code)
            if side is not None:
                self._modifier(side, bool(down))
                return
            if code == _CAPSLOCK and down:
                if self._caps:
                    time.sleep(self.SETTLE_S)   # as a modifier let go: what
                    # was typed with it on is handled with it on
                self._caps = not self._caps
                self._sync()
            target = self._focus()
            again = down and vk in self._down
            if down:
                self._down.add(vk)
            else:
                self._down.discard(vk)
            # With Alt held, the way Windows sends it: a system key, so that
            # Alt+F4 closes and a program's Alt shortcuts work.
            alt = bool(self._mods & {VK_LMENU, VK_RMENU})
            msg = ((WM_SYSKEYDOWN if down else WM_SYSKEYUP) if alt
                   else (WM_KEYDOWN if down else WM_KEYUP))
            user32.PostMessageW(target, msg, vk, _key_lp(vk, down, alt, again))

    def _modifier(self, side, down) -> None:
        """A modifier pressed or let go over there. The modifier's own key
        goes to the window as well, as Windows' generic key - Shift, Ctrl,
        Alt - which is what programs are sent. Alt alone is sent as an
        ordinary key: as a system key, a tap of it would put the hidden
        window's menu bar in charge of every key that followed."""
        if down:
            fresh = side not in self._mods
            self._mods.add(side)
            if fresh:
                self._sync()
        elif side in self._mods:
            time.sleep(self.SETTLE_S)      # what was sent with it is handled first
            self._mods.discard(side)
            self._sync()
        else:
            return                         # a release of something never pressed
        user32.PostMessageW(self._focus(), WM_KEYDOWN if down else WM_KEYUP,
                            _GENERIC.get(side, side), _key_lp(side, down))

    def _sync(self) -> None:
        """Make the window's thread see the modifiers (and Caps Lock) held
        over there - by sharing its input state (AttachThreadInput) for as
        long as any is held, and setting the keys in it. The state does not
        outlive the sharing (measured), so it lasts exactly that long."""
        want = bool(self._mods) or self._caps
        if want and self._attached is None:
            me = kernel32.GetCurrentThreadId()
            them = user32.GetWindowThreadProcessId(self.hwnd, None)
            msg = (ctypes.c_byte * 64)()
            user32.PeekMessageW(msg, None, 0, 0, 0)    # a queue of our own first
            if them and them != me and user32.AttachThreadInput(me, them, True):
                self._attached = (me, them)
            elif not self._told_unattached:
                self._told_unattached = True
                self.log("Ctrl, Shift and Alt from the other computer cannot be "
                         "held in this window: it would not share its input")
        if self._attached is not None:
            state = (ctypes.c_ubyte * 256)()
            user32.GetKeyboardState(state)
            for vk in _SIDED.values():
                state[vk] = 0x80 if vk in self._mods else 0
            for generic in (VK_SHIFT, VK_CONTROL, VK_MENU):
                held = any(_GENERIC.get(m) == generic for m in self._mods)
                state[generic] = 0x80 if held else 0
            state[VK_CAPITAL] = 1 if self._caps else 0
            user32.SetKeyboardState(state)
            if not want:
                self._detach()

    def _detach(self) -> None:
        if self._attached is not None:
            me, them = self._attached
            self._attached = None
            try:
                user32.AttachThreadInput(me, them, False)   # any thread may
            except Exception:
                pass

    def _mk(self) -> int:
        """The MK_ flags a mouse message carries for the modifiers held."""
        flags = 0
        if self._mods & {VK_LSHIFT, VK_RSHIFT}:
            flags |= MK_SHIFT
        if self._mods & {VK_LCONTROL, VK_RCONTROL}:
            flags |= MK_CONTROL
        return flags

    def _let_go(self) -> None:
        """Nothing may be left held down when a window comes home - a stuck
        Ctrl or mouse button is the kind of thing the person cannot undo."""
        with self._keys_lock:
            for vk in list(self._down):
                try:
                    user32.PostMessageW(self._focus(), WM_KEYUP, vk,
                                        _key_lp(vk, False))
                except Exception:
                    pass
            self._down.clear()
            for side in list(self._mods):
                try:
                    user32.PostMessageW(self._focus(), WM_KEYUP,
                                        _GENERIC.get(side, side),
                                        _key_lp(side, False))
                except Exception:
                    pass
            self._mods.clear()
            self._caps = False
            self._detach()                 # and with it, the state goes
        for name, (_, up, bit) in _BUTTON_MSGS.items():
            if self._held & bit:
                x, y = getattr(self, "_at", (0, 0))
                hwnd, cx, cy = self._aim(x, y)
                self._held &= ~bit
                user32.PostMessageW(hwnd, up, self._held, _lp(cx, cy))


def _key_lp(vk, down, alt=False, again=False) -> int:
    """A key message's lParam, as Windows fills it in: the scan code (web
    pages tell keys apart by it), extended or not, Alt held, and whether the
    key was already down (a repeat) or is going up."""
    lp = 1 | ((user32.MapVirtualKeyW(vk, 0) & 0xFF) << 16)
    if vk in _EXTENDED:
        lp |= 1 << 24
    if alt:
        lp |= 1 << 29
    if not down:
        lp |= (1 << 30) | (1 << 31)
    elif again:
        lp |= 1 << 30
    return lp


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
