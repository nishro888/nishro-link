"""Find the pointer on Windows: a spotlight round it, as PowerToys does.

Every screen darkens except a circle round the pointer, which shrinks onto
it, follows it while it moves, and fades - about a second and a quarter.

One window over all the monitors, of the layered kind: one colour in it (KEY)
is see-through, and everything else is drawn at one transparency, so the dark
screen and the clear circle are two GDI fills. It never takes a click
(WS_EX_TRANSPARENT), the focus (WS_EX_NOACTIVATE) or a taskbar button
(WS_EX_TOOLWINDOW). It lives on its own thread with its own message loop, so
show() - called from wherever a shake is noticed - only posts a message and
returns.

In the service, the desk agent draws it (agent.py): the agent is the part on
the desktop that is showing, the lock screen included.

Every Win32 call has its argument and return types declared. Without that
ctypes passes and returns plain C ints, and a 64-bit handle is cut in half -
the reason the service once hung starting (winsvc.py).
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes as W

KEY = 0x00FF00FF                 # magenta, as COLORREF 0x00BBGGRR: see-through
DARK = 0x00000000
RING = 0x00FFFFFF
SHRINK_S, HOLD_S, FADE_S = 0.35, 0.55, 0.35
ALPHA = 150                      # how dark the rest of the screen goes, of 255
FRAME_MS = 16

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00080000, 0x00000020
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x8, 0x80, 0x08000000
LWA_COLORKEY, LWA_ALPHA = 0x1, 0x2
WM_DESTROY, WM_PAINT, WM_TIMER, WM_ERASEBKGND = 0x0002, 0x000F, 0x0113, 0x0014
WM_APP_SHOW = 0x8000 + 1
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
HWND_TOPMOST = W.HWND(-1)
SM_XV, SM_YV, SM_CXV, SM_CYV = 76, 77, 78, 79
PS_SOLID, NULL_BRUSH, SRCCOPY = 0, 5, 0x00CC0020

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR),
                ("hIconSm", W.HICON)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", W.HDC), ("fErase", W.BOOL), ("rcPaint", W.RECT),
                ("fRestore", W.BOOL), ("fIncUpdate", W.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


def _api():
    u, g, k = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32"), ctypes.WinDLL("kernel32")
    sig = [
        (u.RegisterClassExW, [ctypes.POINTER(WNDCLASSEXW)], W.ATOM),
        (u.CreateWindowExW, [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND,
                             W.HMENU, W.HINSTANCE, W.LPVOID], W.HWND),
        (u.DefWindowProcW, [W.HWND, W.UINT, W.WPARAM, W.LPARAM], LRESULT),
        (u.SetLayeredWindowAttributes, [W.HWND, W.COLORREF, W.BYTE, W.DWORD], W.BOOL),
        (u.ShowWindow, [W.HWND, ctypes.c_int], W.BOOL),
        (u.SetWindowPos, [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, W.UINT], W.BOOL),
        (u.SetTimer, [W.HWND, ctypes.c_size_t, W.UINT, W.LPVOID], ctypes.c_size_t),
        (u.KillTimer, [W.HWND, ctypes.c_size_t], W.BOOL),
        (u.InvalidateRect, [W.HWND, W.LPVOID, W.BOOL], W.BOOL),
        (u.BeginPaint, [W.HWND, ctypes.POINTER(PAINTSTRUCT)], W.HDC),
        (u.EndPaint, [W.HWND, ctypes.POINTER(PAINTSTRUCT)], W.BOOL),
        (u.GetMessageW, [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], W.BOOL),
        (u.TranslateMessage, [ctypes.POINTER(W.MSG)], W.BOOL),
        (u.DispatchMessageW, [ctypes.POINTER(W.MSG)], LRESULT),
        (u.PostMessageW, [W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
        (u.GetCursorPos, [ctypes.POINTER(W.POINT)], W.BOOL),
        (u.GetSystemMetrics, [ctypes.c_int], ctypes.c_int),
        (u.GetDC, [W.HWND], W.HDC),
        (u.ReleaseDC, [W.HWND, W.HDC], ctypes.c_int),
        (u.FillRect, [W.HDC, ctypes.POINTER(W.RECT), W.HBRUSH], ctypes.c_int),
        (g.CreateSolidBrush, [W.COLORREF], W.HBRUSH),
        (g.CreatePen, [ctypes.c_int, ctypes.c_int, W.COLORREF], W.HPEN),
        (g.GetStockObject, [ctypes.c_int], W.HGDIOBJ),
        (g.SelectObject, [W.HDC, W.HGDIOBJ], W.HGDIOBJ),
        (g.DeleteObject, [W.HGDIOBJ], W.BOOL),
        (g.Ellipse, [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int], W.BOOL),
        (g.CreateCompatibleDC, [W.HDC], W.HDC),
        (g.CreateCompatibleBitmap, [W.HDC, ctypes.c_int, ctypes.c_int], W.HBITMAP),
        (g.DeleteDC, [W.HDC], W.BOOL),
        (g.BitBlt, [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    W.HDC, ctypes.c_int, ctypes.c_int, W.DWORD], W.BOOL),
        (k.GetModuleHandleW, [W.LPCWSTR], W.HMODULE),
    ]
    for fn, args, res in sig:
        fn.argtypes, fn.restype = args, res
    return u, g, k


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def frame(elapsed: float, big: int, small: int) -> tuple:
    """(radius, alpha) of the spotlight `elapsed` seconds in, or None when it
    is over. Pure, for the tests: shrink in, hold, fade."""
    if elapsed < SHRINK_S:
        k = _ease_out(elapsed / SHRINK_S)
        return round(big + (small - big) * k), round(ALPHA * min(1.0, k * 1.6))
    if elapsed < SHRINK_S + HOLD_S:
        return small, ALPHA
    if elapsed < SHRINK_S + HOLD_S + FADE_S:
        k = (elapsed - SHRINK_S - HOLD_S) / FADE_S
        return small, round(ALPHA * (1 - k))
    return None


class Spotlight:
    """show() from any thread; the drawing happens on the spotlight's own."""

    def __init__(self):
        self._hwnd = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._t0 = None
        self._geom = None             # (x, y, w, h) of the whole desktop
        self._mem = None              # (dc, bitmap, old) the frame is drawn in
        self._thread = None

    # ------------------------------------------------------------ outside
    def show(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
        if self._ready.wait(2.0) and self._hwnd:
            self._u.PostMessageW(self._hwnd, WM_APP_SHOW, 0, 0)

    # ------------------------------------------------------------- its own
    def _run(self) -> None:
        try:
            self._u, self._g, k = _api()
            try:
                # Physical pixels on every monitor, as the cursor reports them.
                aware = self._u.SetThreadDpiAwarenessContext
                aware.argtypes, aware.restype = [ctypes.c_void_p], ctypes.c_void_p
                aware(ctypes.c_void_p(-4))
            except (AttributeError, OSError):
                pass                          # before Windows 10 1607
            hinst = k.GetModuleHandleW(None)
            self._proc = WNDPROC(self._wndproc)       # kept, or it is collected
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.lpszClassName = "NishroLinkSpotlight"
            self._u.RegisterClassExW(ctypes.byref(wc))
            x, y, w, h = self._desktop()
            self._hwnd = self._u.CreateWindowExW(
                WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
                | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                "NishroLinkSpotlight", "", WS_POPUP, x, y, w, h,
                None, None, hinst, None)
            self._dark = self._g.CreateSolidBrush(DARK)
            self._key = self._g.CreateSolidBrush(KEY)
            self._ring = self._g.CreatePen(PS_SOLID, 4, RING)
        except Exception:
            self._hwnd = None
        finally:
            self._ready.set()
        if not self._hwnd:
            return
        msg = W.MSG()
        while self._u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self._u.TranslateMessage(ctypes.byref(msg))
            self._u.DispatchMessageW(ctypes.byref(msg))

    def _desktop(self) -> tuple:
        m = self._u.GetSystemMetrics
        return m(SM_XV), m(SM_YV), max(1, m(SM_CXV)), max(1, m(SM_CYV))

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_APP_SHOW:
                self._start(hwnd)
                return 0
            if msg == WM_TIMER:
                self._tick(hwnd)
                return 0
            if msg == WM_ERASEBKGND:
                return 1                      # painted whole in WM_PAINT
            if msg == WM_PAINT:
                self._paint(hwnd)
                return 0
        except Exception:
            pass                              # never take the desktop down
        return self._u.DefWindowProcW(hwnd, msg, wp, lp)

    def _start(self, hwnd) -> None:
        running = self._t0 is not None
        geom = self._desktop()
        if geom != self._geom:                # a monitor came or went
            self._geom = geom
            self._free_frame()
            self._u.SetWindowPos(hwnd, HWND_TOPMOST, *geom, SWP_NOACTIVATE)
        if running and time.monotonic() - self._t0 > SHRINK_S:
            # Shaken again while it shows: hold it, rather than start over.
            self._t0 = time.monotonic() - SHRINK_S
            return
        self._t0 = time.monotonic()
        self._u.SetLayeredWindowAttributes(hwnd, KEY, 0, LWA_COLORKEY | LWA_ALPHA)
        self._u.SetWindowPos(hwnd, HWND_TOPMOST, *geom,
                             SWP_NOACTIVATE | SWP_SHOWWINDOW)
        self._u.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        self._u.SetTimer(hwnd, 1, FRAME_MS, None)
        self._u.InvalidateRect(hwnd, None, False)

    def _tick(self, hwnd) -> None:
        f = self._now()
        if f is None:
            self._u.KillTimer(hwnd, 1)
            self._u.ShowWindow(hwnd, SW_HIDE)
            self._t0 = None
            return
        self._u.SetLayeredWindowAttributes(hwnd, KEY, f[1], LWA_COLORKEY | LWA_ALPHA)
        self._u.InvalidateRect(hwnd, None, False)

    def _now(self):
        if self._t0 is None:
            return None
        _, _, w, h = self._geom
        big = max(w, h)
        small = max(60, min(w, h) // 12)
        return frame(time.monotonic() - self._t0, big, small)

    def _paint(self, hwnd) -> None:
        ps = PAINTSTRUCT()
        hdc = self._u.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            f = self._now()
            if f is None or not hdc:
                return
            gx, gy, w, h = self._geom
            if self._mem is None:
                dc = self._g.CreateCompatibleDC(hdc)
                bmp = self._g.CreateCompatibleBitmap(hdc, w, h)
                self._mem = (dc, bmp, self._g.SelectObject(dc, bmp))
            dc = self._mem[0]
            pt = W.POINT()
            self._u.GetCursorPos(ctypes.byref(pt))
            cx, cy, r = pt.x - gx, pt.y - gy, f[0]
            whole = W.RECT(0, 0, w, h)
            self._u.FillRect(dc, ctypes.byref(whole), self._dark)
            old_brush = self._g.SelectObject(dc, self._key)
            old_pen = self._g.SelectObject(dc, self._g.GetStockObject(8))  # NULL_PEN
            self._g.Ellipse(dc, cx - r, cy - r, cx + r, cy + r)
            self._g.SelectObject(dc, self._g.GetStockObject(NULL_BRUSH))
            self._g.SelectObject(dc, self._ring)
            self._g.Ellipse(dc, cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2)
            self._g.SelectObject(dc, old_brush)
            self._g.SelectObject(dc, old_pen)
            self._g.BitBlt(hdc, 0, 0, w, h, dc, 0, 0, SRCCOPY)
        finally:
            self._u.EndPaint(hwnd, ctypes.byref(ps))

    def _free_frame(self) -> None:
        if self._mem is not None:
            dc, bmp, old = self._mem
            self._g.SelectObject(dc, old)
            self._g.DeleteObject(bmp)
            self._g.DeleteDC(dc)
            self._mem = None


_ONE = None


def show() -> None:
    """Show the spotlight round the pointer, now. One per process."""
    global _ONE
    if _ONE is None:
        _ONE = Spotlight()
    _ONE.show()
