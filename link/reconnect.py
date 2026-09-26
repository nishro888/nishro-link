"""Reconnecting after a drop, and deciding what may be resumed.

See DESIGN.md section 7. Pure logic with the clock and the RNG injected, so a
two-hour flapping link is proved in milliseconds instead of waited out.

The old client.py retried on a flat `time.sleep(1.5)`, which is wrong in both
directions at once: too slow for a Wi-Fi blip, and far too fast for a machine
that is simply switched off, which it would hammer all night.
"""
from __future__ import annotations

import random
import time
import uuid

_MAX_SHIFT = 32   # 2**32 * base already dwarfs any sane cap; don't build bignums


def _now_ms() -> float:
    return time.monotonic() * 1000.0


class Backoff:
    """Exponential backoff with equal jitter, a flap guard, and escalation.

        attempt 0   immediate                    most drops are transient
        otherwise   d = min(cap, base * 2**n)
        sleep       d/2 + random(0, d/2)         equal jitter

    giving roughly 0, .19, .38, .75, 1.5, 3, 6, 11, 11-15 seconds.

    Jitter is not ceremony: without it two nodes dropped by the same event retry
    in lockstep forever, and the backoff can phase-lock with whatever periodic
    thing broke the link in the first place.
    """

    def __init__(self, base_ms: float = 250.0, cap_ms: float = 15000.0,
                 stable_after_ms: float = 5000.0, rediscover_after: int = 6,
                 clock=_now_ms, rng=random.random):
        self.base_ms = float(base_ms)
        self.cap_ms = float(cap_ms)
        self.stable_after_ms = float(stable_after_ms)
        self.rediscover_after = int(rediscover_after)
        self._clock = clock
        self._rng = rng
        self._failures = 0
        self._connected_at = None

    @property
    def failures(self) -> int:
        return self._failures

    def next_delay_ms(self) -> float:
        """How long to wait before the next attempt."""
        if self._failures == 0:
            return 0.0                        # most drops are a blip; try at once
        shift = min(self._failures - 1, _MAX_SHIFT)
        d = min(self.cap_ms, self.base_ms * (2 ** shift))
        return d / 2.0 + self._rng() * (d / 2.0)

    # ---- outcomes ----
    def on_failure(self) -> None:
        """A connection attempt failed outright."""
        self._failures += 1
        self._connected_at = None

    def on_connected(self) -> None:
        """Connected - but the counter does NOT reset yet. See on_disconnected."""
        self._connected_at = self._clock()

    def on_disconnected(self) -> None:
        """A live connection dropped.

        The counter resets only if that connection was HEALTHY - not merely
        established. A link that dies 200ms after connecting is failing, and
        resetting on connect would retry it at full speed indefinitely.
        """
        if self.stable():
            self._failures = 0                # earned a fast retry
        else:
            self._failures += 1               # flapping; keep backing off
        self._connected_at = None

    def stable(self) -> bool:
        return (self._connected_at is not None
                and self._clock() - self._connected_at >= self.stable_after_ms)

    def target(self) -> str:
        """Escalate in kind, not only in delay.

        A peer whose DHCP lease moved is unreachable at its old address no matter
        how patiently you wait, so past a point we alternate the configured
        address with LAN discovery rather than just waiting longer.
        """
        if self._failures < self.rediscover_after:
            return "address"
        return "discovery" if self._failures % 2 == 0 else "address"

    def reset(self) -> None:
        self._failures = 0
        self._connected_at = None


class SessionStore:
    """Hub-side: which sessions a returning peer may still resume.

    Only ever gates BULK state - file transfer offsets, the last clipboard
    announcement. Input state is never resumed: the baton is re-granted under a
    fresh epoch and the held-key set starts empty, because P3 already released
    everything and re-pressing keys the user has physically let go of would be
    worse than forgetting them.
    """

    def __init__(self, grace_ms: float = 60000.0, clock=_now_ms):
        self.grace_ms = float(grace_ms)
        self._clock = clock
        self._sessions: dict[str, dict] = {}

    def open(self, node: str) -> str:
        sid = uuid.uuid4().hex
        self._sessions[sid] = {"node": node, "seen": self._clock()}
        return sid

    def touch(self, sid: str) -> None:
        s = self._sessions.get(sid)
        if s is not None:
            s["seen"] = self._clock()

    def close(self, sid: str) -> None:
        """Peer dropped. Keep it around for `grace_ms` in case it comes back."""
        self.touch(sid)

    def can_resume(self, sid: str, node: str = None) -> bool:
        s = self._sessions.get(sid)
        if s is None:
            return False
        if node is not None and s["node"] != node:
            return False                      # a session belongs to one node
        return (self._clock() - s["seen"]) <= self.grace_ms

    def forget(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def sweep(self) -> int:
        """Drop sessions past the grace window. Returns how many went."""
        now = self._clock()
        dead = [k for k, v in self._sessions.items()
                if now - v["seen"] > self.grace_ms]
        for k in dead:
            del self._sessions[k]
        return len(dead)

    def __len__(self) -> int:
        return len(self._sessions)
