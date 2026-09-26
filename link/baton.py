"""The baton: who is driving right now, and who is therefore suppressed.

Exactly one node holds the baton. It owns the cursor (cursor.py) and captures
its own physical input; every other node suppresses its mouse and injects what
it is told. Touch a machine's mouse and it claims. See DESIGN.md section 3.

Pure logic - the clock is injected - so contention, split-brain and the P2
watchdog are proved in milliseconds in tests rather than hunted on real
hardware by losing both mice at once.

Three pieces, because three different machines need three different things:

    ClaimDetector   on every node: is this local input a bid to drive?
    Arbiter         on the hub only: the single authority on who holds it.
    BatonState      on every node: what we believe, and the P2 watchdog.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

POLICIES = ("motion", "click", "hotkey")


def now_ms() -> float:
    """Monotonic milliseconds. Monotonic, not wall clock: a clock that can go
    backwards would make a watchdog think no time had passed."""
    return time.monotonic() * 1000.0


_now_ms = now_ms          # the name the rest of this module already uses


@dataclass(frozen=True)
class Grant:
    """The hub's authoritative answer to 'who is driving'.

    Carries the whole handover state so the new holder makes its state match
    rather than inferring it from a stream whose beginning it may have missed -
    the self-healing form of P3.
    """
    holder: str
    epoch: int
    screen: str
    x: int = 0
    y: int = 0
    held: tuple = ()


# --------------------------------------------------------------- every node
class ClaimDetector:
    """Is this local input a deliberate bid to drive, or just a knock?

    Motion accumulates within a short window, so a desk bump cannot steal
    control but reaching for the mouse takes it immediately.

    Injected input must NEVER reach this. That is P6, and it is not a detail:
    every node captures and injects at the same time, so feeding an injection
    back in here makes A's injection claim B's baton, forever, at wire speed.
    """

    def __init__(self, policy: str = "motion", threshold: int = 8,
                 window_ms: float = 300.0, clock=_now_ms):
        if policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}, got {policy!r}")
        self.policy = policy
        self.threshold = int(threshold)
        self.window_ms = float(window_ms)
        self._clock = clock
        self._accum = 0
        self._started = None

    def motion(self, dx: int, dy: int) -> bool:
        if self.policy != "motion":
            return False
        now = self._clock()
        if self._started is None or now - self._started > self.window_ms:
            self._started, self._accum = now, 0
        # Manhattan distance: a pixel is a pixel, and it avoids a sqrt per event
        # on a path that runs for every mouse move.
        self._accum += abs(int(dx)) + abs(int(dy))
        if self._accum >= self.threshold:
            self.reset()
            return True
        return False

    def button(self, down: bool) -> bool:
        """A click is always deliberate - unless the policy is hotkey-only."""
        if not down or self.policy == "hotkey":
            return False
        self.reset()
        return True

    def hotkey(self) -> bool:
        self.reset()
        return True

    def reset(self) -> None:
        self._accum = 0
        self._started = None


# ------------------------------------------------------------------ the hub
class Arbiter:
    """Hub-side: the single authority on who holds the baton.

    All claims come here. A claim is rare - only when you swap mice - so the
    round trip costs nothing, and central arbitration makes split-brain
    impossible rather than merely unlikely.
    """

    def __init__(self, holder: str, screen: str, x: int = 0, y: int = 0,
                 min_hold_ms: float = 200.0, clock=_now_ms):
        self._grant = Grant(holder, 1, screen, int(x), int(y))
        self.min_hold_ms = float(min_hold_ms)
        self._clock = clock
        # None, not clock(): the anti-thrash guard exists to stop rapid
        # HANDOVERS, and at startup there has not been one. Seeding it with the
        # current time would refuse every claim for the first min_hold_ms of
        # process life, so a node starting up next to a peer you are already
        # using could not take the baton.
        self._granted_at = None

    @property
    def grant(self) -> Grant:
        return self._grant

    @property
    def holder(self) -> str:
        return self._grant.holder

    @property
    def epoch(self) -> int:
        return self._grant.epoch

    def claim(self, node: str, screen: str, x: int, y: int,
              may_drive: bool = True) -> Grant | None:
        """Grant the baton to `node`, or None if the claim is refused or moot.

        `screen`/`x`/`y` are where the cursor should appear: the claiming node's
        remembered home, because the cursor jumps to the machine you touched.
        """
        if not may_drive:
            return None                       # policy says this node never drives
        if node == self._grant.holder:
            return None                       # already yours; nothing to broadcast
        if (self._granted_at is not None
                and self._clock() - self._granted_at < self.min_hold_ms):
            # Anti-thrash. The claim threshold already filters knocks; this stops
            # two mice being jostled at once from ping-ponging the baton. A real
            # hand moving between two mice takes far longer than this.
            return None
        self._grant = Grant(node, self._grant.epoch + 1, screen, int(x), int(y))
        self._granted_at = self._clock()
        return self._grant

    def take(self, node: str, screen: str, x: int, y: int) -> Grant:
        """Grant unconditionally, under a new epoch. For the failsafe only.

        claim() refuses when `node` already holds and during the anti-thrash
        window, both right for a hand on a mouse and both wrong for someone who
        has just asked for their machine back. The new epoch matters too: it
        makes any input still in flight from the old holder stale on arrival.
        """
        self._grant = Grant(node, self._grant.epoch + 1, screen, int(x), int(y))
        self._granted_at = self._clock()
        return self._grant

    def move_cursor(self, screen: str, x: int, y: int) -> None:
        """The holder reports where the cursor went, so a later handover or
        resume starts from the truth rather than from where it last changed hands."""
        self._grant = replace(self._grant, screen=screen, x=int(x), y=int(y))

    def resume(self) -> Grant:
        """Reconnect: re-grant under a FRESH epoch.

        Events still in flight from before the drop carry the old epoch and are
        then discarded on arrival by the ordinary staleness rule, so a resumed
        link cannot be hit by a burst of pre-disconnect input.

        Deliberately does NOT arm the anti-thrash guard: the holder is unchanged,
        so this is not a handover. Arming it here meant every claim in the first
        min_hold_ms after a connection was silently dropped - so reaching for the
        other machine's mouse right after it connected did nothing at all.
        """
        self._grant = replace(self._grant, epoch=self._grant.epoch + 1, held=())
        return self._grant


# --------------------------------------------------------------- every node
class BatonState:
    """What this node believes about the baton - and the P2 watchdog.

    P2 is the peer model's hard safety property. In a master/slave design only
    one machine ever suppresses input, so there is always an escape. Here BOTH
    machines suppress, so a lost baton - crash, dead Wi-Fi, hung process - could
    kill both mice at once and leave the power button as the only way out.

    So: suppression requires a LIVE baton. `ttl_ms` without fresh traffic and
    this node un-suppresses unconditionally, whatever it last believed. Local
    input wins whenever anything is in doubt.

    "Fresh traffic" has to mean ANY traffic, not just input. A holder who simply
    is not moving the mouse sends nothing at all, so an idle peer would be
    indistinguishable from a crashed one - and suppression would drop out on a
    perfectly healthy link after a second of nobody touching anything. Found
    exactly that way on real hardware. The keep-alive ping is what feeds it, and
    its interval must stay well under ttl_ms: 400ms against 1500ms, so three can
    go missing before we call it dead.
    """

    def __init__(self, node: str, ttl_ms: float = 1500.0, clock=_now_ms):
        self.node = node
        self.ttl_ms = float(ttl_ms)
        self._clock = clock
        self.holder = None
        self.epoch = 0
        self._seen = None

    # ---- what we know ----
    def apply(self, grant: Grant) -> bool:
        """Adopt a grant. False if it is stale and was ignored."""
        if grant.epoch < self.epoch:
            return False
        self.holder, self.epoch = grant.holder, grant.epoch
        self._seen = self._clock()
        return True

    def accepts(self, epoch: int) -> bool:
        """Should an input message under this epoch be acted on?

        Exact match, not >=. Grants and input travel the same ordered TCP
        channel, so a newer epoch cannot arrive before the grant that created
        it; anything that does not match is genuinely stale.
        """
        return epoch == self.epoch

    def touch(self) -> None:
        """Fresh baton traffic seen - the watchdog stays fed."""
        self._seen = self._clock()

    def lost(self) -> None:
        """The link died. Forget the baton so P2 un-suppresses at once.

        The epoch is kept here, so anything still arriving on THIS link stays
        stale. A new connection is different - see restart().
        """
        self.holder = None
        self._seen = None

    def restart(self) -> None:
        """A new connection: epochs are whatever the hub now says they are.

        Keeping the old epoch across a reconnect was meant to make late arrivals
        from the old link stale. But nothing from the old link can arrive: it
        was a different socket, and its reader has returned before the next
        handshake starts. What keeping it DID do was make a restarted hub - which
        counts from 1 again - look stale for as long as its epoch stayed below
        ours. On the Ubuntu box that was every rebuild of the laptop: grants and
        input discarded, the cursor frozen on arrival, seven self-rescues.
        """
        self.holder = None
        self.epoch = 0
        self._seen = None

    def holds(self) -> bool:
        return self.holder == self.node

    def expired(self) -> bool:
        return self._seen is None or (self._clock() - self._seen) > self.ttl_ms

    # ---- what we do about it ----
    def suppress_mouse(self, cursor_remote: bool) -> bool:
        """Mouse suppression follows the BATON.

        A node that does not hold it suppresses its mouse, whose only remaining
        local effect is to claim. The holder suppresses only while driving a far
        screen, so its own pointer stays parked instead of drifting into a corner.
        """
        if self.holder is None or self.expired():
            # P2 overrides everything. "Nobody holds it" has to count as well as
            # "the watchdog ran out": keep-alive pings feed the watchdog whether
            # or not anyone holds the baton, so after a self-rescue the next ping
            # made a holderless baton look live, and "not holds()" then
            # suppressed this mouse on behalf of nobody at all.
            return False
        return (not self.holds()) or cursor_remote

    def suppress_keyboard(self, cursor_remote: bool) -> bool:
        """Keyboard suppression follows the CURSOR, not the baton.

        Whoever hosts the cursor types locally; everyone else captures and
        forwards. That is what lets you drive with one machine's mouse and type
        on the other machine's keyboard.
        """
        if self.holder is None or self.expired():
            return False                      # P2, as for the mouse
        return cursor_remote
