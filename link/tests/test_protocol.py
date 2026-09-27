"""Wire protocol v2: framing, versioning, epochs. See DESIGN.md section 6."""
import json
import socket
import threading

import pytest

from link import protocol as p


# ---------------------------------------------------------------- encoding
def test_round_trip():
    ev = p.pos("aio", 0.5, 0.25, epoch=8)
    assert p.decode(p.encode(ev).rstrip(b"\n")) == ev


def test_frames_end_in_exactly_one_newline():
    raw = p.encode(p.ping(1))
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1


def test_encoding_is_compact():
    """No spaces: this runs hundreds of times a second."""
    assert b", " not in p.encode(p.pos("s", 0.1, 0.2, epoch=1))


def test_unicode_survives():
    ev = p.clipdata(1, "text", 0, 1, "সালাম — naïve   \U0001F600")
    assert p.decode(p.encode(ev).rstrip(b"\n"))["v"] == ev["v"]


def test_an_oversized_outbound_frame_is_refused():
    with pytest.raises(p.ProtocolError):
        p.encode(p.clipdata(1, "text", 0, 1, "x" * (p.MAX_LINE + 1)))


@pytest.mark.parametrize("ch", [" ", "\U0001F600", '"', "\\", "\x00", "স"])
def test_a_clipboard_chunk_always_fits_a_frame(ch):
    """CLIP_CHUNK is in characters, and JSON escaping inflates them: 6 bytes for
    a BMP character, 12 for an astral one, which is a single Python character but
    a surrogate pair on the wire. Sizing the chunk by len() of the raw string
    would blow the frame cap the first time anyone copied an emoji."""
    worst = ch * p.CLIP_CHUNK
    assert len(p.encode(p.clipdata(9, "text", 0, 1, worst))) <= p.MAX_LINE


# ------------------------------------------------------------- negotiation
def test_matching_versions_pass():
    assert p.check_version(p.hello("aio", [])) == p.VERSION


@pytest.mark.parametrize("bad", [1, 2, 3, 4, 5, 6, 7, 9, None, "8"])
def test_a_version_mismatch_is_a_clean_error(bad):
    """Not a puzzling parse failure three messages later."""
    with pytest.raises(p.ProtocolError) as e:
        p.check_version({"t": "hello", "v": bad})
    assert "version" in str(e.value)


def test_hello_carries_what_a_peer_needs_to_place_us():
    ev = p.hello("aio", [{"name": "aio", "w": 1920, "h": 1080}],
                 policy={"may_drive": True}, pin="6120")
    assert ev["node"] == "aio"
    assert ev["screens"][0]["w"] == 1920
    assert ev["policy"]["may_drive"] is True


def test_hello_omits_what_it_was_not_given():
    """An absent key is cleaner on the wire than a null, and easier to test."""
    ev = p.hello("aio", [])
    for k in ("policy", "pin", "resume"):
        assert k not in ev


def test_hello_carries_a_resume_token():
    assert p.hello("aio", [], resume="deadbeef")["resume"] == "deadbeef"


def test_welcome_carries_the_whole_starting_state():
    ev = p.welcome("laptop", 7, "laptop", {"screens": []}, "aio", 960, 540,
                   resumed=True, session="abc")
    assert (ev["epoch"], ev["holder"], ev["resumed"]) == (7, "laptop", True)
    assert (ev["screen"], ev["x"], ev["y"]) == ("aio", 960, 540)
    assert ev["session"] == "abc"


# -------------------------------------------------------------- the epoch
@pytest.mark.parametrize("make", [
    lambda e: p.pos("s", 0.1, 0.2, epoch=e),
    lambda e: p.button("left", True, epoch=e),
    lambda e: p.wheel(0, -1, epoch=e),
    lambda e: p.key(29, True, epoch=e),
])
def test_every_input_message_can_carry_an_epoch(make):
    assert make(8)["e"] == 8


def test_input_from_a_previous_holder_is_stale():
    """What makes a handover atomic: events still in flight when the baton moved
    cannot land afterwards."""
    assert p.is_stale(p.key(29, True, epoch=7), epoch=8) is True
    assert p.is_stale(p.key(29, True, epoch=8), epoch=8) is False


def test_control_messages_are_never_stale():
    """They carry no epoch - they are how the epoch changes in the first place."""
    for ev in (p.claim("aio"), p.baton("aio", 9, "aio"), p.ping(1),
               p.release("aio"), p.hello("aio", [])):
        assert p.is_stale(ev, epoch=8) is False


def test_an_untagged_input_message_is_not_stale():
    """Legacy v1 events carry no epoch and must pass until node.py lands."""
    assert p.is_stale(p.key(29, True), epoch=8) is False


# ------------------------------------------------------------------ shapes
def test_position_is_normalised_and_rounded():
    ev = p.pos("aio", 1 / 3, 2 / 3, epoch=1)
    assert ev["x"] == 0.33333 and ev["y"] == 0.66667


def test_keys_travel_as_evdev_codes():
    assert p.key(29, True)["c"] == 29          # KEY_LEFTCTRL, not a Windows VK


def test_baton_carries_the_authoritative_held_set():
    ev = p.baton("aio", 9, "aio", 960, 540, held=[29, 42],
                 toggles={"caps": 0, "num": 1})
    assert ev["held"] == [29, 42]
    assert ev["toggles"]["num"] == 1


def test_baton_held_is_always_a_list_of_ints():
    """It gets JSON'd - a set or a tuple of numpy ints would not survive."""
    ev = p.baton("aio", 1, "aio", held=(29, 42))
    assert ev["held"] == [29, 42]
    json.dumps(ev)


def test_clipmeta_says_where_it_came_from():
    """origin + seq stop a change echoing back to the machine that made it."""
    ev = p.clipmeta("aio", 3, ["text"], 1204)
    assert (ev["origin"], ev["seq"], ev["bytes"]) == ("aio", 3, 1204)


def test_hot_path_keys_are_short_and_control_keys_are_readable():
    assert set(p.pos("s", 0, 0, epoch=1)) == {"t", "s", "x", "y", "e"}
    assert "screen" in p.baton("aio", 1, "aio")


# ------------------------------------------------------------- LineChannel
def socketpair():
    """socket.socketpair() works on Windows in 3.5+, over loopback TCP."""
    a, b = socket.socketpair()
    return a, b


def test_send_and_receive_over_a_real_socket():
    a, b = socketpair()
    ca, cb = p.LineChannel(a), p.LineChannel(b)
    try:
        ca.send(p.ping(42))
        assert cb.recv() == p.ping(42)
    finally:
        ca.close(), cb.close()


def test_several_frames_in_one_packet_are_split():
    a, b = socketpair()
    cb = p.LineChannel(b)
    try:
        a.sendall(p.encode(p.ping(1)) + p.encode(p.ping(2)) + p.encode(p.ping(3)))
        assert [cb.recv()["i"] for _ in range(3)] == [1, 2, 3]
    finally:
        a.close(), cb.close()


def test_a_frame_split_across_packets_is_reassembled():
    a, b = socketpair()
    cb = p.LineChannel(b)
    try:
        raw = p.encode(p.pos("aio", 0.5, 0.5, epoch=1))
        a.sendall(raw[:7])
        a.sendall(raw[7:])
        assert cb.recv()["s"] == "aio"
    finally:
        a.close(), cb.close()


def test_recv_returns_none_when_the_peer_hangs_up():
    a, b = socketpair()
    cb = p.LineChannel(b)
    try:
        a.close()
        assert cb.recv() is None
    finally:
        cb.close()


def test_a_peer_that_never_sends_a_newline_cannot_exhaust_memory():
    """Without the cap, _buf grows until the process dies - a free denial of
    service against the machine that owns the mouse."""
    a, b = socketpair()
    cb = p.LineChannel(b)
    try:
        def flood():
            try:
                for _ in range(64):
                    a.sendall(b"x" * 4096)     # 256 KiB, not a newline in sight
            except OSError:
                pass
        t = threading.Thread(target=flood, daemon=True)
        t.start()
        with pytest.raises(p.ProtocolError) as e:
            for _ in range(200):
                cb.recv()
        assert "frame end" in str(e.value)
    finally:
        a.close(), cb.close()


def test_undecodable_frames_raise_rather_than_crash_the_reader():
    a, b = socketpair()
    cb = p.LineChannel(b)
    try:
        a.sendall(b"{not json at all\n")
        with pytest.raises(p.ProtocolError):
            cb.recv()
    finally:
        a.close(), cb.close()


def test_send_never_blocks_even_when_the_queue_is_full():
    """P1. The caller may be a WH_MOUSE_LL hook, and Windows freezes ALL mouse
    input for as long as that callback runs."""
    a, b = socketpair()
    ca = p.LineChannel(a, queue_size=4)
    try:
        for _ in range(5000):
            ca.send(p.pos("aio", 0.5, 0.5, epoch=1))   # must not raise or hang
        assert ca.dropped > 0                          # and it tells us it shed
    finally:
        ca.close(), b.close()


def test_a_full_queue_keeps_the_NEWEST_frames():
    """Positions are absolute, so the latest is the only accurate one. Dropping
    incoming events instead would turn the queue into a latency buffer: the
    cursor would arrive late and go on arriving late."""
    a, b = socketpair()
    ca = p.LineChannel(a, queue_size=4)
    try:
        for i in range(200):
            ca.send(p.pos("aio", i / 1000.0, 0.5, epoch=1))
        held = []
        while not ca._q.empty():
            held.append(ca._q.get_nowait())
        # whatever survived must come from the END of the stream, not the start
        assert held, "queue should not be empty"
        assert all(h["x"] > 0.1 for h in held), [h["x"] for h in held]
        assert ca.dropped > 0
    finally:
        ca.close(), b.close()


def test_queue_depth_bounds_staleness_not_lag():
    a, b = socketpair()
    ca = p.LineChannel(a, queue_size=8)
    try:
        for i in range(5000):
            ca.send(p.pos("aio", 0.5, 0.5, epoch=1))
        assert ca._q.qsize() <= 8
    finally:
        ca.close(), b.close()


def test_dropped_starts_at_zero():
    a, b = socketpair()
    ca = p.LineChannel(a)
    try:
        assert ca.dropped == 0
    finally:
        ca.close(), b.close()
