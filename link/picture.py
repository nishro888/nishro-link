"""Turning part of a window into bytes to send, and nothing else.

A moved window's changed rectangles (docs/moving-windows.md) are encoded here
as PNG, written by hand with `zlib` - no GDI+, no GdkPixbuf, no Pillow, the
same code on Windows and Linux, and testable anywhere including CI.

WHY PNG, WRITTEN HERE. Measured on this laptop, on a real 916x639 window full
of text (the common case for a moved window - a document, a terminal, code):

    zlib level 1, no filter    12.5 ms    63 KB     <- what we use for big areas
    zlib level 6, no filter    22.7 ms    36 KB     <- and for a whole window
    zlib level 1, "Up" filter   254 ms    69 KB     <- rejected: see below
    one 64x64 square, level 1  0.18 ms   895 bytes  <- the usual case by far

Almost every picture after the first is a few small squares, which cost under
a millisecond together, so the encoder is nowhere near the limit (N3: a third
of a core). PNG also keeps text sharp, which a lossy codec does not, and Tk
reads these bytes directly - so the computer showing the window decodes
nothing itself, and a changed square is copied into the frame in about
0.01 ms.

PNG's row filters would compress better, but every one of them is defined
byte by byte against the row above, which in Python means a loop over every
byte: 254 ms for one window, twenty times the cost of not filtering. So the
rows go unfiltered and zlib does the work. (A C-coded filter, through GDI+ or
GdkPixbuf, would be the way to squeeze this further - worth it only if the
network, not the encoder, turns out to be the limit.)

Pictures arrive from the systems as BGRA, top row first, 4 bytes a pixel -
which is what Windows' GetDIBits and GStreamer's BGRx both give. The alpha is
dropped: a window's picture is opaque, and three bytes a pixel is a quarter
less to compress.
"""
from __future__ import annotations

import zlib

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# zlib's level, by how much there is to compress. A small square is already
# tiny, so speed is what matters; a whole window is worth the extra 10 ms to
# halve what goes over the air, which on a 2.4 GHz Wi-Fi is the slower part.
BIG = 256 * 1024                # raw bytes, above which we compress harder
FAST_LEVEL = 1
SMALL_LEVEL = 6


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (len(data).to_bytes(4, "big") + kind + data
            + zlib.crc32(kind + data).to_bytes(4, "big"))


def png(raw: bytes, w: int, h: int, level: int = None) -> bytes:
    """`raw` (BGRA, top row first, w*h*4 bytes) as a PNG.

    Raises ValueError if the bytes and the size do not agree - a caller that
    has muddled a rectangle's size would otherwise write a picture of
    whatever happened to follow it in memory.
    """
    if w <= 0 or h <= 0:
        raise ValueError(f"a picture cannot be {w}x{h}")
    need = w * h * 4
    if len(raw) != need:
        raise ValueError(f"a {w}x{h} picture needs {need}B, got {len(raw)}B")
    if level is None:
        level = FAST_LEVEL if need < BIG else SMALL_LEVEL
    # BGRA -> RGB, as three strided copies: each one is a single C-level
    # memcpy-with-a-stride, which is the only reason this is fast enough in
    # Python at all. Written byte by byte it would take seconds.
    rgb = bytearray(w * h * 3)
    rgb[0::3] = raw[2::4]           # red comes from BGRA's third byte
    rgb[1::3] = raw[1::4]
    rgb[2::3] = raw[0::4]           # blue from its first
    stride = w * 3
    rows = b"".join(b"\x00" + bytes(rgb[y * stride:(y + 1) * stride])
                    for y in range(h))
    head = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes(
        [8,      # bits a channel
         2,      # colour type 2: red, green, blue
         0, 0, 0])
    return (PNG_MAGIC + _chunk(b"IHDR", head)
            + _chunk(b"IDAT", zlib.compress(rows, level))
            + _chunk(b"IEND", b""))


def encode(raw: bytes, w: int, h: int):
    """(codec, bytes) for one of a window's changed rectangles - what
    winmove.HomeSession is given and what travels on the channel."""
    return "png", png(raw, w, h)


def size(data: bytes):
    """The (width, height) a PNG says it is, or None if it is not one.

    The frame uses this to check a rectangle's bytes really are the size its
    header claimed, before drawing them at that place.
    """
    if len(data) < 24 or not data.startswith(PNG_MAGIC):
        return None
    if data[12:16] != b"IHDR":
        return None
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))
