"""The capture the screenshot scripts share: a window as it draws itself."""
import ctypes
import time
from PIL import Image


def settle(w, n=8):
    for _ in range(n):
        w.update()
        time.sleep(0.05)


from ctypes import wintypes  # noqa: E402

U32, G32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
U32.GetParent.argtypes = [wintypes.HWND]
U32.GetParent.restype = wintypes.HWND
U32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
U32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
U32.GetDC.restype = wintypes.HDC
U32.GetDC.argtypes = [wintypes.HWND]
U32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
G32.CreateCompatibleDC.restype = wintypes.HDC
G32.CreateCompatibleDC.argtypes = [wintypes.HDC]
G32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
G32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
G32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
G32.SelectObject.restype = wintypes.HGDIOBJ
G32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
G32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
G32.DeleteDC.argtypes = [wintypes.HDC]


def _render(win):
    """The window as it draws itself (PrintWindow, full content): never
    anything else on the screen - no other window, tooltip or pointer."""
    hwnd = U32.GetParent(win.winfo_id()) or win.winfo_id()
    r = wintypes.RECT()
    U32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    sdc = U32.GetDC(None)
    dc = G32.CreateCompatibleDC(sdc)
    bmp = G32.CreateCompatibleBitmap(sdc, w, h)
    old = G32.SelectObject(dc, bmp)
    U32.PrintWindow(hwnd, dc, 2)                    # PW_RENDERFULLCONTENT

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]
    bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    G32.SelectObject(dc, old)
    G32.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bih), 0)
    G32.DeleteObject(bmp)
    G32.DeleteDC(dc)
    U32.ReleaseDC(None, sdc)
    return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1), (r.left, r.top)


def grab(win, over=()):
    """The inside of a window (no title bar), with any popups `over` it drawn
    where they are; and where the inside is on the screen."""
    settle(win)
    img, (wx, wy) = _render(win)
    x, y = win.winfo_rootx(), win.winfo_rooty()
    for pop in over:
        pimg, (px, py) = _render(pop)
        img.paste(pimg, (px - wx, py - wy))
    cx, cy = x - wx, y - wy
    img = img.crop((cx, cy, cx + win.winfo_width(), cy + win.winfo_height()))
    return img, (x, y)


