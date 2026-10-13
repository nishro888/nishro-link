"""Moving windows between computers: the window channel.

The full design is docs/moving-windows.md. This module holds the one piece
that is the same on every system - the window channel's transport - so it can
be built and tested on its own, before any of the system-specific capture,
hiding or framing is written.

A moved window's pictures must never queue in front of the pointer, so they
travel on a connection of their own, dialled and proved exactly like the link
(node.py does the handshake, as it does for the files channel). Once proved,
the connection is handed to a BinChannel here: sealed binary frames, length
first, so a picture pays no base64 and no 128 KB line cap.
"""
from __future__ import annotations

import json
import socket
import struct

from .secure import SecureError

# A frame bigger than this is broken or hostile, not a picture: a whole
# 1920x1080 picture raw is ~8 MB, and pictures travel compressed. The cap is
# generous so a burst of changed regions still fits, but bounded so a bad
# length cannot make us try to read gigabytes.
MAX_FRAME = 48 * 1024 * 1024
_LEN = struct.Struct(">I")              # a frame's length: 4 bytes, big-endian
_HDR = struct.Struct(">H")              # the header's length inside a frame

MAX_SIDE = 8192                 # pixels; no real window is wider or taller
MAX_REGIONS = 256               # changed rectangles in one picture, at most
AHEAD = 2                       # pictures sent and not yet shown, at most
TILE = 64                       # pictures are compared in squares this big
CODECS = ("jpeg", "png", "raw")


class ChannelError(Exception):
    """The window channel broke: a bad length, a frame that would not
    decrypt, or the far end hung up. Always ends the channel (and with it the
    moved window, which comes home)."""


# ------------------------------------------------------------------ messages
# Every frame is a 2-byte header length, a small JSON header, then the
# payload as it is - so a picture's bytes are never escaped into JSON (which
# would cost base64 again) and a header stays cheap to read.
#
# home -> away   meta (title, size), pic (a picture's changed regions), gone
# away -> home   ready, ack, in (input), size, close, home
#
# Unknown message types and unknown header fields are ignored by both ends,
# which is what lets a later 1.x add to this without breaking an older one.

def pack(header: dict, payload: bytes = b"") -> bytes:
    """One frame's bytes, for BinChannel.send."""
    head = json.dumps(header, separators=(",", ":")).encode("utf-8")
    if len(head) > 0xFFFF:
        raise ChannelError(f"a window message header is {len(head)}B, over 65535B")
    return _HDR.pack(len(head)) + head + payload


def unpack(frame: bytes):
    """(header, payload) from one frame. Raises ChannelError on anything
    malformed - the far end is proved, but a proved peer can still be a
    newer or buggier version, and a short frame must not raise IndexError
    deep inside a session."""
    if len(frame) < _HDR.size:
        raise ChannelError("a window message with no header length")
    (n,) = _HDR.unpack(frame[:_HDR.size])
    end = _HDR.size + n
    if len(frame) < end:
        raise ChannelError(f"a window message says its header is {n}B, "
                           f"but only {len(frame) - _HDR.size}B followed")
    try:
        header = json.loads(frame[_HDR.size:end].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ChannelError(f"a window message header that will not read: {e}") from None
    if not isinstance(header, dict):
        raise ChannelError("a window message header that is not an object")
    return header, frame[end:]


def pic(n: int, regions) -> bytes:
    """A picture frame. `regions` is (x, y, w, h, codec, bytes) for each
    changed rectangle; their bytes go in the payload one after another, and
    the header says how long each is."""
    if len(regions) > MAX_REGIONS:
        raise ChannelError(f"{len(regions)} changed regions, over {MAX_REGIONS}")
    out, parts = [], []
    for x, y, w, h, codec, data in regions:
        if codec not in CODECS:
            raise ChannelError(f"unknown codec {codec!r}")
        out.append([int(x), int(y), int(w), int(h), codec, len(data)])
        parts.append(bytes(data))
    return pack({"t": "pic", "n": int(n), "r": out}, b"".join(parts))


def regions(header: dict, payload: bytes):
    """The regions of a picture frame, as (x, y, w, h, codec, bytes).

    Every length and size is checked against the payload actually received:
    a header that claims more than arrived, or a rectangle outside any real
    window, is refused rather than trusted."""
    rows = header.get("r")
    if not isinstance(rows, list):
        raise ChannelError("a picture with no regions")
    if len(rows) > MAX_REGIONS:
        raise ChannelError(f"a picture with {len(rows)} regions, over {MAX_REGIONS}")
    out, at = [], 0
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise ChannelError("a picture region that is not x, y, w, h, codec, length")
        x, y, w, h, codec, length = row[:6]
        try:
            x, y, w, h, length = int(x), int(y), int(w), int(h), int(length)
        except (TypeError, ValueError):
            raise ChannelError("a picture region with numbers that are not numbers") \
                from None
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > MAX_SIDE or y + h > MAX_SIDE:
            raise ChannelError(f"a picture region outside any real window: "
                               f"{w}x{h} at {x},{y}")
        if codec not in CODECS:
            raise ChannelError(f"a picture region in an unknown codec {codec!r}")
        if length < 0 or at + length > len(payload):
            raise ChannelError("a picture region longer than the bytes that arrived")
        out.append((x, y, w, h, codec, payload[at:at + length]))
        at += length
    if at != len(payload):
        raise ChannelError(f"a picture with {len(payload) - at}B more than its "
                           f"regions account for")
    return out


def changed(old: bytes, new: bytes, w: int, h: int, tile: int = TILE, was=None):
    """The rectangles covering every pixel that differs between two pictures.

    Both are BGRA, top row first, 4 bytes a pixel, w*h*4 long. Compared in
    squares of `tile` pixels, because comparing and sending pixel-perfect
    rectangles costs more than sending a few unchanged pixels along with the
    changed ones. Neighbouring changed squares are joined into as few
    rectangles as possible: a run across a row first, then a run of identical
    runs down the rows, so a changed line of text becomes one rectangle
    rather than twenty.

    `old` empty means everything changed: the whole picture comes back as one
    rectangle. So does a resize - pass `was`, the (width, height) `old` was
    captured at, and the whole picture is sent whenever it differs. Length
    alone cannot tell: a window resized from 64x16 to 32x32 has a picture of
    exactly the same number of bytes, and comparing those tile by tile would
    report nothing changed and leave the frame showing the old shape for
    ever. The length check stays as a cheap second guard.
    """
    need = w * h * 4
    if w <= 0 or h <= 0:
        return []
    if len(new) < need:
        raise ValueError(f"a {w}x{h} picture needs {need}B, got {len(new)}B")
    if was is not None and tuple(was) != (w, h):
        return [(0, 0, w, h)]
    if len(old) != len(new):
        return [(0, 0, w, h)]
    stride = w * 4
    cols = (w + tile - 1) // tile
    rows = (h + tile - 1) // tile
    o, n = memoryview(old), memoryview(new)
    out = []
    open_runs: dict = {}          # (first col, last col + 1) -> index in out
    for r in range(rows):
        y0, y1 = r * tile, min((r + 1) * tile, h)
        dirty = [False] * cols
        for c in range(cols):
            x0, x1 = c * tile, min((c + 1) * tile, w)
            a, b = x0 * 4, x1 * 4
            for y in range(y0, y1):
                base = y * stride
                if o[base + a:base + b] != n[base + a:base + b]:
                    dirty[c] = True
                    break
        runs, c = [], 0
        while c < cols:
            if dirty[c]:
                start = c
                while c < cols and dirty[c]:
                    c += 1
                runs.append((start, c))
            else:
                c += 1
        still_open = {}
        for key in runs:
            c0, c1 = key
            x = c0 * tile
            rect_w = min(c1 * tile, w) - x
            if key in open_runs:
                i = open_runs[key]
                rx, ry, rw, _ = out[i]
                out[i] = (rx, ry, rw, y1 - ry)     # the same run again: grow it down
                still_open[key] = i
            else:
                out.append((x, y0, rect_w, y1 - y0))
                still_open[key] = len(out) - 1
        open_runs = still_open
    return out


class Pacer:
    """How far ahead home may get of the window the person is actually
    looking at.

    A moved window must never become a queue of old pictures: if the network
    or the away computer cannot keep up, the right thing is a slower but
    current window. So home sends a picture only while fewer than `ahead` of
    them are unacknowledged, and away acknowledges each one it has shown.
    Picture numbers only ever rise; an acknowledgement for one already
    acknowledged, or for one never sent, changes nothing.
    """

    def __init__(self, ahead: int = AHEAD):
        self.ahead = max(1, int(ahead))
        self.sent = 0                 # the last picture number handed to the channel
        self.shown = 0                # the last one away said it had shown

    def may_send(self) -> bool:
        return self.sent - self.shown < self.ahead

    def next_number(self) -> int:
        """The number for the picture about to be sent."""
        self.sent += 1
        return self.sent

    def acked(self, n) -> None:
        try:
            n = int(n)
        except (TypeError, ValueError):
            return
        if self.shown < n <= self.sent:
            self.shown = n

    @property
    def waiting(self) -> int:
        return self.sent - self.shown


class BinChannel:
    """Sealed length-prefixed frames over a socket, one window channel's worth.

    node.py does the line-based handshake (hello/ok, proof both ways, key
    exchange) on the raw socket, then hands it here with the Sealer/Opener it
    derived and whatever bytes it had already read past the last line
    (protocol.LineChannel.detach). From then on every frame is
    `seal_bytes(payload)` with a 4-byte length in front - the Sealer's frame
    numbering carries straight on from the handshake, so the first binary
    frame is simply the next number.

    Not thread-safe for concurrent senders: one thread sends, one receives,
    as the sessions use it.
    """

    def __init__(self, sock: socket.socket, sealer, opener, rest: bytes = b""):
        self.sock = sock
        self._sealer = sealer
        self._opener = opener
        self._buf = bytearray(rest)         # bytes read during the handshake but past its last line
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def send(self, payload: bytes) -> None:
        """Seal and send one frame. Raises ChannelError if the socket is gone,
        so the caller brings the window home rather than sending blind."""
        sealed = self._sealer.seal_bytes(payload)
        try:
            self.sock.sendall(_LEN.pack(len(sealed)) + sealed)
        except OSError as e:
            raise ChannelError(f"the window channel could not send: {e}") from None

    def _fill(self, n: int) -> None:
        """Read until the buffer holds at least n bytes, or the peer hangs up."""
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(65536)
            except OSError as e:
                raise ChannelError(f"the window channel dropped: {e}") from None
            if not chunk:
                raise ChannelError("the other end closed the window channel")
            self._buf += chunk

    def recv(self) -> bytes:
        """The next frame's payload. Raises ChannelError on a bad length, a
        frame that fails to decrypt (altered, replayed or reordered - the seal
        catches it), or a hang-up."""
        self._fill(_LEN.size)
        (length,) = _LEN.unpack(self._buf[:_LEN.size])
        if length > MAX_FRAME:
            raise ChannelError(f"a window frame says it is {length}B, over the "
                               f"{MAX_FRAME}B cap - broken or hostile")
        self._fill(_LEN.size + length)
        sealed = bytes(self._buf[_LEN.size:_LEN.size + length])
        del self._buf[:_LEN.size + length]
        try:
            return self._opener.open_bytes(sealed)
        except SecureError as e:
            raise ChannelError(str(e)) from None

    def close(self) -> None:
        for step in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                step()
            except OSError:
                pass
