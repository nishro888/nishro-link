"""Baton: who drives, and who is suppressed. See DESIGN.md section 3."""
import pytest

from link.baton import Arbiter, BatonState, ClaimDetector, Grant


class FakeClock:
    """Milliseconds, under our control - so a 1s watchdog is tested instantly."""

    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, ms):
        self.t += float(ms)
        return self.t


# ============================================================ ClaimDetector
def test_a_knock_does_not_steal_control():
    """A desk bump is a few pixels. It must not take the baton."""
    d = ClaimDetector(threshold=8, clock=FakeClock())
    assert d.motion(3, 0) is False
    assert d.motion(-2, 1) is False           # 6 so far


def test_reaching_for_the_mouse_takes_it_at_once():
    d = ClaimDetector(threshold=8, clock=FakeClock())
    d.motion(3, 0)
    assert d.motion(6, 0) is True             # crosses 8


def test_motion_accumulates_diagonally():
    d = ClaimDetector(threshold=8, clock=FakeClock())
    assert d.motion(4, 4) is True             # manhattan, so 4+4 counts


def test_drift_spread_over_time_never_accumulates_into_a_claim():
    """The window is what separates a slow drift from a deliberate grab."""
    clk = FakeClock()
    d = ClaimDetector(threshold=8, window_ms=300, clock=clk)
    for _ in range(20):
        assert d.motion(3, 0) is False
        clk.advance(301)                      # each nudge lands in a fresh window


def test_a_claim_resets_the_accumulator():
    clk = FakeClock()
    d = ClaimDetector(threshold=8, clock=clk)
    assert d.motion(9, 0) is True
    assert d.motion(3, 0) is False            # starts again from zero


def test_a_click_is_always_deliberate():
    d = ClaimDetector(clock=FakeClock())
    assert d.button(True) is True
    assert d.button(False) is False           # release never claims


def test_click_policy_ignores_motion():
    d = ClaimDetector(policy="click", clock=FakeClock())
    assert d.motion(500, 500) is False
    assert d.button(True) is True


def test_hotkey_policy_ignores_everything_else():
    d = ClaimDetector(policy="hotkey", clock=FakeClock())
    assert d.motion(500, 500) is False
    assert d.button(True) is False
    assert d.hotkey() is True


def test_unknown_policy_rejected():
    with pytest.raises(ValueError):
        ClaimDetector(policy="telepathy")


# =================================================================== Arbiter
def test_the_first_holder_starts_at_epoch_one():
    a = Arbiter("laptop", "laptop", clock=FakeClock())
    assert (a.holder, a.epoch) == ("laptop", 1)


def test_a_claim_moves_the_baton_and_bumps_the_epoch():
    clk = FakeClock()
    a = Arbiter("laptop", "laptop", clock=clk)
    clk.advance(1000)
    g = a.claim("aio", "aio", 960, 540)
    assert (g.holder, g.epoch) == ("aio", 2)
    assert (g.screen, g.x, g.y) == ("aio", 960, 540)   # jumps to the claimer
    assert a.holder == "aio"


def test_claiming_what_you_already_hold_is_a_no_op():
    """No epoch churn and nothing to broadcast."""
    clk = FakeClock()
    a = Arbiter("laptop", "laptop", clock=clk)
    clk.advance(1000)
    assert a.claim("laptop", "laptop", 0, 0) is None
    assert a.epoch == 1


def test_a_node_that_may_not_drive_is_refused():
    clk = FakeClock()
    a = Arbiter("laptop", "laptop", clock=clk)
    clk.advance(1000)
    assert a.claim("kiosk", "kiosk", 0, 0, may_drive=False) is None
    assert a.holder == "laptop"


def test_two_mice_jostled_at_once_cannot_ping_pong_the_baton():
    """Anti-thrash: a real hand moving between two mice takes far longer than
    min_hold_ms, so this only ever suppresses a fight."""
    clk = FakeClock()
    a = Arbiter("laptop", "laptop", min_hold_ms=200, clock=clk)
    clk.advance(1000)
    assert a.claim("aio", "aio", 0, 0) is not None      # first one wins
    clk.advance(50)
    assert a.claim("laptop", "laptop", 0, 0) is None    # too soon
    clk.advance(200)
    assert a.claim("laptop", "laptop", 0, 0) is not None


def test_the_very_first_claim_is_never_blocked_by_anti_thrash():
    """Seeding the guard with the start time would refuse every claim for the
    first min_hold_ms of process life - so a node starting up next to a peer you
    are already using could not take the baton."""
    a = Arbiter("laptop", "laptop", min_hold_ms=200, clock=FakeClock())
    assert a.claim("aio", "aio", 0, 0) is not None


def test_simultaneous_claims_produce_one_winner_and_distinct_epochs():
    """Serial arbitration is what makes split-brain impossible rather than
    merely unlikely."""
    clk = FakeClock()
    a = Arbiter("laptop", "laptop", min_hold_ms=0, clock=clk)
    g1 = a.claim("aio", "aio", 0, 0)
    g2 = a.claim("laptop", "laptop", 0, 0)
    assert g1.epoch != g2.epoch
    assert a.holder == "laptop"               # last one processed holds it
    assert a.epoch == 3


def test_the_holder_reports_where_the_cursor_went():
    a = Arbiter("laptop", "laptop", clock=FakeClock())
    a.move_cursor("aio", 100, 200)
    assert (a.grant.screen, a.grant.x, a.grant.y) == ("aio", 100, 200)
    assert a.epoch == 1                       # moving the cursor is not a handover


def test_resume_does_not_arm_the_anti_thrash_guard():
    """A resume keeps the same holder, so it is not a handover. Arming the guard
    here meant every claim in the first min_hold_ms after connecting was silently
    dropped - reaching for the other machine's mouse right after it connected did
    nothing at all."""
    clk = FakeClock()
    a = Arbiter("laptop", "laptop", min_hold_ms=200, clock=clk)
    a.resume()                                 # what the hub does on every connect
    clk.advance(50)                            # peer grabs its mouse straight away
    assert a.claim("aio", "aio", 960, 540) is not None


def test_resume_re_grants_under_a_fresh_epoch():
    """So input still in flight from before the drop is discarded on arrival."""
    a = Arbiter("laptop", "laptop", clock=FakeClock())
    before = a.epoch
    g = a.resume()
    assert g.epoch == before + 1
    assert g.holder == "laptop"               # same driver, new epoch
    assert g.held == ()                       # nothing is held across a reconnect


# ================================================================ BatonState
def state(node, holder, epoch=1, clock=None):
    b = BatonState(node, ttl_ms=1000, clock=clock or FakeClock())
    b.apply(Grant(holder, epoch, "somewhere"))
    return b


def test_applying_a_grant_adopts_it():
    b = state("aio", "laptop", epoch=4)
    assert (b.holder, b.epoch) == ("laptop", 4)
    assert b.holds() is False


def test_a_stale_grant_is_ignored():
    b = state("aio", "laptop", epoch=5)
    assert b.apply(Grant("aio", 3, "aio")) is False
    assert b.holder == "laptop"


def test_only_the_current_epoch_is_accepted():
    b = state("aio", "laptop", epoch=5)
    assert b.accepts(5) is True
    assert b.accepts(4) is False
    assert b.accepts(6) is False              # cannot arrive before its own grant


def test_losing_the_link_forgets_the_baton_but_keeps_the_epoch():
    """So late arrivals from the dead link stay stale even after a resume."""
    b = state("aio", "laptop", epoch=5)
    b.lost()
    assert b.holder is None
    assert b.expired() is True
    assert b.accepts(5) is True               # epoch survives


# ---- the suppression matrix ----
@pytest.mark.parametrize("holds,cursor_remote,mouse,keyboard", [
    # driving, cursor at home: an ordinary computer
    (True,  False, False, False),
    # driving a far screen: park the local pointer and forward everything
    (True,  True,  True,  True),
    # NOT driving but hosting the cursor: the mouse can only claim, and the
    # keyboard types locally - this is "drive with one mouse, type on the other"
    (False, False, True,  False),
    # idle: everything forwards
    (False, True,  True,  True),
])
def test_suppression_matrix(holds, cursor_remote, mouse, keyboard):
    b = state("aio", "aio" if holds else "laptop")
    assert b.suppress_mouse(cursor_remote) is mouse
    assert b.suppress_keyboard(cursor_remote) is keyboard


# ---- P2: the property that stops you reaching for the power button ----
def test_a_lost_baton_never_leaves_both_machines_suppressed():
    """P2. In the peer model BOTH machines suppress, so a lost baton - crash,
    dead Wi-Fi, hung process - could kill both mice at once and leave no way out
    but the power button. Suppression therefore requires a LIVE baton."""
    clk = FakeClock()
    laptop = BatonState("laptop", ttl_ms=1000, clock=clk)
    aio = BatonState("aio", ttl_ms=1000, clock=clk)
    g = Grant("laptop", 7, "aio", 960, 540)   # laptop drives, cursor on the AIO
    laptop.apply(g)
    aio.apply(g)

    assert laptop.suppress_mouse(cursor_remote=True) is True
    assert aio.suppress_mouse(cursor_remote=False) is True   # both dead

    clk.advance(1001)                          # ...and the link goes silent

    assert laptop.suppress_mouse(cursor_remote=True) is False
    assert aio.suppress_mouse(cursor_remote=False) is False
    assert laptop.suppress_keyboard(cursor_remote=True) is False


def test_the_watchdog_is_fed_by_traffic():
    clk = FakeClock()
    b = state("aio", "laptop", clock=clk)
    for _ in range(10):
        clk.advance(900)
        assert b.expired() is False
        b.touch()
    clk.advance(1001)
    assert b.expired() is True


def test_a_node_that_never_heard_a_grant_is_not_suppressed():
    """Starting up next to a machine that is switched off must not lock the mouse."""
    b = BatonState("aio", ttl_ms=1000, clock=FakeClock())
    assert b.expired() is True
    assert b.suppress_mouse(cursor_remote=True) is False


def test_keepalive_traffic_does_not_revive_a_baton_nobody_holds():
    """Found in the Ubuntu box's log, the line right after a self-rescue:

        asked for control repeatedly and did not get it - taking this machine back
        baton=nobody epoch=23 cursor=aio suppress(mouse=1,kbd=0)

    lost() cleared the watchdog so suppression dropped - and then the next
    keep-alive ping fed the watchdog again. A fresh watchdog with no holder read
    as "someone else holds it", so the mouse was suppressed on behalf of NOBODY,
    within 400ms of the rescue that was supposed to hand it back.
    """
    clk = FakeClock()
    b = state("aio", "laptop", clock=clk)
    assert b.suppress_mouse(cursor_remote=False) is True     # laptop drives: fine
    b.lost()
    assert b.suppress_mouse(cursor_remote=False) is False    # rescue: released
    b.touch()                                                # ...then a ping lands
    assert b.suppress_mouse(cursor_remote=False) is False, \
        "a ping re-suppressed the mouse with no holder"
    assert b.suppress_keyboard(cursor_remote=True) is False


def test_a_new_connection_starts_a_new_epoch_space():
    """A restarted hub counts from 1 again. The dialler used to keep its epoch
    across reconnects, so after the laptop restarted every grant it sent looked
    stale - see test_node_live for what that did to a real session."""
    b = state("aio", "laptop", epoch=23)
    assert b.apply(Grant("laptop", 2, "laptop")) is False    # the old behaviour
    b.restart()
    assert b.apply(Grant("laptop", 2, "laptop")) is True
    assert (b.holder, b.epoch) == ("laptop", 2)
