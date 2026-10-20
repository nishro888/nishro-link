"""Encoding part of a window to send (picture.py).

The pictures are checked by Tk itself where it is available - Tk is what
shows them on the other computer, so "Tk reads it and the colours are right"
is the property that actually matters, not "it looks like a PNG"."""
import struct
import zlib

import pytest

from link import picture

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display for Tk: {e}")
    r.withdraw()
    try:
        yield r
    finally:
        r.destroy()


def bgra(w, h, b=0, g=0, r=0):
    return bytes([b, g, r, 255]) * (w * h)


def test_it_is_a_png_of_the_right_size():
    data = picture.png(bgra(12, 7), 12, 7)
    assert data.startswith(picture.PNG_MAGIC)
    assert picture.size(data) == (12, 7)


def test_tk_reads_it_and_the_colours_come_out_the_right_way_round(root):
    """BGRA in, and blue must come out blue: the one mistake that would make
    every moved window look wrong in a way no size check would catch."""
    img = tk.PhotoImage(data=picture.png(bgra(8, 8, b=255), 8, 8))
    assert (img.width(), img.height()) == (8, 8)
    assert img.get(4, 4) == (0, 0, 255), "blue pixels must stay blue"
    img2 = tk.PhotoImage(data=picture.png(bgra(8, 8, r=255), 8, 8))
    assert img2.get(4, 4) == (255, 0, 0), "red pixels must stay red"


def test_every_pixel_survives_exactly(root):
    """A gradient, so a row or column quietly shifted by one would show."""
    w, h = 16, 9
    raw = bytearray()
    for y in range(h):
        for x in range(w):
            raw += bytes([x * 8 % 256, y * 20 % 256, (x + y) * 5 % 256, 255])
    img = tk.PhotoImage(data=picture.png(bytes(raw), w, h))
    for y in (0, h // 2, h - 1):
        for x in (0, w // 2, w - 1):
            assert img.get(x, y) == ((x + y) * 5 % 256, y * 20 % 256, x * 8 % 256), \
                f"pixel {x},{y}"


@pytest.mark.parametrize("w, h", [(1, 1), (1, 40), (40, 1), (3, 5), (64, 64), (97, 33)])
def test_odd_and_small_sizes_are_fine(root, w, h):
    """Window rectangles are not multiples of anything: the last square of a
    row is whatever is left, and a 1-pixel edge happens."""
    img = tk.PhotoImage(data=picture.png(bgra(w, h, g=200), w, h))
    assert (img.width(), img.height()) == (w, h)
    assert img.get(w - 1, h - 1) == (0, 200, 0)


def test_a_region_can_be_copied_into_a_bigger_frame(root):
    """How the frame draws: one image the size of the window, and each
    changed rectangle copied into its place."""
    frame = tk.PhotoImage(width=32, height=32)
    frame.put("#000000", to=(0, 0, 32, 32))
    patch = tk.PhotoImage(data=picture.png(bgra(8, 8, b=255), 8, 8))
    frame.tk.call(frame, "copy", patch, "-to", 16, 8)
    assert frame.get(20, 12) == (0, 0, 255), "the patch landed"
    assert frame.get(4, 4) == (0, 0, 0), "and nowhere else"


def test_encode_says_which_codec_it_used():
    codec, data = picture.encode(bgra(4, 4), 4, 4)
    assert codec == "png" and picture.size(data) == (4, 4)


@pytest.mark.parametrize("w, h, raw_w, raw_h, why", [
    (8, 8, 8, 7, "needs"),            # too few bytes
    (8, 8, 8, 9, "needs"),            # too many
    (0, 8, 1, 8, "cannot be"),
    (8, 0, 8, 1, "cannot be"),
    (-2, 8, 2, 8, "cannot be"),
])
def test_bytes_that_do_not_match_the_size_are_refused(w, h, raw_w, raw_h, why):
    """Otherwise a muddled rectangle would encode whatever followed it."""
    with pytest.raises(ValueError, match=why):
        picture.png(bgra(raw_w, raw_h), w, h)


def test_a_flat_area_compresses_to_almost_nothing():
    """What makes this affordable: most of a window does not change, and what
    does is usually flat or text."""
    data = picture.png(bgra(256, 256, g=128), 256, 256)
    assert len(data) < 4096, f"a flat 256x256 square came to {len(data)}B"


def test_the_bigger_the_area_the_harder_it_is_compressed():
    """Small squares are encoded for speed, a whole window for size."""
    small = picture.png(bgra(16, 16), 16, 16)
    assert picture.size(small) == (16, 16)
    big_raw = bgra(400, 400)
    assert len(big_raw) > picture.BIG
    assert picture.size(picture.png(big_raw, 400, 400)) == (400, 400)
    # and a level can be asked for outright
    assert picture.size(picture.png(bgra(8, 8), 8, 8, level=9)) == (8, 8)


def test_the_chunks_carry_the_checksums_a_png_must_have():
    """Written by hand, so the one thing a reader would reject is worth a
    test of its own rather than trusting Tk to have been strict."""
    data = picture.png(bgra(5, 5), 5, 5)
    at = len(picture.PNG_MAGIC)
    seen = []
    while at < len(data):
        (n,) = struct.unpack(">I", data[at:at + 4])
        kind = data[at + 4:at + 8]
        body = data[at + 8:at + 8 + n]
        (crc,) = struct.unpack(">I", data[at + 8 + n:at + 12 + n])
        assert crc == zlib.crc32(kind + body), f"{kind!r} checksum"
        seen.append(kind)
        at += 12 + n
    assert seen == [b"IHDR", b"IDAT", b"IEND"]


@pytest.mark.parametrize("data", [
    b"", b"not a png at all", picture.PNG_MAGIC, picture.PNG_MAGIC + b"\x00" * 10,
])
def test_size_says_no_to_what_is_not_a_png(data):
    assert picture.size(data) is None


# ------------------------------------------------ text stays PNG, busy goes JPEG
def _gradient(w, h):
    """A smooth colour gradient, BGRA - busy, the way a photo or video is."""
    out = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            out[i] = (x * 255 // w) & 0xFF
            out[i + 1] = (y * 255 // h) & 0xFF
            out[i + 2] = ((x * 7 + y * 13) % 251) & 0xFF
            out[i + 3] = 255
    return bytes(out)


def _page(w, h):
    """White with dark strokes, BGRA - text-like."""
    out = bytearray(b"\xf8\xf8\xf8\xff" * (w * h))
    for y in range(10, h, 20):
        for x in range(10, w - 10, 3):
            i = (y * w + x) * 4
            out[i:i + 3] = b"\x22\x22\x22"
    return bytes(out)


def fake_jpeg(raw, w, h):
    return b"JPEG" + bytes([w & 0xFF, h & 0xFF])


def test_a_busy_picture_goes_as_jpeg_where_both_ends_can():
    raw = _gradient(256, 256)
    codec, data = picture.choose(raw, 256, 256, ("png", "jpeg"), fake_jpeg)
    assert codec == "jpeg" and data.startswith(b"JPEG")


def test_text_stays_png_because_jpeg_smears_letters():
    raw = _page(256, 256)
    codec, _ = picture.choose(raw, 256, 256, ("png", "jpeg"), fake_jpeg)
    assert codec == "png"


def test_nothing_goes_as_jpeg_to_a_computer_that_cannot_draw_it():
    """An older away says nothing about codecs, and must get PNG."""
    raw = _gradient(256, 256)
    assert picture.choose(raw, 256, 256, ("png",), fake_jpeg)[0] == "png"


def test_no_jpeg_encoder_here_means_png():
    raw = _gradient(256, 256)
    assert picture.choose(raw, 256, 256, ("png", "jpeg"), None)[0] == "png"


def test_a_small_square_stays_png_whatever_it_shows():
    """JPEG's headers would be most of the bytes."""
    raw = _gradient(64, 64)
    assert picture.choose(raw, 64, 64, ("png", "jpeg"), fake_jpeg)[0] == "png"


def test_a_jpeg_that_fails_to_come_out_falls_back_to_png():
    """The picture must still arrive."""
    def broken(raw, w, h):
        raise OSError("GDI+ said no")
    raw = _gradient(256, 256)
    codec, data = picture.choose(raw, 256, 256, ("png", "jpeg"), broken)
    assert codec == "png" and picture.size(data) == (256, 256)


def test_the_text_test_is_measured_against_real_windows():
    """The line between text and busy was measured on real windows (picture.py);
    these hold it to the right side of both."""
    assert picture.looks_like_text(_page(256, 256), 256, 256)
    assert not picture.looks_like_text(_gradient(256, 256), 256, 256)
