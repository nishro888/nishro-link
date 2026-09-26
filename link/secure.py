"""Encryption for the link.

Every connection runs an ephemeral Diffie-Hellman exchange inside the password
handshake. Both public values are bound into the password proofs, so nobody
without the password can sit in the middle; the session keys come from the
exchange AND the password, and are thrown away with the connection - so traffic
recorded today stays unreadable even if the password is guessed tomorrow
(forward secrecy).

After the handshake every frame, in both directions, is encrypted and
authenticated:

    keystream  BLAKE2b(key=enc, seq || block) in counter mode, 64 bytes a block
    tag        BLAKE2b(key=mac, seq || ciphertext), 16 bytes, checked first
    frame      base64(ciphertext || tag), one line

`seq` counts frames in each direction and is never sent: TCP delivers them in
order, so a dropped, replayed, reordered or altered frame fails its tag and
ends the connection. Four independent keys - encrypt and authenticate, each
way - come from HKDF-SHA256.

WHY NOT TLS. Python's own TLS cannot key a connection from a shared password
before 3.13 (the Windows build is 3.11), and certificates would need a library
this program does not otherwise carry. These are standard constructions -
finite-field DH (RFC 3526 group 14, 2048 bits), HKDF (RFC 5869), a keyed hash
as a PRF in counter mode, encrypt-then-MAC - built from the standard library's
own hashes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# RFC 3526, group 14: a 2048-bit safe prime (P = 2Q + 1), generator 2.
P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74"
    "020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437"
    "4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05"
    "98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB"
    "9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718"
    "3995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)
G = 2
Q = (P - 1) // 2
_BYTES = (P.bit_length() + 7) // 8

TAG = 16
BLOCK = 64


class SecureError(Exception):
    """A frame that did not come from the other end, intact and in order."""


# ------------------------------------------------------------ key exchange
def keypair():
    """(private, public as hex) for one connection."""
    x = secrets.randbits(256) | (1 << 255)       # 256 bits: twice the strength
    return x, format(pow(G, x, P), "x")


def shared(private: int, peer_public: str) -> bytes:
    """The DH secret. Refuses a public value outside the prime-order subgroup
    - 1, P-1, or anything of small order - rather than trust it."""
    try:
        y = int(str(peer_public), 16)
    except (TypeError, ValueError):
        raise SecureError("the other side's key is not a number") from None
    if not 2 <= y <= P - 2 or pow(y, Q, P) != 1:
        raise SecureError("the other side's key is not valid")
    return pow(y, private, P).to_bytes(_BYTES, "big")


def hkdf(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 with SHA-256."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


def session(secret: bytes, psk: str, hub_nonce: str, dialer_nonce: str,
            role: str):
    """(Sealer, Opener) for this end. `psk` is the password key both sides
    proved; mixing it in means the keys need the password AND the exchange."""
    okm = hkdf(secret + (psk or "").encode(),
               f"{hub_nonce}|{dialer_nonce}".encode(), b"nishro-link v6 keys", 128)
    hub_to_dialer = (okm[0:32], okm[32:64])
    dialer_to_hub = (okm[64:96], okm[96:128])
    send, recv = ((hub_to_dialer, dialer_to_hub) if role == "hub"
                  else (dialer_to_hub, hub_to_dialer))
    return Sealer(*send), Opener(*recv)


# ------------------------------------------------------------------ frames
def _keystream(key: bytes, seq: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.blake2b(seq + i.to_bytes(4, "big"), key=key,
                               digest_size=BLOCK).digest()
        i += 1
    return bytes(out[:n])


def _xor(a: bytes, b: bytes) -> bytes:
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(
        len(a), "big")


class Sealer:
    """Encrypts this end's frames, numbering them."""

    def __init__(self, enc: bytes, mac: bytes):
        self._enc, self._mac, self._seq = enc, mac, 0

    def seal(self, plain: bytes) -> bytes:
        seq = self._seq.to_bytes(8, "big")
        self._seq += 1
        ct = _xor(plain, _keystream(self._enc, seq, len(plain))) if plain else b""
        tag = hashlib.blake2b(seq + ct, key=self._mac, digest_size=TAG).digest()
        return base64.b64encode(ct + tag)


class Opener:
    """Checks and decrypts the other end's frames, in the order they were sent."""

    def __init__(self, enc: bytes, mac: bytes):
        self._enc, self._mac, self._seq = enc, mac, 0

    def open(self, line: bytes) -> bytes:
        try:
            raw = base64.b64decode(line, validate=True)
        except (ValueError, TypeError):
            raise SecureError("a frame that is not an encrypted frame") from None
        if len(raw) < TAG:
            raise SecureError("a frame too short to be genuine")
        ct, tag = raw[:-TAG], raw[-TAG:]
        seq = self._seq.to_bytes(8, "big")
        want = hashlib.blake2b(seq + ct, key=self._mac, digest_size=TAG).digest()
        if not hmac.compare_digest(want, tag):
            raise SecureError("a frame that was altered, replayed or out of order")
        self._seq += 1
        return _xor(ct, _keystream(self._enc, seq, len(ct))) if ct else b""
