"""Two real Nodes over a real socket, with the OS faked out.

Everything below the socket is genuine - handshake, threads, LineChannel,
epochs, the heartbeat. Only the mouse and the screen are pretend, because CI
has neither and neither does a laptop you are still using.
"""
import json
import socket
import struct
import sys
import threading
import time

import pytest

from link.clip import ClipboardSync
from link.desk import simple
from link.node import Node, NodeCore


class FakeBoard:
    """A clipboard we can drive, standing in for PowerShell / wl-paste."""

    def __init__(self):
        self.text = ""
        self.seq = 1

    def read(self):
        return self.text

    def write(self, text):
        self.text = text
        self.seq += 1
        return True

    def sequence(self):
        return self.seq

    def copy(self, text):
        self.text = text
        self.seq += 1


class FakeCapture:
    def __init__(self):
        self.sink = None
        self.suppress = (False, False)
        self.stopped = False
        self.injected = []
        self.cursor_here = False

    def start(self, sink):
        self.sink = sink

    def stop(self):
        self.stopped = True

    def set_suppress(self, mouse, keyboard, cursor_here=False):
        self.suppress = (mouse, keyboard)
        self.cursor_here = cursor_here

    def note_injected(self, x, y):
        self.injected.append((x, y))


class FakeInjector:
    def __init__(self):
        self.calls = []

    def move_abs(self, x, y):
        self.calls.append(("move_abs", x, y))

    def button(self, name, down):
        self.calls.append(("button", name, down))

    def key(self, code, down):
        self.calls.append(("key", code, down))

    def wheel(self, dx, dy):
        self.calls.append(("wheel", dx, dy))

    def close(self):
        pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Pair:
    """A hub and a peer, wired to each other and actually running."""

    def __init__(self, pin="", **policy):
        self.port = free_port()
        hub_lay = simple("laptop", (1366, 768), "aio", (1920, 1080), "left")
        aio_lay = simple("aio", (1920, 1080), "laptop", (1366, 768), "right")

        self.hub_cap, self.hub_inj = FakeCapture(), FakeInjector()
        self.aio_cap, self.aio_inj = FakeCapture(), FakeInjector()
        self.hub_core = NodeCore("laptop", hub_lay, {}, is_hub=True)
        self.aio_core = NodeCore("aio", aio_lay, policy, is_hub=False)

        # Both nodes' logs are kept, so a test that times out says WHY instead
        # of only "timed out" - an intermittent failure with no evidence is one
        # that gets rerun rather than understood.
        self.logs = {"hub": [], "aio": []}
        self.hub = Node(self.hub_core, self.hub_cap, self.hub_inj,
                        port=self.port, pin=pin,
                        on_log=lambda m: self.logs["hub"].append(m))
        self.aio = Node(self.aio_core, self.aio_cap, self.aio_inj,
                        port=self.port, pin=pin, peer_addr="127.0.0.1",
                        on_log=lambda m: self.logs["aio"].append(m))

        # Swap in clipboards we control. The real ones shell out to PowerShell,
        # which would put a fifth of a second into every assertion here.
        self.hub_board, self.aio_board = FakeBoard(), FakeBoard()
        for n, name, b in ((self.hub, "laptop", self.hub_board),
                           (self.aio, "aio", self.aio_board)):
            n.clip = ClipboardSync(name, read=b.read, write=b.write,
                                   seq_fn=b.sequence)

    def start(self):
        for n in (self.hub, self.aio):
            threading.Thread(target=n.run, daemon=True).start()
        return self

    def stop(self):
        for n in (self.hub, self.aio):
            n.stop()

    def wait(self, cond, timeout=5.0, what="condition"):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.01)
        tail = "\n".join(f"  {who}: {line}" for who in ("hub", "aio")
                         for line in self.logs[who][-12:])
        pytest.fail(f"timed out waiting for {what} (port {self.port})\n{tail}")

    def connected(self):
        self.wait(lambda: self.hub.ch is not None and self.aio.ch is not None,
                  what="both ends to connect")
        return self


@pytest.fixture
def pair():
    p = Pair()
    try:
        yield p
    finally:
        p.stop()


# ------------------------------------------------------------- handshake
def test_they_find_each_other(pair):
    pair.start().connected()
    assert pair.aio_core.epoch == pair.hub_core.epoch
    assert pair.aio_core.baton.holder == "laptop"


def test_the_peer_learns_who_is_driving(pair):
    pair.start().connected()
    assert pair.hub_core.holds() is True
    assert pair.aio_core.holds() is False


def test_a_wrong_pin_is_refused():
    p = Pair(pin="6120")
    p.aio.pin = "0000"
    try:
        p.start()
        time.sleep(0.8)
        assert p.aio.ch is None
        assert p.aio_core.suppress_mouse() is False   # and nothing gets frozen
    finally:
        p.stop()


def test_the_peer_gets_a_session_it_can_resume_with(pair):
    pair.start().connected()
    pair.wait(lambda: pair.aio.session is not None, what="a session id")
    assert pair.hub.sessions.can_resume(pair.aio.session, "aio") is True


# ------------------------------------------------------- driving across
def test_crossing_an_edge_moves_the_other_machines_pointer(pair):
    pair.start().connected()
    # The laptop's OS pointer is clamped at its left edge and still pushing.
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)

    pair.wait(lambda: any(c[0] == "move_abs" for c in pair.aio_inj.calls),
              what="the AIO pointer to move")
    assert pair.hub_core.cursor.screen == "aio"
    x, y = pair.hub_core.cursor.x, pair.hub_core.cursor.y
    assert ("move_abs", x, y) in pair.aio_inj.calls


def test_the_driver_starts_suppressing_and_the_far_side_does_not(pair):
    pair.start().connected()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)
    pair.wait(lambda: pair.hub_cap.suppress[0] is True, what="the hub to suppress")
    # the AIO hosts the cursor: its mouse only claims, but it types locally
    pair.wait(lambda: pair.aio_cap.suppress == (True, False),
              what="the AIO to suppress only its mouse")


def test_typing_lands_on_the_machine_hosting_the_cursor(pair):
    pair.start().connected()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)
    pair.wait(lambda: pair.hub_core.cursor_is_remote(), what="the crossing")
    pair.hub_cap.sink.on_key(30, True)
    pair.wait(lambda: ("key", 30, True) in pair.aio_inj.calls, what="the keystroke")


def test_clicks_and_scroll_carry_across(pair):
    pair.start().connected()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)
    pair.wait(lambda: pair.hub_core.cursor_is_remote(), what="the crossing")
    pair.hub_cap.sink.on_button("left", True)
    pair.hub_cap.sink.on_wheel(0, -1)
    pair.wait(lambda: ("button", "left", True) in pair.aio_inj.calls, what="the click")
    pair.wait(lambda: ("wheel", 0, -1) in pair.aio_inj.calls, what="the scroll")


def test_the_cursor_comes_home(pair):
    pair.start().connected()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)
    pair.wait(lambda: pair.hub_core.cursor_is_remote(), what="the crossing")
    pair.hub_cap.sink.on_motion(5000, 0)
    assert pair.hub_core.cursor.screen == "laptop"
    pair.wait(lambda: pair.hub_cap.suppress[0] is False, what="the hub to release")


# --------------------------------------------- the other machine drives
def test_the_peer_can_take_control_with_its_own_mouse(pair):
    """The whole point. The AIO is the 'client' and its mouse drives anyway."""
    pair.start().connected()
    pair.aio_cap.sink.on_pointer(500, 500, 20, 0)      # a deliberate grab

    pair.wait(lambda: pair.aio_core.holds(), what="the AIO to take the baton")
    assert pair.hub_core.holds() is False
    assert pair.hub_core.epoch == pair.aio_core.epoch


def test_control_can_be_handed_back_and_forth(pair):
    pair.start().connected()

    def settled(holder):
        """A handover is not finished until the news has reached the far end.

        Asserting on one side alone races: for a moment after the laptop takes
        the baton back, the AIO still believes it holds it, and a nudge in that
        window is consumed as driving rather than as a claim. Its events go out
        under the old epoch and are correctly discarded on arrival - safe and
        self-correcting, but not something a test may assert through.
        """
        return (pair.hub_core.baton.holder == holder
                and pair.aio_core.baton.holder == holder
                and pair.hub_core.epoch == pair.aio_core.epoch)

    # A claim refused by the anti-thrash guard is silent - the claimer is not
    # told and does not retry. A real mouse streams events, so it re-claims
    # 8px later; a test that sends ONE synthetic event gets nothing, so it has
    # to leave the guard's window clear before each handover.
    for _ in range(3):
        pair.aio_cap.sink.on_pointer(500, 500, 20, 0)
        pair.wait(lambda: settled("aio"), what="the AIO to take it")
        time.sleep(0.25)
        pair.hub_cap.sink.on_pointer(500, 300, 20, 0)
        pair.wait(lambda: settled("laptop"), what="the laptop to take it back")
        time.sleep(0.25)


def test_a_kiosk_can_be_driven_but_never_drives():
    p = Pair(may_drive=False)
    try:
        p.start().connected()
        p.aio_cap.sink.on_pointer(500, 500, 200, 0)    # frantic waving
        time.sleep(0.4)
        assert p.aio_core.holds() is False
        assert p.hub_core.holds() is True
    finally:
        p.stop()


# ------------------------------------------- P2: it always lets go again
def test_an_idle_link_is_not_mistaken_for_a_dead_one(pair):
    """Found on real hardware. The watchdog was fed only by INPUT, and a holder
    who is simply not moving the mouse sends none - so after a second of nobody
    touching anything, suppression dropped out on a perfectly healthy link.
    Keep-alive traffic is what tells the two apart."""
    pair.start().connected()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)        # cross; the AIO now hosts it
    pair.wait(lambda: pair.aio_cap.suppress[0] is True, what="suppression")

    time.sleep(3.0)                                    # twice the TTL, zero input

    assert pair.aio_cap.suppress[0] is True
    assert pair.hub_cap.suppress[0] is True
    assert pair.aio.ch is not None                     # and still genuinely connected


def test_killing_the_link_un_suppresses_both_machines(pair):
    """The lockout that would otherwise need the power button."""
    pair.start().connected()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)
    pair.wait(lambda: pair.hub_cap.suppress[0] is True, what="suppression")

    pair.aio.ch.close()                                # the link dies
    pair.wait(lambda: pair.hub_cap.suppress == (False, False), timeout=6,
              what="the hub to release its mouse")
    pair.wait(lambda: pair.aio_cap.suppress == (False, False), timeout=6,
              what="the AIO to release its mouse")


def test_a_held_key_is_released_when_the_link_dies(pair):
    """A key latched on a uinput device outlives the process that set it."""
    pair.start().connected()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)
    pair.wait(lambda: pair.hub_core.cursor_is_remote(), what="the crossing")
    pair.hub_cap.sink.on_key(29, True)                 # Ctrl down, over there
    pair.wait(lambda: ("key", 29, True) in pair.aio_inj.calls, what="Ctrl down")

    pair.aio.ch.close()
    pair.wait(lambda: ("key", 29, False) in pair.aio_inj.calls, timeout=6,
              what="Ctrl to be released")


def test_the_peer_reconnects_by_itself(pair):
    pair.start().connected()
    first = pair.aio.session
    pair.aio.ch.close()
    pair.wait(lambda: pair.aio.ch is None, what="the drop")
    pair.wait(lambda: pair.aio.ch is not None, timeout=8, what="the reconnect")
    assert pair.aio.session == first                   # same session, resumed


def test_the_epoch_moves_on_every_reconnect(pair):
    """So input still in flight from before the drop cannot land afterwards."""
    pair.start().connected()
    before = pair.hub_core.epoch
    pair.aio.ch.close()
    pair.wait(lambda: pair.aio.ch is not None, timeout=8, what="the reconnect")
    pair.wait(lambda: pair.hub_core.epoch > before, what="a fresh epoch")


def test_a_peer_that_redials_as_sharing_goes_off_is_refused(pair):
    """Seen on the real machines, from the tray: sharing switched off on the
    hub, the peer dialled straight back, and the accept already under way let
    it in - linked to a computer whose sharing was off. The accept loop only
    checks between accepts; the handshake must check too."""
    pair.start().connected()
    real = pair.hub._greet

    def greet_after_switching_off(ch, addr):
        pair.hub.enabled = False                # off, after accept() let it in
        return real(ch, addr)
    pair.hub._greet = greet_after_switching_off
    pair.aio.ch.close()
    pair.wait(lambda: any("sharing is off on laptop" in m for m in pair.logs["aio"]),
              timeout=8, what="the peer to be told sharing is off")
    pair.wait(lambda: not pair.hub.links, what="the hub to hold no link")
    assert sum("peer connected" in m for m in pair.logs["hub"]) == 1, pair.logs["hub"]
    assert pair.aio.blocked is None, "a pause is not a wrong password: it retries"


def test_sharing_switched_off_mid_handshake_leaves_no_link(pair):
    """Off after the handshake's check but before the link was registered for
    set_enabled() to close: the handshake closes it itself."""
    pair.start().connected()
    real = pair.hub_core.peer_online

    def online_then_off(name):
        pair.hub.enabled = False                # just before _register()
        return real(name)
    pair.hub_core.peer_online = online_then_off
    pair.aio.ch.close()
    pair.wait(lambda: sum("disconnected" in m for m in pair.logs["hub"]) >= 2,
              timeout=8, what="the new link to be closed again")
    pair.wait(lambda: not pair.hub.links, what="the hub to hold no link")


def test_stopping_never_leaves_input_suppressed(pair):
    pair.start().connected()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)
    pair.wait(lambda: pair.hub_cap.suppress[0] is True, what="suppression")
    pair.hub.stop()
    assert pair.hub_cap.suppress == (False, False)


def test_the_failsafe_frees_a_stuck_machine(pair):
    pair.start().connected()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)
    pair.wait(lambda: pair.hub_cap.suppress[0] is True, what="suppression")
    pair.hub_cap.sink.on_failsafe()
    assert pair.hub_cap.suppress == (False, False)


# -------------------------------------------------------------- clipboard
def test_copy_on_one_machine_pastes_on_the_other(pair):
    pair.start().connected()
    pair.hub_board.copy("copied on the laptop")
    pair.wait(lambda: pair.aio_board.text == "copied on the laptop",
              timeout=5, what="the clipboard to arrive on the AIO")


def test_the_clipboard_travels_both_ways(pair):
    pair.start().connected()
    pair.aio_board.copy("copied on the AIO")
    pair.wait(lambda: pair.hub_board.text == "copied on the AIO",
              timeout=5, what="the clipboard to arrive on the laptop")


def test_a_shared_clipboard_does_not_ping_pong(pair):
    """Both machines can originate, so an echo would loop forever."""
    pair.start().connected()
    pair.hub_board.copy("just the once")
    pair.wait(lambda: pair.aio_board.text == "just the once", timeout=5,
              what="the clipboard to arrive")
    seqs = (pair.hub_board.seq, pair.aio_board.seq)
    time.sleep(1.5)                       # several poll rounds on both sides
    assert (pair.hub_board.seq, pair.aio_board.seq) == seqs


def test_a_big_clipboard_arrives_in_one_piece(pair):
    pair.start().connected()
    big = "".join(f"line {i}\n" for i in range(4000))
    pair.hub_board.copy(big)
    pair.wait(lambda: pair.aio_board.text == big, timeout=8,
              what="a multi-chunk clipboard")


# ----------------------------------------------------------------- health
def test_the_link_measures_its_own_latency(pair):
    """'Why does it feel laggy' should have a number for an answer."""
    pair.start().connected()
    pair.wait(lambda: pair.hub.rtt_ms is not None or pair.aio.rtt_ms is not None,
              timeout=8, what="a ping round trip")


def test_a_machine_learns_the_cursor_has_left_it(pair):
    """Found on real hardware. While the cursor is on the holder's own screen no
    positions are sent - that is the point - so the machine it just left never
    heard it go, still believed it hosted the cursor, and kept its keyboard live.
    Typing there would have gone to the wrong computer."""
    pair.start().connected()

    # The AIO takes control and drives the cursor onto the laptop.
    pair.aio_cap.sink.on_pointer(500, 500, 20, 0)
    pair.wait(lambda: pair.aio_core.holds(), what="the AIO to take the baton")
    pair.aio_cap.sink.on_motion(3000, 0)   # laptop is on the AIO's RIGHT here
    pair.wait(lambda: pair.hub_core.cursor.screen == "laptop",
              what="the laptop to see the cursor arrive")
    assert pair.hub_cap.suppress[1] is False          # keyboard free: cursor is here

    # ...and then takes it away again.
    pair.aio_cap.sink.on_motion(-9000, 0)
    pair.wait(lambda: pair.hub_core.cursor.screen == "aio", timeout=5,
              what="the laptop to see the cursor leave")
    pair.wait(lambda: pair.hub_cap.suppress[1] is True, timeout=5,
              what="the laptop keyboard to start forwarding again")


# ------------------------------- learning instead of being told
def test_names_and_screen_sizes_are_learned_not_configured():
    """Both are already in the handshake. Asking a user to type them is asking
    for a fact the machines have, plus a chance to get it wrong - and a wrong
    screen size puts the cursor in the wrong place the moment it arrives.

    Both sides start here with deliberately wrong values and must end up right.
    """
    port = free_port()
    # The hub has never heard of "aio" and guesses its own size for the peer.
    hub_core = NodeCore("laptop", simple("laptop", (1366, 768), "peer", (1366, 768),
                                         "left"), {}, is_hub=True, side="left")
    # The peer has nonsense: wrong name, wrong size, and the wrong side.
    aio_core = NodeCore("aio", simple("aio", (1920, 1080), "nonsense", (640, 480),
                                      "top"), {}, is_hub=False, side="top")

    hub = Node(hub_core, FakeCapture(), FakeInjector(), port=port)
    aio = Node(aio_core, FakeCapture(), FakeInjector(), port=port,
               peer_addr="127.0.0.1")
    for n in (hub, aio):
        threading.Thread(target=n.run, daemon=True).start()
    try:
        end = time.monotonic() + 5
        while time.monotonic() < end and not (hub.ch and aio.ch):
            time.sleep(0.01)
        assert hub.ch and aio.ch, "did not connect"
        time.sleep(0.3)

        # the hub learned the peer's real name and size from its hello
        peer = [n for n in hub_core.layout.names() if n != "laptop"]
        assert peer == ["aio"], peer
        assert (hub_core.layout.get("aio").w,
                hub_core.layout.get("aio").h) == (1920, 1080)

        # the peer adopted the hub's layout wholesale - so it learned the name,
        # the size AND which side, none of which it was configured with
        assert sorted(aio_core.layout.names()) == ["aio", "laptop"]
        assert (aio_core.layout.get("laptop").w,
                aio_core.layout.get("laptop").h) == (1366, 768)
        assert aio_core.layout.mine() == ["aio"]
        # and the two graphs now agree, which is the point
        assert aio_core.layout.to_dict() == hub_core.layout.to_dict()
    finally:
        hub.stop(); aio.stop()


def test_a_learned_layout_crosses_correctly():
    """Learning the geometry is only worth anything if crossing then works."""
    port = free_port()
    hub_core = NodeCore("laptop", simple("laptop", (1366, 768), "peer", (1366, 768),
                                         "left"), {}, is_hub=True, side="left")
    aio_core = NodeCore("aio", simple("aio", (1920, 1080), "nonsense", (640, 480),
                                      "top"), {}, is_hub=False, side="top")
    hcap, hinj = FakeCapture(), FakeInjector()
    acap, ainj = FakeCapture(), FakeInjector()
    hub = Node(hub_core, hcap, hinj, port=port)
    aio = Node(aio_core, acap, ainj, port=port, peer_addr="127.0.0.1")
    for n in (hub, aio):
        threading.Thread(target=n.run, daemon=True).start()
    try:
        end = time.monotonic() + 5
        while time.monotonic() < end and not (hub.ch and aio.ch):
            time.sleep(0.01)
        time.sleep(0.3)
        hcap.sink.on_pointer(0, 384, -5, 0)            # off the laptop's left edge
        end = time.monotonic() + 5
        while time.monotonic() < end and not any(c[0] == "move_abs" for c in ainj.calls):
            time.sleep(0.01)
        assert hub_core.cursor.screen == "aio"
        # lands on the AIO's real 1920-wide screen, not the guessed 1366
        assert hub_core.cursor.x == 1915
        assert ("move_abs", 1915, hub_core.cursor.y) in ainj.calls
    finally:
        hub.stop(); aio.stop()


# ================================ the password never crosses the wire
def wiretap():
    """Record every frame either end puts on the wire, the way a listener
    sitting on the LAN would see it. Returns (frames, restore)."""
    import link.protocol as proto
    real = proto.encode
    seen = []

    def spy(ev):
        seen.append(ev)
        return real(ev)

    proto.encode = spy
    return seen, lambda: setattr(proto, "encode", real)


def test_the_password_is_never_sent(pair):
    """A passive listener on the LAN must learn nothing they can reuse. The old
    handshake put the PIN in hello in the clear."""
    seen, restore = wiretap()
    try:
        pair.start().connected()
        time.sleep(0.3)
    finally:
        restore()
    blob = json.dumps(seen)
    assert "5813" not in blob, "the password appeared on the wire"
    assert any(m.get("t") == "auth" for m in seen), "nobody was challenged"
    assert any(m.get("t") == "hello" and m.get("proof") for m in seen)
    assert any(m.get("t") == "welcome" and m.get("proof") for m in seen)


def test_both_ends_verify_each_other(pair):
    pair.start().connected()
    assert pair.hub.trusted_peer == "aio"
    assert pair.aio.trusted_peer == "laptop"


def test_a_wrong_password_is_refused():
    """And refused by NAME, so the log says which machine and why."""
    p = Pair(pin="5813")
    p.aio.pin = "0000"
    try:
        p.start()
        time.sleep(1.0)
        assert p.aio.ch is None
        assert p.hub.trusted_peer is None
        assert p.aio_core.suppress_mouse() is False    # and nothing gets frozen
    finally:
        p.stop()


def test_a_peer_that_cannot_prove_itself_is_not_allowed_to_drive():
    """Mutual. A machine must never accept injected keystrokes from something
    that has not proved it knows the password - that is how a stranger on the
    LAN would type into it."""
    p = Pair(pin="5813")
    p.hub.pin = "0000"          # the LISTENER is the impostor this time
    try:
        p.start()
        time.sleep(1.0)
        assert p.aio.trusted_peer is None
        assert p.aio_core.suppress_mouse() is False
    finally:
        p.stop()


def test_trust_is_forgotten_when_the_link_drops(pair):
    pair.start().connected()
    assert pair.aio.trusted_peer == "laptop"
    pair.aio.ch.close()
    pair.wait(lambda: pair.aio.trusted_peer is None, timeout=6,
              what="trust to be dropped with the connection")


def test_repairing_while_waiting_is_not_reported_as_a_crash():
    """reconfigure() closes the listening socket to break accept() out of its
    wait, and the OSError that follows is us. Logging it as "link loop stopped"
    made every ordinary re-pair look like a failure in the log - the one place
    someone goes to find out what really broke. Seen on the real machine while
    walking the pairing dialog: WinError 10038 logged right under "device
    forgotten".

    It has to be done from a hub sitting in accept() with nobody connected,
    which is where a machine waiting to be paired spends all its time. A hub
    with a live connection takes a different path and never raised.
    """
    lines = []
    port = free_port()
    lay = simple("laptop", (1366, 768), "aio", (1920, 1080), "left")
    core = NodeCore("laptop", lay, {}, is_hub=True)
    n = Node(core, FakeCapture(), FakeInjector(), port=port)
    n._log = lines.append
    threading.Thread(target=n.run, daemon=True).start()
    try:
        end = time.monotonic() + 5
        while time.monotonic() < end and not any("listening" in x for x in lines):
            time.sleep(0.01)
        assert any("listening" in x for x in lines), lines

        n.reconfigure(hub=False, peer_addr="127.0.0.1")
        end = time.monotonic() + 5
        while time.monotonic() < end and not any("reconfigured" in x for x in lines):
            time.sleep(0.01)
        assert any("reconfigured" in x for x in lines), lines
        time.sleep(0.5)                   # let the loop come back round
        assert not [x for x in lines if "link loop stopped" in x], lines
        assert n.core.is_hub is False and n.peer_addr == "127.0.0.1"
    finally:
        n.stop()


def test_a_connection_reset_during_the_handshake_is_an_ordinary_retry():
    """A hub that restarts, or closes its listening socket just as a member
    reaches it, resets the connection mid-handshake. That escaped the dialling
    loop: logged as "link loop stopped" and retried at once, with no backoff.
    Found by CI on Windows, where the race above happened to land this way."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    resets = []

    def reset_everyone():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            # SO_LINGER on, 0 s: close with a reset, not a goodbye. (Two
            # unsigned shorts on Windows, two ints elsewhere.)
            c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                         struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0))
            c.close()
            resets.append(1)
    threading.Thread(target=reset_everyone, daemon=True).start()

    lines = []
    lay = simple("aio", (1920, 1080), "laptop", (1366, 768), "right")
    n = Node(NodeCore("aio", lay, {}, is_hub=False), FakeCapture(), FakeInjector(),
             port=port, peer_addr="127.0.0.1")
    n._log = lines.append
    threading.Thread(target=n.run, daemon=True).start()
    try:
        end = time.monotonic() + 8
        while time.monotonic() < end and n.backoff.failures < 2:
            time.sleep(0.02)
        assert n.backoff.failures >= 2, lines
        assert not [x for x in lines if "link loop stopped" in x], lines
        assert any("lost during the handshake" in x for x in lines), lines
        assert any("retrying in" in x for x in lines), "it waits between attempts"
    finally:
        n.stop()
        srv.close()


def test_both_sides_say_the_password_was_checked():
    """The dialling side used to log only "connected", so its log read the same
    whether the password had been verified or not. The Ubuntu box, which dials,
    had to ask whether silence meant success. It should not have to ask."""
    p = Pair(pin="amber-cedar-rowan-42")
    hub_lines, aio_lines = [], []
    p.hub._log, p.aio._log = hub_lines.append, aio_lines.append
    try:
        p.start().connected()
        p.wait(lambda: any("verified both ways" in x for x in aio_lines),
               what="the dialling side to confirm the password")
        assert any("verified both ways" in x for x in hub_lines), hub_lines
        assert not [x for x in hub_lines + aio_lines if "WARNING" in x]
    finally:
        p.stop()


def test_no_password_is_called_out_on_both_sides():
    p = Pair(pin="")
    hub_lines, aio_lines = [], []
    p.hub._log, p.aio._log = hub_lines.append, aio_lines.append
    try:
        p.start().connected()
        p.wait(lambda: any("WARNING: no password" in x for x in aio_lines),
               what="the dialling side to warn")
        assert any("WARNING: no password" in x for x in hub_lines), hub_lines
    finally:
        p.stop()


def test_a_restarted_hub_is_not_ignored_by_a_peer_that_kept_running():
    """The cause of seven self-rescues on the Ubuntu box.

    A hub process counts epochs from 1. The dialler kept its own epoch across
    reconnects, so when the laptop restarted - every rebuild - and the AIO kept
    running, the AIO sat at epoch 23 while the new hub sent 2, 3, 4. Every grant
    and every input message failed the staleness check. The cursor pushed onto
    the AIO did not move; grabbing the AIO's own mouse sent claims the hub
    granted and the AIO threw away, until the self-rescue gave up. It fixed
    itself only once the hub's epoch happened to climb past 23.

    Setting the peer's epoch before it connects is exactly the state it was in:
    remembering a hub that no longer exists.
    """
    p = Pair()
    p.aio_core.baton.epoch = 23
    try:
        p.start().connected()
        p.wait(lambda: p.aio_core.baton.holder == "laptop",
               what="the peer to adopt the restarted hub's grant")
        assert p.aio_core.epoch == p.hub_core.epoch

        # input from the hub is acted on, not discarded as stale...
        p.hub_cap.sink.on_pointer(0, 384, -5, 0)
        p.wait(lambda: any(c[0] == "move_abs" for c in p.aio_inj.calls),
               what="the AIO pointer to move")

        # ...and the peer's own mouse can take control
        p.aio_cap.sink.on_pointer(500, 500, 20, 0)
        p.wait(lambda: p.aio_core.holds(), what="the AIO to take the baton")
    finally:
        p.stop()


def test_the_hub_can_take_control_back_after_its_own_failsafe(pair):
    """Seen live, 04:27 on the laptop:

        baton=nobody epoch=9 cursor=laptop suppress(mouse=0,kbd=0)
        asked for control repeatedly and did not get it - taking this machine back
        asked for control repeatedly and did not get it - taking this machine back

    The failsafe (both Ctrls, or Release in the window) made the hub forget the
    baton - but the hub's ARBITER still recorded the hub as holder. So the hub's
    next claim was "already yours" and granted nothing, the retries went out to
    the peer, which cannot grant anything, and the hub - the one machine that
    decides who holds the baton - self-rescued, twice, and could no longer
    drive the other screen at all.
    """
    pair.start().connected()
    lines = []
    pair.hub._log = lines.append
    pair.hub.on_failsafe()
    assert pair.hub_core.suppress_mouse() is False      # the failsafe did its job

    # ...and the laptop's own mouse can then drive again, straight away
    pair.hub_cap.sink.on_pointer(600, 400, 20, 0)
    pair.wait(lambda: pair.hub_core.holds(), what="the hub to hold the baton again")
    assert pair.hub_core.baton.holder == pair.hub_core.arbiter.holder
    time.sleep(2.0)                                   # a full retry cycle and more
    assert not [x for x in lines if "did not get it" in x], lines

    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)       # and it can cross
    pair.wait(lambda: any(c[0] == "move_abs" for c in pair.aio_inj.calls),
              what="the AIO pointer to move")


def test_a_failsafe_on_the_hub_takes_control_from_the_peer(pair):
    """The person at the hub asked for their machine back. If the peer was
    driving, that has to mean the hub drives now - with the arbiter and both
    machines agreeing - not a hub that believes nobody holds anything."""
    pair.start().connected()
    pair.aio_cap.sink.on_pointer(500, 500, 20, 0)
    pair.wait(lambda: pair.aio_core.holds(), what="the AIO to take the baton")

    pair.hub.on_failsafe()
    pair.wait(lambda: pair.hub_core.holds() and pair.aio_core.baton.holder == "laptop",
              what="both sides to agree the hub holds it")
    assert pair.hub_core.arbiter.holder == "laptop"
    assert pair.hub_core.suppress_mouse() is False
    assert pair.hub_core.suppress_keyboard() is False


def test_the_round_trip_is_measured_finer_than_windows_clock_tick(pair):
    """Windows' monotonic clock ticks every 15.6 ms: a round trip of a few
    milliseconds read 0 (shown as no reading) or 16 - and "16 ms" on Home made
    a quick link look slow. Over loopback it is well under a tick."""
    pair.start().connected()
    seen = []
    pair.wait(lambda: (seen.append(pair.hub.rtt_ms) or
                       len([r for r in seen if r is not None]) >= 5),
              timeout=8, what="five round trips")
    got = [r for r in seen if r is not None]
    # The old clock gave whole milliseconds only - over loopback almost always
    # exactly 0. A fine one gives a fraction, and never 0.
    assert all(r > 0 for r in got), got
    assert any(r != int(r) for r in got), f"whole milliseconds only: {got}"


def test_an_image_crosses_without_holding_up_the_keys(pair):
    """An image is hundreds of chunks on the link the keys use. Sent at once
    they filled its queue - and a full queue drops its oldest frame - then the
    network's buffer, with the keys behind them. Paced, a key pressed in the
    middle of the transfer arrives at once, and so does the whole image."""
    import os
    png = b"\x89PNG\r\n\x1a\n" + os.urandom(300_000)   # ~100 chunks
    pair.start().connected()
    pair.hub.CLIP_RATE = 400_000                 # about a second for this one
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)            # onto the AIO
    pair.wait(lambda: pair.hub_core.cursor.screen == "aio", what="crossing")
    pair.hub_board.copy(("image/png", png))                # copied on the laptop
    time.sleep(0.6)                                        # under way
    assert pair.aio_board.text != ("image/png", png), "still arriving"
    pressed = time.monotonic()
    pair.hub_cap.sink.on_key(30, 1)                        # an "a", meanwhile
    pair.hub_cap.sink.on_key(30, 0)
    pair.wait(lambda: ("key", 30, 1) in pair.aio_inj.calls, what="the key")
    assert time.monotonic() - pressed < 0.5, "the key waited behind the image"
    pair.wait(lambda: pair.aio_board.text == ("image/png", png), timeout=15,
              what="the whole image")
