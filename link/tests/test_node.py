"""NodeCore: the peer state machine. See DESIGN.md sections 2 and 3."""
import pytest

from link import protocol
from link.desk import Desk
from link.node import NodeCore


class FakeClock:
    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, ms):
        self.t += float(ms)
        return self.t


def desk(node="laptop"):
    """1366x768 laptop (hub), 1920x1080 AIO to its left."""
    lay = Desk(node=node)
    lay.add("laptop", 1366, 768, owner="laptop")
    # the AIO against the laptop's left edge, centres lined up
    lay.add("aio", 1920, 1080, owner="aio", x=-1920, y=(768 - 1080) // 2)
    return lay


def hub(clock=None, **policy):
    """The hub with the AIO connected - the shell says so on every real
    connection; these tests drive the core without one."""
    c = NodeCore("laptop", desk("laptop"), policy, is_hub=True,
                 clock=clock or FakeClock())
    c.peer_online("aio")
    return c


def peer(clock=None, **policy):
    c = NodeCore("aio", desk("aio"), policy, is_hub=False,
                 clock=clock or FakeClock())
    c.peer_online("laptop")
    return c


def sent(a, t):
    return [m for m in a.send if m["t"] == t]


def grant_to(who, epoch, screen, x=0, y=0):
    return protocol.baton(who, epoch, screen, x, y)


# ============================================================= the basics
def test_the_hub_starts_holding_its_own_baton():
    n = hub()
    assert n.holds() is True
    assert n.epoch == 1
    assert n.cursor_is_remote() is False


def test_a_node_owning_no_screen_is_refused():
    lay = Desk(node="ghost")
    lay.add("laptop", 1366, 768, owner="laptop")
    with pytest.raises(ValueError):
        NodeCore("ghost", lay, {}, is_hub=False)


def test_a_fresh_peer_suppresses_nothing():
    """It has heard no grant, so P2 says local input wins."""
    n = peer()
    assert n.suppress_mouse() is False
    assert n.suppress_keyboard() is False


# ====================================================== driving locally
def test_moving_on_our_own_screen_sends_nothing():
    n = hub()
    a = n.local_pointer("laptop", 600, 300, 5, 5)
    assert a.send == [] and a.inject == []


def test_the_os_pointer_is_the_truth_while_we_are_not_suppressing():
    """It has already applied the user's acceleration curve; fighting it would
    make the virtual cursor disagree with what they can see."""
    n = hub()
    n.local_pointer("laptop", 900, 400, 3, 0)
    assert (n.cursor.x, n.cursor.y) == (900, 400)


def test_pushing_past_the_edge_crosses_and_starts_sending():
    n = hub()
    a = n.local_pointer("laptop", 0, 384, -4, 0)   # OS has clamped us at x=0
    p = sent(a, "p")
    assert len(p) == 1
    assert p[0]["s"] == "aio"
    assert n.cursor_is_remote() is True
    assert n.suppress_mouse() is True              # park the local pointer


def test_movement_the_os_did_not_discard_never_crosses():
    """Sitting at the edge but moving INWARD must not fall off it."""
    n = hub()
    a = n.local_pointer("laptop", 0, 384, +4, 0)
    assert a.send == []
    assert n.cursor_is_remote() is False


def test_the_overshoot_is_what_carries_us_across():
    n = hub()
    n.local_pointer("laptop", 0, 384, -5, 0)
    assert n.cursor.screen == "aio"
    assert n.cursor.x == 1915                      # 5 past the edge, as a monitor


def test_positions_stream_while_the_cursor_is_away():
    n = hub()
    n.local_pointer("laptop", 0, 384, -5, 0)
    a = n.local_motion(-30, 10)                    # now pinned, so raw deltas
    assert len(sent(a, "p")) == 1
    assert sent(a, "p")[0]["e"] == n.epoch


def test_coming_home_puts_the_real_pointer_back():
    """Otherwise it resumes from wherever we parked it."""
    n = hub()
    n.local_pointer("laptop", 0, 384, -5, 0)
    a = n.local_motion(+50, 0)
    assert n.cursor_is_remote() is False
    assert ("move_abs", n.cursor.x, n.cursor.y) in a.inject
    assert n.suppress_mouse() is False


def test_buttons_and_wheel_forward_only_while_away():
    n = hub()
    assert n.local_button("left", True).send == []
    assert n.local_wheel(0, -1).send == []
    n.local_pointer("laptop", 0, 384, -5, 0)
    assert len(sent(n.local_button("left", True), "b")) == 1
    assert len(sent(n.local_wheel(0, -1), "w")) == 1


# ================================================ keyboard follows cursor
def test_typing_goes_nowhere_while_the_cursor_is_at_home():
    n = hub()
    assert n.local_key(30, True).send == []


def test_typing_follows_the_cursor_away():
    n = hub()
    n.local_pointer("laptop", 0, 384, -5, 0)
    k = sent(n.local_key(30, True), "k")
    assert k[0]["c"] == 30 and k[0]["e"] == n.epoch


def test_a_node_that_does_not_drive_still_forwards_its_keyboard():
    """The whole point of decoupling them: drive with one machine's mouse and
    type on the other machine's keyboard."""
    n = peer()
    n.on_message(grant_to("laptop", 2, "laptop"))   # the laptop drives, cursor there
    assert n.holds() is False
    assert n.cursor_is_remote() is True
    assert len(sent(n.local_key(30, True), "k")) == 1


def test_the_node_hosting_the_cursor_types_locally():
    n = peer()
    n.on_message(grant_to("laptop", 2, "aio"))      # laptop drives, cursor is HERE
    assert n.holds() is False
    assert n.suppress_keyboard() is False           # so type straight into itself
    assert n.suppress_mouse() is True               # but the mouse can only claim


# =========================================================== claiming
def test_a_knock_does_not_claim():
    n = peer()
    n.on_message(grant_to("laptop", 2, "laptop"))
    assert n.local_pointer("aio", 500, 500, 3, 0).send == []


def test_reaching_for_the_mouse_claims():
    n = peer()
    n.on_message(grant_to("laptop", 2, "laptop"))
    a = n.local_pointer("aio", 500, 500, 9, 0)
    c = sent(a, "claim")
    assert c[0]["node"] == "aio" and c[0]["reason"] == "motion"


def test_a_click_claims():
    n = peer()
    n.on_message(grant_to("laptop", 2, "laptop"))
    assert sent(n.local_button("left", True), "claim")[0]["reason"] == "click"


def test_a_node_forbidden_to_drive_never_claims():
    """may_drive:false + may_be_driven:true is a kiosk."""
    n = peer(may_drive=False)
    n.on_message(grant_to("laptop", 2, "laptop"))
    assert n.local_pointer("aio", 500, 500, 99, 0).send == []
    assert n.local_button("left", True).send == []


def test_the_hub_grants_a_claim_and_the_epoch_moves():
    n = hub()
    before = n.epoch
    a = n.on_message(protocol.claim("aio", "motion"))
    b = sent(a, "baton")
    assert b[0]["holder"] == "aio"
    assert b[0]["epoch"] == before + 1
    assert n.holds() is False


def test_the_cursor_jumps_to_the_machine_that_claimed():
    n = hub()
    a = n.on_message(protocol.claim("aio", "motion"))
    assert sent(a, "baton")[0]["screen"] == "aio"


def test_claiming_back_returns_the_cursor_to_our_remembered_home():
    """You left the laptop at a spot; taking it back should not dump you in a
    corner."""
    clk = FakeClock()
    n = hub(clock=clk)
    n.local_pointer("laptop", 800, 250, 1, 0)      # remember where we were
    clk.advance(1000)
    n.on_message(protocol.claim("aio", "motion"))
    clk.advance(1000)
    a = n.on_message(protocol.claim("laptop", "motion"))
    g = sent(a, "baton")[0]
    assert (g["screen"], g["x"], g["y"]) == ("laptop", 800, 250)


def test_the_hub_arbitrates_its_own_claim_instead_of_posting_it():
    """It used to send itself a claim over the wire, where only a peer that
    cannot answer would see it - so the hub could give the baton away but never
    take it back."""
    clk = FakeClock()
    n = hub(clock=clk)
    n.on_message(protocol.claim("aio", "motion"))      # hand it over
    assert n.holds() is False
    clk.advance(1000)                                  # past the anti-thrash guard

    a = n.local_pointer("laptop", 500, 300, 20, 0)     # reach for our own mouse
    assert sent(a, "claim") == []                      # not posted to anyone
    assert sent(a, "baton")[0]["holder"] == "laptop"   # granted on the spot
    assert n.holds() is True


def test_only_the_hub_arbitrates():
    n = peer()
    assert n.on_message(protocol.claim("aio", "motion")).send == []


# ============================================================ being driven
def test_a_position_for_our_screen_is_injected():
    n = peer()
    n.on_message(grant_to("laptop", 2, "aio"))
    a = n.on_message(protocol.pos("aio", 0.5, 0.5, epoch=2))
    assert a.inject == [("move_abs", 960, 540)]


def test_a_position_for_somebody_elses_screen_is_ignored():
    n = peer()
    n.on_message(grant_to("laptop", 2, "aio"))
    assert n.on_message(protocol.pos("laptop", 0.5, 0.5, epoch=2)).inject == []


def test_input_from_a_stale_epoch_is_discarded():
    """This is what makes a handover atomic - a burst still in flight when the
    baton moved must not land afterwards."""
    n = peer()
    n.on_message(grant_to("laptop", 5, "aio"))
    assert n.on_message(protocol.pos("aio", 0.9, 0.9, epoch=4)).inject == []
    assert n.on_message(protocol.key(30, True, epoch=4)).inject == []


def test_a_node_that_may_not_be_driven_injects_nothing():
    n = peer(may_be_driven=False)
    n.on_message(grant_to("laptop", 2, "aio"))
    assert n.on_message(protocol.pos("aio", 0.5, 0.5, epoch=2)).inject == []
    assert n.on_message(protocol.key(30, True, epoch=2)).inject == []


def test_keys_and_buttons_are_injected():
    n = peer()
    n.on_message(grant_to("laptop", 2, "aio"))
    assert n.on_message(protocol.key(30, True, epoch=2)).inject == [("key", 30, True)]
    assert n.on_message(protocol.button("left", True, epoch=2)).inject == \
        [("button", "left", True)]
    assert n.on_message(protocol.wheel(0, -1, epoch=2)).inject == [("wheel", 0, -1)]


def test_a_ping_is_answered():
    n = peer()
    assert n.on_message(protocol.ping(7)).send == [protocol.pong(7)]


def test_being_driven_feeds_the_watchdog():
    clk = FakeClock()
    n = peer(clock=clk)
    n.on_message(grant_to("laptop", 2, "aio"))
    for _ in range(5):
        clk.advance(1400)                     # just inside the 1500ms TTL
        n.on_message(protocol.pos("aio", 0.5, 0.5, epoch=2))
        assert n.suppress_mouse() is True
    clk.advance(1501)
    assert n.suppress_mouse() is False


# ======================================================= P3: nothing sticks
def test_a_handover_releases_everything_we_are_holding_down():
    """A key latched on a uinput device outlives the process that set it."""
    n = peer()
    n.on_message(grant_to("laptop", 2, "aio"))
    n.on_message(protocol.key(29, True, epoch=2))       # Ctrl down
    n.on_message(protocol.button("left", True, epoch=2))
    a = n.on_message(grant_to("aio", 3, "aio"))
    assert ("key", 29, False) in a.inject
    assert ("button", "left", False) in a.inject


def test_a_dead_link_releases_everything_and_un_suppresses():
    """P2 and P3 together, without asking anyone's permission."""
    n = peer()
    n.on_message(grant_to("laptop", 2, "aio"))     # laptop drives, cursor is here
    n.on_message(protocol.key(29, True, epoch=2))  # so Ctrl really does go down
    assert n.suppress_mouse() is True
    a = n.link_lost()
    assert ("key", 29, False) in a.inject
    assert n.suppress_mouse() is False


def test_the_failsafe_works_on_any_node_not_just_the_hub():
    """In the peer model every node can be the one that is stuck."""
    n = peer()
    n.on_message(grant_to("laptop", 2, "laptop"))
    a = n.local_failsafe()
    assert sent(a, "release")[0]["node"] == "aio"
    assert n.suppress_mouse() is False


def test_a_key_up_is_always_accepted():
    """Refusing one is how a modifier gets stuck down forever."""
    n = peer()
    n.on_message(grant_to("laptop", 2, "aio"))
    n.on_message(protocol.key(29, True, epoch=2))
    n.on_message(grant_to("laptop", 2, "laptop"))       # cursor leaves us
    assert n.cursor_is_remote() is True
    assert n.on_message(protocol.key(29, False, epoch=2)).inject == [("key", 29, False)]


def test_a_key_down_for_a_screen_we_no_longer_host_is_refused():
    n = peer()
    n.on_message(grant_to("laptop", 2, "laptop"))
    assert n.on_message(protocol.key(30, True, epoch=2)).inject == []


def test_a_stale_grant_changes_nothing():
    n = peer()
    n.on_message(grant_to("laptop", 5, "aio"))
    n.on_message(grant_to("aio", 3, "laptop"))
    assert n.epoch == 5
    assert n.holds() is False


# ================================================== the round trip, end to end
def test_a_full_crossing_and_return():
    """The laptop drives onto the AIO, the AIO injects, and it comes back."""
    laptop, aio = hub(), peer()
    aio.on_message(grant_to("laptop", 1, "laptop"))

    a = laptop.local_pointer("laptop", 0, 384, -5, 0)      # cross over
    assert laptop.cursor.screen == "aio"
    for m in a.send:
        aio.on_message(m)
    assert aio.cursor.screen == "aio"
    assert (aio.cursor.x, aio.cursor.y) == (laptop.cursor.x, laptop.cursor.y)

    a = laptop.local_motion(-300, 100)                     # drive around over there
    for m in a.send:
        aio.on_message(m)
    assert (aio.cursor.x, aio.cursor.y) == (laptop.cursor.x, laptop.cursor.y)

    a = laptop.local_motion(+5000, 0)                      # and push back home
    assert laptop.cursor.screen == "laptop"
    assert laptop.suppress_mouse() is False
    # Exactly one position goes out on the way home - no stream while the cursor
    # is on our own screen, but the machine we left has to be told it has gone,
    # or it keeps believing it still hosts the cursor.
    assert [m["s"] for m in sent(a, "p")] == ["laptop"]


# ========================= never leave someone unable to type
def test_a_claim_that_goes_unanswered_is_chased():
    """A claim refused by the hub's anti-thrash guard is SILENT - the claimer is
    told nothing. A real mouse papers over that by streaming events, but a lost
    claim, a wedged hub or a half-dead link leaves a machine whose mouse and
    keyboard both go nowhere."""
    clk = FakeClock()
    n = peer(clock=clk)
    n.on_message(grant_to("laptop", 2, "laptop"))
    assert sent(n.local_pointer("aio", 500, 500, 20, 0), "claim")   # asked once

    assert n.check_claim().send == []          # too soon to chase
    clk.advance(401)
    assert sent(n.check_claim(), "claim")[0]["reason"] == "retry"


def test_it_stops_waiting_and_takes_the_machine_back():
    """The whole point. Handing someone back their own keyboard is always right;
    the worst case is two machines briefly both live, which is recoverable -
    unlike a computer you cannot type on."""
    clk = FakeClock()
    n = peer(clock=clk)
    n.on_message(grant_to("laptop", 2, "laptop"))
    n.local_pointer("aio", 500, 500, 20, 0)
    assert n.suppress_mouse() is True

    a = None
    for _ in range(6):                          # nobody ever answers
        clk.advance(401)
        a = n.check_claim()
        if a.gave_up:
            break                               # and stops asking after that

    assert a.gave_up is True
    assert sent(a, "release")[0]["node"] == "aio"
    assert n.suppress_mouse() is False          # mouse is ours again
    assert n.suppress_keyboard() is False       # and so is the keyboard


def test_it_gives_up_within_a_couple_of_seconds():
    """Long enough not to fight a busy link, short enough that nobody concludes
    the machine has crashed."""
    clk = FakeClock()
    n = peer(clock=clk)
    n.on_message(grant_to("laptop", 2, "laptop"))
    n.local_pointer("aio", 500, 500, 20, 0)
    start = clk.t
    while True:
        clk.advance(401)
        if n.check_claim().gave_up:
            break
        assert clk.t - start < 5000, "took too long to rescue the user"
    assert clk.t - start <= 2500


def test_a_granted_claim_stops_the_chase():
    clk = FakeClock()
    n = peer(clock=clk)
    n.on_message(grant_to("laptop", 2, "laptop"))
    n.local_pointer("aio", 500, 500, 20, 0)
    n.on_message(grant_to("aio", 3, "aio"))     # the hub answers
    clk.advance(5000)
    a = n.check_claim()
    assert a.send == [] and a.gave_up is False


def test_nothing_is_chased_when_nothing_was_asked():
    clk = FakeClock()
    n = peer(clock=clk)
    n.on_message(grant_to("laptop", 2, "laptop"))
    clk.advance(10000)
    assert n.check_claim().send == []


def test_giving_up_also_releases_anything_held():
    """P3 still applies on this path - it is a transition like any other."""
    clk = FakeClock()
    n = peer(clock=clk)
    n.on_message(grant_to("laptop", 2, "aio"))        # laptop drives, cursor HERE
    n.on_message(protocol.key(29, True, epoch=2))     # so Ctrl really goes down
    n.local_pointer("aio", 500, 500, 20, 0)           # we reach for our own mouse
    for _ in range(6):
        clk.advance(401)
        a = n.check_claim()
        if a.gave_up:
            break
    assert ("key", 29, False) in a.inject


# ---------------------------- auto-repeat must reach the injector intact
def test_a_repeat_reaches_the_injector_as_a_repeat():
    """Flattening it into another press is the bug: on Linux that reads as a
    duplicate press, which libinput filters while restarting the compositor's
    repeat timer - so a held key typed exactly one character."""
    n = peer()
    n.on_message(grant_to("laptop", 2, "aio"))
    got = []
    for value in (protocol.KEY_DOWN, protocol.KEY_REPEAT, protocol.KEY_REPEAT,
                  protocol.KEY_UP):
        got += n.on_message(protocol.key(32, value, epoch=2)).inject
    assert got == [("key", 32, 1), ("key", 32, 2), ("key", 32, 2), ("key", 32, 0)]


def test_a_repeat_keeps_the_key_held_for_P3():
    """A repeat must not look like a release, or the key would be dropped from
    the held set and never released on a handover."""
    n = peer()
    n.on_message(grant_to("laptop", 2, "aio"))
    n.on_message(protocol.key(29, protocol.KEY_DOWN, epoch=2))
    n.on_message(protocol.key(29, protocol.KEY_REPEAT, epoch=2))
    a = n.on_message(grant_to("aio", 3, "aio"))       # handover
    assert ("key", 29, False) in a.inject             # still released


def test_a_repeat_is_refused_for_a_screen_we_do_not_host():
    n = peer()
    n.on_message(grant_to("laptop", 2, "laptop"))
    assert n.on_message(protocol.key(32, protocol.KEY_REPEAT, epoch=2)).inject == []


def test_typing_forwards_the_repeat_value_it_was_given():
    n = hub()
    n.local_pointer("laptop", 0, 384, -5, 0)          # cursor is away
    k = sent(n.local_key(32, protocol.KEY_REPEAT), "k")
    assert k[0]["d"] == protocol.KEY_REPEAT


# ------------------------------------------------ machines that are not there
def test_a_machine_that_is_not_connected_is_a_wall():
    """Seen on the laptop with linking off: pushing past the edge towards the
    AIO - arranged, but not connected - flipped the cursor between the two
    machines dozens of times a second. With linking on, the laptop's own pointer
    would have frozen while it steered a screen that was not there."""
    c = NodeCore("laptop", desk("laptop"), {}, is_hub=True, clock=FakeClock())
    a = c.local_pointer("laptop", 0, 384, -5, 0)
    assert c.cursor.screen == "laptop"
    assert not c.cursor_is_remote() and c.suppress_mouse() is False
    assert not sent(a, "p"), "nothing sent towards a machine that is not there"


def test_losing_the_link_makes_the_peer_a_wall_again():
    c = hub()
    c.local_pointer("laptop", 0, 384, -5, 0)
    assert c.cursor.screen == "aio"
    a = c.link_lost()
    # home at once, to where it left - and the real pointer put there too
    assert (c.cursor.screen, c.cursor.x, c.cursor.y) == ("laptop", 0, 384)
    assert ("move_abs", 0, 384) in a.inject
    c.baton.apply(c.arbiter.grant)                # holding control again
    c.local_pointer("laptop", 0, 384, -5, 0)
    assert c.cursor.screen == "laptop", "and cannot go back while it is gone"


def test_a_message_from_a_newer_1x_is_ignored_not_an_error():
    """The 1.x promise: a later 1.x may add message types and fields, and an
    older one ignores what it does not know. If this ever raised or acted,
    a 1.1 could not talk to a 1.0."""
    from link.desk import simple
    lay = simple("laptop", (1366, 768), "aio", (1920, 1080), "right")
    for hub in (True, False):
        c = NodeCore("laptop", lay, {}, is_hub=hub)
        for msg in ({"t": "a_feature_from_the_future", "x": 1},
                    {"t": "baton", "holder": "laptop", "epoch": 1,
                     "screen": "laptop", "x": 1, "y": 1, "held": [],
                     "a_new_optional_field": {"nested": True}}):
            a = c.on_message(dict(msg))
            if msg["t"] != "baton":
                assert (a.send, a.inject) == ([], []), msg
