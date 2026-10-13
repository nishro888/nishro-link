"""A plain Windows window that writes down every message it is sent.

Run as a program, by test_window_win.py, in a subprocess of its own. It
exists because the moved-window tests need to know whether a posted click
reached the right window at the right place - and no toolkit can answer
that honestly:

* Tk does not act on posted clicks at all, even when the window is on the
  screen, so a Tk window cannot tell a broken click from a Tk quirk.
* Edge does act on them (measured), but a test must not need a browser
  installed.

So this is Windows' own window, with Windows' own message loop, which is
exactly what every real program is underneath.

    stdin   "report"  -> one JSON line: what has arrived since the last one
            "colour N" -> repaint in a different shade, so a capture changes
            "quit"
"""
import ctypes
import ctypes.wintypes as wt
import json
import sys
import threading

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32")

WM_DESTROY, WM_PAINT, WM_CLOSE = 0x0002, 0x000F, 0x0010
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
WM_APP_COLOUR = 0x8000 + 1
WS_OVERLAPPEDWINDOW, WS_VISIBLE = 0x00CF0000, 0x10000000
CW_USEDEFAULT = -0x80000000
IDC_ARROW = 32512

NAMES = {
    WM_MOUSEMOVE: "move", WM_LBUTTONDOWN: "left-down", WM_LBUTTONUP: "left-up",
    WM_RBUTTONDOWN: "right-down", WM_RBUTTONUP: "right-up",
    WM_MBUTTONDOWN: "middle-down", WM_MBUTTONUP: "middle-up",
    WM_MOUSEWHEEL: "wheel", WM_MOUSEHWHEEL: "hwheel",
    WM_KEYDOWN: "key-down", WM_KEYUP: "key-up", WM_CHAR: "char",
}

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT),
                ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


for fn, args, res in [
    ("DefWindowProcW", [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], ctypes.c_long),
    ("RegisterClassW", [ctypes.c_void_p], wt.WORD),
    ("CreateWindowExW", [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND,
                         wt.HMENU, wt.HINSTANCE, ctypes.c_void_p], wt.HWND),
    ("GetMessageW", [ctypes.c_void_p, wt.HWND, wt.UINT, wt.UINT], wt.BOOL),
    ("TranslateMessage", [ctypes.c_void_p], wt.BOOL),
    ("DispatchMessageW", [ctypes.c_void_p], ctypes.c_long),
    ("PostQuitMessage", [ctypes.c_int], None),
    ("BeginPaint", [wt.HWND, ctypes.c_void_p], wt.HDC),
    ("EndPaint", [wt.HWND, ctypes.c_void_p], wt.BOOL),
    ("FillRect", [wt.HDC, ctypes.c_void_p, wt.HBRUSH], ctypes.c_int),
    ("GetClientRect", [wt.HWND, ctypes.c_void_p], wt.BOOL),
    ("InvalidateRect", [wt.HWND, ctypes.c_void_p, wt.BOOL], wt.BOOL),
    ("LoadCursorW", [wt.HINSTANCE, ctypes.c_void_p], ctypes.c_void_p),
    ("PostMessageW", [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL),
]:
    f = getattr(user32, fn)
    f.argtypes, f.restype = args, res
gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]

seen = []
lock = threading.Lock()
shade = [0x302010]          # BGR, as Windows takes it
handle = [None]


def proc(hwnd, msg, wparam, lparam):
    name = NAMES.get(msg)
    if name:
        with lock:
            if name == "move" and seen and seen[-1][0] == "move":
                seen[-1] = [name, lparam & 0xFFFF, (lparam >> 16) & 0xFFFF]
            elif name in ("wheel", "hwheel"):
                seen.append([name, ctypes.c_short((wparam >> 16) & 0xFFFF).value])
            elif name.startswith("key") or name == "char":
                seen.append([name, int(wparam)])
            else:
                seen.append([name, lparam & 0xFFFF, (lparam >> 16) & 0xFFFF])
    if msg == WM_PAINT:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        rc = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        brush = gdi32.CreateSolidBrush(shade[0])
        user32.FillRect(hdc, ctypes.byref(rc), brush)
        gdi32.DeleteObject(brush)
        user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0
    if msg == WM_APP_COLOUR:
        shade[0] = int(wparam)
        user32.InvalidateRect(hwnd, None, True)
        return 0
    if msg == WM_CLOSE:
        with lock:
            seen.append(["close-asked"])
        user32.PostQuitMessage(0)
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_proc = WNDPROC(proc)


def reader():
    for line in sys.stdin:
        word = line.split()
        if not word:
            continue
        if word[0] == "report":
            with lock:
                out, seen[:] = list(seen), []
            print(json.dumps(out), flush=True)
        elif word[0] == "colour" and handle[0]:
            user32.PostMessageW(handle[0], WM_APP_COLOUR, int(word[1], 16), 0)
        elif word[0] == "quit" and handle[0]:
            user32.PostMessageW(handle[0], WM_CLOSE, 0, 0)


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "NL win32 subject"
    cls = WNDCLASS()
    cls.lpfnWndProc = _proc
    cls.hInstance = kernel32.GetModuleHandleW(None)
    cls.lpszClassName = "NLWin32Subject"
    cls.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
    cls.hbrBackground = gdi32.CreateSolidBrush(0x302010)
    if not user32.RegisterClassW(ctypes.byref(cls)):
        raise SystemExit(f"could not register the window class: "
                         f"{ctypes.get_last_error()}")
    hwnd = user32.CreateWindowExW(0, cls.lpszClassName, title,
                                  WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                  120, 120, 320, 240, None, None,
                                  cls.hInstance, None)
    if not hwnd:
        raise SystemExit(f"could not make the window: {ctypes.get_last_error()}")
    handle[0] = hwnd
    print(json.dumps({"hwnd": int(hwnd)}), flush=True)
    threading.Thread(target=reader, daemon=True).start()
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
