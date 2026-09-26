"""One Nishro Link node: captures AND injects, and may hold the baton.

This replaces the old server.py/client.py split. Under the peer model there is
no server build and no client build - every machine runs this, and what differs
is configuration plus one token that moves. See DESIGN.md section 2.

NodeCore here is the pure state machine: local input and wire messages go in,
`Actions` come out, and nothing touches a socket or an OS. The I/O shell that
drives it lives in Node (sockets, threads, capture, injection). Keeping them
apart is what makes the interesting half - who is suppressed, what crosses which
edge, which messages are stale - provable in tests.

Coordinate note. Two different things move, and confusing them is how cursors
drift:

    the VIRTUAL cursor   what cursor.py tracks, spanning every screen
    the OS pointer       what the local windowing system draws

While we hold the baton and the virtual cursor is on one of our own screens we
do not suppress, so the OS pointer moves under its own acceleration curve and is
the truth: we slave the virtual cursor to it. The OS clamps that pointer at the
screen edge, so pushing further produces movement the OS throws away - and that
discarded part is exactly what carries the cursor onto the next screen.
"""
from __future__ import annotations

import queue
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field

from . import protocol, runtime
from .baton import Arbiter, BatonState, ClaimDetector, Grant, now_ms
from .clip import ClipboardSync
from .motion import Cursor, exits, to_pixels
from .desk import Desk, beside, normal_parts
from .reconnect import Backoff, SessionStore
from .runtime import firewall_hint

CLAIM_RETRY_MS = 400     # how long to wait before asking for control again
CLAIM_TRIES = 4          # then stop waiting: ~1.6s from first ask to self-rescue

# "Leave this setting alone". Needed because None is a value reconfigure() has to
# be able to SET - unpairing is peer_addr = None - and a sentinel that collides
# with a real value silently drops the change.
KEEP = object()


@dataclass
class Actions:
    """What the shell should do about whatever just happened.

    `inject` entries are ("move_abs", x, y) | ("button", name, down)
    | ("key", code, down) | ("wheel", dx, dy).
    """
    send: list = field(default_factory=list)
    inject: list = field(default_factory=list)
    gave_up: bool = False        # we stopped waiting for control and took it back
    placement_changed: bool = False   # the hub's arrangement changed; persist it

    def __bool__(self) -> bool:
        return bool(self.send or self.inject or self.placement_changed)


class NodeCore:
    def __init__(self, node: str, layout: Desk, policy: dict = None,
                 is_hub: bool = False, screen: str = None, clock=None,
                 side: str = "left", placement=None):
        self.node = node
        self.layout = layout
        self.policy = dict(policy or {})
        self.is_hub = is_hub
        self.side = side              # where the peer sits; only the hub uses it
        # Boxes on a shared desk, if the layout came from the arrangement editor.
        # When set, this is the source of truth and the layout is derived from it.
        # `placement` is accepted and ignored: the desk carries every position
        # itself now, and self.placement is read from it.
        self._clock = clock or now_ms
        # An unanswered request for control of our own machine. See check_claim:
        # this is what stops a refused claim leaving someone with a computer
        # they cannot type on.
        self._claim_at = None
        self._claim_tries = 0
        # Peer NODES connected right now. The pointer may enter only their
        # machines and ours: an arrangement lists machines that are switched
        # off, and pushing into one of those flipped the cursor between the
        # two machines dozens of times a second - seen on the laptop, with
        # linking off - or, with linking on, froze this machine's pointer while
        # it steered a screen that was not there.
        self.online = set()
        self.devices = []             # the group as the hub last described it

        mine = layout.mine()
        if not mine:
            raise ValueError(f"node {node!r} owns no screen in this layout")
        start = screen or mine[0]
        s = layout.get(start)

        kw = {"clock": clock} if clock else {}
        self._clock_kw = kw
        self.cursor = Cursor(layout, start, s.w // 2, s.h // 2)
        self.baton = BatonState(node, **kw)
        self.claims = ClaimDetector(self.policy.get("claim", "motion"), **kw)
        self.arbiter = Arbiter(node, start, s.w // 2, s.h // 2, **kw) if is_hub else None

        # Where our own pointer sits when the cursor is elsewhere, so a claim can
        # bring it back to where we left off rather than to an arbitrary corner.
        self.home = (start, s.w // 2, s.h // 2)

        # What WE have injected and not yet released. P3 hangs off this: a key
        # latched on a uinput device outlives the process that set it.
        self._held_keys: set = set()
        self._held_buttons: set = set()

        if is_hub:                       # the hub starts out holding its own baton
            self.baton.apply(self.arbiter.grant)

    # ------------------------------------------------------------- queries
    @property
    def epoch(self) -> int:
        return self.baton.epoch

    def holds(self) -> bool:
        return self.baton.holds()

    def cursor_is_remote(self) -> bool:
        """Is the shared cursor somewhere other than one of OUR screens?"""
        return not self.layout.is_local(self.cursor.screen)

    # ------------------------------------------------- learning the layout
    def adopt_layout(self, lay: Desk, keep_cursor: bool = False) -> None:
        """Replace the screen graph.

        In a handshake nothing has crossed yet, so the cursor starts fresh on our
        own screen. A change made mid-session is different: the cursor is in
        use, maybe on the other machine, and jumping it home because someone
        dragged a box in a window would be a surprise. With keep_cursor it stays
        where it is, as long as that screen still exists.
        """
        old = getattr(self, "cursor", None)
        self.layout = lay
        if keep_cursor and old is not None and old.screen in lay.names():
            self.cursor = Cursor(lay, old.screen, old.x, old.y)
            if self.home[0] not in lay.names() or not lay.is_local(self.home[0]):
                mine = lay.mine()[0]
                s = lay.get(mine)
                self.home = (mine, s.w // 2, s.h // 2)
            return
        start = lay.mine()[0]
        s = lay.get(start)
        self.cursor = Cursor(lay, start, s.w // 2, s.h // 2)
        self.home = (start, s.w // 2, s.h // 2)
        if self.arbiter:
            self.arbiter.move_cursor(start, s.w // 2, s.h // 2)

    @property
    def placement(self) -> list:
        """The arrangement as boxes - what the config saves and the window draws.
        Read from the desk, which carries every position itself."""
        return self.layout.boxes()

    def adopt_peer(self, name: str, w: int, h: int, parts=(),
                   keep_cursor: bool = False) -> bool:
        """Learn a machine's name and desktop from its handshake.

        Both are already on the wire in `hello`, so asking a person to type them
        is asking for a fact the machines have and a chance to get it wrong.
        Where it SITS is the arrangement's business, and kept: a machine already
        on the desk keeps its place (resize() moves neighbours so nothing ends
        up underneath it), the placeholder for "the other machine" takes its
        real name, and a machine the desk has never seen goes against the edge
        of everything, on `side`, until someone drags it. Returns True if
        anything changed.
        """
        desk = self.layout
        w, h = int(w), int(h)
        want = normal_parts(parts, w, h)
        if name not in desk.names():
            owned = [n for n in desk.names()
                     if desk.get(n).owner == name and not desk.is_local(n)]
            placeholder = [n for n in desk.names()
                           if n == "peer" and not desk.is_local(n)]
            if owned or placeholder:
                desk.rename((owned or placeholder)[0], name, owner=name)
            else:
                beside(desk, name, w, h, name, self.side, want)
                self.adopt_layout(desk, keep_cursor=True)
                return True
        m = desk.get(name)
        if (m.w, m.h, m.parts, m.owner) == (w, h, want, name):
            return False
        desk.resize(name, w, h, want)
        self.adopt_layout(desk, keep_cursor=keep_cursor)
        return True

    def set_hub(self, on: bool) -> None:
        """Become - or stop being - the machine that listens and arbitrates.

        Pairing has to be able to do this without a restart: the whole point of
        "show my details" versus "enter theirs" is that the choice is made in the
        UI, once, and then it works. The arbiter is the state that differs, so it
        is created or dropped here and the baton is reset either way - keeping a
        grant issued under the old arrangement would be worse than starting over.
        """
        on = bool(on)
        if on == self.is_hub:
            return
        self.is_hub = on
        start = self.cursor.screen
        if on:
            self.arbiter = Arbiter(self.node, start, self.cursor.x, self.cursor.y,
                                   **self._clock_kw)
            self.baton.apply(self.arbiter.grant)
        else:
            self.arbiter = None
            self.baton.lost()            # P2: nothing suppressed until told again

    def set_placement(self, boxes, keep_cursor: bool = True) -> None:
        """Replace the arrangement."""
        self.adopt_layout(self.check_arrangement(boxes), keep_cursor=keep_cursor)

    def check_arrangement(self, boxes):
        """The desk these boxes describe, or ValueError saying what is wrong."""
        from . import desk as _desk
        if not boxes:
            raise ValueError("no screens given")
        need = {"name", "w", "h", "x", "y"}
        for b in boxes:
            missing = need - set(b)
            if missing:
                raise ValueError(f"a screen is missing {sorted(missing)}")
        names = [b["name"] for b in boxes]
        if len(set(names)) != len(names):
            raise ValueError("two screens have the same name")
        try:
            lay = _desk.place(self.node, boxes)
        except (KeyError, TypeError) as e:
            raise ValueError(str(e)) from e
        lay.check()                  # no screen of ours, or two overlapping
        return lay

    def arrange(self, boxes) -> Actions:
        """Rearrange the desk, from this machine's window.

        The hub keeps the one true layout. So on the hub this applies it and
        tells the peer at once; anywhere else it asks the hub, which applies it
        and sends it back. Both machines change together - the failure this
        replaces was an arrangement that reached one of them.
        """
        a = Actions()
        self.check_arrangement(boxes)             # refuse here, before anything
        if self.arbiter:
            self.set_placement(boxes)
            a.send.append(protocol.layout_msg(self.layout.to_dict(), self.placement))
            a.placement_changed = True
        else:
            a.send.append(protocol.arrange(boxes))
        return a

    def local_screen(self) -> str:
        """Which of our screens the OS pointer is on.

        v1 assumes one screen per node. Supporting several needs the capture to
        report which monitor a position came from; the layout model already
        handles it, so only the capture side is missing.
        """
        return self.layout.mine()[0]

    def suppress_mouse(self) -> bool:
        return self.baton.suppress_mouse(self.cursor_is_remote())

    def suppress_keyboard(self) -> bool:
        return self.baton.suppress_keyboard(self.cursor_is_remote())

    def may_drive(self) -> bool:
        return bool(self.policy.get("may_drive", True))

    def may_be_driven(self) -> bool:
        return bool(self.policy.get("may_be_driven", True))

    # --------------------------------------------------------- local input
    def local_pointer(self, screen: str, x: int, y: int, dx: int, dy: int) -> Actions:
        """The OS pointer moved on one of our screens, and we are NOT suppressing.

        The OS pointer is authoritative here - it has already applied whatever
        acceleration curve the user configured, and fighting it would make the
        virtual cursor disagree with what they can see. So we slave to it, and
        cross edges only on the movement the OS discarded at the boundary.
        """
        a = Actions()
        if not self.holds():
            return self._maybe_claim(a, self.claims.motion(dx, dy), "motion")

        self.cursor.warp(screen, x, y)
        self.home = (screen, x, y)
        eat_x, eat_y = exits(self.layout.get(screen), x, y, dx, dy)
        if eat_x or eat_y:
            self._advance(a, eat_x, eat_y)
        return a

    def local_motion(self, dx: int, dy: int) -> Actions:
        """A raw delta while we ARE suppressing - the OS pointer is pinned, so
        these deltas are the only movement information that exists."""
        a = Actions()
        if not self.holds():
            return self._maybe_claim(a, self.claims.motion(dx, dy), "motion")
        self._advance(a, dx, dy)
        return a

    def local_button(self, name: str, down: bool) -> Actions:
        a = Actions()
        if not self.holds():
            return self._maybe_claim(a, self.claims.button(down), "click")
        if self.cursor_is_remote():
            a.send.append(self._to_owner(protocol.button(name, down, epoch=self.epoch)))
        return a

    def local_wheel(self, dx: int, dy: int) -> Actions:
        a = Actions()
        if self.holds() and self.cursor_is_remote():
            a.send.append(self._to_owner(protocol.wheel(dx, dy, epoch=self.epoch)))
        return a

    def local_key(self, code: int, down: bool) -> Actions:
        """Keyboard follows the CURSOR, not the baton - so this forwards whenever
        the cursor is elsewhere, whether or not we are the one driving."""
        a = Actions()
        if self.cursor_is_remote():
            a.send.append(self._to_owner(protocol.key(code, down, epoch=self.epoch)))
        return a

    def local_failsafe(self) -> Actions:
        """Panic hotkey. Give up the baton and stop touching anything.

        Every node has one, not just the hub - in the peer model every node can
        be the one that is stuck.
        """
        a = Actions()
        if self.arbiter:
            # The hub cannot "let go" the way a peer does: the arbiter would go on
            # recording it as holder while its own state said nobody, so its next
            # claim was "already yours", nothing was granted, and it ended up
            # self-rescuing and unable to drive the other screen. Seen live. Here
            # the failsafe means what the person meant - this machine, now,
            # cursor home - and goes through the arbiter so everyone agrees.
            screen, x, y = self.home
            g = _grant_msg(self.arbiter.take(self.node, screen, x, y))
            a.send.append(g)
            self._apply_grant(a, g)             # releases injected keys (P3)
            self._claim_at, self._claim_tries = None, 0
            return a
        self._release_injected(a)
        self.baton.lost()
        a.send.append(protocol.release(self.node))
        return a

    # --------------------------------------------------------- wire input
    def on_message(self, msg: dict) -> Actions:
        a = Actions()
        t = msg.get("t")

        if t == "baton":
            self._apply_grant(a, msg)
        elif t == "claim":
            self._arbitrate(a, msg)
        elif t == "release":
            self._on_release(a, msg)
        elif t == "layout":
            if self._on_layout(msg):
                a.placement_changed = True
        elif t == "arrange":
            self._on_arrange(a, msg)
        elif t == "roster":
            self._on_roster(msg)
        elif t == "geom":
            self._on_geom(a, msg)
        elif t == "ping":
            a.send.append(protocol.pong(msg.get("i", 0)))
        elif t in ("p", "b", "w", "k"):
            self._on_input(a, msg, t)
        return a

    def _on_input(self, a: Actions, msg: dict, t: str) -> None:
        if protocol.is_stale(msg, self.epoch):
            return                       # from a holder that no longer holds
        self.baton.touch()               # traffic from the holder feeds the watchdog

        if t == "p":
            # Track the cursor even when it is on SOMEONE ELSE'S screen. That is
            # how we learn it has left ours - and therefore to stop typing
            # locally and start forwarding again. Ignoring those used to leave a
            # machine convinced it still hosted a cursor that had long gone.
            if msg["s"] not in self.layout.names():
                return                   # a screen we have never heard of
            s = self.layout.get(msg["s"])
            x, y = to_pixels(msg["x"], msg["y"], s.w, s.h)
            self.cursor.warp(msg["s"], x, y)
            if self.layout.is_local(msg["s"]) and self.may_be_driven():
                a.inject.append(("move_abs", x, y))
            return

        if not self.may_be_driven():
            return                       # policy: nothing is injected here, ever
        if t == "b":
            down = bool(msg["d"])
            # Always accept an UP, even if we think we should not: refusing one
            # is how a button gets stuck down forever.
            if down and self.cursor_is_remote():
                return
            a.inject.append(("button", msg["k"], down))
            (self._held_buttons.add if down else self._held_buttons.discard)(msg["k"])
        elif t == "w":
            a.inject.append(("wheel", msg["x"], msg["y"]))
        elif t == "k":
            # 0 up, 1 down, 2 auto-repeat. The value travels to the injector
            # intact; flattening a repeat into another press is the bug this
            # field exists to avoid.
            value = int(msg["d"])
            if value and self.cursor_is_remote():
                return                   # same reasoning as buttons
            a.inject.append(("key", msg["c"], value))
            if value:
                self._held_keys.add(msg["c"])
            else:
                self._held_keys.discard(msg["c"])

    # ------------------------------------------------------------ the hub
    def _arbitrate(self, a: Actions, msg: dict) -> None:
        """Only the hub answers claims. Serial arbitration is what makes
        split-brain impossible rather than merely unlikely."""
        if not self.arbiter:
            return
        who = msg.get("node")
        screen, x, y = self._home_of(who)
        grant = self.arbiter.claim(who, screen, x, y, may_drive=True)
        if grant:
            a.send.append(_grant_msg(grant))
            self._apply_grant(a, _grant_msg(grant))
        elif (who == self.node and self.arbiter.holder == self.node
              and not self.holds()):
            # The arbiter says we hold it and our own state says we do not. On
            # the hub those two are meant to be the same fact, and when they
            # drift the hub can never win its own claim ("already yours"). The
            # arbiter is the truth; adopt it.
            self._apply_grant(a, _grant_msg(self.arbiter.grant))

    def _on_layout(self, msg: dict) -> None:
        """The hub's arrangement. Adopted as it stands - the hub is the truth."""
        if self.arbiter:
            return False                  # we ARE the truth; nobody overrides it
        try:
            lay = Desk.from_dict(msg.get("layout") or {}, node=self.node)
        except (KeyError, ValueError, TypeError):
            return False
        if not lay.mine():
            return False                  # a layout without us in it is not ours
        self.adopt_layout(lay, keep_cursor=True)
        return True

    def _on_arrange(self, a: Actions, msg: dict) -> None:
        """The peer rearranged the desk in its own window."""
        if not self.arbiter:
            return
        try:
            self.set_placement(msg.get("placement") or [])
            a.placement_changed = True
        except ValueError:
            pass                          # answered below with what still stands
        # Either way the peer gets the layout that is now in force, so a refused
        # arrangement snaps back in its window instead of lingering there.
        a.send.append(protocol.layout_msg(self.layout.to_dict(), self.placement))

    def _on_roster(self, msg: dict) -> None:
        """Who is connected, as the hub sees it. Peers only hear from the hub,
        so this is the only way they learn which other machines are there."""
        if self.arbiter:
            return
        self.online = {n for n in msg.get("online") or [] if n != self.node}
        self.devices = list(msg.get("devices") or [])

    def _on_geom(self, a: Actions, msg: dict) -> None:
        """A peer's displays changed. The hub updates the arrangement, which
        then reaches everyone."""
        if not self.arbiter:
            return
        who = msg.get("from")
        screens = msg.get("screens") or []
        if not who or not screens:
            return
        s = screens[0]
        if self.adopt_peer(who, s["w"], s["h"], s.get("parts") or (), keep_cursor=True):
            a.send.append(protocol.layout_msg(self.layout.to_dict(), self.placement))
            a.placement_changed = True

    def own_geom(self, w: int, h: int, parts=()) -> Actions:
        """THIS machine's displays changed. The hub applies it and tells everyone;
        a peer tells the hub, which does the same."""
        a = Actions()
        me = self.layout.mine()[0]
        m = self.layout.get(me)
        want = normal_parts(parts, w, h)
        if (m.w, m.h, m.parts) == (int(w), int(h), want):
            return a
        if self.arbiter:
            self.layout.resize(me, w, h, want)
            self.adopt_layout(self.layout, keep_cursor=True)
            a.send.append(protocol.layout_msg(self.layout.to_dict(), self.placement))
            a.placement_changed = True
        else:
            a.send.append(protocol.geom([dict({"name": me, "w": int(w), "h": int(h)},
                                              **({"parts": [list(p) for p in want]}
                                                 if want else {}))]))
        return a

    def forget_machine(self, name: str) -> Actions:
        """Take a machine that is not connected off the arrangement (the hub
        only; everyone else hears it as a new layout)."""
        a = Actions()
        if (not self.arbiter or name not in self.layout.names()
                or self.layout.is_local(name)
                or self.layout.get(name).owner in self.online):
            return a
        self.layout.remove(name)
        self.adopt_layout(self.layout, keep_cursor=True)
        a.send.append(protocol.layout_msg(self.layout.to_dict(), self.placement))
        a.placement_changed = True
        return a

    def peer_lost(self, node: str) -> Actions:
        """One peer left - the others are still here. (A peer losing the hub
        is link_lost: everything goes then.)

        If it held control, or the cursor was on its machine, the hub takes
        control back and puts the cursor home - broadcast, so every machine
        agrees and releases anything it was holding down for the one that left.
        Otherwise it is simply a wall from now on.
        """
        a = Actions()
        self.online.discard(node)
        if not self.arbiter:
            return a
        on_it = (self.cursor.screen in self.layout.names()
                 and self.layout.get(self.cursor.screen).owner == node)
        if self.arbiter.holder == node or on_it:
            screen, x, y = self.home
            g = _grant_msg(self.arbiter.take(self.node, screen, x, y))
            a.send.append(g)
            self._apply_grant(a, g)
        return a

    def _on_release(self, a: Actions, msg: dict) -> None:
        if not self.arbiter or msg.get("node") != self.arbiter.holder:
            return
        screen, x, y = self.home
        grant = self.arbiter.claim(self.node, screen, x, y)   # hub takes it back
        if grant:
            a.send.append(_grant_msg(grant))
            self._apply_grant(a, _grant_msg(grant))

    def _home_of(self, who: str):
        """Where the cursor should appear when `who` claims: their remembered
        home, because the cursor jumps to the machine you touched."""
        if who == self.node:
            return self.home
        for name in self.layout.names():
            s = self.layout.get(name)
            if s.owner == who:
                return name, s.w // 2, s.h // 2
        return self.cursor.screen, self.cursor.x, self.cursor.y

    # --------------------------------------------------------- transitions
    def _apply_grant(self, a: Actions, msg: dict) -> None:
        g = Grant(msg["holder"], msg["epoch"], msg["screen"],
                  msg.get("x", 0), msg.get("y", 0), tuple(msg.get("held", ())))
        if g.screen not in self.layout.names():
            # A screen we have never heard of: the two machines were started with
            # names that do not agree. Ignore rather than raise, so a misconfigured
            # peer cannot take our input down with it.
            return
        if not self.baton.apply(g):
            return                       # stale grant, ignore
        # P3: whatever we were holding down on behalf of the old holder goes up.
        # The grant's own `held` set is authoritative for what should be down,
        # and across a handover that set is empty by construction.
        self._release_injected(a)
        self.cursor.warp(g.screen, g.x, g.y)
        if self.layout.is_local(g.screen):
            a.inject.append(("move_abs", g.x, g.y))
            self.home = (g.screen, g.x, g.y)
        self.claims.reset()

    def _advance(self, a: Actions, dx: int, dy: int) -> None:
        """Move the virtual cursor and say what that means on the wire."""
        was_remote = self.cursor_is_remote()
        spot = self.cursor.move(dx, dy, allowed=self.reachable())
        if self.arbiter:
            self.arbiter.move_cursor(spot.screen, spot.x, spot.y)
        if self.cursor_is_remote():
            nx, ny = self.cursor.norm()
            m = protocol.pos(spot.screen, nx, ny, epoch=self.epoch)
            # A crossing goes to everyone - every machine's keyboard follows the
            # cursor, so each must learn it has moved. Movement within one
            # machine goes only to that machine, which is the one that injects.
            a.send.append(m if spot.crossed else self._to_owner(m))
        else:
            self.home = (spot.screen, spot.x, spot.y)
            if was_remote:
                # It came home. Put the real pointer where the virtual one is,
                # or it would resume from wherever we parked it.
                a.inject.append(("move_abs", spot.x, spot.y))
                # And tell everyone else, once. While the cursor is on our own
                # screen we send nothing - that is the whole point - so without
                # this one message the machine we just left would go on believing
                # it still hosts the cursor, and keep typing locally.
                nx, ny = self.cursor.norm()
                a.send.append(protocol.pos(spot.screen, nx, ny, epoch=self.epoch))

    def _to_owner(self, msg: dict) -> dict:
        """Address a message to the node driving the machine the cursor is on."""
        msg["to"] = self.layout.get(self.cursor.screen).owner
        return msg

    def _maybe_claim(self, a: Actions, fired: bool, reason: str) -> Actions:
        if not (fired and self.may_drive()):
            return a
        self._claim_at = self._clock()
        self._claim_tries = 1
        if self.arbiter:
            # We ARE the hub. Arbitrating our own claim locally rather than
            # posting it to a peer that cannot answer it - which is what used to
            # happen, so the hub could give the baton away but never take it back.
            self._arbitrate(a, {"node": self.node})
        else:
            a.send.append(protocol.claim(self.node, reason))
        return a

    def check_claim(self) -> Actions:
        """Chase an unanswered request for control, and give up safely.

        A claim refused by the hub's anti-thrash guard is SILENT - the claimer
        is told nothing and nothing retries it. A real mouse papers over that by
        streaming events, but if the claim is lost, or the hub is wedged, or the
        link is half dead, the result is a machine whose mouse and keyboard both
        go nowhere and whose only escape is a hotkey nobody remembers.

        So: ask again, a few times. If control still has not arrived after
        CLAIM_GIVE_UP_MS, stop waiting and un-suppress locally. Handing the user
        back their own keyboard is always right; the worst case is that two
        machines are briefly both live, which is recoverable, unlike the
        alternative.
        """
        a = Actions()
        if self._claim_at is None:
            return a
        if self.holds():                      # granted; nothing left to chase
            self._claim_at, self._claim_tries = None, 0
            return a
        now = self._clock()
        if now - self._claim_at < CLAIM_RETRY_MS:
            return a
        if self._claim_tries < CLAIM_TRIES:
            self._claim_tries += 1
            self._claim_at = now
            if self.arbiter:
                # The hub decides its own claims, same as in _maybe_claim.
                # Retries used to go out over the wire, to a peer that cannot
                # grant anything - so a hub refused once by the anti-thrash
                # guard never asked the one party that could say yes.
                self._arbitrate(a, {"node": self.node})
            else:
                a.send.append(protocol.claim(self.node, "retry"))
            return a
        self._claim_at, self._claim_tries = None, 0
        self._release_injected(a)
        self.baton.lost()                     # P2 path: local input wins
        a.send.append(protocol.release(self.node))
        a.gave_up = True
        return a

    def _release_injected(self, a: Actions) -> None:
        """Let go of everything we put down. Any transition, without exception."""
        for code in sorted(self._held_keys):
            a.inject.append(("key", code, False))
        for name in sorted(self._held_buttons):
            a.inject.append(("button", name, False))
        self._held_keys.clear()
        self._held_buttons.clear()

    def peer_online(self, node: str, on: bool = True) -> None:
        if on:
            self.online.add(node)
        else:
            self.online.discard(node)

    def reachable(self) -> set:
        """Machines the pointer may enter now: ours, and connected peers'."""
        return {m.name for m in self.layout.machines()
                if m.owner == self.node or m.owner in self.online}

    def link_lost(self) -> Actions:
        """The connection died. P2 and P3, immediately and without asking anyone."""
        a = Actions()
        self._release_injected(a)
        self.baton.lost()
        self.online.clear()
        if self.cursor.screen not in self.reachable():
            # The machine the cursor was on is gone. Bring it home - to where it
            # left this machine - and the real pointer with it, rather than
            # leave the cursor "on" a screen that is no longer there until
            # someone happens to claim control back.
            screen, x, y = self.home
            self.cursor.warp(screen, x, y)
            a.inject.append(("move_abs", self.cursor.x, self.cursor.y))
        return a

    def new_session(self) -> None:
        """A fresh, authenticated connection is about to hand us its grant.

        Adopt its epochs as they are - the hub may be a new process counting from
        1 - and drop any claim still being chased from the last link, which would
        otherwise run out its retries against a hub that never saw it and fire a
        self-rescue on a connection that had done nothing wrong.
        """
        self.baton.restart()
        self._claim_at, self._claim_tries = None, 0


class _Link:
    """One live connection: to the hub (on a peer) or to one peer (on the hub)."""

    __slots__ = ("name", "ch", "addr", "since", "rtt_ms", "ping_at")

    def __init__(self, name, ch, addr):
        self.name, self.ch, self.addr = name, ch, addr
        self.since = time.time()
        self.rtt_ms = None
        self.ping_at = {}


class Node:
    """The I/O shell around NodeCore: sockets, threads, capture and injection.

    Everything that decides anything lives in NodeCore. This part only moves
    bytes and calls the OS, so when something misbehaves there are two places to
    look and only one of them needs a second machine to reproduce.

    A `capture` must provide:
        start(sink)                     sink gets the on_* callbacks below
        stop()
        set_suppress(mouse, keyboard)   cheap; called several times a second

    and call, from its own thread:
        sink.on_pointer(x, y, dx, dy)   OS pointer moved, we are NOT suppressing
        sink.on_motion(dx, dy)          raw delta while we ARE suppressing
        sink.on_button(name, down) / on_wheel(dx, dy) / on_key(code, down)
        sink.on_failsafe()              panic hotkey
    """

    TICK_S = 0.2          # how often suppression is re-evaluated (P2 is on a clock)
    # Ticks between keep-alives. This MUST stay well under BatonState.ttl_ms:
    # the ping is the only traffic on an idle link, and it is what stops an idle
    # holder looking like a dead one. 2 ticks = 400ms against a 1500ms TTL.
    PING_EVERY = 2

    def __init__(self, core: NodeCore, capture, injector, port: int = 8770,
                 pin: str = "", peer_addr: str = None, on_log=None,
                 device_id: str = "", peer_name: str = None, peer_id: str = None):
        self.core = core
        self.capture = capture
        self.injector = injector
        self.port = port
        self.pin = pin
        # Who we dial, as a NAME and a permanent ID. peer_addr is only where it
        # was last found: DHCP moves machines, so it is tried first because it
        # is usually still right, and looked up again when it is not.
        self.peer_name = peer_name or None
        self.peer_id = peer_id or None
        self.peer_addr = peer_addr
        self.device_id = device_id or secrets.token_hex(8)
        self._log = on_log or (lambda *a: None)

        # Live connections by the name of the node at the other end. A peer has
        # one - to the hub. The hub has one per connected peer, and routes
        # between them: a peer only ever talks to the hub.
        self.links: dict = {}
        self._links_lock = threading.Lock()
        # () -> desktop.Desktop, to notice monitors being plugged in or out.
        # Set by the program; tests leave it unset and nothing is watched.
        self.detect_desktop = None
        self.desktop_now = None
        self.DISPLAYS_EVERY_S = 3.0
        self.on_devices = None        # (event, name, info) - a device joined or left
        self.session = None
        # Two misses at the remembered address and we look for the peer by name
        # instead of waiting out four more - a lease that moved will not move back.
        self.backoff = Backoff(rediscover_after=2)
        self.responder = None
        self.on_paired = None         # (name, id, addr) once a dial has worked
        # Where discovery asks. Tests point it at loopback; everything else
        # leaves it alone and broadcasts on the local network.
        self.discover_hints = ()
        self.discover_broadcast = True
        self.last_found = None        # what the most recent search turned up
        self.sessions = SessionStore() if core.is_hub else None
        self.rtt_ms = None
        self.trusted_peer = None      # the last to prove itself, for the UI
        self.on_placement = None      # called with the hub's new arrangement

        self._stop = False
        self._reconfig = False
        self._srv_sock = None
        self._noted = None            # last (holder, screen, suppression) we logged
        # Linking can be turned off while the process keeps running. A tray or
        # settings UI needs that: killing the process would take the UI with it.
        self.enabled = True
        self.clip = ClipboardSync(core.node)
        # Clipboard work never runs on the reader or a hook: clip.get()/set()
        # shell out (~200ms on Windows), which would stall input for as long as
        # it takes. The reader only ever enqueues.
        self._clip_q: queue.Queue = queue.Queue(maxsize=256)
        # Log lines go to one worker, not a thread each. Spawning a thread per
        # line is fine at one line a minute and ruinous during a fault, which is
        # exactly when logging matters: the oscillation bug would have created
        # thousands of them a second while we were trying to diagnose it.
        self._log_q: queue.Queue = queue.Queue(maxsize=256)
        # NodeCore is not thread-safe and two threads reach it: the capture's
        # hook thread and the socket reader. Critical sections are pure
        # arithmetic plus queued sends, so this is worth well under a
        # millisecond - inside the P1 budget for a WH_MOUSE_LL callback.
        self._lock = threading.RLock()

    # ------------------------------------------------------------ lifecycle
    def run(self) -> None:
        self.capture.start(self)
        # On its own thread: finding this machine's addresses to join the
        # multicast group can take a while on a slow resolver, and nothing about
        # being found by name may delay the link itself. It did, by a few ms -
        # enough for a peer to dial before we listened, and on Windows a refused
        # local connect is retried only after 500ms.
        threading.Thread(target=self._start_responder, daemon=True).start()
        threading.Thread(target=self._heartbeat, daemon=True).start()
        threading.Thread(target=self._clipboard, daemon=True).start()
        if self.detect_desktop is not None:
            threading.Thread(target=self._watch_displays, daemon=True).start()
        threading.Thread(target=self._log_worker, daemon=True).start()
        # A supervisor rather than one long-lived loop: pairing changes the role,
        # the address and the port, and each of those decides WHICH loop should be
        # running. Letting reconfigure() break us out and come back round here is
        # what makes "pair, and it connects" possible without a restart.
        try:
            while not self._stop:
                self._reconfig = False
                if not self.enabled or not self.paired():
                    time.sleep(0.25)
                    continue
                try:
                    if self.core.is_hub:
                        self._serve()
                    else:
                        self._connect_loop()
                except protocol.ProtocolError as e:
                    self._log_async(f"protocol error: {e}")
                    time.sleep(0.5)
                except OSError as e:
                    # reconfigure() closes the listening socket on purpose, to
                    # break accept() out of its wait. The OSError that follows is
                    # us, not a fault, and logging it as "link loop stopped" made
                    # every ordinary re-pair look like a crash in the log - the
                    # one place someone goes to find out what actually broke.
                    if not self._reconfig:
                        self._log_async(f"link loop stopped: {e}")
                        time.sleep(0.5)
        finally:
            self.stop()

    # ---------------------------------------------------------------- links
    @property
    def ch(self):
        """A channel, or None when nothing is connected. Kept for everything that
        only needs to know whether a link is up - and for a peer, whose only link
        this is."""
        with self._links_lock:
            for link in self.links.values():
                return link.ch
        return None

    def connected(self) -> bool:
        return bool(self.links)

    def link_stats(self) -> tuple:
        """(messages dropped, messages queued) across every link - summed, now
        that there can be several; it used to read whichever came first."""
        with self._links_lock:
            chans = [l.ch for l in self.links.values()]
        dropped = sum(getattr(c, "dropped", 0) for c in chans)
        queued = 0
        for c in chans:
            q = getattr(c, "_q", None)
            queued += q.qsize() if q is not None else 0
        return dropped, queued

    def peers(self) -> list:
        """The live links, for the window."""
        with self._links_lock:
            return [{"name": l.name, "addr": l.addr[0] if l.addr else None,
                     "rtt_ms": round(l.rtt_ms, 2) if l.rtt_ms else None,
                     "since": l.since} for l in self.links.values()]

    def _register(self, name, ch, addr) -> None:
        with self._links_lock:
            old = self.links.get(name)
            self.links[name] = _Link(name, ch, addr)
        if old is not None and old.ch is not ch:
            # The same machine connected again before its old link was noticed
            # dead - a laptop waking from sleep. The new link wins.
            old.ch.close()

    def _unregister(self, name, ch) -> bool:
        """Forget a link - only if it is still THIS channel. A reconnect may
        already have replaced it, and must not be torn down by the old one."""
        with self._links_lock:
            cur = self.links.get(name)
            if cur is None or cur.ch is not ch:
                return False
            del self.links[name]
            return True

    def _close_links(self) -> None:
        with self._links_lock:
            chans = [l.ch for l in self.links.values()]
        for ch in chans:
            ch.close()

    def _send(self, msg: dict, skip=None) -> None:
        """Route one message. Never blocks: each channel queues.

        On a peer, everything goes to the hub, which routes it. On the hub, `to`
        says who: one node, or - with no `to` - every connected node except
        `skip`, the one it came from.
        """
        msg.setdefault("from", self.core.node)
        to = msg.get("to")
        with self._links_lock:
            links = list(self.links.values())
        if not self.core.is_hub:
            for l in links:
                l.ch.send(msg)
            return
        for l in links:
            if l.name == skip:
                continue
            if to is None or l.name == to:
                l.ch.send(msg)

    def _broadcast_group(self) -> None:
        """The hub tells everyone who is here and how things are arranged.
        Called on every join and leave."""
        if not self.core.is_hub:
            return
        with self._links_lock:
            online = [self.core.node] + list(self.links)
        with self._lock:
            lay = protocol.layout_msg(self.core.layout.to_dict(), self.core.placement)
            self.core.online = set(online) - {self.core.node}
        self._send(protocol.roster(online, self.core.devices or None))
        self._send(lay)

    def paired(self) -> bool:
        """Is there enough to try a connection at all?

        A fresh install has no device paired, and must still start and show its
        window - refusing to run is how someone ends up with no way to pair.
        """
        return bool(self.core.is_hub or self.peer_addr or self.peer_name
                    or self.peer_id)

    # ------------------------------------------------------------ discovery
    def identity(self) -> dict:
        """What this device says when asked who it is. Read fresh every time,
        so a rename or a change of role is answered correctly at once."""
        return {"name": self.core.node, "id": self.device_id, "port": self.port,
                "waiting": bool(self.core.is_hub and self.enabled)}

    def _start_responder(self) -> None:
        from . import discovery
        r = discovery.Responder(self.port, self.identity, log=self._log_async)
        self.responder = r if r.start() else None

    def find(self, q: str = "*") -> list:
        """Devices on this network answering to `q`. Blocks for about a second."""
        from . import discovery
        hints = [h for h in (self.peer_addr, *self.discover_hints) if h]
        r = self.responder
        if r is not None:
            # From port 8770, so answers come back to 8770 - see discovery.py.
            return r.ask(q, self.port, self.device_id, hints=hints,
                         broadcast=self.discover_broadcast)
        return discovery.find(q, self.port, my_id=self.device_id, hints=hints,
                              broadcast=self.discover_broadcast)

    def reconfigure(self, hub=KEEP, peer_addr=KEEP, port=KEEP, pin=KEEP,
                    peer_name=KEEP, peer_id=KEEP) -> None:
        """Apply new connection settings now, not on the next restart.

        Drops whatever is in progress and lets run() pick the right loop again.

        KEEP, not None, means "leave this alone": None is a value callers need to
        SET. Forgetting a device is exactly "peer_addr = None", and with None as
        the sentinel that call did nothing at all - the config said nothing was
        paired while the node carried on believing it was.
        """
        if pin is not KEEP:
            self.pin = pin
        if peer_addr is not KEEP:
            self.peer_addr = peer_addr or None
        if peer_name is not KEEP:
            self.peer_name = peer_name or None
        if peer_id is not KEEP:
            self.peer_id = peer_id or None
        if port is not KEEP and port and int(port) != self.port:
            self.port = int(port)
            if self.responder is not None:     # answer on the new port too
                self.responder.stop()
                self._start_responder()
        if hub is not KEEP:
            self.core.set_hub(bool(hub))
        self._reconfig = True
        self.backoff.reset()
        self._close_links()
        srv = self._srv_sock
        if srv is not None:
            try:
                srv.close()              # breaks accept() out of its timeout
            except OSError:
                pass
        self._log_async(
            f"reconfigured: {'listening' if self.core.is_hub else 'dialling ' + str(self.peer_addr)}"
            f" on port {self.port}")

    def set_enabled(self, on: bool) -> None:
        """Turn linking on or off without exiting.

        Off means: drop the peer, forget the baton, un-suppress everything, and
        stop dialling or accepting. The process stays up so its UI and its log
        stay up with it.
        """
        on = bool(on)
        if on == self.enabled:
            return
        self.enabled = on
        self._log_async(f"linking {'enabled' if on else 'DISABLED'}")
        if not on:
            self._close_links()          # the read loops notice and clean up
            self._act(self.core.link_lost)

    def stop(self) -> None:
        self._stop = True
        if self.responder is not None:
            self.responder.stop()
        try:
            self.capture.stop()
        except Exception:
            pass
        self.capture.set_suppress(False, False)   # never exit leaving input dead

    # -------------------------------------------------- capture sink (P1!)
    # These run on the capture's hook thread. Nothing here may block: no
    # sendall, no print, no clipboard, no config write. See P1.
    def on_pointer(self, x, y, dx, dy):
        self._act(self.core.local_pointer, self.core.local_screen(), x, y, dx, dy)

    def on_motion(self, dx, dy):
        self._act(self.core.local_motion, dx, dy)

    def on_button(self, name, down):
        self._act(self.core.local_button, name, down)

    def on_wheel(self, dx, dy):
        self._act(self.core.local_wheel, dx, dy)

    def on_key(self, code, down):
        self._act(self.core.local_key, code, down)

    def on_failsafe(self):
        self._act(self.core.local_failsafe)
        self._log_async("failsafe - control released, local input restored")

    # --------------------------------------------------------- the machinery
    def _act(self, fn, *args) -> None:
        """Run a NodeCore method and carry out whatever it asked for.

        The lock spans the call AND the effects, so injected key-ups cannot be
        reordered against a concurrent handover - that is how a modifier gets
        stuck down.
        """
        with self._lock:
            try:
                a = fn(*args)
            except Exception as e:                      # P4: never eat input
                self._log_async(f"core error (ignored): {e!r}")
                return
            self._apply(a)
            self._sync_suppression()

    def _apply(self, a: Actions) -> None:
        for msg in a.send:
            self._send(msg)                             # queued; never blocks
        for what in a.inject:
            self._inject(what)
        if a.placement_changed and self.on_placement:
            try:
                self.on_placement([dict(b) for b in self.core.placement or []])
            except Exception as e:
                self._log_async(f"could not save the arrangement: {e!r}")

    def arrange(self, boxes):
        """Rearrange from the window. Returns an error to show, or None.

        Checked before it goes through _act, which - rightly, for input - logs
        and swallows any error. A refusal here needs to reach the person who
        dragged the boxes, not the log.
        """
        with self._lock:
            try:
                self.core.check_arrangement(boxes)
            except ValueError as e:
                return f"that arrangement does not work: {e}"
        if not self.core.is_hub and not self.connected():
            return ("not connected - the arrangement is kept by the machine this "
                    "one connects to, so connect first")
        self._act(self.core.arrange, boxes)
        return None

    def _inject(self, what) -> None:
        kind = what[0]
        try:
            if kind == "move_abs":
                self.injector.move_abs(what[1], what[2])
                # The capture measures real movement against where it last saw
                # the pointer, so it has to hear about the ones we make.
                note = getattr(self.capture, "note_injected", None)
                if note:
                    note(what[1], what[2])
            elif kind == "button":
                self.injector.button(what[1], what[2])
            elif kind == "key":
                self.injector.key(what[1], what[2])
            elif kind == "wheel":
                self.injector.wheel(what[1], what[2])
        except Exception as e:
            self._log_async(f"inject {kind} failed: {e!r}")

    def _sync_suppression(self) -> None:
        m, k = self.core.suppress_mouse(), self.core.suppress_keyboard()
        self.capture.set_suppress(m, k, not self.core.cursor_is_remote())
        self._note(m, k)

    def _note(self, m: bool, k: bool) -> None:
        """Log who is driving, whenever that changes.

        Without this the only way to tell what the software thinks is happening
        is to infer it from where the pointer ended up - which is guesswork the
        moment a human touches a mouse at the same time.
        """
        state = (self.core.baton.holder, self.core.cursor.screen, m, k)
        if state == self._noted:
            return
        self._noted = state
        holder, screen, _, _ = state
        who = "us" if self.core.holds() else (holder or "nobody")
        self._log_async(f"baton={who} epoch={self.core.epoch} cursor={screen} "
                        f"suppress(mouse={int(m)},kbd={int(k)})")

    def _log_async(self, msg) -> None:
        """Queue a log line. Never blocks, never allocates a thread.

        Console I/O must stay off the hook thread: a selected console window
        (Windows QuickEdit) blocks print() indefinitely, and on the hook thread
        that freezes every mouse on the machine. Dropping a line under pressure
        is better than either blocking or drowning in threads."""
        try:
            self._log_q.put_nowait(msg)
        except queue.Full:
            pass

    def _log_worker(self) -> None:
        while not self._stop:
            try:
                msg = self._log_q.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                self._log(msg)
            except Exception:
                pass

    def _watch_displays(self) -> None:
        """Notice a monitor plugged in or unplugged, and tell the group.

        Every few seconds: enumerating monitors is a millisecond on Windows and
        one small xrandr call on Linux, and nobody minds the arrangement taking
        three seconds to catch up with a cable.
        """
        while not self._stop:
            time.sleep(self.DISPLAYS_EVERY_S)
            try:
                d = self.detect_desktop()
            except Exception:
                continue
            if d is None or d == self.desktop_now:
                continue
            self.desktop_now = d
            self._displays_changed(d)

    def _displays_changed(self, d) -> None:
        n = len(d.parts) or 1
        self._log_async(f"displays changed: {n} display{'s' if n != 1 else ''}, "
                        f"desktop {d.w}x{d.h}")
        live = False
        for part in (self.capture, self.injector):
            f = getattr(part, "set_origin", None)
            if f is not None:
                f(d.x, d.y)
                live = True
        if not live:
            self._log_async("the arrangement has the new displays; restart Nishro "
                            "Link on this machine for the pointer to use them")
        self._act(self.core.own_geom, d.w, d.h, d.parts)

    def forget_machine(self, name: str):
        """From the window: take an offline machine off the arrangement."""
        if not self.core.is_hub:
            return "only the machine the others connect to keeps the device list"
        with self._links_lock:
            if name in self.links:
                return (f"{name} is connected - it would only come straight back. "
                        f"To keep it out, make a new password.")
        self._act(self.core.forget_machine, name)
        return None

    def _heartbeat(self) -> None:
        """Re-evaluate suppression on a clock, and keep the link honest.

        P2 expires on TIME, not on events, so without this tick a silently dead
        link would leave the capture suppressed forever - nothing would arrive
        to tell it otherwise. This is the thread that actually un-freezes the
        mouse when the peer disappears.
        """
        i = 0
        while not self._stop:
            time.sleep(self.TICK_S)
            with self._lock:
                self._sync_suppression()
            if self.connected():
                a = None
                with self._lock:
                    a = self.core.check_claim()
                    if a:
                        self._apply(a)
                        self._sync_suppression()
                if a is not None and a.gave_up:
                    self._log_async(
                        "asked for control repeatedly and did not get it - "
                        "taking this machine back. Your mouse and keyboard are "
                        "live again here.")
            i += 1
            if i % self.PING_EVERY == 0:
                with self._links_lock:
                    links = list(self.links.values())
                for l in links:          # per link: it is each link we measure
                    l.ping_at[i] = time.monotonic()
                    if len(l.ping_at) > 16:
                        l.ping_at.pop(min(l.ping_at))
                    l.ch.send(protocol.ping(i))

    def _clipboard(self) -> None:
        """Watch our clipboard, and handle the other machine's.

        Both halves live here because both can block for a fifth of a second,
        and neither may ever do that on the reader or a hook. Idle cost is one
        GetClipboardSequenceNumber() call every 400ms on Windows - a function
        call, not a PowerShell process - so the expensive read only happens when
        something really has been copied.
        """
        while not self._stop:
            try:
                msg = self._clip_q.get(timeout=0.4)
            except queue.Empty:
                msg = None
            if not self.connected():
                continue                      # nobody to tell, nothing to fetch
            try:
                out = self.clip.on_message(msg) if msg else self.clip.poll()
            except Exception as e:
                self._log_async(f"clipboard error: {e!r}")
                continue
            for m in out:
                # With several machines, a request goes to the one that copied
                # and the data back to the one that asked. Broadcast, a request
                # could be answered by a machine whose own clipboard happened to
                # carry the same sequence number.
                if msg is not None:
                    if m.get("t") == "clipget":
                        m["to"] = msg.get("origin") or msg.get("from")
                    elif m.get("t") == "clipdata":
                        m["to"] = msg.get("from")
                self._send(m)

    # ----------------------------------------------------------- networking
    def _serve(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Not a bare SO_REUSEADDR: on Windows that lets another program bind
        # 8770 alongside us and take some of the connections meant for the link.
        runtime.exclusive(srv)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(8)
        self._log(f"hub '{self.core.node}' listening on 0.0.0.0:{self.port}")
        # Blocked inbound traffic is indistinguishable from a peer that is
        # switched off, so say the quiet part after a while rather than sitting
        # there looking healthy. Cost: one timer.
        self._ever_connected = False

        def nag():
            if not self._ever_connected and not self._stop:
                self._log(firewall_hint(self.port))
        threading.Timer(30.0, nag).start()
        self._srv_sock = srv
        srv.settimeout(0.5)              # so `enabled` is checked, not blocked on
        while not self._stop and not self._reconfig:
            if not self.enabled:
                time.sleep(0.2)
                continue
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            # One thread per peer. The accept loop goes straight back to
            # listening, so any number of machines can be connected at once.
            threading.Thread(target=self._serve_one, args=(conn, addr),
                             daemon=True).start()
        try:
            srv.close()                  # or a later rebind on this port fails
        except OSError:
            pass
        self._srv_sock = None
        self._close_links()

    def _serve_one(self, conn, addr) -> None:
        ch = protocol.LineChannel(conn)
        try:
            who = self._greet(ch, addr)
            if who:
                self._ever_connected = True
                self._pump(ch, who, addr)
        except protocol.ProtocolError as e:
            self._log(f"protocol error from {addr}: {e}")
        except OSError as e:
            self._log(f"link to {addr} lost during the handshake: {e}")
        finally:
            ch.close()

    def _greet(self, ch, addr):
        """The hub's side of the handshake. The peer's name if it proved itself,
        else None."""
        # We speak first, with a challenge, so the caller has something to answer
        # and the password never crosses the wire.
        chal = protocol.nonce()
        ch.send(protocol.auth(self.core.node, chal, self.device_id))
        hello = ch.recv()
        if hello is None:
            return None
        protocol.check_version(hello)
        who = hello.get("node", str(addr))

        if not protocol.verify(self.pin, chal, who, self.core.node,
                               hello.get("proof", "")):
            ch.send(protocol.err("authentication failed"))
            self._log(f"rejected {who} at {addr}: wrong password"
                      + ("" if hello.get("proof") else
                         " (it sent no proof at all - older build?)"))
            return None

        # ...and prove ourselves back, so a machine never accepts injected
        # keystrokes from something that cannot prove it knows the password.
        their_chal = hello.get("nonce")
        if not their_chal:
            ch.send(protocol.err("challenge us back before we will drive you"))
            self._log(f"rejected {who}: it did not challenge us (older build?)")
            return None
        if who == self.core.node:
            ch.send(protocol.err(f"this group already has a machine called {who!r}"))
            self._log(f"rejected a machine calling itself {who!r} - that is our name")
            return None
        my_proof = protocol.proof(self.pin, their_chal, self.core.node, who)
        # Learn their name and screen size rather than making someone type them.
        screens = hello.get("screens") or []
        if who and screens:
            with self._lock:
                if self.core.adopt_peer(who, screens[0]["w"], screens[0]["h"],
                                        screens[0].get("parts") or ()):
                    self._log_async(f"peer '{who}' is {screens[0]['w']}x"
                                    f"{screens[0]['h']}, on our {self.core.side}")
        resume = hello.get("resume")
        resumed = bool(resume and self.sessions.can_resume(resume, who))
        sid = resume if resumed else self.sessions.open(who)
        with self._lock:
            with self._links_lock:
                others = [n for n in self.links if n != who]
            if others:
                # Others are connected and one of them may be driving. A fresh
                # epoch would have to be announced to all of them and would
                # snap the driver's cursor; the new link cannot carry stale
                # input anyway - it is a new socket. So it joins the epoch in
                # force.
                g = self.core.arbiter.grant
            else:
                g = self.core.arbiter.resume()   # fresh epoch; stale input dies
                self.core.on_message(_grant_msg(g))
            ch.send(protocol.welcome(self.core.node, g.epoch, g.holder,
                                     self.core.layout.to_dict(), g.screen,
                                     g.x, g.y, resumed=resumed, session=sid,
                                     proof=my_proof))
            ch.send(_grant_msg(g))
            # The placement too, not just the graph: the peer's window draws
            # the desk from it, and without it showed a row of boxes that had
            # nothing to do with the arrangement in force.
            ch.send(protocol.layout_msg(self.core.layout.to_dict(),
                                        self.core.placement))
        self.trusted_peer = who
        with self._lock:
            self.core.peer_online(who)
        self._register(who, ch, addr)
        if self.on_devices:
            try:
                self.on_devices("joined", who, {
                    "id": hello.get("id"), "addr": addr[0] if addr else None,
                    "screens": screens})
            except Exception as e:
                self._log(f"could not save the device list: {e!r}")
        self._broadcast_group()          # everyone learns who joined, and the desk
        self._log(f"peer connected: {who} (password verified both ways)"
                  + ("  (resumed)" if resumed else "")
                  + (f" - {len(self.links)} connected" if len(self.links) > 1 else ""))
        if not self.pin:
            self._log("WARNING: no password is set, so anything on this network "
                      "that speaks the protocol can connect and type here")
        return who

    def _connect_loop(self) -> None:
        while not self._stop and not self._reconfig:
            if not self.enabled:
                time.sleep(0.2)
                continue
            delay = self.backoff.next_delay_ms() / 1000.0
            if delay:
                self._log(f"retrying in {delay:.1f}s "
                          f"(attempt {self.backoff.failures + 1}, "
                          f"via {self.backoff.target()})")
                time.sleep(delay)
            if self._stop:
                return
            target = self._target()
            if target is None:
                self.backoff.on_failure()
                if self.backoff.failures in (1, 4) or self.backoff.failures % 10 == 0:
                    self._log(f"cannot find '{self.peer_name or self.peer_id}' on this "
                              f"network - is it switched on and waiting for a "
                              f"connection? If it is, its firewall may be dropping "
                              f"the search (UDP {self.port}).")
                continue
            addr, port = target
            try:
                sock = socket.create_connection((addr, port), timeout=2)
            except OSError as e:
                self.backoff.on_failure()
                self._log(f"connect to {addr} failed: {e}")
                if self.backoff.failures == 3:
                    # Three in a row is not a blip. Name the usual cause once,
                    # rather than repeating the same unhelpful line forever. A
                    # firewalled hub looks exactly like a hub that is switched
                    # off, and four hours of "timed out" said neither.
                    self._log(f"three failures in a row - if the other machine "
                              f"IS running, it is probably firewalled. On it, "
                              f"allow inbound TCP {self.port} for Nishro Link.")
                continue
            ch = protocol.LineChannel(sock)
            try:
                if not self._say_hello(ch, addr):
                    self.backoff.on_failure()
                    continue
                self.backoff.on_connected()
                sock.settimeout(None)
                hub = self.trusted_peer or self.peer_name or "hub"
                self._register(hub, ch, (addr, port))
                self._pump(ch, hub, (addr, port))
                self.backoff.on_disconnected()
            except protocol.ProtocolError as e:
                self._log(f"protocol error: {e}")
                self.backoff.on_disconnected()
            finally:
                ch.close()

    def _target(self):
        """(address, port) to dial this time round, or None if not found.

        The remembered address first - it is usually still right, and trying it
        costs nothing. After it fails (Backoff escalates to "discovery"), or when
        there is none, the peer is looked up by ID, else by name.
        """
        if not (self.peer_name or self.peer_id):
            return (self.peer_addr, self.port) if self.peer_addr else None
        if self.peer_addr and self.backoff.target() == "address":
            return self.peer_addr, self.port
        q = self.peer_id or self.peer_name
        found = [f for f in self.find(q) if f.waiting]
        self.last_found = found[0] if found else None
        if not found:
            return None
        f = found[0]
        if f.addr != self.peer_addr:
            self._log(f"found '{f.name}' at {f.addr}")
        return f.addr, f.port or self.port

    def _expected(self, name: str, dev_id: str) -> bool:
        """Is the machine that answered at this address the one we paired with?"""
        if self.peer_id and dev_id:
            return dev_id == self.peer_id
        if self.peer_name and name:
            return name.casefold() == self.peer_name.casefold()
        return True                  # dialled by bare address: take what is there

    def _say_hello(self, ch, addr=None) -> bool:
        # The listener challenges us first. Answer it, and challenge back.
        greet = ch.recv()
        if greet is None:
            return False
        if greet.get("t") == "err":
            self._log(f"refused by the other machine: {greet.get('msg')}")
            return False
        protocol.check_version(greet)
        if greet.get("t") != "auth" or not greet.get("nonce"):
            self._log("the other machine did not challenge us - it is running an "
                      "older build. Update both sides.")
            return False
        hub = greet.get("node") or "?"
        hub_id = greet.get("id")
        if not self._expected(hub, hub_id):
            # A remembered address that DHCP has since given to another machine
            # running Nishro Link. Nothing has been proved or sent yet; forget
            # the address and look for the right machine by name.
            self._log(f"{addr} is now '{hub}', not '{self.peer_name}' - looking "
                      f"for '{self.peer_name}' on the network instead")
            self.peer_addr = None
            return False
        mine = protocol.nonce()

        screens = []
        for n in self.core.layout.mine():
            s = self.core.layout.get(n)
            screens.append(dict({"name": n, "w": s.w, "h": s.h},
                                **({"parts": [list(p) for p in s.parts]}
                                   if s.parts else {})))
        ch.send(protocol.hello(
            self.core.node, screens, self.core.policy,
            proof=protocol.proof(self.pin, greet["nonce"], self.core.node, hub),
            chal=mine, resume=self.session, dev_id=self.device_id))

        reply = ch.recv()
        if reply is None:
            return False
        if reply.get("t") == "err":
            self._log(f"refused by {hub}: {reply.get('msg')}")
            return False
        protocol.check_version(reply)

        # Mutual: we do not let anything inject into this machine until it has
        # proved it knows the password too.
        if not protocol.verify(self.pin, mine, hub, self.core.node,
                               reply.get("proof", "")):
            self._log(f"{hub} could not prove it knows the password - refusing to "
                      f"be driven by it")
            return False
        self.trusted_peer = hub
        with self._lock:
            self.core.peer_online(hub)
        # The hub's layout is the shared truth. It already holds both screens -
        # ours as we reported it a moment ago, and its own - so adopting it
        # wholesale means this machine never has to be told the other one's
        # name, its screen size, or which side it is on. It also means the two
        # can no longer disagree about any of them.
        with self._lock:
            lay_d = reply.get("layout")
            if lay_d:
                try:
                    lay = Desk.from_dict(lay_d, node=self.core.node)
                    if not lay.mine():
                        raise ValueError(
                            f"the hub's layout has no screen owned by "
                            f"{self.core.node!r} - it knows {lay.owners()}")
                    self.core.adopt_layout(lay)
                except (KeyError, ValueError) as e:
                    self._log(f"cannot use the hub's layout: {e}")
                    return False
            if reply.get("screen") not in self.core.layout.names():
                self._log(f"the hub says the cursor is on screen "
                          f"{reply.get('screen')!r}, which is not in the layout "
                          f"it sent. Refusing to start.")
                return False
        self.session = reply.get("session") or self.session
        with self._lock:
            self.core.new_session()
            self.core.on_message(protocol.baton(
                reply["holder"], reply["epoch"], reply["screen"],
                reply.get("x", 0), reply.get("y", 0)))
        # Say it out loud, the same words the hub uses. This side used to log
        # only "connected", so the dialling machine's log was the same whether
        # the password had been checked or not - silence standing in for a
        # security property, which is how nobody notices when it stops holding.
        addr = addr or self.peer_addr
        learned = (hub, hub_id or self.peer_id, addr)
        if learned != (self.peer_name, self.peer_id, self.peer_addr):
            # First connection after pairing, or the peer moved or was renamed.
            # Remember all three: the ID so a rename cannot lose it, the address
            # so the next reconnect is instant.
            self.peer_name, self.peer_id, self.peer_addr = learned
            if self.on_paired:
                try:
                    self.on_paired(*learned)
                except Exception as e:
                    self._log(f"could not save the pairing: {e!r}")
        self._log(f"connected to {hub} at {addr}:{self.port} "
                  f"(password verified both ways)"
                  + ("  (session resumed)" if reply.get("resumed") else ""))
        if not self.pin:
            self._log("WARNING: no password is set, so anything on this network "
                      "that speaks the protocol can connect and drive this machine")
        return True

    def _pump(self, ch, name=None, addr=None) -> None:
        """Read one link until it goes away. Then P2 and P3, immediately.

        On the hub this also routes: a message for another node is passed on
        and not acted on here; one for everybody is passed on AND acted on.
        """
        if name is None:                  # a caller that registered nothing
            name = self.trusted_peer or "peer"
            self._register(name, ch, addr)
        hub = self.core.is_hub
        try:
            while not self._stop:
                msg = ch.recv()
                if msg is None:
                    break
                # Any message at all proves the link is alive, so it feeds the P2
                # watchdog - not just input. Otherwise a holder who is simply not
                # moving the mouse looks exactly like a crashed one.
                with self._lock:
                    self.core.baton.touch()
                t = msg.get("t", "")
                if t in protocol.HOP_LOCAL:
                    self._hop_local(ch, name, msg, t)
                    continue
                if hub:
                    if t in protocol.HUB_ONLY:
                        continue          # only the hub says these; see protocol
                    msg["from"] = name    # who it came from is ours to say
                    to = msg.get("to")
                    if to and to != self.core.node:
                        self._send(msg)   # someone else's: pass it on, and only that
                        continue
                    if not to:
                        self._send(msg, skip=name)   # everyone's: pass it on too
                if t.startswith("clip"):
                    try:
                        self._clip_q.put_nowait(msg)   # never block the reader
                    except queue.Full:
                        pass
                    continue
                self._act(self.core.on_message, msg)
                if t == "roster" and not hub and self.on_devices:
                    try:
                        self.on_devices("roster", None,
                                        {"devices": msg.get("devices") or []})
                    except Exception:
                        pass
        except OSError as e:
            self._log(f"link to {name} lost: {e}")
        finally:
            mine = self._unregister(name, ch)
            if mine:
                if hub:
                    self._act(self.core.peer_lost, name)
                    if self.trusted_peer == name:
                        with self._links_lock:
                            self.trusted_peer = next(iter(self.links), None)
                    if self.on_devices:
                        try:
                            self.on_devices("left", name, {})
                        except Exception:
                            pass
                    self._broadcast_group()
                    self._log(f"{name} disconnected"
                              + (f" - {len(self.links)} still connected"
                                 if self.links else " - local input restored"))
                else:
                    self.trusted_peer = None
                    self._act(self.core.link_lost)
                    self._log("disconnected - local input restored")

    def _hop_local(self, ch, name, msg, t) -> None:
        """Keep-alives answer on the link they came from - routed, a pong would
        have gone to every machine connected to the hub."""
        if t == "ping":
            ch.send(protocol.pong(msg.get("i", 0)))
        elif t == "pong":
            with self._links_lock:
                l = self.links.get(name)
            sent = l.ping_at.pop(msg.get("i"), None) if l else None
            if sent:
                l.rtt_ms = self.rtt_ms = (time.monotonic() - sent) * 1000.0
        elif t == "err":
            self._log(f"{name} says: {msg.get('msg')}")


def _grant_msg(g: Grant) -> dict:
    return protocol.baton(g.holder, g.epoch, g.screen, g.x, g.y, g.held)
