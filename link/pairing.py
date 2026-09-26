"""Passwords for pairing, generated rather than chosen.

Finding devices by name means a machine on the network can answer "I am aio"
without being it. It still cannot connect - the handshake needs proof of the
password - but it does get one proof to take away and guess against offline, as
fast as its hardware allows. A password somebody picked ("1234", a pet's name)
falls to that in seconds. So every device generates one, the way remote desktop
tools do, and a person reads it across to the other machine.

12 characters from 31 that cannot be confused with each other (no 0/o, 1/l/i),
in three groups: 31^12, about 59 bits. Beyond offline guessing on anything a
home network is likely to contain, and still short enough to read out loud.

The dashes are only there to make it readable. Whether someone types them is a
coin toss - it was reported as "not sure that has to be entered or not" - so
they do not count, and neither do spaces or capitals.
"""
from __future__ import annotations

import secrets

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
MIN_LENGTH = 8


def new_password() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return display(raw)


def normalise(pw) -> str:
    """What gets stored and proved: no spaces, no dashes, lower case. Everything
    else is kept exactly."""
    return "".join(ch for ch in str(pw or "")
                   if not ch.isspace() and ch != "-").lower()


def display(pw) -> str:
    """For showing: a generated password in its three groups of four. Anything
    else is shown as it is."""
    p = normalise(pw)
    if len(p) == 12 and all(c in ALPHABET for c in p):
        return f"{p[0:4]}-{p[4:8]}-{p[8:12]}"
    return str(pw or "")


def problem(pw) -> str | None:
    """Why this password will not do, or None if it will."""
    pw = normalise(pw)
    if len(pw) < MIN_LENGTH:
        return (f"use a password of at least {MIN_LENGTH} characters - it is what "
                f"stops another machine on this network from connecting")
    return None
