"""The window channel's transport (winmove.BinChannel): sealed, length-first
binary frames, as the moved-window feature will carry pictures and input on.

The full feature is designed in docs/moving-windows.md; this is stage 2's
foundation - no real window yet, just the pipe and that it is safe."""
import socket
import struct
import threading

import pytest

from link import secure, winmove
from link.winmove import BinChannel, ChannelError


def _keys():
    """A sealer/opener pair for each direction, as node.py derives after the
    handshake (secure.session), here straight from a shared secret."""
    a_priv, a_pub = secure.keypair()
    b_priv, b_pub = secure.keypair()
    secret_a = secure.shared(a_priv, b_pub)
    secret_b = secure.shared(b_priv, a_pub)
    a_tx, a_rx = secure.session(secret_a, "pw", "n1", "n2", "hub")
    b_tx, b_rx = secure.session(secret_b, "pw", "n1", "n2", "dialer")
    return (a_tx, a_rx), (b_tx, b_rx)


def _pair(rest_a=b"", rest_b=b""):
    """Two ends of a window channel over a real socket pair. Each end seals
    with its own sealer and opens with its own opener; secure.session already
    derives the hub's sealer and the dialer's opener from the same half-key,
    and vice versa, so hub-seal/away-open and away-seal/hub-open line up."""
    (a_tx, a_rx), (b_tx, b_rx) = _keys()
    s1, s2 = socket.socketpair()
    hub = BinChannel(s1, a_tx, a_rx, rest_a)
    away = BinChannel(s2, b_tx, b_rx, rest_b)
    return hub, away


def test_a_frame_goes_there_and_back_sealed():
    hub, away = _pair()
    try:
        hub.send(b"a picture's bytes")
        assert away.recv() == b"a picture's bytes"
        away.send(b"the frame's reply")
        assert hub.recv() == b"the frame's reply"
    finally:
        hub.close()
        away.close()


def test_many_frames_keep_their_order_and_boundaries():
    hub, away = _pair()
    try:
        payloads = [bytes([i % 256]) * (i * 7 + 1) for i in range(50)]
        for p in payloads:
            hub.send(p)
        assert [away.recv() for _ in payloads] == payloads
    finally:
        hub.close()
        away.close()


def test_an_empty_frame_is_a_frame():
    """A header-only frame (no payload) must round-trip: the sessions use
    bare acknowledgements."""
    hub, away = _pair()
    try:
        hub.send(b"")
        assert away.recv() == b""
    finally:
        hub.close()
        away.close()


def test_a_big_frame_crosses_whole():
    """A 2 MB frame spans many recv() reads: the length prefix must reassemble
    it exactly, with nothing of the next frame swallowed. Sent from a thread -
    2 MB is far more than a socket's send buffer, so a single thread would
    block in sendall with nothing draining the other end (a real property of
    the channel: the sessions always send and receive on different threads)."""
    hub, away = _pair()
    big = bytes(range(256)) * (8 * 1024)            # 2 MB
    sender = threading.Thread(target=lambda: (hub.send(big), hub.send(b"after it")),
                              daemon=True)
    sender.start()
    try:
        assert away.recv() == big
        assert away.recv() == b"after it"
    finally:
        sender.join(5)
        hub.close()
        away.close()


def test_bytes_read_during_the_handshake_are_not_lost():
    """node.py's LineChannel may read past the handshake's last line into the
    first binary frames; it hands those bytes over, and recv() must use them."""
    (a_tx, a_rx), (b_tx, b_rx) = _keys()
    # a seals two frames up front; we feed them to b as `rest`, nothing on the wire.
    f1 = a_tx.seal_bytes(b"first")
    f2 = a_tx.seal_bytes(b"second")
    rest = struct.pack(">I", len(f1)) + f1 + struct.pack(">I", len(f2)) + f2
    s1, s2 = socket.socketpair()
    away = BinChannel(s2, b_tx, b_rx, rest)
    try:
        assert away.recv() == b"first"
        assert away.recv() == b"second"
    finally:
        s1.close()
        away.close()


def test_a_frame_over_the_cap_is_refused_before_it_is_read():
    """A length header claiming more than MAX_FRAME must be rejected on sight,
    not believed and read - a bad peer could otherwise ask us for gigabytes."""
    hub, away = _pair()
    try:
        hub.sock.sendall(struct.pack(">I", winmove.MAX_FRAME + 1) + b"\x00\x00")
        with pytest.raises(ChannelError, match="over the"):
            away.recv()
    finally:
        hub.close()
        away.close()


def test_a_frame_too_short_to_hold_a_tag_is_refused():
    """A sealed frame is at least a 16-byte tag; a shorter one cannot be
    genuine and must be refused, not handed to the cipher (which open_bytes
    guards against - the binary path's own check, not base64's)."""
    hub, away = _pair()
    try:
        hub.sock.sendall(struct.pack(">I", 4) + b"\x00\x00\x00\x00")
        with pytest.raises(ChannelError, match="too short"):
            away.recv()
    finally:
        hub.close()
        away.close()


def test_a_tampered_frame_is_caught_and_ends_the_channel():
    """Flip a byte of a sealed frame: the seal must reject it (altered or
    replayed), as it does on the link."""
    (a_tx, a_rx), (b_tx, b_rx) = _keys()
    s1, s2 = socket.socketpair()
    away = BinChannel(s2, b_tx, b_rx)
    try:
        sealed = bytearray(a_tx.seal_bytes(b"a picture"))
        sealed[-1] ^= 0x01
        s1.sendall(struct.pack(">I", len(sealed)) + bytes(sealed))
        with pytest.raises(ChannelError):
            away.recv()
    finally:
        s1.close()
        away.close()


def test_a_frame_out_of_order_is_caught():
    """The seal numbers frames; reading them in the wrong order fails, so a
    reordered or dropped frame cannot pass (as on the link, TCP keeps order,
    but the guarantee is the seal's, not TCP's)."""
    (a_tx, a_rx), (b_tx, b_rx) = _keys()
    s1, s2 = socket.socketpair()
    away = BinChannel(s2, b_tx, b_rx)
    try:
        a_tx.seal_bytes(b"one")        # frame 0: never sent, only advances the seal's count
        second = a_tx.seal_bytes(b"two")   # frame 1
        # send frame 1 first: the opener expects frame 0 and must reject it
        s1.sendall(struct.pack(">I", len(second)) + second)
        with pytest.raises(ChannelError):
            away.recv()
    finally:
        s1.close()
        away.close()


def test_recv_raises_when_the_peer_hangs_up():
    hub, away = _pair()
    hub.close()
    with pytest.raises(ChannelError, match="closed|dropped"):
        away.recv()
    away.close()


def test_send_after_the_peer_is_gone_raises():
    hub, away = _pair()
    away.close()
    hub.sock.close()
    with pytest.raises(ChannelError):
        hub.send(b"into the void")


def test_a_truncated_length_header_at_a_clean_close_is_a_hangup():
    """The peer closes having sent fewer than 4 bytes of a length: recv() must
    report a hang-up, not block or misread."""
    hub, away = _pair()
    hub.sock.sendall(b"\x00\x00")       # two bytes of a four-byte length, then close
    hub.sock.close()
    with pytest.raises(ChannelError, match="closed|dropped"):
        away.recv()
    away.close()
