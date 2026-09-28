"""Encryption of the link: the exchange, the frames, and the wire itself.

Asked for before the lock and login screens are made to work: those are where
passwords are typed, and until now every keystroke crossed the network in the
clear.
"""
import socket
import threading
import time

import pytest

from link import pairing, protocol, secure

from test_node_live import Pair


def test_the_exchange_is_x25519_as_published():
    """RFC 7748, section 6.1: Alice's private key and Bob's public key give
    the published shared secret. Standard, not home-made."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    alice = X25519PrivateKey.from_private_bytes(bytes.fromhex(
        "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"))
    bob_public = "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
    assert secure.shared(alice, bob_public).hex() == (
        "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")


def test_every_connection_gets_a_new_key():
    _, a = secure.keypair()
    _, b = secure.keypair()
    assert a != b and len(a) == len(b) == secure.KEY_HEX


def test_both_ends_agree_on_the_secret():
    a, A = secure.keypair()
    b, B = secure.keypair()
    assert secure.shared(a, B) == secure.shared(b, A)


@pytest.mark.parametrize("bad", [
    "1", "0", "zz", "", None, "00" * 31, "ab" * 33,
    "00" * 32,                               # the zero point: an all-zero secret
    "01" + "00" * 31,                        # a point of low order: likewise
    "e0eb7a7c3b41b8ae1656e3faf19fc46ada098deb9c32b1fd866205165f49b800",
])
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
    hs.seal(b"one")                       # the first frame, never delivered
    second = hs.seal(b"two")
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


# ------------------------------------------------------------- one secret
def test_a_wrapped_secret_opens_only_with_the_same_password_and_nonces():
    """A group's password, handed to a new device or to members on a password
    change, is sealed with ChaCha20-Poly1305 under a key from HKDF."""
    box = secure.wrap(b"tiger-lemon-coral-radio", "key", "a1", "b1")
    assert secure.unwrap(box, "key", "a1", "b1") == b"tiger-lemon-coral-radio"
    for psk, a, b in (("other", "a1", "b1"), ("key", "a2", "b1"),
                      ("key", "a1", "b2"), ("key", "b1", "a1")):
        with pytest.raises(secure.SecureError):
            secure.unwrap(box, psk, a, b)
    assert b"tiger" not in bytes.fromhex(box)


@pytest.mark.parametrize("junk", ["", "zz", "00" * 15, None, 5, [], "00" * 40])
def test_a_wrapped_secret_that_is_not_one_is_refused(junk):
    with pytest.raises(secure.SecureError):
        secure.unwrap(junk, "key", "a", "b")


def test_a_wrapped_secret_altered_by_one_bit_is_refused():
    box = bytearray.fromhex(secure.wrap(b"secret", "key", "a", "b"))
    for i in range(len(box)):
        bad = bytearray(box)
        bad[i] ^= 1
        with pytest.raises(secure.SecureError):
            secure.unwrap(bad.hex(), "key", "a", "b")


@pytest.mark.parametrize("box", [None, {}, {"ct": 5}, {"ct": "zz"}, "text",
                                 {"ct": secure.wrap(b"\xff\xfe", "k", "a", "b")}])
def test_the_protocol_refuses_a_wrapped_secret_it_cannot_read(box):
    with pytest.raises(ValueError):
        protocol.unwrap(box, "k", "a", "b")


def test_an_invitation_crosses_the_wire_encrypted():
    """Adding a device hands it the group's hub, address and password. That
    runs over the same exchange as every link: after the proofs, nothing of
    the invitation is readable - and the proofs are bound to both key-exchange
    values, so a machine in the middle cannot take it over."""
    from test_groups_live import accept_invites, device, listening, wait
    laptop = device("laptop", "tiger-lemon-coral-radio")
    aio = device("aio", "bench-grape-molar-stump")
    accept_invites(aio)
    tap = Tap(aio.port)
    for n in (laptop, aio):
        threading.Thread(target=n.run, daemon=True).start()
    try:
        listening(aio)
        r = laptop.invite("aio", "bench-grape-molar-stump", addr="127.0.0.1",
                          port=tap.port)
        assert r["ok"], laptop.adding
        wait(lambda: aio.pin == pairing.normalise("tiger-lemon-coral-radio"),
             "the group's password",
             (laptop, aio))
        wire = bytes(tap.seen)
        assert b'"t":"invite_ok"' in wire, "the proofs themselves are plain"
        for plain in (b'"t":"invite"', b'"t":"invite_done"', b'"hub"',
                      b"tiger", b"bench", b'"hub_id"'):
            assert plain not in wire, plain
    finally:
        for n in (laptop, aio):
            n.stop()


def test_cryptography_before_3_1_which_insists_on_a_backend(monkeypatch):
    """Ubuntu 20.04 ships cryptography 2.8 and Debian 11 3.3, whose HKDF
    refuses to be made without a `backend`. Both ends still agree on the keys."""
    real = secure.HKDF

    def old_hkdf(*, algorithm, length, salt, info, backend=None):
        if backend is None:
            raise TypeError("__init__() missing 1 required positional argument: "
                            "'backend'")
        return real(algorithm=algorithm, length=length, salt=salt, info=info)
    monkeypatch.setattr(secure, "HKDF", old_hkdf)
    a, pa = secure.keypair()
    b, pb = secure.keypair()
    tx, _ = secure.session(secure.shared(a, pb), "k", "n1", "n2", "hub")
    _, rx = secure.session(secure.shared(b, pa), "k", "n1", "n2", "dialer")
    assert rx.open(tx.seal(b"key press")) == b"key press"
    assert secure.unwrap(secure.wrap(b"pw", "k", "a", "b"), "k", "a", "b") == b"pw"
