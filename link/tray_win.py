"""The Windows tray icon: Shell_NotifyIcon through ctypes.

No new dependency, nothing bigger in the installer: a hidden window receives
the icon's clicks, a popup menu is built from tray.menu()'s data each time it
opens, and Windows shows notifications as toasts. Everything that touches the
icon happens on this window's thread; other threads post it a message.

Left-click (or double-click) opens the window; right-click shows the menu. If
Explorer restarts, it broadcasts "TaskbarCreated" and the icon is added again.
"""
from __future__ import annotations

import base64
import ctypes
import threading
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
shell = ctypes.WinDLL("shell32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                             wintypes.LPARAM)

WM_DESTROY, WM_CLOSE, WM_NULL, WM_COMMAND = 0x0002, 0x0010, 0x0000, 0x0111
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
WM_APP = 0x8000
CALLBACK, APPLY, NOTIFY = WM_APP + 1, WM_APP + 2, WM_APP + 3
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
MF_STRING, MF_GRAYED, MF_CHECKED, MF_POPUP, MF_SEPARATOR = 0x0, 0x1, 0x8, 0x10, 0x800
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x0002, 0x0080, 0x0100
SM_CXSMICON = 49


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wintypes.HICON)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def _types() -> None:
    u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                   wintypes.LPARAM]
    u32.DefWindowProcW.restype = LRESULT
    u32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    u32.RegisterClassW.restype = wintypes.ATOM
    u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                    wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    u32.CreateWindowExW.restype = wintypes.HWND
    u32.DestroyWindow.argtypes = [wintypes.HWND]
    u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                 wintypes.LPARAM]
    u32.PostQuitMessage.argtypes = [ctypes.c_int]
    u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT]
    u32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    u32.RegisterWindowMessageW.restype = wintypes.UINT
    u32.CreatePopupMenu.restype = wintypes.HMENU
    u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                                wintypes.LPCWSTR]
    u32.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
    u32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
    u32.TrackPopupMenu.restype = wintypes.BOOL
    u32.DestroyMenu.argtypes = [wintypes.HMENU]
    u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    u32.GetSystemMetrics.argtypes = [ctypes.c_int]
    u32.CreateIconFromResourceEx.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL,
                                             wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                             wintypes.UINT]
    u32.CreateIconFromResourceEx.restype = wintypes.HICON
    u32.DestroyIcon.argtypes = [wintypes.HICON]
    shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell.Shell_NotifyIconW.restype = wintypes.BOOL
    k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    k32.GetModuleHandleW.restype = wintypes.HMODULE


def build_menu(items, ids: dict):
    """A popup menu from tray.menu()'s data. `ids` collects command id ->
    action. Returns (the menu, the default item's id or 0)."""
    h = u32.CreatePopupMenu()
    default = 0
    for it in items:
        kind = it["kind"]
        if kind == "sep":
            u32.AppendMenuW(h, MF_SEPARATOR, 0, None)
            continue
        flags = MF_STRING | (0 if it.get("enabled", True) else MF_GRAYED)
        if kind == "sub":
            sub, _ = build_menu(it["items"], ids)
            u32.AppendMenuW(h, flags | MF_POPUP, sub, it["label"])
            continue
        if kind == "check" and it.get("checked"):
            flags |= MF_CHECKED
        n = 1000 + len(ids)
        ids[n] = it.get("action")
        u32.AppendMenuW(h, flags, n, it["label"])
        if it.get("default"):
            default = n
    return h, default


class WinTray:
    def __init__(self, log=print):
        _types()
        self.log = log
        self.on_action = lambda action: None
        self._state, self._menu = None, []
        self._notes = []
        self._lock = threading.Lock()
        self._icons = {}
        self._proc = WNDPROC(self._wndproc)       # kept: Windows calls it
        # Before the window exists: Windows calls _wndproc while creating it.
        self._taskbar_created = u32.RegisterWindowMessageW("TaskbarCreated")
        self._added = False
        inst = k32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=inst,
                       lpszClassName="NishroLinkTray")
        u32.RegisterClassW(ctypes.byref(wc))
        # A hidden top-level window, not a message-only one: a popup menu
        # closes when its owner loses the foreground, and only a real window
        # can own it.
        self.hwnd = u32.CreateWindowExW(0, "NishroLinkTray", "Nishro Link", 0, 0, 0,
                                        0, 0, None, None, inst, None)
        if not self.hwnd:
            raise OSError(f"could not create the tray's window ({ctypes.get_last_error()})")

    # ---- icons
    def _icon(self, state: str):
        if state not in self._icons:
            from .icon import TRAY
            want = u32.GetSystemMetrics(SM_CXSMICON) or 16
            sizes = sorted(TRAY[state])
            n = next((s for s in sizes if s >= want), sizes[-1])
            png = base64.b64decode(TRAY[state][n])
            buf = ctypes.create_string_buffer(png, len(png))
            self._icons[state] = u32.CreateIconFromResourceEx(buf, len(png), True,
                                                              0x00030000, n, n, 0)
        return self._icons[state]

    def _data(self, flags) -> NOTIFYICONDATAW:
        d = NOTIFYICONDATAW()
        d.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        d.hWnd, d.uID, d.uFlags = self.hwnd, 1, flags
        return d

    def _apply(self) -> None:
        with self._lock:
            st = self._state
        if st is None:
            return
        d = self._data(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        d.uCallbackMessage = CALLBACK
        d.hIcon = self._icon(st["icon"])
        d.szTip = st["tooltip"][:127]
        if not self._added:
            self._added = bool(shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(d)))
            if not self._added:
                self.log("tray: Windows did not add the icon (no notification area?)")
        else:
            shell.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(d))

    def _notify_now(self) -> None:
        with self._lock:
            notes, self._notes = self._notes, []
        for title, text in notes:
            d = self._data(NIF_INFO)
            d.szInfoTitle, d.szInfo = title[:63], text[:255]
            d.dwInfoFlags = NIIF_INFO
            shell.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(d))

    def _popup(self) -> None:
        with self._lock:
            items = list(self._menu)
        ids = {}
        h, default = build_menu(items, ids)
        if default:
            u32.SetMenuDefaultItem(h, default, False)
        pt = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.SetForegroundWindow(self.hwnd)      # or the menu never closes
        n = u32.TrackPopupMenu(h, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
                               pt.x, pt.y, 0, self.hwnd, None)
        u32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        u32.DestroyMenu(h)
        if n and ids.get(n):
            self.on_action(ids[n])

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == CALLBACK:
                if lparam == WM_RBUTTONUP:
                    self._popup()
                elif lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.on_action("open")
                return 0
            if msg == APPLY:
                self._apply()
                return 0
            if msg == NOTIFY:
                self._notify_now()
                return 0
            if msg == self._taskbar_created:        # Explorer restarted
                self._added = False
                self._apply()
                return 0
            if msg == WM_CLOSE:
                if self._added:
                    shell.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data(0)))
                    self._added = False
                u32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                u32.PostQuitMessage(0)
                return 0
        except Exception as e:                       # never out through Windows,
            self.log(f"tray: {e!r}")                  # and never refuse a message:
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)   # 0 aborts creation

    # ---- what tray.Tray calls: safe from any thread
    def show(self, st, items) -> None:
        with self._lock:
            self._state, self._menu = st, items
        u32.PostMessageW(self.hwnd, APPLY, 0, 0)

    def notify(self, title, text) -> None:
        with self._lock:
            self._notes.append((title, text))
        u32.PostMessageW(self.hwnd, NOTIFY, 0, 0)

    def quit(self) -> None:
        u32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def run(self) -> None:
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        for h in self._icons.values():
            u32.DestroyIcon(h)


def make(log=print):
    try:
        return WinTray(log)
    except Exception as e:
        log(f"tray: could not start ({e!r})")
        return None
