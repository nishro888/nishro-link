"""Finding the pointer by shaking the mouse, and the latency work beside it.

Asked for: "sometimes the mouse is missing from sight - shaking the mouse
faster should focus it, like Windows PowerToys", and "still sometimes laggy".
The lag was measured on the two real machines: Wi-Fi power saving held one
packet in ten back 65 ms or more after a quiet moment (keep-awake, below), and
a thousand mouse reports a second were written to the network one by one
(batching, collapse).
"""
import socket
import sys
import time
import os

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from link import protocol                    # noqa: E402
from link.desk import simple                            # noqa: E402
from link.inject import locate_gnome                    # noqa: E402
from link.node import NodeCore                          # noqa: E402
from link.shake import Shake                            # noqa: E402


# ================================================================= shaking
def play(moves, rate=1000, start=0):
    """Feed (dx, dy) moves at `rate` a second; the times it fired, in ms."""
    s, fired = Shake(), []
    for i, (dx, dy) in enumerate(moves):
        t = start + i * 1000 // rate
        if s.feed(dx, dy, t):
            fired.append(t)
    return fired


def shake_x(seconds=0.7, speed=18, every_ms=70, rate=1000, first=1):
    """Back and forth along x: `speed` px per report, turning every_ms."""
    out, d = [], first
    for i in range(int(seconds * rate)):
        if i and i % every_ms == 0:
            d = -d
        out.append((d * speed, 0))
    return out


def test_a_shake_is_noticed_once():
    fired = play(shake_x())
    assert len(fired) == 1 and fired[0] < 700


def test_a_shake_up_and_down_counts_too():
    moves = [(y, x) for x, y in shake_x()]
    assert len(play(moves)) == 1


def test_a_long_fast_drag_is_not_a_shake():
    assert play([(25, 3)] * 800) == []


@pytest.mark.parametrize("turns_per_second", [1, 2.5, 4, 6])
def test_circles_are_not_a_shake_however_fast(turns_per_second):
    import math
    moves = []
    for i in range(1500):
        a = i / 1000 * 2 * math.pi * turns_per_second
        moves.append((round(20 * math.cos(a)), round(20 * math.sin(a))))
    assert play(moves) == []


def test_a_diagonal_shake_counts():
    moves = [(dx, dx) for dx, _ in shake_x(speed=14)]
    assert len(play(moves)) == 1


def test_a_zigzag_across_the_screen_is_not_a_shake():
    moves = [(14, dx) for dx, _ in shake_x(speed=14)]
    assert play(moves) == []


def test_a_slow_wiggle_is_not_a_shake():
    assert play(shake_x(speed=2, rate=125, every_ms=40)) == []


def test_one_shake_one_spotlight_then_again_after_a_while():
    moves = shake_x(seconds=1.2)
    assert len(play(moves)) == 1, "shaking on does not fire again at once"
    later = shake_x(seconds=3.5)
    assert len(play(later)) >= 2


def test_movement_before_a_rest_is_forgotten():
    s = Shake()
    for i, (dx, dy) in enumerate(shake_x(seconds=0.2)):
        s.feed(dx, dy, i)
    # a long rest, then a single ordinary move
    assert not s.feed(5, 0, 10_000) and not s.feed(5, 0, 10_030)


def test_a_mouse_at_1000_reports_a_second_costs_little():
    s = Shake()
    moves = shake_x(seconds=2)
    t0 = time.perf_counter()
    for i, (dx, dy) in enumerate(moves):
        s.feed(dx, dy, i)
    per = (time.perf_counter() - t0) / len(moves) * 1e6
    assert per < 25, f"{per:.1f} µs a report"


# ============================================================ finding it
def core_on_aio():
    lay = simple("laptop", (1366, 768), "aio", (1920, 1080), "left")
    core = NodeCore("laptop", lay, {}, is_hub=True, clock=lambda: core_on_aio.t)
    core_on_aio.t = 0
    core.online.add("aio")
    core.local_pointer("laptop", 5, 400, -10, 0)
    core.local_pointer("laptop", 0, 400, -10, 0)
    return core


core_on_aio.t = 0


def shake_core(core, remote=True):
    acts = []
    for i, (dx, dy) in enumerate(shake_x(speed=6, first=-1)):
        core_on_aio.t = 1000 + i
        if remote:
            acts.append(core.local_motion(dx, dy))
        else:
            acts.append(core.local_pointer("laptop", 600, 400, dx, dy))
    return acts


def test_shaking_while_the_pointer_is_on_another_machine_tells_that_machine():
    core = core_on_aio()
    assert core.cursor.screen == "aio"
    finds = [m for a in shake_core(core) for m in a.send if m.get("t") == "find"]
    assert len(finds) == 1
    assert finds[0]["to"] == "aio" and finds[0]["s"] == "aio"


def test_the_machine_told_shows_it_where_the_pointer_is():
    lay = simple("aio", (1920, 1080), "laptop", (1366, 768), "right")
    aio = NodeCore("aio", lay, {}, is_hub=False)
    a = aio.on_message(protocol.find("aio", 0.5, 0.25))
    assert ("spotlight", 960, 270) in a.inject


def test_a_find_for_another_machine_shows_nothing_here():
    lay = simple("aio", (1920, 1080), "laptop", (1366, 768), "right")
    aio = NodeCore("aio", lay, {}, is_hub=False)
    assert not aio.on_message(protocol.find("laptop", 0.5, 0.5)).inject


def test_shaking_on_this_machine_shows_it_here():
    core = core_on_aio()
    core.local_pointer("aio", 0, 0, 0, 0)            # ignored: not ours
    core.cursor.warp("laptop", 600, 400)
    core.local_pointer("laptop", 600, 400, 1, 0)     # the cursor is home
    spots = [i for a in shake_core(core, remote=False) for i in a.inject
             if i[0] == "spotlight"]
    assert len(spots) == 1


def test_movement_that_only_asks_for_control_does_not_count():
    lay = simple("aio", (1920, 1080), "laptop", (1366, 768), "right")
    aio = NodeCore("aio", lay, {}, is_hub=False, clock=lambda: core_on_aio.t)
    assert not aio.holds()
    finds = [m for a in shake_core(aio) for m in a.send if m.get("t") == "find"]
    assert finds == []


def test_it_can_be_turned_off():
    core = core_on_aio()
    core.find_on_shake = False
    assert not [m for a in shake_core(core) for m in a.send if m.get("t") == "find"]


def test_find_from_the_menu_needs_no_shake():
    core = core_on_aio()
    a = core.find()
    assert [m["to"] for m in a.send if m["t"] == "find"] == ["aio"]


# ================================================ the Linux ripple (GNOME)
class Keys:
    def __init__(self):
        self.pressed = []

    def key(self, code, down):
        self.pressed.append((code, down))


def fake_gsettings(value="false", code=0):
    calls = []

    def run(args, **kw):
        calls.append(args[1:])
        out = value if args[1] == "get" else ""
        return type("R", (), {"returncode": code, "stdout": out + "\n"})
    return run, calls


def test_gnome_locate_pointer_is_turned_on_for_the_moment_and_back_off():
    run, calls = fake_gsettings("false")
    keys = Keys()
    assert locate_gnome(keys, settle=0, restore_after=0, run=run)
    assert [c[0] for c in calls] == ["get", "set", "set"]
    assert calls[1][-1] == "true" and calls[2][-1] == "false"
    assert keys.pressed == [(29, True), (29, False)], "a lone Ctrl, down and up"


def test_a_setting_that_was_on_is_left_alone():
    run, calls = fake_gsettings("true")
    assert locate_gnome(Keys(), settle=0, restore_after=0, run=run)
    assert [c[0] for c in calls] == ["get"]


def test_without_gnome_nothing_is_pressed():
    run, _ = fake_gsettings(code=1)
    keys = Keys()
    assert not locate_gnome(keys, settle=0, restore_after=0, run=run)
    assert keys.pressed == []


# ============================================ the Windows spotlight's curve
@pytest.mark.skipif(sys.platform != "win32", reason="spotlight_win needs Windows")
def test_the_spotlight_shrinks_holds_and_fades():
    from link import spotlight_win as W
    first = W.frame(0.0, 2000, 90)
    mid = W.frame(W.SHRINK_S + 0.1, 2000, 90)
    fading = W.frame(W.SHRINK_S + W.HOLD_S + W.FADE_S / 2, 2000, 90)
    assert first[0] == 2000 and first[1] == 0
    assert mid == (90, W.ALPHA)
    assert fading[0] == 90 and 0 < fading[1] < W.ALPHA
    assert W.frame(W.SHRINK_S + W.HOLD_S + W.FADE_S + 0.01, 2000, 90) is None
    radii = [W.frame(t / 100, 2000, 90)[0] for t in range(int(W.SHRINK_S * 100))]
    assert radii == sorted(radii, reverse=True), "it only ever closes in"


def test_the_service_passes_a_spotlight_to_the_agent():
    from link.agent import AgentInjector
    sent = []
    hub = type("Hub", (), {"send": lambda self, m: sent.append(m)})()
    AgentInjector(hub).spotlight(10, 20)
    assert sent == [{"t": "inj", "k": "spotlight", "a": [10, 20]}]


# =================================================== positions, collapsed
def pos(x, s="aio", e=3, to="aio"):
    m = protocol.pos(s, x, 0.5, epoch=e)
    m["to"] = to
    return m


def test_a_position_replaced_before_it_went_is_not_sent():
    out = protocol.collapse([pos(0.1), pos(0.2), pos(0.3)])
    assert [m["x"] for m in out] == [0.3]


def test_nothing_but_a_stale_position_is_ever_removed_or_moved():
    b = protocol.button("left", True, epoch=3)
    batch = [pos(0.1), pos(0.2), b, pos(0.3), pos(0.4, s="laptop"),
             pos(0.5, e=4), protocol.keep_awake(), pos(0.6), pos(0.7)]
    out = protocol.collapse(batch)
    assert out == [pos(0.2), b, pos(0.3), pos(0.4, s="laptop"), pos(0.5, e=4),
                   protocol.keep_awake(), pos(0.7)]


def test_a_burst_arrives_in_order_and_ends_where_it_should():
    a, b = socket.socketpair()
    tx, rx = protocol.LineChannel(a), protocol.LineChannel(b)
    try:
        for i in range(2000):
            tx.send(pos(i / 2000))
        tx.send(protocol.button("left", True, epoch=3))
        got = []
        while True:
            m = rx.recv()
            got.append(m)
            if m["t"] == "b":
                break
        xs = [m["x"] for m in got if m["t"] == "p"]
        assert xs == sorted(xs), "never out of order"
        assert xs[-1] == round(1999 / 2000, 5), "the newest position always goes"
        # Faster than any mouse: the queue sheds its OLDEST positions when
        # full - stale anyway - and the rest are collapsed. None is lost that
        # was not superseded.
        assert tx.coalesced + tx.dropped + len(xs) == 2000
    finally:
        tx.close()
        rx.close()


# ============================================================ keep-awake
def test_a_driven_machine_keeps_its_radio_awake_and_stops_after():
    from test_node_live import Pair
    p = Pair().start().connected()
    try:
        seen = []
        orig = p.aio.ch.send
        p.aio.ch.send = lambda m: (seen.append(m.get("t")), orig(m))
        assert p.aio.driven_from() is None
        p.hub.on_pointer(5, 400, -10, 0)
        time.sleep(0.2)
        p.hub.on_pointer(0, 400, -10, 0)            # onto the AIO
        p.wait(lambda: p.aio.driven_from() == "laptop", what="aio driven")
        time.sleep(0.4)
        n = seen.count("ka")
        assert n >= 5, f"only {n} in 0.4 s"
        p.hub.on_motion(3000, 0)                    # back home
        p.wait(lambda: p.aio.driven_from() is None, what="cursor home")
        time.sleep(0.1)
        before = seen.count("ka")
        time.sleep(0.4)
        assert seen.count("ka") == before, "an idle link is left alone"
    finally:
        p.stop()


def test_the_driving_machine_keeps_its_radio_awake_while_its_mouse_rests():
    """Reported: with the AIO's mouse on the laptop's screen, the pointer was
    slow to start moving after a rest. Only the machine being driven kept its
    radio awake; the driver's dozed while its mouse was still, and the first
    movements after a rest waited for it to wake."""
    from test_node_live import Pair
    p = Pair().start().connected()
    try:
        seen = []
        link = p.hub.links["aio"]
        orig = link.ch.send
        link.ch.send = lambda m: (seen.append(m.get("t")), orig(m))
        assert not p.hub.driving_elsewhere()
        p.hub.on_pointer(5, 400, -10, 0)
        time.sleep(0.2)
        p.hub.on_pointer(0, 400, -10, 0)            # onto the AIO, then still
        p.wait(lambda: p.hub.driving_elsewhere(), what="the laptop driving the aio")
        time.sleep(0.1)
        before = seen.count("ka")
        time.sleep(0.4)                             # its mouse at rest
        n = seen.count("ka") - before
        assert n >= 5, f"only {n} in 0.4 s"
        p.hub.on_motion(3000, 0)                    # back home
        p.wait(lambda: not p.hub.driving_elsewhere(), what="cursor home")
        time.sleep(0.1)
        before = seen.count("ka")
        time.sleep(0.4)
        assert seen.count("ka") == before, "an idle link is left alone"
    finally:
        p.stop()


@pytest.mark.parametrize("px_per_s, turn_ms", [(1250, 72), (2000, 72), (3000, 48)])
def test_an_ordinary_mouse_shaken_by_hand_is_noticed(px_per_s, turn_ms):
    """A 125 Hz office mouse, shaken moderately to hard."""
    moves = shake_x(seconds=1.0, speed=px_per_s // 125, every_ms=turn_ms // 8,
                    rate=125)
    assert len(play(moves, rate=125)) == 1


def test_showing_the_pointer_is_written_in_the_log():
    from test_node_live import Pair
    p = Pair().start().connected()
    try:
        p.aio_inj.spotlight = lambda x, y: None
        p.hub.on_pointer(5, 400, -10, 0)
        time.sleep(0.2)
        p.hub.on_pointer(0, 400, -10, 0)
        p.wait(lambda: p.hub_core.cursor.screen == "aio", what="crossing")
        p.hub._act(p.hub_core.find)
        p.wait(lambda: any("asked it to show" in l for l in p.logs["hub"]),
               what="the hub's line")
        p.wait(lambda: any("showing it here" in l for l in p.logs["aio"]),
               what="the aio's line")
    finally:
        p.stop()
