"""Clipboard sharing: lazy, chunked, loop-free. See DESIGN.md section 6."""
import pytest

from link import protocol
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
