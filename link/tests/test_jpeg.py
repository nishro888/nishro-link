"""JPEG through the system's own codec (jpeg.py).

Where this system has one - GDI+ on Windows, GdkPixbuf on Linux. Where it
has none, the module says None and the pictures stay PNG; that is tested
too, so CI without GdkPixbuf is a test of the fallback rather than a skip.
"""
import pytest

from link import jpeg


def gradient(w, h):
    out = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            out[i] = (x * 255 // w) & 0xFF
            out[i + 1] = (y * 255 // h) & 0xFF
            out[i + 2] = 200
            out[i + 3] = 255
    return bytes(out)


enc, dec = jpeg.encoder(), jpeg.decoder()
needs_codec = pytest.mark.skipif(enc is None or dec is None,
                                 reason="no JPEG codec on this system")


@needs_codec
def test_a_picture_survives_the_round_trip():
    w, h = 120, 80
    raw = gradient(w, h)
    data = enc(raw, w, h)
    assert data[:2] == b"\xff\xd8", "not a JPEG"
    dw, dh, rgb = dec(data)
    assert (dw, dh) == (w, h)
    assert len(rgb) == w * h * 3, "padding was left in the rows"
    # lossy, so loosely - at a few points, BGRA -> RGB
    for x, y in ((5, 5), (60, 40), (115, 75)):
        i4, i3 = (y * w + x) * 4, (y * w + x) * 3
        want = (raw[i4 + 2], raw[i4 + 1], raw[i4])
        got = tuple(rgb[i3:i3 + 3])
        assert max(abs(a - b) for a, b in zip(want, got)) < 24, (x, y, want, got)


@needs_codec
def test_the_bytes_are_the_jpeg_and_nothing_after_it():
    """GDI+ writes into a block of memory that can be bigger than what it
    wrote; only what it wrote may travel. A JPEG ends at its end marker."""
    for w, h in ((120, 80), (333, 77), (64, 64)):
        data = enc(gradient(w, h), w, h)
        assert data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9", (w, h)


@needs_codec
def test_an_odd_width_comes_back_without_its_row_padding():
    """GDI+'s rows are padded to four bytes; 3 * 121 is not a multiple."""
    w, h = 121, 9
    dw, dh, rgb = dec(enc(gradient(w, h), w, h))
    assert (dw, dh) == (w, h) and len(rgb) == w * h * 3


@needs_codec
def test_it_is_much_smaller_than_png_for_a_busy_picture():
    from link import picture
    w, h = 400, 300
    raw = gradient(w, h)
    assert len(enc(raw, w, h)) < len(picture.png(raw, w, h)) / 2


@needs_codec
def test_bytes_that_are_not_a_jpeg_are_refused():
    with pytest.raises(Exception):
        dec(b"not a jpeg at all")


def test_a_ppm_says_its_size_in_its_header():
    p = jpeg.ppm(3, 2, bytes(18))
    assert p.startswith(b"P6\n3 2\n255\n") and len(p) == len(b"P6\n3 2\n255\n") + 18


def test_with_no_codec_both_ends_say_so_rather_than_raising(monkeypatch):
    monkeypatch.setattr(jpeg, "_gdiplus", lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(jpeg, "_pixbuf", lambda: (_ for _ in ()).throw(ImportError()))
    assert jpeg.encoder() is None and jpeg.decoder() is None
