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

from . import keymap, pairing, protocol, runtime, secure
from .baton import Arbiter, BatonState, ClaimDetector, Grant, now_ms
from .clip import ClipboardSync
from .motion import Cursor, exits, to_pixels
from .desk import Desk, beside, normal_parts
from .reconnect import Backoff, SessionStore
from .runtime import firewall_hint
from .shake import Shake

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
        # Shaking the mouse shows where the pointer is (shake.py). A setting.
        self.shake = Shake()
        self.find_on_shake = True
        # Peer NODES connected right now. The pointer may enter only their
        # machines and ours: an arrangement lists machines that are switched
        # off, and pushing into one of those flipped the cursor between the
        # two machines dozens of times a second - seen on the laptop, with
        # linking off - or, with linking on, froze this machine's pointer while
        # it steered a screen that was not there.
        self.online = set()
        self.devices = []             # the group as the hub last described it
        # The other machines' control rights: name -> {"may_drive",
        # "may_be_driven"}. One that may not be driven is a wall, like one that
        # is switched off - it refuses input anyway (P6), but the pointer should
        # not try to go where it cannot arrive.
        self.rights = {}

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
        self._home_keys: set = set()      # power/sleep/wake pressed here for us
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

    def rename(self, new: str) -> None:
        """This machine is called `new` now - in the arrangement, the baton and
        the arbiter. Only with no links up: the caller reconnects, and every
        other machine learns the name in the handshake."""
        old = self.node
        if new == old:
            return
        for m in list(self.layout.machines()):
            if m.owner == old:
                self.layout.rename(m.name, new if m.name == old else m.name,
                                   owner=new)
        self.layout.node = new
        self.node = new
        hub = self.is_hub
        self.arbiter, self.is_hub = None, False
        self.baton = BatonState(new, **self._clock_kw)
        start = self.layout.mine()[0]
        s = self.layout.get(start)
        self.cursor = Cursor(self.layout, start, s.w // 2, s.h // 2)
        self.home = (start, s.w // 2, s.h // 2)
        if hub:
            self.set_hub(True)

    def rename_peer(self, old: str, new: str) -> Actions:
        """The hub: another machine is called `new` now. Only while it is not
        connected - the caller makes sure - so neither the cursor nor the baton
        can be on it."""
        a = Actions()
        if not self.arbiter or old == new or old not in self.layout.owners():
            return a
        if new in self.layout.names():
            self.layout.remove(new)        # a stale machine under that name
        for m in list(self.layout.machines()):
            if m.owner == old:
                self.layout.rename(m.name, new if m.name == old else m.name,
                                   owner=new)
        self.online.discard(old)
        if old in self.rights:
            self.rights[new] = self.rights.pop(old)
        self.adopt_layout(self.layout, keep_cursor=True)
        a.send.append(protocol.layout_msg(self.layout.to_dict(), self.placement))
        a.placement_changed = True
        return a

    def alone(self) -> None:
        """Only this machine left on the arrangement - after leaving a group or
        being removed from one. The cursor comes home if it was elsewhere."""
        for m in list(self.layout.machines()):
            if m.owner != self.node:
                self.layout.remove(m.name)
        self.online.clear()
        self.devices = []
        self.adopt_layout(self.layout, keep_cursor=True)

    def set_placement(self, boxes, keep_cursor: bool = True) -> None:
        """Replace the arrangement."""
        self.adopt_layout(self.check_arrangement(boxes), keep_cursor=keep_cursor)

    def check_arrangement(self, boxes):
        """The desk these boxes describe, or ValueError saying what is wrong."""
        from . import desk as _desk
        if not boxes or not isinstance(boxes, (list, tuple)):
            raise ValueError("no screens given")
        for b in boxes:
            if not isinstance(b, dict):
                raise ValueError("a screen is not described properly")
            need = ({"copy_of", "x", "y"} if "copy_of" in b
                    else {"name", "w", "h", "x", "y"})
            missing = need - set(b)
            if missing:
                raise ValueError(f"a screen is missing {sorted(missing)}")
        names = [b["name"] for b in boxes if "copy_of" not in b]
        if len(set(names)) != len(names):
            raise ValueError("two screens have the same name")
        try:
            lay = _desk.place(self.node, boxes)
        except (KeyError, TypeError) as e:
            raise ValueError(str(e)) from e
        lay.check()          # no screen of ours, an overlap, a border to two places
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
        self._shaken(a, dx, dy)
        return a

    def local_motion(self, dx: int, dy: int) -> Actions:
        """A raw delta while we ARE suppressing - the OS pointer is pinned, so
        these deltas are the only movement information that exists."""
        a = Actions()
        if not self.holds():
            return self._maybe_claim(a, self.claims.motion(dx, dy), "motion")
        self._advance(a, dx, dy)
        self._shaken(a, dx, dy)
        return a

    def _shaken(self, a: Actions, dx: int, dy: int) -> None:
        """Only movement of a machine that is driving counts: movement that
        merely asks for control has not taken the pointer anywhere yet, and
        the spotlight would go up where the pointer used to be."""
        if self.find_on_shake and self.shake.feed(dx, dy, self._clock()):
            self.find(a)

    def find(self, a: Actions = None) -> Actions:
        """Show where the pointer is: a spotlight round it, on the machine it
        is on - this one, or another, which is told to."""
        a = a if a is not None else Actions()
        screen = self.cursor.screen
        if self.layout.is_local(screen):
            a.inject.append(("spotlight", self.cursor.x, self.cursor.y))
        else:
            nx, ny = self.cursor.norm()
            msg = protocol.find(screen, nx, ny)
            msg["to"] = self.layout.get(screen).owner
            a.send.append(msg)
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
        if code in keymap.SYSTEM:
            # Power, sleep and wake are about THIS computer. While the cursor is
            # away its keyboard is held for the other one, so they are pressed
            # here again - and a release follows its press here even if the
            # cursor came home in between, or the key would stay down.
            if self.cursor_is_remote() or code in self._home_keys:
                a.inject.append(("key", code, down))
                (self._home_keys.add if down else self._home_keys.discard)(code)
            return a
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
        elif t == "find":
            s = msg.get("s")
            if s in self.layout.names() and self.layout.is_local(s):
                m = self.layout.get(s)
                x, y = to_pixels(msg.get("x", 0.5), msg.get("y", 0.5), m.w, m.h)
                a.inject.append(("spotlight", x, y))
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
        allowed = (self.may_drive() if who == self.node else
                   self.rights.get(who, {}).get("may_drive", True))
        grant = self.arbiter.claim(who, screen, x, y, may_drive=allowed)
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
        self.rights = {d["name"]: {"may_drive": d.get("may_drive", True),
                                   "may_be_driven": d.get("may_be_driven", True)}
                       for d in self.devices
                       if d.get("name") and d["name"] != self.node}

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
        if g.holder == self.node:
            # Our request is answered - settled HERE, not when check_claim next
            # looks. Left for it, a handover that came first (the hub taking
            # control back when a machine left) found the request still open
            # and "retried" it: control jumped back to a machine nobody had
            # touched. Seen as a test that passed only by that accident.
            self._claim_at, self._claim_tries = None, 0
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
            if spot.crossed and not was_remote:
                # Through a copy of this machine and back onto it - the wrap
                # round. The OS pointer is still at the border it left by.
                a.inject.append(("move_abs", spot.x, spot.y))
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
        """Machines the pointer may enter now: ours, and connected peers' that
        may be driven."""
        return {m.name for m in self.layout.machines()
                if m.owner == self.node
                or (m.owner in self.online
                    and self.rights.get(m.owner, {}).get("may_be_driven", True))}

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
    # While the pointer is on another machine's screen: a tiny frame this
    # often, from BOTH ends. Wi-Fi power saving lets a radio that has been
    # quiet doze, and what crosses it then waits for it - measured between the
    # laptop and the AIO: one packet in ten held back 65 ms or more, the worst
    # 126 ms, exactly when the mouse starts moving again after a rest. Sending
    # every 40 ms keeps the radio awake: one in ten then 14-21 ms, the worst
    # 49-53. About 2.5 KB/s, and only while a pointer is across.
    #
    # At first only the machine being driven sent it. Reported later: with
    # the AIO's mouse on the laptop's screen, the pointer was slow to start
    # moving after a rest - the DRIVING machine's radio dozed while its mouse
    # was still, and its first movements waited for it to wake.
    KEEP_AWAKE_S = 0.04

    def __init__(self, core: NodeCore, capture, injector, port: int = 8770,
                 pin: str = "", peer_addr: str = None, on_log=None,
                 device_id: str = "", peer_name: str = None, peer_id: str = None):
        self.core = core
        self.capture = capture
        self.injector = injector
        self.port = port
        # Compared without dashes, spaces or capitals - see pairing.py.
        self.pin = pairing.normalise(pin)
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

        # What dialling is doing, for the window - so that pairing ends in
        # "connected" or in the reason it is not, rather than in a dialog that
        # closed itself and left the person guessing. phase: idle, searching,
        # connecting, verifying, connected, retrying (why) or failed (why).
        self.dial = {"phase": "idle"}
        # Set when trying again cannot help: a wrong password, removed from the
        # group, a name already taken. Dialling waits until the next pairing
        # instead of hammering the hub with the same refusal forever.
        self.blocked = None
        self.joining = False          # the next hello is a pairing someone asked for
        self.removed_ids = set()      # hub: devices removed from the group
        self._leaving = set()         # hub: forget these when they disconnect
        self.on_event = None          # (kind, info) - for the window's notices
        self.on_invited = None        # (hub, hub_id, addr, port, secret, by) -> bool
        self.on_removed = None        # (by) - this device was removed from its group
        # Adding a device, either way - invite() or probe() - for the window.
        self.adding = {"phase": "idle"}
        self._miss = None             # why the last search found nothing usable
        self._hub_id = None           # a member: its hub's ID, the key's salt
        # The hub: each device's name by its permanent ID, so one that comes
        # back under a new name is the same machine renamed, not a new one.
        self.names_by_id = {}
        self.pending_names = {}       # hub: ID -> the name it is to take
        self.versions = {}            # hub: name -> the version it runs
        from . import __version__
        self.version = __version__
        self.on_rename = None         # (new) - this device was renamed from the hub
        self.on_manage = None         # hub: (op, args, by) -> {"ok"} or {"error"}
        self._asked = {}              # member: request id -> [Event, result]
        self.on_policy = None         # (policy) - its rights were set from the hub

        self._stop = False
        self._reconfig = False
        self._srv_sock = None
        self._noted = None            # last (holder, screen, suppression) we logged
        # Linking can be turned off while the process keeps running. A tray or
        # settings UI needs that: killing the process would take the UI with it.
        self.enabled = True
        self.clip = ClipboardSync(core.node)
        self.clip.log = self._log_async
        # Clipboard work never runs on the reader or a hook: clip.get()/set()
        # shell out (~200ms on Windows), which would stall input for as long as
        # it takes. The reader only ever enqueues.
        self._clip_q: queue.Queue = queue.Queue(maxsize=256)
        self._was_remote = False      # where the cursor was at the last clipboard tick
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
        threading.Thread(target=self._keep_awake, daemon=True).start()
        if hasattr(self.injector, "spotlight"):
            try:
                self.injector.on_log = self._log_async   # its own failures, told
            except AttributeError:
                pass
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
        self._send(protocol.roster(online, self.roster_devices() or None))
        self._send(lay)

    def roster_devices(self) -> list:
        """The group as the hub describes it, with each device's rights and
        version: every window shows them, and every driver needs the rights."""
        listed = [dict(d) for d in self.core.devices or [] if d.get("name")]
        names = {d["name"] for d in listed}
        # Everyone on the arrangement, whether or not the saved list has caught
        # up: a device's rights must reach the others even so.
        if self.core.node not in names:
            listed.insert(0, {"name": self.core.node, "id": self.device_id,
                              "hub": True})
        for owner in self.core.layout.owners():
            if owner not in names and owner != self.core.node:
                listed.append({"name": owner})
        out = []
        for d in listed:
            name = d.get("name")
            rights = (dict(may_drive=self.core.may_drive(),
                           may_be_driven=self.core.may_be_driven())
                      if name == self.core.node else self.core.rights.get(name, {}))
            version = self.version if name == self.core.node else \
                self.versions.get(name)
            out.append(dict(d, **rights, **({"version": version} if version
                                             else {})))
        return out

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
        hub = self.core.is_hub
        return {"name": self.core.node, "id": self.device_id, "port": self.port,
                "waiting": bool(hub and self.enabled),
                "group": self.core.node if hub else (self.trusted_peer
                                                     or self.peer_name),
                "alone": bool(hub and not self.members())}

    def members(self) -> list:
        """The hub's other machines: everyone on the arrangement but us."""
        if not self.core.is_hub:
            return []
        return sorted(m.owner for m in self.core.layout.machines()
                      if m.owner != self.core.node)

    def _own_key(self) -> str:
        """The key this device's password proves, as a hub: salted with its
        own ID. See pairing.key."""
        return pairing.key(self.pin, self.device_id)

    def _key_for(self, hub_id) -> str:
        """The key this device's password proves to a hub with that ID."""
        return pairing.key(self.pin, hub_id)

    def _event(self, kind: str, **info) -> None:
        cb = self.on_event
        if cb is not None:
            try:
                cb(kind, info)
            except Exception as e:
                self._log_async(f"event {kind} not delivered: {e!r}")

    def _dial_state(self, phase: str, reason: str = None, detail=None) -> None:
        self.dial = {"phase": phase, "reason": reason, "detail": detail,
                     "target": self.peer_name or self.peer_addr,
                     "attempts": self.backoff.failures, "since": time.time()}

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
                    peer_name=KEEP, peer_id=KEEP, joining=KEEP) -> None:
        """Apply new connection settings now, not on the next restart.

        Drops whatever is in progress and lets run() pick the right loop again.

        KEEP, not None, means "leave this alone": None is a value callers need to
        SET. Forgetting a device is exactly "peer_addr = None", and with None as
        the sentinel that call did nothing at all - the config said nothing was
        paired while the node carried on believing it was.
        """
        if pin is not KEEP:
            self.pin = pairing.normalise(pin)
        if joining is not KEEP:
            self.joining = bool(joining)
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
        self.blocked = None                # a new pairing deserves a new try
        self.dial = {"phase": "idle"}
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
        if on:
            self.blocked = None            # switching it on again means "try again"
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
            if msg.get("t") == "find":
                self._log_async(f"find the pointer: it is on {msg.get('s')} - "
                                f"asked it to show where")
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
            elif kind == "spotlight":
                show = getattr(self.injector, "spotlight", None)
                if show:
                    show(what[1], what[2])
                    self._log_async("find the pointer: showing it here")
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

    def remove(self, name: str):
        """From the hub's window: take a device out of the group. Connected, it
        is told first and forgets the group; switched off, it is refused when it
        next comes back. Returns an error to show, or None."""
        if not self.core.is_hub:
            return "only the hub can remove devices - ask it, or leave the group"
        if name == self.core.node or name not in self.core.layout.names():
            return f"{name} is not another device in this group"
        owner = self.core.layout.get(name).owner
        with self._links_lock:
            link = self.links.get(owner)
        if link is None:
            self._act(self.core.forget_machine, name)
            return None
        self._leaving.add(owner)           # forgotten once its link is gone
        link.ch.send(protocol.removed(self.core.node))
        threading.Thread(target=link.ch.close, kwargs={"flush": 1.0},
                         daemon=True).start()
        return None

    def leave(self) -> None:
        """From a peer's window: tell the hub, then go. The caller resets this
        device to a group of its own."""
        with self._links_lock:
            chans = [l.ch for l in self.links.values()]
        for ch in chans:
            ch.send(protocol.leave())
            ch.close(flush=1.0)

    def go_alone(self, pin: str) -> None:
        """Become a group of one: listening, with its own new password and only
        its own screens on the arrangement."""
        with self._links_lock:
            chans = [l.ch for l in self.links.values()]
        for ch in chans:
            ch.close()
        self.reconfigure(hub=True, peer_addr=None, peer_name=None, peer_id=None,
                         pin=pin, joining=False)
        with self._lock:
            self.core.alone()
        self.removed_ids = set()
        self.trusted_peer = None

    def rekey(self, pin: str) -> int:
        """The hub's new password, to every device connected now - sealed under
        the old one, which they all know and nobody else does. Devices that are
        switched off miss it and will have to be given it by hand. Returns how
        many were told."""
        old, new = self.pin, pairing.normalise(pin)
        with self._links_lock:
            links = list(self.links.values())
        for l in links:
            a, b = protocol.nonce(), protocol.nonce()
            l.ch.send({"t": "rekey", "to": l.name, "a": a, "b": b,
                       "secret": protocol.wrap(new, pairing.key(old, self.device_id),
                                               a, b)})
        self.pin = new
        return len(links)

    def rename(self, new: str) -> None:
        """Call this device `new` from now on, and reconnect so every other
        machine learns it."""
        with self._lock:
            self.core.rename(new)
        self.clip.node = new
        self.reconfigure()

    def rename_other(self, name: str, new: str):
        """The hub renames another device. Connected, it is told now and comes
        back under the new name; switched off, it is renamed here at once and
        told when it returns. Returns an error to show, or None."""
        if not self.core.is_hub:
            return "only the hub renames other devices"
        if name not in self.core.layout.owners() or name == self.core.node:
            return f"{name} is not another device in this group"
        dev_id = next((i for i, n in self.names_by_id.items() if n == name), None)
        with self._links_lock:
            link = self.links.get(name)
        if link is not None:
            link.ch.send({"t": "rename", "to": name, "name": new})
            return None
        if dev_id:
            self.pending_names[dev_id] = new
            self.names_by_id[dev_id] = new
        self._act(self.core.rename_peer, name, new)
        if name in self.versions:
            self.versions[new] = self.versions.pop(name)
        return None

    def set_rights(self, name: str, may_drive: bool, may_be_driven: bool):
        """Set what a device may do. This device's own, anywhere; another's,
        from the hub and while it is connected - it is the one that applies
        them. Returns an error to show, or None."""
        rights = {"may_drive": bool(may_drive), "may_be_driven": bool(may_be_driven)}
        if name == self.core.node:
            self.core.policy.update(rights)
            if self.core.is_hub:
                self._broadcast_group()
            else:
                self._send(dict({"t": "policy"}, **rights))
            return None
        if not self.core.is_hub:
            return "only the hub sets another device's rights"
        with self._links_lock:
            link = self.links.get(name)
        if link is None:
            return f"{name} is offline - rights can change when it's online"
        link.ch.send(dict({"t": "set_policy", "to": name}, **rights))
        return None

    def request(self, op: str, timeout: float = 5.0, **args) -> dict:
        """A member asks the hub to rename, remove or set the rights of a
        device, and waits for the answer - the window shows it."""
        if self.core.is_hub:
            return {"error": "this device is the hub"}
        if not self.connected():
            return {"error": f"not connected to {self.peer_name or 'the hub'}"}
        rid = secrets.token_hex(6)
        box = [threading.Event(), None]
        self._asked[rid] = box
        try:
            self._send(dict(args, t="manage", op=op, rid=rid))
            if not box[0].wait(timeout):
                return {"error": f"{self.peer_name or 'the hub'} did not answer"}
            return box[1] or {"error": "no answer"}
        finally:
            self._asked.pop(rid, None)

    def _manage(self, msg: dict, by: str) -> None:
        """The hub carries out a member's request, off the reader thread (it
        may rename, reconnect and save), and answers."""
        cb = self.on_manage
        try:
            args = {k: v for k, v in msg.items()
                    if k not in ("t", "op", "rid", "from", "to")}
            r = cb(msg.get("op"), args, by) if cb else {"error": "not supported"}
        except Exception as e:
            r = {"error": repr(e)}
        ok = bool(r and r.get("ok"))
        self._send({"t": "manage_result", "to": by, "rid": msg.get("rid"),
                    "ok": ok, "error": None if ok else (r or {}).get("error"),
                    "pending": bool((r or {}).get("pending"))})

    def _renamed_to(self, new: str) -> None:
        self._log(f"the hub renamed this device to {new!r}")
        self._event("renamed_by_hub", name=new)
        cb = self.on_rename
        if cb is not None:
            threading.Thread(target=cb, args=(new,), daemon=True).start()

    def _removed_by(self, by: str) -> None:
        """The hub removed this device, now or while it was switched off."""
        self.blocked = "removed"
        self._dial_state("failed", "removed", by)
        self._log(f"{by} removed this device from its group")
        self._event("removed", name=by)
        cb = self.on_removed
        if cb is not None:
            threading.Thread(target=cb, args=(by,), daemon=True).start()

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
                    # perf_counter, not monotonic: on Windows monotonic ticks
                    # every 15.6 ms, so a 3 ms round trip read 0 or 16 - and 0
                    # showed as no reading at all.
                    l.ping_at[i] = time.perf_counter()
                    if len(l.ping_at) > 16:
                        l.ping_at.pop(min(l.ping_at))
                    l.ch.send(protocol.ping(i))

    def _keep_awake(self) -> None:
        """While the pointer is across - another machine driving this one, or
        this one driving another's screen - keep this machine's radio awake:
        see KEEP_AWAKE_S. An idle link is left alone."""
        while not self._stop:
            time.sleep(self.KEEP_AWAKE_S)
            if self.driven_from() is None and not self.driving_elsewhere():
                continue
            with self._links_lock:
                links = list(self.links.values())
            for l in links:
                l.ch.send(protocol.keep_awake())

    def driving_elsewhere(self) -> bool:
        """Is this machine's mouse driving another machine's screen now?"""
        core = self.core
        with self._lock:
            mine = core.baton.holder == core.node
            away = core.cursor_is_remote()
        return mine and away and self.connected()

    def driven_from(self):
        """The machine driving this one right now, or None."""
        core = self.core
        with self._lock:
            holder = core.baton.holder
            here = not core.cursor_is_remote()
        if holder in (None, core.node) or not here or not self.connected():
            return None
        return holder

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
                self._was_remote = False
                continue                      # nobody to tell, nothing to fetch
            try:
                out = self.clip.on_message(msg) if msg else self._clip_look()
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
                if m.get("t") == "clipdata":
                    self._clip_room()
                self._send(m)
                if m.get("t") == "clipdata":
                    time.sleep(len(m.get("v") or "") / self.CLIP_RATE)

    # An image is hundreds of chunks on the same link as the pointer. Sent at
    # once they filled its queue - and a full queue makes room by dropping its
    # oldest frame, which could have been a key - then the network's own
    # buffer, where the pointer would wait behind them. So each chunk waits
    # for the queue to be nearly empty, and they go no faster than this.
    CLIP_BACKLOG = 8
    CLIP_RATE = 3_000_000                # bytes a second

    def _clip_room(self, patience: float = 10.0) -> None:
        """Wait until the links have hardly anything waiting to go."""
        end = time.monotonic() + patience
        while time.monotonic() < end and not self._stop:
            with self._links_lock:
                busiest = max((getattr(l.ch, "backlog", int)() for l in self.links.values()),
                              default=0)
            if busiest < self.CLIP_BACKLOG:
                return
            time.sleep(0.002)

    def _clip_look(self) -> list:
        """One tick of watching our own clipboard.

        Where a look is nearly free (Windows' change counter), look every tick.
        Where it runs a program (Linux), look only when the pointer has just
        left this machine - the moment a copy made here can next be pasted
        anywhere else. Looking every 400ms made GNOME's dock flicker (clip.py).
        """
        remote = self.core.cursor_is_remote()
        left = remote and not self._was_remote
        self._was_remote = remote
        # On leaving, always a real look, whatever the change counter says:
        # the moment a copy made here can next be pasted elsewhere, and a
        # counter that is missing or wrong must not keep it back.
        return self.clip.poll(force=left) if (self.clip.cheap() or left) else []

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
            # Flushed: a refusal ("wrong password", "removed") is queued just
            # before this, and closing under it lost it - the other side then
            # saw a hang-up and retried forever instead of saying why.
            ch.close(flush=1.0)

    def _greet(self, ch, addr):
        """The hub's side of the handshake. The peer's name if it proved itself,
        else None."""
        # We speak first, with a challenge, so the caller has something to answer
        # and the password never crosses the wire.
        chal = protocol.nonce()
        dh_priv, dh_pub = secure.keypair()
        ch.send(protocol.auth(self.core.node, chal, self.device_id, dh=dh_pub))
        hello = ch.recv()
        if hello is None:
            return None
        protocol.check_version(hello)
        who = hello.get("node", str(addr))
        if hello.get("invite"):
            return self._invited(ch, addr, chal, hello, dh_priv, dh_pub)
        their_dh = hello.get("dh")
        if not their_dh:
            ch.send(protocol.err("no key exchange - update Nishro Link", code="auth"))
            return None
        bind = f"{dh_pub}|{their_dh}"

        if not protocol.verify(self._own_key(), chal, who, self.core.node,
                               hello.get("proof", ""), bind):
            ch.send(protocol.err("wrong password", code="auth"))
            self._log(f"rejected {who} at {addr}: wrong password"
                      + ("" if hello.get("proof") else
                         " (it sent no proof at all - older build?)"))
            self._event("rejected", name=who, reason="wrong_password")
            return None
        if hello.get("probe"):
            # Only checking the password before switching groups. Answer, prove
            # ourselves back, and change nothing - not even
            # a removed device's standing: only joining does that.
            if hello.get("nonce"):
                ch.send({"t": "probe_ok", "v": protocol.VERSION,
                         "node": self.core.node,
                         "proof": protocol.proof(self._own_key(), hello["nonce"],
                                                 self.core.node, who, bind)})
            return None
        if not self.enabled:
            # Sharing was switched off while this connection was being
            # accepted: the accept loop checks only between accepts, and a
            # member dials straight back when its link drops. Refuse - it tries
            # again later, as after any refusal - rather than link a paused
            # computer. Before anything about the device is changed.
            ch.send(protocol.err(f"sharing is off on {self.core.node}", code="paused"))
            return None
        their_id = hello.get("id")
        want = self.pending_names.get(their_id) if their_id else None
        if want and want != who:
            # Renamed from here while it was off: it takes the name first.
            # {**a, **b}, not a | b: dict union is Python 3.9, and on 3.8 this
            # killed the handshake of every device renamed while it was off.
            ch.send({**protocol.err(f"renamed to {want}", code="rename"), "name": want})
            return None
        if their_id and their_id in self.removed_ids:
            if not hello.get("join"):
                # Removed while it was switched off. It still knows the password,
                # so the password cannot keep it out; this does, until someone
                # pairs it again on purpose.
                ch.send(protocol.err(f"removed from {self.core.node}'s group",
                                     code="removed"))
                self._log(f"refused {who}: it was removed from this group")
                return None
            self.removed_ids.discard(their_id)

        # ...and prove ourselves back, so a machine never accepts injected
        # keystrokes from something that cannot prove it knows the password.
        their_chal = hello.get("nonce")
        if not their_chal:
            ch.send(protocol.err("challenge us back before we will drive you"))
            self._log(f"rejected {who}: it did not challenge us (older build?)")
            return None
        if who == self.core.node:
            ch.send(protocol.err(f"this group already has a machine called {who!r}",
                                 code="name"))
            self._log(f"rejected a machine calling itself {who!r} - that is our name")
            return None
        my_proof = protocol.proof(self._own_key(), their_chal, self.core.node, who,
                                  bind)
        try:
            dh_secret = secure.shared(dh_priv, their_dh)
        except secure.SecureError as e:
            ch.send(protocol.err(str(e)))
            return None
        if their_id:
            self.pending_names.pop(their_id, None)
            old = self.names_by_id.get(their_id)
            if old and old != who:
                # Renamed on its own screen. Same machine: keep its place.
                with self._links_lock:
                    stale = self.links.pop(old, None)
                if stale is not None:
                    stale.ch.close()
                    self._act(self.core.peer_lost, old)
                self._act(self.core.rename_peer, old, who)
                if old in self.versions:
                    self.versions[who] = self.versions.pop(old)
                if self.on_devices:
                    try:
                        self.on_devices("renamed", who, {"old": old, "id": their_id})
                    except Exception:
                        pass
                self._log(f"{old} is called {who} now")
                self._event("renamed", name=who, old=old)
            self.names_by_id[their_id] = who
        pol = hello.get("policy") or {}
        with self._lock:
            self.core.rights[who] = {"may_drive": pol.get("may_drive", True),
                                     "may_be_driven": pol.get("may_be_driven", True)}
        if hello.get("version"):
            self.versions[who] = hello["version"]
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
            # Everything after the welcome is encrypted, both ways.
            ch.seal(*secure.session(dh_secret, self._own_key(), chal, their_chal,
                                    "hub"))
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
        if not self.enabled:
            # Switched off during the handshake, after the check above but
            # before this link was registered for set_enabled() to close.
            # Closed here instead; the pump sees it and cleans up.
            ch.close()
            return who
        if self.on_devices:
            try:
                self.on_devices("joined", who, {
                    "id": hello.get("id"), "addr": addr[0] if addr else None,
                    "screens": screens, "version": hello.get("version")})
            except Exception as e:
                self._log(f"could not save the device list: {e!r}")
        self._broadcast_group()          # everyone learns who joined, and the desk
        self._event("joined", name=who, first=bool(hello.get("join")))
        self._log(f"peer connected: {who} (password verified both ways)"
                  + ("  (resumed)" if resumed else "")
                  + (f" - {len(self.links)} connected" if len(self.links) > 1 else ""))
        if not self.pin:
            self._log("WARNING: no password is set, so anything on this network "
                      "that speaks the protocol can connect and type here")
        return who

    def _invited(self, ch, addr, chal, hello, dh_priv, dh_pub):
        """Another device typed OUR password and asks us to join ITS group.

        Only a device on its own accepts: one with devices of its own would
        strand them. Both sides prove the password - bound to both sides'
        key-exchange values, as on any link - before anything changes; the
        invitation is then sent encrypted, and the group's password inside it
        is sealed under ours as well (protocol.wrap)."""
        who = hello.get("node", "?")
        their_dh = hello.get("dh")
        if not their_dh:
            ch.send(protocol.err("no key exchange - update Nishro Link", code="auth"))
            return None
        bind = f"{dh_pub}|{their_dh}"
        if not protocol.verify(self._own_key(), chal, who, self.core.node,
                               hello.get("proof", ""), bind):
            ch.send(protocol.err("wrong password", code="auth"))
            self._log(f"{who} tried to add this device with the wrong password")
            self._event("rejected", name=who, reason="wrong_password")
            return None
        their_chal = hello.get("nonce")
        if not their_chal:
            ch.send(protocol.err("challenge us back", code="auth"))
            return None
        if self.members():
            ch.send(protocol.err(
                f"{self.core.node} already has devices in its own group "
                f"({', '.join(self.members())})", code="busy"))
            return None
        if self.on_invited is None:
            ch.send(protocol.err("this device cannot be added from elsewhere",
                                 code="busy"))
            return None
        try:
            keys = secure.session(secure.shared(dh_priv, their_dh),
                                  self._own_key(), chal, their_chal, "hub")
        except secure.SecureError as e:
            ch.send(protocol.err(str(e), code="auth"))
            self._log(f"{who} tried to add this device: {e}")
            return None
        ch.send(protocol.invite_ok(
            self.core.node, protocol.proof(self._own_key(), their_chal,
                                           self.core.node, who, bind)))
        # Everything after this is encrypted, both ways.
        ch.seal(*keys)
        msg = ch.recv()
        if not msg or msg.get("t") != "invite":
            return None
        try:
            secret = protocol.unwrap(msg.get("secret") or {}, self._own_key(),
                                     chal, their_chal)
            hub, port = str(msg["hub"]), int(msg.get("port") or self.port)
        except (KeyError, TypeError, ValueError) as e:
            ch.send(protocol.invite_done(False, f"could not read the invitation: {e}"))
            return None
        where = msg.get("addr") or (addr[0] if addr else None)
        ok = False
        try:
            ok = bool(self.on_invited(hub, msg.get("hub_id"), where, port, secret,
                                      who))
        except Exception as e:
            self._log(f"could not join {hub}'s group: {e!r}")
        ch.send(protocol.invite_done(ok))
        ch.close(flush=1.0)
        if ok:
            self._log(f"{who} added this device to {hub}'s group")
            self._event("invited", name=who, group=hub)
        return None

    # ------------------------------------------------------- adding, inviting
    _ADDING = {"searching": "looking for it on the network",
               "connecting": "connecting to {d}",
               "verifying": "checking the password with {d}",
               "joining": "the password is right - waiting for {d} to connect "
                          "and join",
               "checked": "the password is right",
               "connected": "done - {d} is in the group"}

    def _log_adding(self, mode, target, phase, reason, detail) -> None:
        """Adding a device leaves a trace in the log: each step, and how it
        ended. Seen: two attempts that stuck, with nothing in either computer's
        log to say where. Never the password - `detail` holds names, addresses
        and the other side's words."""
        what = f"adding {target}" if mode == "invite" else f"joining {target}'s group"
        if phase == "failed":
            self._log_async(f"{what}: failed - {reason}"
                            + (f" ({detail})" if detail else ""))
        elif phase in self._ADDING:
            self._log_async(f"{what}: "
                            + self._ADDING[phase].format(d=detail or target))

    def invite(self, name: str, pin: str, addr: str = None, port: int = None) -> dict:
        """Add a device that is on its own to THIS group, using the password it
        shows. Blocks for a few seconds; progress is in `adding`.

        {"ok": True} once it has joined, else {"ok": False, "reason", "detail"}.
        """
        pin = pairing.normalise(pin)

        def state(phase, reason=None, detail=None):
            self.adding = {"mode": "invite", "phase": phase, "reason": reason,
                           "detail": detail, "target": name, "since": time.time()}
            self._log_adding("invite", name, phase, reason, detail)
            return {"ok": phase == "connected", "reason": reason, "detail": detail}

        if self.core.is_hub:
            hub, hub_id, hub_addr, hub_port = (self.core.node, self.device_id, None,
                                               self.port)
        elif self.connected():
            hub = self.trusted_peer or self.peer_name
            hub_id, hub_addr, hub_port = self.peer_id, self.peer_addr, self.port
        else:
            return state("failed", "not_connected",
                         "this device is not connected to its group right now")
        if not self.pin:
            return state("failed", "no_password", "this group has no password")

        state("searching")
        if addr is None:
            found = [f for f in self.find(name)
                     if f.name.casefold() == name.casefold() or f.id == name]
            if not found:
                return state("failed", "not_found", name)
            f = found[0]
            if not f.waiting:
                return state("failed", "in_group", f.group)
            if not f.alone:
                return state("failed", "busy", f.name)
            addr, port = f.addr, f.port or self.port
        port = port or self.port

        state("connecting", detail=addr)
        try:
            sock = socket.create_connection((addr, port), timeout=3)
        except OSError as e:
            return state("failed", "unreachable", str(e))
        ch = protocol.LineChannel(sock)
        try:
            sock.settimeout(8)
            greet = ch.recv()
            if not greet or greet.get("t") != "auth" or not greet.get("nonce"):
                return state("failed", "refused",
                             (greet or {}).get("msg") or "it did not answer")
            protocol.check_version(greet)
            them = greet.get("node") or name
            state("verifying", detail=them)
            mine = protocol.nonce()
            theirs = pairing.key(pin, greet.get("id"))     # ITS password, ITS salt
            dh_priv, dh_pub = secure.keypair()
            bind = f"{greet.get('dh')}|{dh_pub}"
            ch.send(protocol.hello(
                self.core.node, [], proof=protocol.proof(theirs, greet["nonce"],
                                                         self.core.node, them, bind),
                chal=mine, dev_id=self.device_id, invite=True, dh=dh_pub))
            reply = ch.recv()
            if not reply:
                return state("failed", "refused", "it hung up")
            if reply.get("t") == "err":
                code = reply.get("code")
                return state("failed", {"auth": "wrong_password",
                                        "busy": "busy"}.get(code, "refused"),
                             reply.get("msg"))
            if reply.get("t") != "invite_ok" or not protocol.verify(
                    theirs, mine, them, self.core.node, reply.get("proof", ""),
                    bind):
                return state("failed", "impostor", them)
            try:
                ch.seal(*secure.session(secure.shared(dh_priv, greet.get("dh")),
                                        theirs, greet["nonce"], mine, "dialer"))
            except secure.SecureError as e:
                return state("failed", "impostor", f"{them}: {e}")
            ch.send(protocol.invite(hub, hub_id, hub_addr, hub_port,
                                    protocol.wrap(self.pin, theirs, greet["nonce"],
                                                  mine)))
            done = ch.recv()
            if not done or not done.get("ok"):
                return state("failed", "refused",
                             (done or {}).get("msg") or f"{them} did not accept")
        except protocol.ProtocolError as e:
            return state("failed", "version", str(e))
        except OSError as e:
            return state("failed", "unreachable", str(e))
        finally:
            ch.close()

        # It accepted and is now dialling the group. Say "connected" only when
        # it is: here, if this is the hub; in the roster, if not.
        state("joining", detail=them)
        end = time.monotonic() + 20
        while time.monotonic() < end and not self._stop:
            with self._links_lock:
                here = them in self.links
            if here or them in self.core.online:
                self._log(f"added {them} to the group")
                return state("connected", detail=them)
            time.sleep(0.1)
        return state("failed", "timeout", them)

    def probe(self, name: str, pin: str, addr: str = None, port: int = None) -> dict:
        """Before joining a group: find its hub and check the password with it,
        both ways, without joining. Blocks for a few seconds; progress is in
        `adding`. {"ok": True, "hub", "hub_id", "addr", "port"} or a reason.

        `name` may be any device in the group: one that is not the hub says
        which group it is in, and the hub is looked for instead.
        """
        pin = pairing.normalise(pin)
        target = name

        def state(phase, reason=None, detail=None, **extra):
            self.adding = {"mode": "join", "phase": phase, "reason": reason,
                           "detail": detail, "target": target, "since": time.time()}
            self._log_adding("join", target, phase, reason, detail)
            return dict({"ok": phase == "checked", "reason": reason,
                         "detail": detail}, **extra)

        state("searching")
        hub_id = None
        if addr is None:
            for _ in range(2):             # the device itself, then its hub
                found = [f for f in self.find(target)
                         if f.name.casefold() == target.casefold() or f.id == target]
                if not found:
                    return state("failed", "not_found", target)
                f = found[0]
                if f.waiting:
                    addr, port, hub_id = f.addr, f.port or self.port, f.id
                    break
                if not f.group or f.group.casefold() == target.casefold():
                    return state("failed", "paused", f.name)
                target = f.group
            else:
                return state("failed", "not_found", target)
        port = port or self.port

        state("connecting", detail=addr)
        try:
            sock = socket.create_connection((addr, port), timeout=3)
        except OSError as e:
            return state("failed", "unreachable", str(e))
        ch = protocol.LineChannel(sock)
        try:
            sock.settimeout(8)
            greet = ch.recv()
            if not greet or greet.get("t") != "auth" or not greet.get("nonce"):
                return state("failed", "refused",
                             (greet or {}).get("msg") or "it did not answer")
            protocol.check_version(greet)
            hub = greet.get("node") or target
            target = hub
            state("verifying", detail=hub)
            mine = protocol.nonce()
            k = pairing.key(pin, greet.get("id"))
            _priv, dh_pub = secure.keypair()
            bind = f"{greet.get('dh')}|{dh_pub}"
            ch.send(protocol.hello(
                self.core.node, [], proof=protocol.proof(k, greet["nonce"],
                                                         self.core.node, hub, bind),
                chal=mine, dev_id=self.device_id, join=True, probe=True,
                dh=dh_pub))
            reply = ch.recv()
            if not reply:
                return state("failed", "refused", "it hung up")
            if reply.get("t") == "err":
                return state("failed", {"auth": "wrong_password",
                                        "name": "name_taken"}.get(
                                            reply.get("code"), "refused"),
                             reply.get("msg"))
            if reply.get("t") != "probe_ok" or not protocol.verify(
                    k, mine, hub, self.core.node, reply.get("proof", ""), bind):
                return state("failed", "impostor", hub)
        except protocol.ProtocolError as e:
            return state("failed", "version", str(e))
        except OSError as e:
            return state("failed", "unreachable", str(e))
        finally:
            ch.close()
        return state("checked", detail=hub, hub=hub,
                     hub_id=greet.get("id") or hub_id, addr=addr, port=port)

    def _connect_loop(self) -> None:
        while not self._stop and not self._reconfig:
            if not self.enabled or self.blocked:
                time.sleep(0.2)
                continue
            delay = self.backoff.next_delay_ms() / 1000.0
            if delay:
                self._log(f"retrying in {delay:.1f}s "
                          f"(attempt {self.backoff.failures + 1}, "
                          f"via {self.backoff.target()})")
                time.sleep(delay)
            if self._stop or self._reconfig or self.blocked:
                continue
            target = self._target()
            if target is None:
                self.backoff.on_failure()
                reason, detail = self._miss or ("not_found", None)
                self._dial_state("retrying", reason, detail)
                if self.backoff.failures in (1, 4) or self.backoff.failures % 10 == 0:
                    self._log(f"cannot find '{self.peer_name or self.peer_id}' on this "
                              f"network - is it switched on and waiting for a "
                              f"connection? If it is, its firewall may be dropping "
                              f"the search (UDP {self.port}).")
                continue
            addr, port = target
            self._dial_state("connecting", detail=addr)
            try:
                sock = socket.create_connection((addr, port), timeout=2)
            except OSError as e:
                self.backoff.on_failure()
                self._dial_state("retrying", "unreachable", str(e))
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
            # Two seconds to connect, but longer for the handshake: a hub's
            # first one after it starts derives its password key (PBKDF2,
            # half a second on a Celeron; more on a busy machine), and with
            # two seconds for that too every attempt could time out while
            # the hub was still working. The same 8 as adding a device.
            sock.settimeout(8)
            ch = protocol.LineChannel(sock)
            try:
                if not self._say_hello(ch, addr):
                    self.backoff.on_failure()
                    continue
                self.backoff.on_connected()
                sock.settimeout(None)
                hub = self.trusted_peer or self.peer_name or "hub"
                self._dial_state("connected", detail=hub)
                self._register(hub, ch, (addr, port))
                self._pump(ch, hub, (addr, port))
                self.backoff.on_disconnected()
                if not (self._stop or self._reconfig or self.blocked):
                    self._dial_state("retrying", "lost", hub)
            except protocol.ProtocolError as e:
                self._log(f"protocol error: {e}")
                self._dial_state("retrying", "version" if "version" in str(e)
                                 else "protocol", str(e))
                self.backoff.on_disconnected()
            except OSError as e:
                # The connection broke during the handshake: the other side
                # restarted, or closed its listening socket just as we reached
                # it. An ordinary failed attempt - counted, and retried after
                # the usual wait. Left to the supervisor it was logged as "link
                # loop stopped" and retried at once, with no backoff at all.
                self.backoff.on_failure()
                if not (self._stop or self._reconfig):
                    self._dial_state("retrying", "unreachable", str(e))
                    self._log(f"connection to {addr} lost during the handshake: {e}")
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
        self._dial_state("searching")
        every = self.find(q)
        found = [f for f in every if f.waiting]
        self.last_found = found[0] if found else None
        if not found:
            # Found but not listening means it is in some other group now - a
            # different thing to tell a person than "switched off".
            self._miss = (("not_waiting", every[0].group) if every
                          else ("not_found", q))
            return None
        self._miss = None
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
            self._dial_state("retrying", "refused", greet.get("msg"))
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
        self._dial_state("verifying", detail=hub)
        if not greet.get("dh"):
            self._log(f"{hub} runs an older Nishro Link without encryption - "
                      f"update it")
            self._dial_state("retrying", "version", hub)
            return False
        dh_priv, dh_pub = secure.keypair()
        bind = f"{greet['dh']}|{dh_pub}"

        screens = []
        for n in self.core.layout.mine():
            s = self.core.layout.get(n)
            screens.append(dict({"name": n, "w": s.w, "h": s.h},
                                **({"parts": [list(p) for p in s.parts]}
                                   if s.parts else {})))
        k = self._key_for(hub_id)
        ch.send(protocol.hello(
            self.core.node, screens, self.core.policy,
            proof=protocol.proof(k, greet["nonce"], self.core.node, hub, bind),
            chal=mine, resume=self.session, dev_id=self.device_id,
            join=self.joining, version=self.version, dh=dh_pub))

        reply = ch.recv()
        if reply is None:
            return False
        if reply.get("t") == "err":
            code = reply.get("code")
            self._log(f"refused by {hub}: {reply.get('msg')}")
            if code == "removed":
                self._removed_by(hub)
            elif code == "rename" and reply.get("name"):
                self._renamed_to(str(reply["name"]))
            elif code in ("auth", "name"):
                # The same attempt would get the same answer. Stop, and say so,
                # until someone pairs again or switches sharing off and on.
                self.blocked = "wrong_password" if code == "auth" else "name_taken"
                self._dial_state("failed", self.blocked, hub)
                self._event("dial_failed", name=hub, reason=self.blocked)
            else:
                self._dial_state("retrying", "refused", reply.get("msg"))
            return False
        protocol.check_version(reply)

        # Mutual: we do not let anything inject into this machine until it has
        # proved it knows the password too.
        if not protocol.verify(k, mine, hub, self.core.node,
                               reply.get("proof", ""), bind):
            self._log(f"{hub} could not prove it knows the password - refusing to "
                      f"be driven by it")
            self._dial_state("retrying", "impostor", hub)
            return False
        try:
            ch.seal(*secure.session(secure.shared(dh_priv, greet["dh"]), k,
                                    greet["nonce"], mine, "dialer"))
        except secure.SecureError as e:
            self._log(f"{hub}: {e}")
            return False
        self.trusted_peer = hub
        self._hub_id = hub_id
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
        first = self.joining
        self.joining = False
        self._event("connected", name=hub, first=first)
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
                if not hub and t == "removed":
                    self._removed_by(msg.get("by") or name)
                    break
                if not hub and t == "rename" and msg.get("name"):
                    self._renamed_to(str(msg["name"]))
                    continue
                if not hub and t == "set_policy":
                    rights = {k: bool(msg[k]) for k in ("may_drive", "may_be_driven")
                              if k in msg}
                    with self._lock:
                        self.core.policy.update(rights)
                    self._send(dict({"t": "policy"}, **{
                        "may_drive": self.core.may_drive(),
                        "may_be_driven": self.core.may_be_driven()}))
                    self._log(f"{name} set this device's rights: {rights}")
                    self._event("rights_set", name=name)
                    if self.on_policy:
                        try:
                            self.on_policy(dict(self.core.policy))
                        except Exception:
                            pass
                    continue
                if hub and t == "manage":
                    threading.Thread(target=self._manage, args=(msg, name),
                                     daemon=True).start()
                    continue
                if not hub and t == "manage_result":
                    box = self._asked.get(msg.get("rid"))
                    if box is not None:
                        box[1] = {k: msg.get(k) for k in ("ok", "error", "pending")}
                        box[0].set()
                    continue
                if hub and t == "policy":
                    with self._lock:
                        self.core.rights[name] = {
                            "may_drive": bool(msg.get("may_drive", True)),
                            "may_be_driven": bool(msg.get("may_be_driven", True))}
                    self._broadcast_group()
                    continue
                if not hub and t == "rekey":
                    try:
                        new = protocol.unwrap(
                            msg.get("secret") or {},
                            self._key_for(self._hub_id or self.peer_id),
                            msg.get("a", ""), msg.get("b", ""))
                    except ValueError as e:
                        self._log(f"ignored a new password from {name}: {e}")
                        continue
                    self.pin = pairing.normalise(new)
                    self._log(f"{name} changed the group's password - saved")
                    self._event("rekeyed", name=name, pin=new)
                    continue
                if hub and t == "leave":
                    self._leaving.add(name)   # forgotten as its link closes
                    self._event("left_group", name=name)
                    self._log(f"{name} left the group")
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
                    if name in self._leaving:
                        # Removed, or left on its own: off the arrangement and
                        # out of the list, now that its link is gone.
                        self._leaving.discard(name)
                        self._act(self.core.forget_machine, name)
                        if self.on_devices:
                            try:
                                self.on_devices("forgotten", name, {})
                            except Exception:
                                pass
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
                    if not (self._stop or self._reconfig or self.blocked):
                        self._event("disconnected", name=name)

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
                l.rtt_ms = self.rtt_ms = (time.perf_counter() - sent) * 1000.0
        elif t == "err":
            self._log(f"{name} says: {msg.get('msg')}")
        # "ka": arriving was its whole job (and it fed the watchdog above)


def _grant_msg(g: Grant) -> dict:
    return protocol.baton(g.holder, g.epoch, g.screen, g.x, g.y, g.held)
