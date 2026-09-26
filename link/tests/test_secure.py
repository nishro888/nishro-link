"""Encryption of the link: the exchange, the frames, and the wire itself.

Asked for before the lock and login screens are made to work: those are where
passwords are typed, and until now every keystroke crossed the network in the
clear.
"""
import random
import socket
import threading
import time

import pytest

from link import protocol, secure

from test_node_live import Pair


def _probably_prime(n, rounds=12):
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        x = pow(random.randrange(2, n - 2), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def test_the_group_is_the_rfc_3526_safe_prime():
    """Written out from the RFC; a slip in any digit would not be prime."""
    assert secure.P.bit_length() == 2048
    assert _probably_prime(secure.P) and _probably_prime(secure.Q)


def test_both_ends_agree_on_the_secret():
    a, A = secure.keypair()
    b, B = secure.keypair()
    assert secure.shared(a, B) == secure.shared(b, A)


@pytest.mark.parametrize("bad", ["1", format(secure.P - 1, "x"), "0", "zz",
                                 format(secure.P + 5, "x")])
def test_a_degenerate_key_is_refused(bad):
    x, _ = secure.keypair()
    with pytest.raises(secure.SecureError):
        secure.shared(x, bad)


def _pair_of_ends(psk="k"):
    a, A = secure.keypair()
    b, B = secure.keypair()
    hub = secure.session(secure.shared(a, B), psk, "n1", "n2", "hub")
    dialer = secure.session(secure.shared(b, A), psk, "n1", "n2", "dialer")
    return hub, dialer


def test_frames_travel_both_ways():
    (hs, ho), (ds, do) = _pair_of_ends()
    assert do.open(hs.seal(b'{"t":"k","c":30,"d":1}')) == b'{"t":"k","c":30,"d":1}'
    assert ho.open(ds.seal(b"hello")) == b"hello"


def test_the_two_directions_use_different_keys():
    (hs, _ho), (ds, _do) = _pair_of_ends()
    assert hs.seal(b"same") != ds.seal(b"same")


def test_a_changed_frame_is_refused():
    (hs, _ho), (_ds, do) = _pair_of_ends()
    line = bytearray(hs.seal(b"type the password"))
    line[4] ^= 1
    with pytest.raises(secure.SecureError):
        do.open(bytes(line))


def test_a_replayed_or_reordered_frame_is_refused():
    (hs, _ho), (_ds, do) = _pair_of_ends()
    first, second = hs.seal(b"one"), hs.seal(b"two")
    with pytest.raises(secure.SecureError):
        do.open(second)                       # out of order
    (hs, _ho), (_ds, do) = _pair_of_ends()
    one = hs.seal(b"one")
    do.open(one)
    with pytest.raises(secure.SecureError):
        do.open(one)                          # replayed


def test_a_different_password_makes_different_keys():
    a, A = secure.keypair()
    b, B = secure.keypair()
    hs, _ = secure.session(secure.shared(a, B), "right", "n1", "n2", "hub")
    _, do = secure.session(secure.shared(b, A), "wrong", "n1", "n2", "dialer")
    with pytest.raises(secure.SecureError):
        do.open(hs.seal(b"x"))


def test_the_keys_are_new_every_connection():
    (hs1, _), _ = _pair_of_ends()
    (hs2, _), _ = _pair_of_ends()
    assert hs1.seal(b"same") != hs2.seal(b"same")


# ------------------------------------------------------------ on the wire
class Tap:
    """A proxy between two nodes that records everything that crosses it."""

    def __init__(self, target_port):
        self.target = target_port
        self.seen = bytearray()
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            u = socket.create_connection(("127.0.0.1", self.target))
            for a, b in ((c, u), (u, c)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    def _pipe(self, a, b):
        while True:
            try:
                data = a.recv(65536)
            except OSError:
                return
            if not data:
                return
            self.seen += data
            try:
                b.sendall(data)
            except OSError:
                return


def test_nothing_after_the_handshake_is_readable_on_the_wire():
    """A key press crosses the link, and the wire carries none of it in the
    clear - only the handshake is plain, and it holds no secret."""
    p = Pair(pin="tiger-lemon-coral-radio")
    tap = Tap(p.port)
    p.aio.port = tap.port                      # the AIO dials through the tap
    try:
        p.start().connected()
        p.hub.core.cursor.warp("aio", 10, 10)  # put the cursor on the AIO
        p.hub._send(protocol.key(30, True, epoch=p.hub.core.epoch))
        time.sleep(0.3)
        wire = bytes(tap.seen)
        assert b'"t":"welcome"' in wire, "the handshake itself is plain"
        after = wire[wire.index(b'"t":"welcome"'):]
        after = after[after.index(b"\n") + 1:]
        assert after, "and something did follow it"
        for plain in (b'"t":"k"', b'"t":"baton"', b'"t":"layout"', b'"t":"p"'):
            assert plain not in after, plain
        assert b"tiger" not in wire and b"lemon" not in wire, "never the password"
    finally:
        p.stop()
