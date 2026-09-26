"""Reconnect: backoff, the flap guard, and resume. See DESIGN.md section 7."""
import pytest

from link.reconnect import Backoff, SessionStore


class FakeClock:
    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, ms):
        self.t += float(ms)
        return self.t


def backoff(clock=None, rng=None, **kw):
    return Backoff(clock=clock or FakeClock(), rng=rng or (lambda: 0.5), **kw)


# ================================================================== backoff
def test_the_first_retry_is_immediate():
    """Most drops are a blip - a Wi-Fi hiccup, a sleep/wake."""
    assert backoff().next_delay_ms() == 0.0


def test_the_delay_grows_and_then_stops_growing():
    b = backoff()
    seen = []
    for _ in range(12):
        seen.append(b.next_delay_ms())
        b.on_failure()
    assert seen[0] == 0.0
    assert seen == sorted(seen)               # never goes backwards
    assert seen[-1] <= 15000.0                # capped
    assert seen[-1] == seen[-2]               # and flat once capped


def test_the_documented_sequence():
    """0, .19, .38, .75, 1.5, 3, 6, 11, 11-15 seconds, at mid-jitter."""
    b = backoff(rng=lambda: 0.5)
    got = []
    for _ in range(9):
        got.append(round(b.next_delay_ms() / 1000.0, 2))
        b.on_failure()
    assert got == [0.0, 0.19, 0.38, 0.75, 1.5, 3.0, 6.0, 11.25, 11.25]


def test_jitter_stays_inside_half_and_whole():
    """Equal jitter: never less than half the interval, never more than all of it."""
    lo = backoff(rng=lambda: 0.0)
    hi = backoff(rng=lambda: 1.0)
    for _ in range(6):
        lo.on_failure()
        hi.on_failure()
    assert lo.next_delay_ms() * 2 == pytest.approx(hi.next_delay_ms())


def test_jitter_actually_varies():
    """Without it, two nodes dropped by the same event retry in lockstep forever."""
    vals = iter([0.1, 0.9, 0.3, 0.7])
    b = backoff(rng=lambda: next(vals))
    b.on_failure()
    assert len({b.next_delay_ms() for _ in range(4)}) > 1


def test_huge_failure_counts_do_not_build_bignums():
    b = backoff()
    b._failures = 10_000
    assert b.next_delay_ms() <= 15000.0


# ---- the flap guard ----
def test_a_healthy_session_earns_a_fast_retry():
    clk = FakeClock()
    b = backoff(clock=clk)
    for _ in range(5):
        b.on_failure()
    assert b.failures == 5

    b.on_connected()
    clk.advance(5001)                         # healthy for longer than stable_after
    b.on_disconnected()
    assert b.failures == 0
    assert b.next_delay_ms() == 0.0


def test_a_flapping_link_keeps_backing_off():
    """Connect, die 200ms later, repeat. Resetting on CONNECT would retry this at
    full speed forever - which is why the counter resets on health, not on connect."""
    clk = FakeClock()
    b = backoff(clock=clk)
    for _ in range(5):
        b.on_connected()
        clk.advance(200)
        b.on_disconnected()
    assert b.failures == 5
    assert b.next_delay_ms() > 1000.0


def test_stable_reports_health_not_mere_connection():
    clk = FakeClock()
    b = backoff(clock=clk)
    b.on_connected()
    assert b.stable() is False
    clk.advance(5001)
    assert b.stable() is True


def test_a_failed_attempt_clears_any_connected_mark():
    clk = FakeClock()
    b = backoff(clock=clk)
    b.on_connected()
    b.on_failure()
    assert b.stable() is False


# ---- escalation ----
def test_it_sticks_to_the_configured_address_at_first():
    b = backoff(rediscover_after=6)
    for _ in range(5):
        assert b.target() == "address"
        b.on_failure()


def test_it_starts_alternating_with_discovery():
    """A peer whose DHCP lease moved is unreachable at the old address no matter
    how long you wait - more delay cannot fix the wrong destination."""
    b = backoff(rediscover_after=6)
    for _ in range(6):
        b.on_failure()
    targets = []
    for _ in range(4):
        targets.append(b.target())
        b.on_failure()
    assert "discovery" in targets
    assert "address" in targets               # still tries the known one too


def test_reset_clears_everything():
    b = backoff()
    for _ in range(9):
        b.on_failure()
    b.reset()
    assert b.failures == 0
    assert b.target() == "address"


# ============================================================ SessionStore
def test_a_session_can_be_resumed_within_the_grace_window():
    clk = FakeClock()
    s = SessionStore(grace_ms=60000, clock=clk)
    sid = s.open("aio")
    s.close(sid)
    clk.advance(59000)
    assert s.can_resume(sid) is True


def test_a_session_expires_after_the_grace_window():
    clk = FakeClock()
    s = SessionStore(grace_ms=60000, clock=clk)
    sid = s.open("aio")
    s.close(sid)
    clk.advance(60001)
    assert s.can_resume(sid) is False


def test_an_unknown_session_is_never_resumable():
    s = SessionStore(clock=FakeClock())
    assert s.can_resume("beef" * 8) is False


def test_a_session_belongs_to_one_node():
    """A resume token from one machine must not resurrect another's session."""
    s = SessionStore(clock=FakeClock())
    sid = s.open("aio")
    assert s.can_resume(sid, node="aio") is True
    assert s.can_resume(sid, node="laptop") is False


def test_touching_keeps_a_session_alive():
    clk = FakeClock()
    s = SessionStore(grace_ms=1000, clock=clk)
    sid = s.open("aio")
    for _ in range(10):
        clk.advance(900)
        s.touch(sid)
    assert s.can_resume(sid) is True


def test_sessions_are_unique():
    s = SessionStore(clock=FakeClock())
    assert len({s.open("aio") for _ in range(50)}) == 50


def test_sweep_drops_only_the_expired():
    clk = FakeClock()
    s = SessionStore(grace_ms=1000, clock=clk)
    old = s.open("aio")
    clk.advance(1001)
    fresh = s.open("laptop")
    assert s.sweep() == 1
    assert len(s) == 1
    assert s.can_resume(fresh) is True
    assert s.can_resume(old) is False


def test_forget_is_immediate():
    s = SessionStore(clock=FakeClock())
    sid = s.open("aio")
    s.forget(sid)
    assert s.can_resume(sid) is False
