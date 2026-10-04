"""The Windows clipboard, through user32 itself - no PowerShell.

Reported: a console window flashed up on every Ctrl+C. Reading the clipboard
ran PowerShell's Get-Clipboard, from the desk agent, which has no console of
its own - so Windows gave PowerShell a window, for a moment, every time; and
again for every paste that arrived. A read cost a fifth of a second, too. These
calls cost microseconds and show nothing.

Setting the clipboard needs a window to own it: a hidden one, made for the
moment and destroyed straight after. Kept, it would be sent a message whenever
another program empties the clipboard - and with no one reading its messages,
that program would hang waiting.

IMAGES travel as PNG. Read: the "PNG" format if a program offered one
(browsers and Office do: it keeps transparency) and this process may have it,
else CF_DIB - the bitmap every image comes as, made into a PNG. Written: both.
The conversions are Windows' own (GDI+): no new dependency, at native speed.

CF_DIB, not CF_BITMAP: the bitmap HANDLE another program put there could not
be converted at all (GDI+ status 7), which is every screenshot; CF_DIB is plain
memory, like text. And to the service's desk agent, which runs as SYSTEM,
Windows lists "PNG" but calls it unavailable - while CF_DIB is there for it.
Both found only by running the real service: reading and writing in one
process, as the tests do, never meets either.
"""
from __future__ import annotations

import ctypes
import functools
import threading
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

u32.OpenClipboard.argtypes = [wintypes.HWND]
u32.OpenClipboard.restype = wintypes.BOOL
u32.CloseClipboard.restype = wintypes.BOOL
u32.EmptyClipboard.restype = wintypes.BOOL
u32.GetClipboardData.argtypes = [wintypes.UINT]
u32.GetClipboardData.restype = wintypes.HANDLE
u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
u32.SetClipboardData.restype = wintypes.HANDLE
u32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
u32.IsClipboardFormatAvailable.restype = wintypes.BOOL
u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
u32.CreateWindowExW.restype = wintypes.HWND
u32.DestroyWindow.argtypes = [wintypes.HWND]
k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
k32.GlobalAlloc.restype = wintypes.HGLOBAL
k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
k32.GlobalFree.restype = wintypes.HGLOBAL
k32.GlobalSize.argtypes = [wintypes.HGLOBAL]
k32.GlobalSize.restype = ctypes.c_size_t


# One clipboard operation at a time in this process. Opening the clipboard
# keeps other PROGRAMS out, not other threads of this one: a thread emptying
# it freed the memory another was still reading - a crash (heap corruption)
# found when several links ran in one process. The agent, too, answers each
# request on a thread of its own.
_LOCK = threading.RLock()


def _serial(fn):
    @functools.wraps(fn)
    def run(*a, **kw):
        with _LOCK:
            return fn(*a, **kw)
    return run


def _open(owner=None, tries: int = 20) -> bool:
    """Open the clipboard - another program may hold it for a moment."""
    for _ in range(tries):
        if u32.OpenClipboard(owner):
            return True
        time.sleep(0.01)
    return False


@_serial
def get_text():
    """The clipboard's text; "" when it holds none; None if it could not be
    opened (another program kept it)."""
    if not _open():
        return None
    try:
        if not u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k32.GlobalLock(h)
        if not p:
            return ""
        try:
            # Bounded by the block's size, not only by the first NUL.
            n = k32.GlobalSize(h) // 2
            return ctypes.wstring_at(p, n).split("\0", 1)[0]
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


@_serial
def set_text(text: str) -> bool:
    data = text.encode("utf-16-le") + b"\0\0"
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return False
    p = k32.GlobalLock(h)
    if not p:
        k32.GlobalFree(h)
        return False
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    owner = u32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
    try:
        if not _open(owner):
            k32.GlobalFree(h)
            return False
        try:
            u32.EmptyClipboard()
            if not u32.SetClipboardData(CF_UNICODETEXT, h):
                k32.GlobalFree(h)
                return False
            return True                   # the clipboard owns the memory now
        finally:
            u32.CloseClipboard()
    finally:
        if owner:
            u32.DestroyWindow(owner)


# ------------------------------------------------------------------ images
_PNG_FORMAT = None


def _png_format() -> int:
    global _PNG_FORMAT
    if _PNG_FORMAT is None:
        u32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        u32.RegisterClipboardFormatW.restype = wintypes.UINT
        _PNG_FORMAT = u32.RegisterClipboardFormatW("PNG")
    return _PNG_FORMAT


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


_PNG_ENCODER = _GUID(0x557CF406, 0x1A04, 0x11D3,
                     (ctypes.c_ubyte * 8)(0x9A, 0x73, 0x00, 0x00, 0xF8, 0x1E, 0xF3, 0x2E))


class _StartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", wintypes.BOOL),
                ("SuppressExternalCodecs", wintypes.BOOL)]


_gdip = None


def _gdiplus():
    """GDI+, started once."""
    global _gdip
    if _gdip is None:
        g = ctypes.WinDLL("gdiplus")
        g.GdiplusStartup.argtypes = [ctypes.POINTER(ctypes.c_size_t),
                                     ctypes.POINTER(_StartupInput), ctypes.c_void_p]
        g.GdipCreateBitmapFromStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        g.GdipCreateBitmapFromHBITMAP.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                                  ctypes.POINTER(ctypes.c_void_p)]
        g.GdipCreateHBITMAPFromBitmap.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE),
                                                  ctypes.c_uint32]
        g.GdipSaveImageToStream.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.POINTER(_GUID), ctypes.c_void_p]
        g.GdipDisposeImage.argtypes = [ctypes.c_void_p]
        g.GdipCreateBitmapFromGdiDib.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                 ctypes.POINTER(ctypes.c_void_p)]
        token = ctypes.c_size_t()
        st = g.GdiplusStartup(ctypes.byref(token), ctypes.byref(_StartupInput(1, None, False, False)),
                              None)
        if st != 0:
            raise OSError(f"GDI+ did not start ({st})")
        _gdip = g
    return _gdip


def _ole():
    ole = ctypes.WinDLL("ole32")
    ole.CreateStreamOnHGlobal.argtypes = [wintypes.HGLOBAL, wintypes.BOOL,
                                          ctypes.POINTER(ctypes.c_void_p)]
    ole.GetHGlobalFromStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.HGLOBAL)]
    return ole


def _shlwapi():
    sh = ctypes.WinDLL("shlwapi")
    sh.SHCreateMemStream.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    sh.SHCreateMemStream.restype = ctypes.c_void_p
    return sh


def _vcall(obj, index, restype, *args):
    """Call method `index` of a COM object's vtable."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *[a[0] for a in args])(vtbl[index])
    return fn(obj, *[a[1] for a in args])


def _release(obj) -> None:
    if obj:
        _vcall(obj, 2, ctypes.c_ulong)                # IUnknown::Release


def _bitmap_to_png(bmp) -> bytes:
    """A GDI+ bitmap, saved as PNG."""
    g, ole = _gdiplus(), _ole()
    stream = ctypes.c_void_p()
    if ole.CreateStreamOnHGlobal(None, True, ctypes.byref(stream)) != 0:
        raise OSError("no stream")
    try:
        st = g.GdipSaveImageToStream(bmp, stream, ctypes.byref(_PNG_ENCODER), None)
        if st != 0:
            raise OSError(f"PNG encoding failed ({st})")
        end = ctypes.c_uint64()
        _vcall(stream, 5, ctypes.c_long,                # IStream::Seek(0, END)
               (ctypes.c_int64, 0), (ctypes.c_ulong, 2),
               (ctypes.POINTER(ctypes.c_uint64), ctypes.byref(end)))
        hg = wintypes.HGLOBAL()
        ole.GetHGlobalFromStream(stream, ctypes.byref(hg))
        p = k32.GlobalLock(hg)
        try:
            return ctypes.string_at(p, end.value)
        finally:
            k32.GlobalUnlock(hg)
    finally:
        _release(stream)


def _trim_png(data: bytes) -> bytes:
    """A block from the clipboard can be larger than the PNG in it."""
    end = data.find(b"IEND")
    return data[:end + 8] if end >= 0 else data


CF_DIB = 8
_BI_BITFIELDS = 3


def _read(fmt):
    """The bytes of one clipboard format, or None. The clipboard is open."""
    if not u32.IsClipboardFormatAvailable(fmt):
        return None
    h = u32.GetClipboardData(fmt)
    p = k32.GlobalLock(h) if h else None
    if not p:
        return None
    try:
        return ctypes.string_at(p, k32.GlobalSize(h))
    finally:
        k32.GlobalUnlock(h)


@_serial
def get_image():
    """The clipboard's image as PNG bytes, or None when it holds none."""
    if not _open():
        return None
    try:
        png = _read(_png_format())
        if png and png.startswith(b"\x89PNG"):
            return _trim_png(png)
        dib = _read(CF_DIB)
    finally:
        u32.CloseClipboard()           # encoding after: nobody waits on it
    return _dib_to_png(dib) if dib else None


def _dib_to_png(dib: bytes):
    """A packed DIB (header, masks, colour table, pixels) as PNG."""
    import struct
    if len(dib) < 40:
        return None
    size, _w, _h, _planes, bpp, comp, _img, _x, _y, used, _imp = \
        struct.unpack_from("<IiiHHIIiiII", dib)
    offset = size
    if comp == _BI_BITFIELDS and size == 40:
        offset += 12                     # the three colour masks
    if bpp <= 8:
        offset += (used or (1 << bpp)) * 4
    elif used:
        offset += used * 4
    if offset >= len(dib):
        return None
    g = _gdiplus()
    buf = ctypes.create_string_buffer(dib, len(dib))     # alive until saved:
    bmp = ctypes.c_void_p()                              # GDI+ may read it late
    if g.GdipCreateBitmapFromGdiDib(ctypes.addressof(buf),
                                    ctypes.addressof(buf) + offset,
                                    ctypes.byref(bmp)) != 0:
        return None
    try:
        return _bitmap_to_png(bmp)
    finally:
        g.GdipDisposeImage(bmp)


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _png_to_dib(png: bytes):
    """A PNG as a packed 32-bit DIB, made in this process (so the bitmap
    handle on the way is ours), transparent parts on white."""
    g = _gdiplus()
    gdi = ctypes.WinDLL("gdi32")
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HANDLE, wintypes.UINT, wintypes.UINT,
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    u32.GetDC.argtypes = [wintypes.HWND]
    u32.GetDC.restype = wintypes.HDC
    u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    g.GdipGetImageWidth.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    g.GdipGetImageHeight.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    stream = _shlwapi().SHCreateMemStream(png, len(png))
    if not stream:
        return None
    bmp, hbm = ctypes.c_void_p(), wintypes.HANDLE()
    w, h = ctypes.c_uint(), ctypes.c_uint()
    try:
        if g.GdipCreateBitmapFromStream(stream, ctypes.byref(bmp)) != 0:
            return None
        g.GdipGetImageWidth(bmp, ctypes.byref(w))
        g.GdipGetImageHeight(bmp, ctypes.byref(h))
        st = g.GdipCreateHBITMAPFromBitmap(bmp, ctypes.byref(hbm), 0xFFFFFFFF)
        g.GdipDisposeImage(bmp)
        if st != 0 or not hbm:
            return None
    finally:
        _release(stream)
    try:
        bi = _BITMAPINFOHEADER(40, w.value, h.value, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.create_string_buffer(w.value * h.value * 4)
        dc = u32.GetDC(None)
        try:
            lines = gdi.GetDIBits(dc, hbm, 0, h.value, bits, ctypes.byref(bi), 0)
        finally:
            u32.ReleaseDC(None, dc)
        if lines != h.value:
            return None
        bi.biSizeImage = len(bits.raw)
        return bytes(bi) + bits.raw
    finally:
        gdi.DeleteObject(hbm)


def _global(data: bytes):
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    p = k32.GlobalLock(h) if h else None
    if not p:
        if h:
            k32.GlobalFree(h)
        return None
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    return h


@_serial
def set_image(png: bytes) -> bool:
    """Put a PNG on the clipboard: as itself, and as CF_DIB for the rest -
    Windows makes every other bitmap form from that."""
    dib = _png_to_dib(png)
    if not dib:
        return False
    hpng, hdib = _global(png), _global(dib)
    if not hdib:
        if hpng:
            k32.GlobalFree(hpng)
        return False
    owner = u32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
    try:
        if not _open(owner):
            for h in (hpng, hdib):
                if h:
                    k32.GlobalFree(h)
            return False
        try:
            u32.EmptyClipboard()
            if hpng and not u32.SetClipboardData(_png_format(), hpng):
                k32.GlobalFree(hpng)
            if not u32.SetClipboardData(CF_DIB, hdib):
                k32.GlobalFree(hdib)
                return False
            return True                       # the clipboard owns them now
        finally:
            u32.CloseClipboard()
    finally:
        if owner:
            u32.DestroyWindow(owner)
