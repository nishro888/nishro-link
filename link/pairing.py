"""Passwords for pairing: generated as words, proved through a slow key.

Finding devices by name means a machine on the network can answer "I am aio"
without being it. It still cannot connect - the handshake needs proof of the
password - but it does get one proof to take away and guess against offline, as
fast as its hardware allows. A password somebody picked ("1234", a pet's name)
falls to that in seconds. So every device generates one.

WORDS, because the password is read off one screen and typed on another:
"tiger-lemon-coral-radio" is read at a glance and typed without looking back,
where "k7qm-2xvp-9hdt" was copied a character at a time. Four words from the
list in words.py (1,239) are 41 bits.

A SLOW KEY makes up the rest. What is proved is not the password but a key
derived from it with PBKDF2-SHA256 at 2^19 iterations - half a second, once,
on the slowest machine this runs on, and then cached - so every offline guess
costs 2^19 hashes instead of one: 41 + 19 = 60 bits of work, a little more
than the twelve random characters it replaces (59). The salt is the hub's
device ID, so a table built against one group is useless against any other.

Dashes, spaces and capitals do not count. They are there to make it readable,
and whether someone types them is a coin toss.
"""
from __future__ import annotations

import functools
import hashlib
import secrets

from .words import WORDS

WORD_COUNT = 4
ITERATIONS = 2 ** 19
MIN_LENGTH = 8
# The old generated style, still accepted and still shown in its groups.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_password() -> str:
    return "-".join(secrets.choice(WORDS) for _ in range(WORD_COUNT))


def normalise(pw) -> str:
    """What the key is made from: no spaces, no dashes, lower case. Everything
    else is kept exactly."""
    return "".join(ch for ch in str(pw or "")
                   if not ch.isspace() and ch != "-").lower()


def display(pw) -> str:
    """For showing: words separated by dashes, an older twelve-character one
    in its three groups, anything else as it was typed."""
    text = str(pw or "")
    p = normalise(text)
    words = split_words(p)
    if words and len(words) >= 3:
        return "-".join(words)
    if len(p) == 12 and all(c in ALPHABET for c in p):
        return f"{p[0:4]}-{p[4:8]}-{p[8:12]}"
    return text


_WORD_SET = frozenset(WORDS)


def split_words(p: str):
    """`p` (normalised) as a run of list words, fewest first; None if it is not
    one. For showing a password that arrived without its dashes."""
    if not p or not p.isalpha():
        return None
    best = [None] * (len(p) + 1)
    best[0] = []
    for i in range(len(p)):
        if best[i] is None:
            continue
        for n in (3, 4, 5):
            w = p[i:i + n]
            if len(w) == n and w in _WORD_SET:
                cand = best[i] + [w]
                if best[i + n] is None or len(cand) < len(best[i + n]):
                    best[i + n] = cand
    return best[len(p)]


def key(pw, salt) -> str:
    """The secret that is actually proved: PBKDF2 of the password, salted with
    the hub's device ID. No password, no key - and no protection, which the
    program warns about loudly."""
    p = normalise(pw)
    if not p:
        return ""
    return _derive(p, str(salt or ""), ITERATIONS)


@functools.lru_cache(maxsize=64)
def _derive(p: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", p.encode("utf-8"),
                               f"nishro-link|{salt}".encode("utf-8"),
                               iterations).hex()


def problem(pw) -> str | None:
    """Why this password will not do, or None if it will."""
    pw = normalise(pw)
    if len(pw) < MIN_LENGTH:
        return (f"use a password of at least {MIN_LENGTH} characters - it is what "
                f"stops another machine on this network from connecting")
    return None
