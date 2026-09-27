"""Encryption for the link: X25519, HKDF-SHA256, ChaCha20-Poly1305.

Every connection runs an ephemeral X25519 key exchange inside the password
handshake. Both public keys are bound into the password proofs, so nobody
without the password can sit in the middle; the session keys come from the
exchange AND the password, and are thrown away with the connection - so traffic
recorded today stays unreadable even if the password is guessed tomorrow
(forward secrecy).

After the handshake every frame, in both directions, is sealed with
ChaCha20-Poly1305 (RFC 8439), an authenticated cipher:

    key     one per direction, 32 bytes, from HKDF-SHA256 (RFC 5869) over the
            X25519 secret and the password key
    nonce   the frame's number in that direction, never sent: TCP delivers
            frames in order, so a dropped, replayed, reordered or altered
            frame fails authentication and ends the connection
    frame   base64(ciphertext || 16-byte tag), one line

These are the same primitives WireGuard and TLS 1.3 use, from the
`cryptography` library (python3-cryptography on Debian and Ubuntu; bundled in
the Windows program). An earlier version built its cipher from the standard
library's hashes; standard constructions from a vetted library are what
anyone reviewing a program that carries keystrokes should find.

WHY NOT TLS. Python's own TLS cannot key a connection from a shared password
before 3.13, and certificates would mean a certificate authority or
trust-on-first-use prompts. A password-authenticated exchange is what pairing
by name and password needs.
"""
from __future__ import annotations

import base64

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey)
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError as e:                       # a clear word, not a traceback
    raise ImportError(
        "Nishro Link needs the 'cryptography' package: on Debian or Ubuntu "
        "'sudo apt install python3-cryptography', elsewhere "
        "'pip install cryptography'") from e

TAG = 16
KEY_HEX = 64                  # an X25519 public key: 32 bytes, as hex


class SecureError(Exception):
    """A frame that did not come from the other end, intact and in order."""


# ------------------------------------------------------------ key exchange
def keypair():
    """(private key, public key as hex) for one connection."""
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw)
    return private, public.hex()


def shared(private, peer_public: str) -> bytes:
    """The X25519 secret. Refuses a key that is not 32 bytes of hex, and one
    of the low-order points that would make the secret all zeros."""
    try:
        raw = bytes.fromhex(str(peer_public))
    except (TypeError, ValueError):
        raise SecureError("the other side's key is not a key") from None
    if len(raw) != 32:
        raise SecureError("the other side's key is the wrong size")
    try:
        secret = private.exchange(X25519PublicKey.from_public_bytes(raw))
    except ValueError:
        raise SecureError("the other side's key is not valid") from None
    if not any(secret):
        raise SecureError("the other side's key is not valid")
    return secret


def session(secret: bytes, psk: str, hub_nonce: str, dialer_nonce: str,
            role: str):
    """(Sealer, Opener) for this end. `psk` is the password key both sides
    proved; mixing it in means the keys need the password AND the exchange."""
    okm = HKDF(algorithm=hashes.SHA256(), length=64,
               salt=f"{hub_nonce}|{dialer_nonce}".encode(),
               info=b"nishro-link v8 keys").derive(secret + (psk or "").encode())
    hub_to_dialer, dialer_to_hub = okm[:32], okm[32:]
    send, recv = ((hub_to_dialer, dialer_to_hub) if role == "hub"
                  else (dialer_to_hub, hub_to_dialer))
    return Sealer(send), Opener(recv)


# ------------------------------------------------------------------ frames
def _nonce(seq: int) -> bytes:
    """96 bits: four zero bytes and the frame's number. Unique for the life
    of a key, which is one connection, in one direction."""
    return b"\0\0\0\0" + seq.to_bytes(8, "big")


class Sealer:
    """Encrypts this end's frames, numbering them."""

    def __init__(self, key: bytes):
        self._aead, self._seq = ChaCha20Poly1305(key), 0

    def seal(self, plain: bytes) -> bytes:
        nonce = _nonce(self._seq)
        self._seq += 1
        return base64.b64encode(self._aead.encrypt(nonce, bytes(plain), None))


class Opener:
    """Checks and decrypts the other end's frames, in the order they were sent."""

    def __init__(self, key: bytes):
        self._aead, self._seq = ChaCha20Poly1305(key), 0

    def open(self, line: bytes) -> bytes:
        try:
            raw = base64.b64decode(line, validate=True)
        except (ValueError, TypeError):
            raise SecureError("a frame that is not an encrypted frame") from None
        if len(raw) < TAG:
            raise SecureError("a frame too short to be genuine")
        try:
            plain = self._aead.decrypt(_nonce(self._seq), raw, None)
        except InvalidTag:
            raise SecureError("a frame that was altered, replayed or out of order") \
                from None
        self._seq += 1
        return plain
