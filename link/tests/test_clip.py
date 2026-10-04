"""Clipboard sharing: lazy, chunked, loop-free. See DESIGN.md section 6."""
import sys

import pytest

from link import clip, protocol
from link.clip import ClipboardSync


class FakeBoard:
    """A clipboard we can drive, standing in for PowerShell / wl-paste."""

    def __init__(self, text=""):
        self.text = text
        self.seq = 1
        self.reads = 0
        self.writes = 0

    def read(self):
        self.reads += 1
        return self.text

    def write(self, text):
        self.text = text
        self.seq += 1
        self.writes += 1
        return True

    def sequence(self):
        return self.seq

    def copy(self, text):
        """The user pressed Ctrl+C."""
        self.text = text
        self.seq += 1


def sync(node="aio", board=None, native=True, **kw):
    b = board or FakeBoard()
    return ClipboardSync(node, read=b.read, write=b.write,
                         seq_fn=(b.sequence if native else lambda: None), **kw), b


def only(msgs, t):
    return [m for m in msgs if m["t"] == t]


# ------------------------------------------------------------- announcing
def test_a_local_copy_is_announced():
    s, b = sync()
    b.copy("hello")
    m = only(s.poll(), "clipmeta")
    assert m[0]["origin"] == "aio"
    assert m[0]["formats"] == ["text"]


def test_nothing_is_announced_when_nothing_changed():
    s, b = sync()
    b.copy("hello")
    s.poll()
    assert s.poll() == []
    assert s.poll() == []


def test_the_announcement_carries_the_byte_length_not_the_text():
    """Lazy: copying 20 MB must not stall a screen switch."""
    s, b = sync()
    b.copy("x" * 5000)
    m = only(s.poll(), "clipmeta")[0]
    assert m["bytes"] == 5000
    assert "v" not in m


def test_byte_length_is_utf8_not_characters():
    s, b = sync()
    b.copy("সালাম")
    assert only(s.poll(), "clipmeta")[0]["bytes"] == len("সালাম".encode("utf-8"))


def test_each_copy_gets_a_new_sequence_number():
    s, b = sync()
    seqs = []
    for text in ("one", "two", "three"):
        b.copy(text)
        seqs.append(only(s.poll(), "clipmeta")[0]["seq"])
    assert seqs == sorted(set(seqs)) and len(seqs) == 3


def test_an_empty_clipboard_is_not_announced():
    s, b = sync()
    b.copy("")
    assert s.poll() == []


# ------------------------------------------- the cheap "did it change" check
def test_the_native_counter_avoids_the_expensive_read():
    """Windows gives a sequence number for the price of a function call. Without
    using it we would spawn PowerShell twice a second, forever, on a laptop that
    cannot spare it."""
    s, b = sync()
    b.copy("hello")
    s.poll()
    before = b.reads
    for _ in range(20):
        s.poll()
    assert b.reads == before          # twenty polls, not one shell-out


def test_without_a_native_counter_it_falls_back_to_reading():
    """Linux has no equivalent, so it must still work - just less cheaply."""
    s, b = sync(native=False)
    b.copy("hello")
    assert only(s.poll(), "clipmeta") != []
    assert b.reads > 0


# ---------------------------------------------------------------- fetching
def test_an_announcement_is_answered_with_a_request():
    s, _ = sync("aio")
    out = s.on_message(protocol.clipmeta("laptop", 4, ["text"], 12))
    assert only(out, "clipget")[0]["seq"] == 4


def test_our_own_announcement_is_not_answered():
    """Any machine may originate a change, so an echo is a real possibility."""
    s, _ = sync("aio")
    assert s.on_message(protocol.clipmeta("aio", 4, ["text"], 12)) == []


def test_something_too_large_is_left_alone():
    s, _ = sync("aio", max_bytes=1000)
    assert s.on_message(protocol.clipmeta("laptop", 4, ["text"], 999_999)) == []


def test_a_request_is_answered_with_the_data():
    s, b = sync("aio")
    b.copy("hello")
    seq = only(s.poll(), "clipmeta")[0]["seq"]
    data = only(s.on_message(protocol.clipget(seq)), "clipdata")
    assert len(data) == 1
    assert data[0]["v"] == "hello"


def test_a_request_for_something_we_no_longer_hold_is_ignored():
    s, b = sync("aio")
    b.copy("hello")
    s.poll()
    assert s.on_message(protocol.clipget(999)) == []


# ---------------------------------------------------------------- chunking
def test_a_large_clipboard_is_split_into_chunks():
    s, b = sync("aio")
    big = "abcd" * protocol.CLIP_CHUNK          # 4 chunks' worth
    b.copy(big)
    seq = only(s.poll(), "clipmeta")[0]["seq"]
    data = only(s.on_message(protocol.clipget(seq)), "clipdata")
    assert len(data) == 4
    assert [d["i"] for d in data] == [0, 1, 2, 3]
    assert all(d["n"] == 4 for d in data)


def test_every_chunk_fits_a_frame():
    """The cap exists because JSON escaping can inflate a character twelvefold."""
    s, b = sync("aio")
    b.copy("\U0001F600" * (protocol.CLIP_CHUNK * 3))
    seq = only(s.poll(), "clipmeta")[0]["seq"]
    for d in s.on_message(protocol.clipget(seq)):
        assert len(protocol.encode(d)) <= protocol.MAX_LINE


def test_chunks_are_reassembled_in_order():
    s, b = sync("aio")
    parts = ["first-", "second-", "third"]
    for i, part in enumerate(parts):
        s.on_message(protocol.clipdata(7, "text", i, len(parts), part))
    assert b.text == "first-second-third"


def test_chunks_arriving_out_of_order_still_reassemble():
    s, b = sync("aio")
    parts = ["first-", "second-", "third"]
    for i in (2, 0, 1):
        s.on_message(protocol.clipdata(7, "text", i, len(parts), parts[i]))
    assert b.text == "first-second-third"


def test_nothing_is_written_until_every_chunk_has_arrived():
    s, b = sync("aio")
    s.on_message(protocol.clipdata(7, "text", 0, 3, "a"))
    s.on_message(protocol.clipdata(7, "text", 1, 3, "b"))
    assert b.writes == 0


# ------------------------------------------------- the loop that must not happen
def test_receiving_a_clipboard_does_not_bounce_it_back():
    """Writing a remote update looks exactly like a fresh local copy. If we did
    not remember what we just wrote, the two machines would announce the same
    text at each other forever."""
    s, b = sync("aio")
    s.on_message(protocol.clipdata(7, "text", 0, 1, "from the laptop"))
    assert b.text == "from the laptop"
    assert s.poll() == []
    assert s.poll() == []


def test_a_genuine_copy_after_receiving_one_is_still_announced():
    """The echo guard must not swallow real changes that follow."""
    s, b = sync("aio")
    s.on_message(protocol.clipdata(7, "text", 0, 1, "from the laptop"))
    assert s.poll() == []
    b.copy("something I typed myself")
    assert only(s.poll(), "clipmeta") != []


def test_a_full_round_trip_between_two_machines():
    laptop, lb = sync("laptop")
    aio, ab = sync("aio")

    lb.copy("copied on the laptop")
    for m in laptop.poll():                      # laptop announces
        for reply in aio.on_message(m):          # AIO asks for it
            for data in laptop.on_message(reply):   # laptop sends it
                aio.on_message(data)             # AIO writes it

    assert ab.text == "copied on the laptop"
    assert aio.poll() == []                      # and does not echo it back
    assert laptop.poll() == []


@pytest.mark.parametrize("text", [
    "plain", "সালাম বাংলা", "emoji \U0001F600 here", "tabs\tand\nnewlines",
    'quotes "and" \\backslashes\\', "   separators",
])
def test_awkward_text_survives_the_round_trip(text):
    laptop, lb = sync("laptop")
    aio, ab = sync("aio")
    lb.copy(text)
    for m in laptop.poll():
        for reply in aio.on_message(m):
            for data in laptop.on_message(reply):
                protocol.encode(data)            # must be a legal frame, too
                aio.on_message(data)
    assert ab.text == text


# ------------------------------------------- Linux: no flicker, no polling
# Reported: while linked, the Ubuntu dock's Trash icon "jumps down and up
# repeatedly". wl-paste, run every 400ms, opens a tiny window on GNOME each time.

def test_gnome_on_wayland_reads_through_xwayland_first():
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0",
           "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
    assert clip.order(env) == ["xclip", "xsel", "wl"]


def test_other_wayland_desktops_use_wl_clipboard_first():
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0",
           "XDG_CURRENT_DESKTOP": "KDE"}
    assert clip.order(env) == ["wl", "xclip", "xsel"]


def test_x11_uses_the_x11_tools_only():
    assert clip.order({"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "GNOME"}) == \
        ["xclip", "xsel"]
    assert clip.order({}) == []


def test_writing_does_not_wait_for_the_tool_that_stays_behind(monkeypatch):
    """wl-copy and xclip keep running to hold the clipboard; a captured pipe
    made every write wait out its whole timeout."""
    import subprocess
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(clip.sys, "platform", "linux")
    monkeypatch.setattr(clip, "_tools", lambda: ("xclip",))
    monkeypatch.setattr(clip.subprocess, "run", run)
    # As a user. As root - the service, or a CI container - the write goes
    # through the logged-in session instead, and a container has none.
    from link import session
    monkeypatch.setattr(session, "running_as_root", lambda: False)
    assert clip.set("hello") is True
    cmd, kw = calls[0]
    assert cmd == clip.WRITE["xclip"]
    assert kw["stdout"] is subprocess.DEVNULL and "capture_output" not in kw


def test_a_counter_makes_looking_cheap_and_its_absence_does_not():
    b = FakeBoard()
    assert ClipboardSync("a", read=b.read, write=b.write, seq_fn=b.sequence).cheap()
    assert not ClipboardSync("a", read=b.read, write=b.write,
                             seq_fn=lambda: None).cheap()


def test_without_a_counter_it_looks_only_when_the_pointer_leaves():
    from link.desk import simple
    from link.node import Node, NodeCore
    from test_node_live import FakeCapture, FakeInjector
    b = FakeBoard()
    reads = []

    def read():
        reads.append(1)
        return b.read()
    core = NodeCore("aio", simple("aio", (1920, 1080), "laptop", (1366, 768),
                                  "right"), {})
    n = Node(core, FakeCapture(), FakeInjector())
    n.clip = ClipboardSync("aio", read=read, write=b.write, seq_fn=lambda: None)
    where = {"remote": False}
    core.cursor_is_remote = lambda: where["remote"]
    for _ in range(10):
        n._clip_look()                    # the pointer is here: no look at all
    assert reads == []
    where["remote"] = True
    n._clip_look()
    assert reads == [1], "it just left: one look"
    for _ in range(10):
        n._clip_look()
    assert reads == [1], "still away: no more"
    where["remote"] = False
    n._clip_look()
    where["remote"] = True
    n._clip_look()
    assert reads == [1, 1], "left again: one more"


# ------------------------------------------------------------------ images
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40          # 10 KB, any bytes


def _pair(a_value, b_value=""):
    from link.clip import ClipboardSync
    boards = {"a": {"v": a_value, "n": 1}, "b": {"v": b_value, "n": 1}}
    made = {}
    for name in ("a", "b"):
        bd = boards[name]
        made[name] = ClipboardSync(
            name, read=lambda bd=bd: bd["v"],
            write=lambda v, bd=bd: bd.update(v=v, n=bd["n"] + 1) or True,
            seq_fn=lambda bd=bd: bd["n"])
    return made["a"], made["b"], boards


def _deliver(src, dst, msgs):
    """Play messages to the other side until nothing more comes back."""
    while msgs:
        nxt = []
        for m in msgs:
            nxt += dst.on_message(m)
        msgs, src, dst = nxt, dst, src


def test_an_image_copied_on_one_machine_pastes_on_the_other():
    """Asked for: photos, not only text."""
    a, b, boards = _pair(("image/png", PNG))
    meta = a.poll()
    assert meta[0]["formats"] == ["image/png"] and meta[0]["bytes"] == len(PNG)
    _deliver(a, b, meta)
    assert boards["b"]["v"] == ("image/png", PNG)


def test_a_received_image_is_not_announced_back():
    a, b, boards = _pair(("image/png", PNG))
    _deliver(a, b, a.poll())
    assert b.poll() == [], "it came from a: no echo"


def test_text_wins_when_both_are_offered():
    from link.clip import ClipboardSync
    b = ClipboardSync("b", read=lambda: "", write=lambda v: True)
    got = b.on_message({"t": "clipmeta", "origin": "a", "seq": 3,
                        "formats": ["text", "image/png"], "bytes": 10})
    assert got[0]["format"] == "text"


def test_an_older_machine_asking_for_text_gets_no_image():
    """1.0 asks for text whatever is offered: it must not be sent base64 PNG."""
    a, _b, _ = _pair(("image/png", PNG))
    seq = a.poll()[0]["seq"]
    assert a.on_message({"t": "clipget", "seq": seq, "format": "text"}) == []
    assert a.on_message({"t": "clipget", "seq": seq}) == []


def test_an_image_too_big_is_not_offered():
    from link.clip import ClipboardSync
    a = ClipboardSync("a", read=lambda: ("image/png", PNG), write=lambda v: True,
                      seq_fn=lambda: 1, max_image_bytes=1000)
    assert a.poll() == []


def test_every_image_chunk_fits_a_frame():
    from link import protocol
    a, _b, _ = _pair(("image/png", PNG * 20))
    seq = a.poll()[0]["seq"]
    for m in a.on_message({"t": "clipget", "seq": seq, "format": "image/png"}):
        assert len(m["v"]) <= protocol.CLIP_CHUNK


def test_a_clipboard_held_by_another_program_is_looked_at_again():
    from link.clip import ClipboardSync
    reads = [None, "hello"]
    a = ClipboardSync("a", read=lambda: reads.pop(0), write=lambda v: True,
                      seq_fn=lambda: 7)
    assert a.poll() == []                 # busy: nothing read, but not forgotten
    assert a.poll()[0]["t"] == "clipmeta", "the same change, read the next time"


def test_images_cross_between_the_service_and_its_agent_as_a_file(tmp_path):
    from link import clip
    spool = tmp_path / "clip-out.png"
    wire = clip.to_wire(("image/png", PNG), lambda: spool)
    assert wire == {"fmt": "image/png", "file": str(spool)}
    assert clip.from_wire(wire) == ("image/png", PNG)
    assert not spool.exists(), "not left on disk once read"
    assert clip.to_wire("text", lambda: 1 / 0) == "text", "no file for text"
    assert clip.from_wire("text") == "text"


def test_linux_reads_an_image_when_no_text_is_offered(monkeypatch):
    import subprocess
    from link import clip
    monkeypatch.setattr(clip.sys, "platform", "linux")
    monkeypatch.setattr(clip, "_tools", lambda: ("xclip",))
    offered = {"targets": "TARGETS\nimage/png\n"}

    def run(cmd, **kw):
        if "TARGETS" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=offered["targets"])
        if "image/png" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=PNG)
        return subprocess.CompletedProcess(cmd, 0, stdout="some text")
    monkeypatch.setattr(clip, "_run", run)
    assert clip.get() == ("image/png", PNG)
    offered["targets"] = "TARGETS\nUTF8_STRING\nimage/png\n"
    assert clip.get() == "some text", "text wins"


def test_linux_writes_an_image_as_png(monkeypatch):
    import subprocess
    from link import clip
    monkeypatch.setattr(clip.sys, "platform", "linux")
    monkeypatch.setattr(clip, "_tools", lambda: ("xclip",))
    seen = []
    monkeypatch.setattr(clip, "_run", lambda cmd, **kw: seen.append((cmd, kw.get("input")))
                        or subprocess.CompletedProcess(cmd, 0))
    assert clip.set(("image/png", PNG)) is True
    assert seen == [(clip.WRITE_IMAGE["xclip"], PNG)]


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows clipboard")
def test_the_windows_clipboard_is_used_by_one_thread_at_a_time():
    """Several threads reading and writing at once crashed the process (heap
    corruption): opening the clipboard keeps other programs out, not other
    threads. Every operation takes one lock."""
    import threading
    import time
    from link import clip_win
    inside, worst = [0], [0]
    real = clip_win._open

    def counting(*a, **kw):
        inside[0] += 1
        worst[0] = max(worst[0], inside[0])
        time.sleep(0.005)
        inside[0] -= 1
        return False                      # never really open it: just count
    clip_win._open = counting
    try:
        ts = [threading.Thread(target=f) for f in
              [clip_win.get_text, clip_win.get_image] * 8]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        clip_win._open = real
    assert worst[0] == 1


def test_a_stuck_change_counter_does_not_keep_a_copy_back():
    """A change counter that does not move must not keep a copy back:
    leaving the computer - when a copy can next be pasted elsewhere - forces
    a real look."""
    value = {"v": "first"}
    a = ClipboardSync("a", read=lambda: value["v"], write=lambda v: True,
                      seq_fn=lambda: 0)
    assert a.poll()[0]["t"] == "clipmeta"          # at start
    value["v"] = "copied later"
    assert a.poll() == [], "the counter says nothing changed"
    out = a.poll(force=True)                       # the pointer just left
    assert out and out[0]["t"] == "clipmeta"
    assert a.poll(force=True) == [], "nothing new: nothing offered twice"


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows clipboard")
def test_an_image_survives_the_trip_through_a_dib():
    """Images are read as CF_DIB - plain memory - not a bitmap handle, which
    could not be converted when another program made it (every screenshot),
    nor read as "PNG" by the service's desk agent. The conversions both ways
    keep every pixel."""
    import io
    import struct
    from PIL import Image
    from link import clip_win
    im = Image.new("RGB", (64, 40))
    px = im.load()
    for x in range(64):
        for y in range(40):
            px[x, y] = (x * 4, y * 6, 150)
    b = io.BytesIO()
    im.save(b, "PNG")
    dib = clip_win._png_to_dib(b.getvalue())
    assert struct.unpack_from("<IiiHH", dib) == (40, 64, 40, 1, 32)
    back = Image.open(io.BytesIO(clip_win._dib_to_png(dib))).convert("RGB")
    assert back.tobytes() == im.tobytes()
    # The same pixels described with colour masks (BI_BITFIELDS), as a
    # screenshot often is: 12 more bytes before the pixels.
    head = bytearray(dib[:40])
    struct.pack_into("<I", head, 16, 3)
    masks = struct.pack("<III", 0x00FF0000, 0x0000FF00, 0x000000FF)
    back = Image.open(io.BytesIO(clip_win._dib_to_png(bytes(head) + masks + dib[40:])))
    assert back.convert("RGB").tobytes() == im.tobytes()
