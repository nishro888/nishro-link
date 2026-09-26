"""Passwords for pairing, generated rather than chosen.

Finding devices by name means a machine on the network can answer "I am aio"
without being it. It still cannot connect - the handshake needs proof of the
password - but it does get one proof to take away and guess against offline, as
fast as its hardware allows. A password somebody picked ("1234", a pet's name)
falls to that in seconds. So the waiting side generates one, the way remote
desktop tools do, and the person reads it across to the other machine.

12 characters from 31 that cannot be confused with each other (no 0/o, 1/l/i),
in three groups: 31^12, about 59 bits. Beyond offline guessing on anything a
home network is likely to contain, and still short enough to read out loud.
"""
from __future__ import annotations

import secrets

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
MIN_LENGTH = 8


def new_password() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


def normalise(pw) -> str:
    """What gets stored and proved. Case and stray spaces are forgiven because a
    code read aloud and typed on another machine picks up both; everything else
    is kept exactly."""
    return "".join(str(pw or "").split()).lower()


def problem(pw) -> str | None:
    """Why this password will not do, or None if it will."""
    pw = normalise(pw)
    if len(pw) < MIN_LENGTH:
        return (f"use a password of at least {MIN_LENGTH} characters - it is what "
                f"stops another machine on this network from connecting")
    return None
