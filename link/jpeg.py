"""JPEG through each system's own codec, for pictures that are not text.

PNG (picture.py) keeps text sharp and costs nothing to install, and for the
usual case - a few small squares of a document changing - it is under a
millisecond. A window whose whole picture moves is another matter. Measured
on the laptop, a real browser frame of moving colour, 1000x700:

    PNG, picture.encode          63 ms     299 KB
    JPEG q75, GDI+               5.8-8.0 ms 52 KB   (decode 8.1-9.3 ms)

Ten times faster and a fifth of the size - and at 2.4 GHz Wi-Fi speeds the
bytes are most of the lag. So pictures that are not text-like travel as
JPEG, where both ends can do it, and text stays PNG (picture.choose).

No new dependency: each system's own codec, the way picture.py writes PNG
with zlib. GDI+'s flat API on Windows (gdiplus.dll, there since XP), and
GdkPixbuf on Linux (python3-gi with gir1.2-gdkpixbuf-2.0, there with GNOME).
Where neither is available, `encoder()` and `decoder()` say None and the
pictures stay PNG - the two computers agree on what each can draw before a
single JPEG is sent (winmove: the "codecs" in "ready").

Pictures arrive as BGRA, top row first, 4 bytes a pixel, exactly as for PNG.
Decoding gives RGB, top row first, 3 bytes a pixel, with any row padding
removed - which is what a PPM, the format Tk draws from bytes, wants.
"""
from __future__ import annotations

import sys
import threading

QUALITY = 75        # visibly clean on moving pictures, a sixth of the PNG


def encoder():
    """`encode(bgra, w, h, quality=QUALITY) -> bytes`, or None here."""
    try:
        if sys.platform == "win32":
            return _gdiplus().encode
        return _pixbuf().encode
    except Exception:
        return None


def decoder():
    """`decode(data) -> (w, h, rgb)`, or None here."""
    try:
        if sys.platform == "win32":
            return _gdiplus().decode
        return _pixbuf().decode
    except Exception:
        return None


def ppm(w: int, h: int, rgb: bytes) -> bytes:
    """RGB as a binary PPM, which Tk's PhotoImage reads from bytes."""
    return b"P6\n%d %d\n255\n" % (w, h) + rgb


def _bgra_to_rgb(raw: bytes, w: int, h: int) -> bytes:
    rgb = bytearray(w * h * 3)
    rgb[0::3] = raw[2::4]
    rgb[1::3] = raw[1::4]
    rgb[2::3] = raw[0::4]
    return bytes(rgb)


# ===================================================================== Windows
_GDI = None
_GDI_LOCK = threading.Lock()


def _gdiplus():
    global _GDI
    with _GDI_LOCK:
        if _GDI is None:
            _GDI = _GdiPlus()
        return _GDI


class _GdiPlus:
    """GDI+'s flat C API through ctypes: JPEG in and out of memory."""

    PixelFormat32bppRGB = 0x00022009
    PixelFormat24bppRGB = 0x00021808
    ImageLockModeRead = 1
    GMEM_MOVEABLE = 0x0002

    def __init__(self):
        import ctypes
        import ctypes.wintypes as wt
        self.c = ctypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        class EncoderParameter(ctypes.Structure):
            _fields_ = [("Guid", GUID), ("NumberOfValues", ctypes.c_ulong),
                        ("Type", ctypes.c_ulong), ("Value", ctypes.c_void_p)]

        class EncoderParameters(ctypes.Structure):
            _fields_ = [("Count", ctypes.c_uint),
                        ("Parameter", EncoderParameter * 1)]

        class StartupInput(ctypes.Structure):
            _fields_ = [("GdiplusVersion", ctypes.c_uint32),
                        ("DebugEventCallback", ctypes.c_void_p),
                        ("SuppressBackgroundThread", wt.BOOL),
                        ("SuppressExternalCodecs", wt.BOOL)]

        class BitmapData(ctypes.Structure):
            _fields_ = [("Width", ctypes.c_uint), ("Height", ctypes.c_uint),
                        ("Stride", ctypes.c_int), ("PixelFormat", ctypes.c_int),
                        ("Scan0", ctypes.c_void_p), ("Reserved", ctypes.c_void_p)]

        self.GUID, self.EncoderParameter = GUID, EncoderParameter
        self.EncoderParameters, self.BitmapData = EncoderParameters, BitmapData
        self.gp = ctypes.WinDLL("gdiplus")
        self.ole = ctypes.WinDLL("ole32")
        self.k32 = ctypes.WinDLL("kernel32")
        for f in ("GlobalLock", "GlobalAlloc"):
            getattr(self.k32, f).restype = ctypes.c_void_p
        self.k32.GlobalSize.restype = ctypes.c_size_t
        for f in ("GlobalLock", "GlobalUnlock", "GlobalSize", "GlobalFree"):
            getattr(self.k32, f).argtypes = [ctypes.c_void_p]
        self.k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        self.jpeg = self._guid("557cf401-1a04-11d3-9a73-0000f81ef32e")
        self.quality = self._guid("1d5be4b5-fa4a-452d-9cdd-5db35105e7eb")
        self.token = ctypes.c_size_t()          # a ULONG_PTR: 64 bits here
        si = StartupInput(1, None, False, False)
        if self.gp.GdiplusStartup(ctypes.byref(self.token), ctypes.byref(si),
                                  None) != 0:
            raise OSError("GDI+ would not start")
        self._release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        self._seek = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                        ctypes.c_longlong, ctypes.c_ulong,
                                        ctypes.POINTER(ctypes.c_ulonglong))

    def _guid(self, text):
        import uuid
        u = uuid.UUID(text)
        g = self.GUID()
        g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        return g

    def _drop(self, com):
        """IUnknown::Release, the third slot of every COM object's table."""
        c = self.c
        table = c.cast(com, c.POINTER(c.POINTER(c.c_void_p)))
        self._release(table[0][2])(com)

    def _written(self, stream) -> int:
        """How many bytes the save wrote: where the stream now stands
        (IStream::Seek, the sixth slot, by nothing from the current place).
        The memory under the stream can be bigger than what is in it."""
        c = self.c
        table = c.cast(stream, c.POINTER(c.POINTER(c.c_void_p)))
        at = c.c_ulonglong()
        if self._seek(table[0][5])(stream, 0, 1, c.byref(at)) != 0:   # STREAM_SEEK_CUR
            raise OSError("could not tell how long the JPEG is")
        return at.value

    def encode(self, bgra: bytes, w: int, h: int, quality: int = QUALITY) -> bytes:
        c = self.c
        if len(bgra) != w * h * 4:
            raise ValueError(f"a {w}x{h} picture needs {w * h * 4}B, got {len(bgra)}B")
        buf = c.create_string_buffer(bgra, len(bgra))
        bmp = c.c_void_p()
        st = self.gp.GdipCreateBitmapFromScan0(w, h, w * 4, self.PixelFormat32bppRGB,
                                               buf, c.byref(bmp))
        if st != 0:
            raise OSError(f"GDI+ could not take the picture ({st})")
        stream = c.c_void_p()
        try:
            if self.ole.CreateStreamOnHGlobal(None, True, c.byref(stream)) != 0:
                raise OSError("no memory stream")
            q = c.c_ulong(int(quality))
            params = self.EncoderParameters(1)
            params.Parameter[0] = self.EncoderParameter(
                self.quality, 1, 4, c.cast(c.pointer(q), c.c_void_p))
            st = self.gp.GdipSaveImageToStream(bmp, stream, c.byref(self.jpeg),
                                               c.byref(params))
            if st != 0:
                raise OSError(f"GDI+ could not write the JPEG ({st})")
            size = self._written(stream)
            hg = c.c_void_p()
            if self.ole.GetHGlobalFromStream(stream, c.byref(hg)) != 0:
                raise OSError("could not reach the JPEG's memory")
            p = self.k32.GlobalLock(hg)
            try:
                data = c.string_at(p, min(size, self.k32.GlobalSize(hg)))
            finally:
                self.k32.GlobalUnlock(hg)
        finally:
            if stream:
                self._drop(stream)
            self.gp.GdipDisposeImage(bmp)
        return data

    def decode(self, data: bytes):
        c = self.c
        hg = self.k32.GlobalAlloc(self.GMEM_MOVEABLE, len(data))
        if not hg:
            raise MemoryError("no memory for the picture")
        p = self.k32.GlobalLock(hg)
        c.memmove(p, data, len(data))
        self.k32.GlobalUnlock(hg)
        stream = c.c_void_p()
        if self.ole.CreateStreamOnHGlobal(c.c_void_p(hg), True, c.byref(stream)) != 0:
            self.k32.GlobalFree(c.c_void_p(hg))     # the stream would have owned it
            raise OSError("no memory stream")
        bmp = c.c_void_p()
        try:
            if self.gp.GdipCreateBitmapFromStream(stream, c.byref(bmp)) != 0:
                raise ValueError("that is not a picture GDI+ can read")
            w, h = c.c_uint(), c.c_uint()
            self.gp.GdipGetImageWidth(bmp, c.byref(w))
            self.gp.GdipGetImageHeight(bmp, c.byref(h))
            w, h = w.value, h.value
            rect = (c.c_int * 4)(0, 0, w, h)
            bd = self.BitmapData()
            if self.gp.GdipBitmapLockBits(bmp, rect, self.ImageLockModeRead,
                                          self.PixelFormat24bppRGB, c.byref(bd)) != 0:
                raise OSError("GDI+ would not give the pixels")
            try:
                stride = abs(bd.Stride)
                raw = c.string_at(bd.Scan0, stride * h)
            finally:
                self.gp.GdipBitmapUnlockBits(bmp, c.byref(bd))
        finally:
            if bmp:
                self.gp.GdipDisposeImage(bmp)
            self._drop(stream)
        # 24bppRGB is B, G, R in memory, and each row is padded to four bytes.
        row = w * 3
        if stride != row:
            raw = b"".join(raw[y * stride:y * stride + row] for y in range(h))
        rgb = bytearray(raw)
        rgb[0::3], rgb[2::3] = raw[2::3], raw[0::3]
        return w, h, bytes(rgb)


# ======================================================================= Linux
_PIX = None


def _pixbuf():
    global _PIX
    if _PIX is None:
        _PIX = _Pixbuf()
    return _PIX


class _Pixbuf:
    """GdkPixbuf, through PyGObject: JPEG in and out of memory."""

    def __init__(self):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, GLib
        if "jpeg" not in {f.get_name() for f in GdkPixbuf.Pixbuf.get_formats()}:
            raise OSError("GdkPixbuf here has no JPEG")
        self.GdkPixbuf, self.GLib = GdkPixbuf, GLib

    def encode(self, bgra: bytes, w: int, h: int, quality: int = QUALITY) -> bytes:
        P = self.GdkPixbuf
        rgb = _bgra_to_rgb(bgra, w, h)
        pb = P.Pixbuf.new_from_bytes(self.GLib.Bytes.new(rgb),
                                     P.Colorspace.RGB, False, 8, w, h, w * 3)
        ok, data = pb.save_to_bufferv("jpeg", ["quality"], [str(int(quality))])
        if not ok:
            raise OSError("GdkPixbuf could not write the JPEG")
        return bytes(data)

    def decode(self, data: bytes):
        loader = self.GdkPixbuf.PixbufLoader.new_with_type("jpeg")
        loader.write(data)
        loader.close()
        pb = loader.get_pixbuf()
        w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
        n = pb.get_n_channels()
        px = bytes(pb.get_pixels())
        row = w * n
        if stride != row:
            px = b"".join(px[y * stride:y * stride + row] for y in range(h))
        if n == 4:
            rgb = bytearray(w * h * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = px[0::4], px[1::4], px[2::4]
            px = bytes(rgb)
        return w, h, px
