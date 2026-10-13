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

import socket
import struct

from .secure import SecureError

# A frame bigger than this is broken or hostile, not a picture: a whole
# 1920x1080 picture raw is ~8 MB, and pictures travel compressed. The cap is
# generous so a burst of changed regions still fits, but bounded so a bad
# length cannot make us try to read gigabytes.
MAX_FRAME = 48 * 1024 * 1024
_LEN = struct.Struct(">I")              # a frame's length: 4 bytes, big-endian


class ChannelError(Exception):
    """The window channel broke: a bad length, a frame that would not
    decrypt, or the far end hung up. Always ends the channel (and with it the
    moved window, which comes home)."""


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
