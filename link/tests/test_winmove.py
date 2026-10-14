"""The window channel's transport (winmove.BinChannel): sealed, length-first
binary frames, as the moved-window feature will carry pictures and input on.

The full feature is designed in docs/moving-windows.md; this is stage 2's
foundation - no real window yet, just the pipe and that it is safe."""
import socket
import struct
import time
import threading

import pytest

from link import secure, winmove
from link.winmove import BinChannel, ChannelError


def _keys():
    """A sealer/opener pair for each direction, as node.py derives after the
    handshake (secure.session), here straight from a shared secret."""
    a_priv, a_pub = secure.keypair()
    b_priv, b_pub = secure.keypair()
    secret_a = secure.shared(a_priv, b_pub)
    secret_b = secure.shared(b_priv, a_pub)
    a_tx, a_rx = secure.session(secret_a, "pw", "n1", "n2", "hub")
    b_tx, b_rx = secure.session(secret_b, "pw", "n1", "n2", "dialer")
    return (a_tx, a_rx), (b_tx, b_rx)


def _pair(rest_a=b"", rest_b=b""):
    """Two ends of a window channel over a real socket pair. Each end seals
    with its own sealer and opens with its own opener; secure.session already
    derives the hub's sealer and the dialer's opener from the same half-key,
    and vice versa, so hub-seal/away-open and away-seal/hub-open line up."""
    (a_tx, a_rx), (b_tx, b_rx) = _keys()
    s1, s2 = socket.socketpair()
    hub = BinChannel(s1, a_tx, a_rx, rest_a)
    away = BinChannel(s2, b_tx, b_rx, rest_b)
    return hub, away


def test_a_frame_goes_there_and_back_sealed():
    hub, away = _pair()
    try:
        hub.send(b"a picture's bytes")
        assert away.recv() == b"a picture's bytes"
        away.send(b"the frame's reply")
        assert hub.recv() == b"the frame's reply"
    finally:
        hub.close()
        away.close()


def test_many_frames_keep_their_order_and_boundaries():
    hub, away = _pair()
    try:
        payloads = [bytes([i % 256]) * (i * 7 + 1) for i in range(50)]
        for p in payloads:
            hub.send(p)
        assert [away.recv() for _ in payloads] == payloads
    finally:
        hub.close()
        away.close()


def test_an_empty_frame_is_a_frame():
    """A header-only frame (no payload) must round-trip: the sessions use
    bare acknowledgements."""
    hub, away = _pair()
    try:
        hub.send(b"")
        assert away.recv() == b""
    finally:
        hub.close()
        away.close()


def test_a_big_frame_crosses_whole():
    """A 2 MB frame spans many recv() reads: the length prefix must reassemble
    it exactly, with nothing of the next frame swallowed. Sent from a thread -
    2 MB is far more than a socket's send buffer, so a single thread would
    block in sendall with nothing draining the other end (a real property of
    the channel: the sessions always send and receive on different threads)."""
    hub, away = _pair()
    big = bytes(range(256)) * (8 * 1024)            # 2 MB
    sender = threading.Thread(target=lambda: (hub.send(big), hub.send(b"after it")),
                              daemon=True)
    sender.start()
    try:
        assert away.recv() == big
        assert away.recv() == b"after it"
    finally:
        sender.join(5)
        hub.close()
        away.close()


def test_bytes_read_during_the_handshake_are_not_lost():
    """node.py's LineChannel may read past the handshake's last line into the
    first binary frames; it hands those bytes over, and recv() must use them."""
    (a_tx, a_rx), (b_tx, b_rx) = _keys()
    # a seals two frames up front; we feed them to b as `rest`, nothing on the wire.
    f1 = a_tx.seal_bytes(b"first")
    f2 = a_tx.seal_bytes(b"second")
    rest = struct.pack(">I", len(f1)) + f1 + struct.pack(">I", len(f2)) + f2
    s1, s2 = socket.socketpair()
    away = BinChannel(s2, b_tx, b_rx, rest)
    try:
        assert away.recv() == b"first"
        assert away.recv() == b"second"
    finally:
        s1.close()
        away.close()


def test_a_frame_over_the_cap_is_refused_before_it_is_read():
    """A length header claiming more than MAX_FRAME must be rejected on sight,
    not believed and read - a bad peer could otherwise ask us for gigabytes."""
    hub, away = _pair()
    try:
        hub.sock.sendall(struct.pack(">I", winmove.MAX_FRAME + 1) + b"\x00\x00")
        with pytest.raises(ChannelError, match="over the"):
            away.recv()
    finally:
        hub.close()
        away.close()


def test_a_frame_too_short_to_hold_a_tag_is_refused():
    """A sealed frame is at least a 16-byte tag; a shorter one cannot be
    genuine and must be refused, not handed to the cipher (which open_bytes
    guards against - the binary path's own check, not base64's)."""
    hub, away = _pair()
    try:
        hub.sock.sendall(struct.pack(">I", 4) + b"\x00\x00\x00\x00")
        with pytest.raises(ChannelError, match="too short"):
            away.recv()
    finally:
        hub.close()
        away.close()


def test_a_tampered_frame_is_caught_and_ends_the_channel():
    """Flip a byte of a sealed frame: the seal must reject it (altered or
    replayed), as it does on the link."""
    (a_tx, a_rx), (b_tx, b_rx) = _keys()
    s1, s2 = socket.socketpair()
    away = BinChannel(s2, b_tx, b_rx)
    try:
        sealed = bytearray(a_tx.seal_bytes(b"a picture"))
        sealed[-1] ^= 0x01
        s1.sendall(struct.pack(">I", len(sealed)) + bytes(sealed))
        with pytest.raises(ChannelError):
            away.recv()
    finally:
        s1.close()
        away.close()


def test_a_frame_out_of_order_is_caught():
    """The seal numbers frames; reading them in the wrong order fails, so a
    reordered or dropped frame cannot pass (as on the link, TCP keeps order,
    but the guarantee is the seal's, not TCP's)."""
    (a_tx, a_rx), (b_tx, b_rx) = _keys()
    s1, s2 = socket.socketpair()
    away = BinChannel(s2, b_tx, b_rx)
    try:
        a_tx.seal_bytes(b"one")        # frame 0: never sent, only advances the seal's count
        second = a_tx.seal_bytes(b"two")   # frame 1
        # send frame 1 first: the opener expects frame 0 and must reject it
        s1.sendall(struct.pack(">I", len(second)) + second)
        with pytest.raises(ChannelError):
            away.recv()
    finally:
        s1.close()
        away.close()


def test_recv_raises_when_the_peer_hangs_up():
    hub, away = _pair()
    hub.close()
    with pytest.raises(ChannelError, match="closed|dropped"):
        away.recv()
    away.close()


def test_send_after_the_peer_is_gone_raises():
    hub, away = _pair()
    away.close()
    hub.sock.close()
    with pytest.raises(ChannelError):
        hub.send(b"into the void")


def test_a_truncated_length_header_at_a_clean_close_is_a_hangup():
    """The peer closes having sent fewer than 4 bytes of a length: recv() must
    report a hang-up, not block or misread."""
    hub, away = _pair()
    hub.sock.sendall(b"\x00\x00")       # two bytes of a four-byte length, then close
    hub.sock.close()
    with pytest.raises(ChannelError, match="closed|dropped"):
        away.recv()
    away.close()

# ------------------------------------------------- messages: header + payload
def test_a_message_keeps_its_header_and_its_payload_apart():
    """Picture bytes travel as they are, beside the header, never escaped
    into JSON - that is the whole point of the binary channel."""
    raw = bytes(range(256))
    header, payload = winmove.unpack(winmove.pack({"t": "pic", "n": 7}, raw))
    assert header == {"t": "pic", "n": 7}
    assert payload == raw


def test_a_message_with_no_payload_is_fine():
    header, payload = winmove.unpack(winmove.pack({"t": "home"}))
    assert header == {"t": "home"}
    assert payload == b""


def test_unknown_message_types_and_fields_survive_a_round_trip():
    """What lets a later 1.x add to this: an older peer must be able to read
    the frame and ignore what it does not know, not fail on it."""
    header, _ = winmove.unpack(winmove.pack({"t": "something-new", "wat": [1, 2]}))
    assert header["t"] == "something-new" and header["wat"] == [1, 2]


@pytest.mark.parametrize("frame, why", [
    (b"", "no header length"),
    (b"\x00", "no header length"),
    (b"\x00\x20" + b"{}", "only"),                 # claims 32B of header, 2 arrived
    (b"\x00\x02" + b"zz", "will not read"),        # not JSON
    (b"\x00\x02" + b"[]", "not an object"),        # JSON, but not an object
])
def test_a_malformed_message_is_refused_not_crashed_on(frame, why):
    with pytest.raises(ChannelError, match=why):
        winmove.unpack(frame)


def test_a_header_too_big_to_frame_is_refused_when_packing():
    with pytest.raises(ChannelError, match="over 65535B"):
        winmove.pack({"t": "meta", "title": "x" * 70000})


# --------------------------------------------------------- picture regions
def test_a_picture_carries_its_regions_and_their_bytes():
    rs = [(0, 0, 32, 16, "png", b"PNGBYTES"), (64, 48, 16, 16, "jpeg", b"JPG")]
    header, payload = winmove.unpack(winmove.pic(3, rs))
    assert header["t"] == "pic" and header["n"] == 3
    assert winmove.regions(header, payload) == rs


def test_a_picture_with_no_regions_is_a_picture_of_nothing():
    header, payload = winmove.unpack(winmove.pic(1, []))
    assert winmove.regions(header, payload) == []


def test_a_region_claiming_more_bytes_than_arrived_is_refused():
    """The length in the header is never believed over what was received."""
    header = {"t": "pic", "n": 1, "r": [[0, 0, 8, 8, "png", 100]]}
    with pytest.raises(ChannelError, match="longer than the bytes"):
        winmove.regions(header, b"short")


def test_payload_left_over_after_the_regions_is_refused():
    header = {"t": "pic", "n": 1, "r": [[0, 0, 8, 8, "png", 2]]}
    with pytest.raises(ChannelError, match="more than its regions"):
        winmove.regions(header, b"xxxxx")


@pytest.mark.parametrize("row, why", [
    ([0, 0, 0, 8, "png", 0], "outside any real window"),        # zero width
    ([0, 0, 8, -1, "png", 0], "outside any real window"),       # negative height
    ([-1, 0, 8, 8, "png", 0], "outside any real window"),       # negative x
    ([9000, 0, 8, 8, "png", 0], "outside any real window"),     # past MAX_SIDE
    ([0, 0, 8, 8, "gif", 0], "unknown codec"),
    ([0, 0, 8, 8], "not x, y, w, h, codec, length"),
    (["a", 0, 8, 8, "png", 0], "not numbers"),
])
def test_a_bad_region_is_refused(row, why):
    with pytest.raises(ChannelError, match=why):
        winmove.regions({"t": "pic", "r": [row]}, b"")


def test_too_many_regions_are_refused_both_ways():
    many = [(0, 0, 8, 8, "png", b"")] * (winmove.MAX_REGIONS + 1)
    with pytest.raises(ChannelError, match="over"):
        winmove.pic(1, many)
    rows = [[0, 0, 8, 8, "png", 0]] * (winmove.MAX_REGIONS + 1)
    with pytest.raises(ChannelError, match="over"):
        winmove.regions({"t": "pic", "r": rows}, b"")


def test_a_picture_in_an_unknown_codec_is_refused_when_packing():
    with pytest.raises(ChannelError, match="unknown codec"):
        winmove.pic(1, [(0, 0, 8, 8, "webp", b"")])


def test_a_picture_whose_regions_are_not_a_list_is_refused():
    with pytest.raises(ChannelError, match="no regions"):
        winmove.regions({"t": "pic", "n": 1}, b"")


# ------------------------------------------------------ what changed, in tiles
def blank(w, h, fill=0):
    return bytes([fill]) * (w * h * 4)


def paint(buf, w, x, y, colour=255):
    b = bytearray(buf)
    b[(y * w + x) * 4] = colour
    return bytes(b)


def test_nothing_changed_sends_nothing():
    a = blank(128, 128)
    assert winmove.changed(a, a, 128, 128) == []


def test_the_first_picture_is_the_whole_window():
    """No previous picture: everything must be sent."""
    assert winmove.changed(b"", blank(64, 32), 64, 32) == [(0, 0, 64, 32)]


@pytest.mark.parametrize("old_w, old_h", [(64, 64), (16, 8), (33, 32)])
def test_a_resized_window_sends_the_whole_picture(old_w, old_h):
    """A previous picture of another size cannot be compared tile by tile at
    all. Shrinking is the case that bites: the bytes that do overlap may well
    be equal, so comparing them would report nothing changed and the frame
    would keep showing the old, bigger picture for ever."""
    assert winmove.changed(blank(old_w, old_h), blank(32, 32), 32, 32) \
        == [(0, 0, 32, 32)]


def test_a_reshaped_window_of_the_same_byte_count_still_sends_everything():
    """64x16 and 32x32 are the same number of bytes, so the length cannot
    tell them apart - the size it was captured at must be passed, or the
    frame would keep showing the old shape."""
    old = blank(64, 16)
    assert winmove.changed(old, blank(32, 32), 32, 32, was=(64, 16)) \
        == [(0, 0, 32, 32)]
    # and with the size given and unchanged, the tile comparison still runs
    a = blank(32, 32)
    assert winmove.changed(a, a, 32, 32, was=(32, 32)) == []


def test_one_changed_pixel_becomes_the_square_it_sits_in():
    a = blank(128, 128)
    assert winmove.changed(a, paint(a, 128, 70, 70), 128, 128) == [(64, 64, 64, 64)]


def test_changed_squares_side_by_side_become_one_rectangle():
    a = blank(128, 128)
    b = paint(paint(a, 128, 10, 10), 128, 70, 10)      # two squares, same row
    assert winmove.changed(a, b, 128, 128) == [(0, 0, 128, 64)]


def test_changed_squares_stacked_become_one_rectangle():
    """A run of identical runs down the rows is one rectangle - which is what
    keeps a changed line of text from becoming twenty messages."""
    a = blank(128, 128)
    b = paint(paint(a, 128, 10, 10), 128, 10, 70)      # same column, two rows
    assert winmove.changed(a, b, 128, 128) == [(0, 0, 64, 128)]


def test_two_far_apart_changes_stay_two_rectangles():
    a = blank(256, 128)
    b = paint(paint(a, 256, 5, 5), 256, 200, 70)
    got = winmove.changed(a, b, 256, 128)
    assert len(got) == 2
    assert (0, 0, 64, 64) in got and (192, 64, 64, 64) in got


def test_the_rectangles_cover_every_changed_pixel_and_stay_inside_the_window():
    """The property that matters: whatever the shape of the change, every
    differing pixel is inside some rectangle, and no rectangle leaves the
    window (a region outside it would be refused at the far end)."""
    import random
    rnd = random.Random(7)
    w, h = 200, 150                                     # not a multiple of the tile
    a = blank(w, h)
    spots = [(rnd.randrange(w), rnd.randrange(h)) for _ in range(25)]
    b = a
    for x, y in spots:
        b = paint(b, w, x, y)
    rects = winmove.changed(a, b, w, h)
    for rx, ry, rw, rh in rects:
        assert rx >= 0 and ry >= 0 and rx + rw <= w and ry + rh <= h
    for x, y in spots:
        assert any(rx <= x < rx + rw and ry <= y < ry + rh
                   for rx, ry, rw, rh in rects), f"({x},{y}) in no rectangle"


def test_a_window_smaller_than_one_tile_is_one_rectangle():
    a = blank(20, 10)
    assert winmove.changed(a, paint(a, 20, 3, 3), 20, 10) == [(0, 0, 20, 10)]


def test_a_picture_shorter_than_its_size_is_an_error():
    with pytest.raises(ValueError, match="needs"):
        winmove.changed(b"", b"\x00" * 10, 64, 64)


def test_no_size_no_rectangles():
    assert winmove.changed(b"", b"", 0, 0) == []


# ----------------------------------------------------------------- the pacing
def test_home_may_send_until_it_is_two_ahead():
    p = winmove.Pacer()
    assert p.may_send()
    p.next_number()
    assert p.may_send()
    p.next_number()
    assert not p.may_send(), "two unshown pictures is the limit"


def test_an_acknowledgement_lets_the_next_picture_go():
    p = winmove.Pacer()
    a, b = p.next_number(), p.next_number()
    assert (a, b) == (1, 2) and not p.may_send()
    p.acked(1)
    assert p.may_send() and p.waiting == 1
    p.acked(2)
    assert p.waiting == 0


def test_acknowledgements_never_go_backwards_or_ahead_of_what_was_sent():
    p = winmove.Pacer()
    p.next_number(), p.next_number()
    p.acked(2)
    p.acked(1)                      # an older one, arriving late
    assert p.shown == 2
    p.acked(99)                     # never sent: a confused or hostile peer
    assert p.shown == 2
    p.acked("not a number")
    assert p.shown == 2


def test_a_pacer_can_be_told_to_allow_just_one():
    p = winmove.Pacer(ahead=1)
    p.next_number()
    assert not p.may_send()
    p.acked(1)
    assert p.may_send()


# ------------------------------------------- never lose a hidden window (R6)
def test_a_window_is_written_down_before_it_is_hidden(tmp_path):
    r = winmove.Rescue(tmp_path / "moved.json")
    assert r.pending() == {}
    r.remember(12345, {"exstyle": 262144, "alpha": 255})
    assert r.pending() == {"12345": {"exstyle": 262144, "alpha": 255}}


def test_a_window_put_back_is_forgotten(tmp_path):
    path = tmp_path / "moved.json"
    r = winmove.Rescue(path)
    r.remember(1, {"a": 1})
    r.remember(2, {"a": 2})
    r.forget(1)
    assert set(r.pending()) == {"2"}
    r.forget(2)
    assert r.pending() == {}
    assert not path.exists(), "with nothing hidden, no file should be left lying about"


def test_forgetting_what_was_never_there_changes_nothing(tmp_path):
    r = winmove.Rescue(tmp_path / "moved.json")
    r.remember(1, {"a": 1})
    r.forget(99)
    assert set(r.pending()) == {"1"}


def test_what_was_hidden_when_it_died_is_still_there_next_time(tmp_path):
    """The whole point: a crash between hiding and showing must leave the
    undo on disk for the next start to find."""
    path = tmp_path / "moved.json"
    winmove.Rescue(path).remember(7, {"exstyle": 1, "alpha": 200})
    fresh = winmove.Rescue(path)              # a new process
    assert fresh.pending() == {"7": {"exstyle": 1, "alpha": 200}}
    fresh.clear()
    assert winmove.Rescue(path).pending() == {}


def test_a_half_written_record_is_never_read(tmp_path):
    """Written whole and renamed over the old one, so a power cut cannot
    leave a file that parses as something else."""
    path = tmp_path / "moved.json"
    r = winmove.Rescue(path)
    r.remember(1, {"a": 1})
    leftovers = [p.name for p in tmp_path.iterdir()]
    assert leftovers == ["moved.json"], f"left behind: {leftovers}"


def test_a_damaged_record_does_not_stop_windows_moving_for_ever(tmp_path):
    """If the file is unreadable, say so and start again - refusing to move
    any window ever again would be worse."""
    path = tmp_path / "moved.json"
    path.write_text("{this is not json", encoding="utf-8")
    said = []
    r = winmove.Rescue(path, log=said.append)
    assert r.pending() == {}
    assert any("could not be read" in m for m in said)
    r.remember(3, {"a": 3})
    assert set(winmove.Rescue(path).pending()) == {"3"}


def test_a_record_that_is_not_an_object_is_ignored(tmp_path):
    path = tmp_path / "moved.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert winmove.Rescue(path).pending() == {}


def test_the_record_makes_its_folder_if_it_has_to(tmp_path):
    r = winmove.Rescue(tmp_path / "deeper" / "still" / "moved.json")
    r.remember(1, {"a": 1})
    assert set(r.pending()) == {"1"}


# ------------------------------- the part with a screen can reach our log
def test_a_log_line_from_the_session_is_logged_not_passed_on():
    """The part of Nishro Link that shows a window runs where its own output
    goes nowhere the service can read, so it sends lines back on the channel.
    Those must reach the log and NOT be passed to the other computer."""
    import threading
    hub, away = _pair()                      # "hub" = the network side here
    net_a, net_b = _pair()
    said = []
    threading.Thread(target=lambda: winmove.relay(net_a, away, log=said.append),
                     daemon=True).start()
    try:
        hub.send(winmove.host_log("no window could be opened here"))
        end = time.monotonic() + 8
        while time.monotonic() < end and not said:
            time.sleep(0.02)
        assert any("no window could be opened here" in m for m in said), said
        # and nothing was forwarded to the other computer
        net_b.sock.settimeout(0.5)
        with pytest.raises(ChannelError):
            net_b.recv()
    finally:
        for ch in (hub, away, net_a, net_b):
            ch.close()


def test_everything_else_is_passed_on_untouched():
    import threading
    hub, away = _pair()
    net_a, net_b = _pair()
    threading.Thread(target=lambda: winmove.relay(net_a, away, log=lambda *_: None),
                     daemon=True).start()
    try:
        hub.send(winmove.pack({"t": "ack", "n": 3}))
        got, _ = winmove.unpack(net_b.recv())
        assert got == {"t": "ack", "n": 3}
    finally:
        for ch in (hub, away, net_a, net_b):
            ch.close()


def test_a_log_frame_says_what_it_is_in_its_first_bytes():
    """Recognised without unpacking, because pictures come this way too and
    their headers are the biggest thing on the channel."""
    frame = winmove.host_log("hello")
    assert frame[2:2 + len(winmove.LOG_FRAME)] == winmove.LOG_FRAME
    assert winmove.unpack(frame)[0]["msg"] == "hello"


def test_a_very_long_log_line_is_cut_rather_than_refused():
    frame = winmove.host_log("x" * 5000)
    assert len(winmove.unpack(frame)[0]["msg"]) <= 400













def test_only_the_first_direction_to_stop_is_news():
    """Seen in the log right after a window came home perfectly:

        the window's frames stopped going in: the other end closed the
          window channel
        the window's frames stopped going out: ... [WinError 10038] An
          operation was attempted on something that is not a socket

    The first line is the truth. The second is only this direction tripping
    over the socket the first one closed on purpose, dressed up as something
    going wrong - and someone reading that log would start hunting for a
    fault that is not there.
    """
    import threading
    hub, away = _pair()
    net_a, net_b = _pair()
    said = []
    t = threading.Thread(
        target=lambda: winmove.relay(net_a, away, log=said.append),
        daemon=True)
    t.start()
    try:
        hub.close()                          # the other computer lets go
        end = time.monotonic() + 8
        while time.monotonic() < end and len(said) < 2:
            time.sleep(0.02)
        assert len(said) >= 2, f"only heard {said}"
        odd = [m for m in said if "WinError" in m or "Errno" in m
               or "OSError" in m]
        assert not odd, f"a system error was reported as news: {odd}"
        assert sum(1 for m in said if "already been closed" in m) == 1, said
    finally:
        for ch in (hub, away, net_a, net_b):
            ch.close()


def test_the_relay_keeps_the_far_sides_reason_and_stops():
    """The production path for "the other computer cannot show it".

    The service relays frames between the network and the part with a
    screen. A `cannot` frame is not passed on - the session over there would
    ignore it - it is kept, so the service can answer with the reason
    instead of with a socket that went away.
    """
    import threading
    # relay(a, b): a is the NETWORK channel, b the part with the screen.
    # So the refusal comes in on the network's far end.
    screen, screen_far = _pair()
    net, net_far = _pair()
    said, trouble = [], {}
    threading.Thread(
        target=lambda: winmove.relay(net, screen, log=said.append,
                                     trouble=trouble),
        daemon=True).start()
    try:
        net_far.send(winmove.cannot("nobody is signed in on aio"))
        end = time.monotonic() + 8
        while time.monotonic() < end and not trouble:
            time.sleep(0.02)
        assert trouble.get("why") == "nobody is signed in on aio", trouble
        assert any("cannot show the window" in m for m in said), said
        # and it was NOT passed on to the part with the screen, which would
        # have ignored it anyway
        screen_far.sock.settimeout(0.5)
        with pytest.raises(ChannelError):
            screen_far.recv()
    finally:
        for ch in (screen, screen_far, net, net_far):
            ch.close()


def test_a_cannot_frame_says_what_it_is_and_fits():
    frame = winmove.cannot("x" * 900)
    header, _ = winmove.unpack(frame)
    assert header["t"] == "cannot"
    assert len(header["why"]) == 300, "a peer must not send us an essay"
    assert frame[2:2 + len(winmove.CANNOT_FRAME)] == winmove.CANNOT_FRAME, (
        "the relay spots these without unpacking every frame")
