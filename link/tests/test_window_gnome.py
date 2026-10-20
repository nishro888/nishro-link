"""GNOME's half of a moved window (window_gnome.py), without GNOME.

What only GNOME can answer - its dialog, PipeWire's pictures, where a
pointer event really lands - is checked on the AIO (TESTLOG). Here the
portal and the stream are fakes that behave the way the real ones were read
to, and what is tested is everything Nishro Link does with them: the same
picture for size() and capture(), the pointer in the stream's logical
coordinates, the wheel the right way up, nothing left held down and the
sharing ended on the way home - and a "no" in the dialog said in words a
person can read.
"""
import threading

import pytest

from link import window_gnome as wg


class FakePortal:
    """Acts as window_gnome.Portal does, and keeps what it was told."""

    def __init__(self, node=42, props=None, devices=wg.KEYBOARD | wg.POINTER,
                 refuse=None):
        self.node, self.devices, self.refuse = node, devices, refuse
        self.props = {"size": (800, 600)} if props is None else props
        self.closed = threading.Event()
        self.said = []

    def open(self, parent=""):
        if self.refuse:
            raise wg.Declined(self.refuse)
        return self.node, dict(self.props), self.devices

    def pipewire(self):
        return 7

    def pointer(self, node, x, y):
        self.said.append(("pointer", node, x, y))

    def button(self, code, down):
        self.said.append(("button", code, down))

    def scroll(self, axis, steps):
        self.said.append(("scroll", axis, steps))

    def key(self, code, down):
        self.said.append(("key", code, down))

    def close(self):
        self.closed.set()
        self.said.append(("close",))


class FakeStream:
    """Hands out the pictures it was given, one each time it is asked."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.ended = None
        self.stopped = False

    def newest(self, wait=0.0):
        return self.frames.pop(0) if self.frames else None

    def stop(self):
        self.stopped = True


def picture(w, h, fill=0):
    return w, h, bytes([fill]) * (w * h * 4)


def window(frames=None, log=None, **portal):
    p = FakePortal(**portal)
    s = FakeStream([picture(800, 600)] if frames is None else frames)
    return wg.Window(wg.PICK, portal=p, stream=s, log=log), p, s


# ------------------------------------------------------------- opening
def test_it_opens_with_the_first_picture():
    w, _p, _s = window()
    assert w.size() == (800, 600)
    assert len(w.capture()) == 800 * 600 * 4
    assert w.alive()


def test_a_no_in_gnomes_dialog_is_said_in_words():
    with pytest.raises(wg.Declined) as e:
        window(refuse="the window was not shared - GNOME's dialog was cancelled")
    assert "cancelled" in str(e.value)


def test_when_no_picture_comes_the_sharing_is_ended():
    """GNOME's indicator would otherwise stay in the top bar, sharing a
    window nobody is shown."""
    p, s = FakePortal(), FakeStream([])
    with pytest.raises(wg.Declined):
        wg.Window(wg.PICK, portal=p, stream=s)
    assert p.closed.is_set() and s.stopped


def test_a_portal_that_shares_only_the_picture_is_said_so():
    said = []
    window(log=said.append, devices=0)
    assert any("not the use of it" in m for m in said), said


# ------------------------------------------------------- size and picture
def test_size_and_picture_always_agree_when_the_window_is_resized():
    """HomeSession cuts the picture into squares by the size it was told:
    a picture of another size, between the two, would be cut wrong."""
    w, _p, _s = window([picture(800, 600), picture(1000, 700, fill=5)])
    assert w.size() == (800, 600)
    assert len(w.capture()) == 800 * 600 * 4, "a new size slipped in"
    assert w.size() == (1000, 700), "and the next round says the new size"
    assert len(w.capture()) == 1000 * 700 * 4


def test_a_newer_picture_of_the_same_size_is_the_one_sent():
    w, _p, _s = window([picture(800, 600), picture(800, 600, fill=9)])
    assert w.capture()[:4] == bytes([9]) * 4


def test_with_nothing_new_the_last_picture_is_sent_again():
    w, _p, _s = window([picture(800, 600, fill=3)])
    assert w.capture() == w.capture()


def test_once_the_stream_ends_there_is_no_picture():
    w, _p, s = window()
    s.ended = "the window was closed, or the sharing stopped"
    assert not w.alive() and w.capture() is None


# --------------------------------------------------------------- driving
def test_the_pointer_goes_where_it_was_aimed_at_100_percent():
    w, p, _s = window()
    w.move(100, 50)
    assert p.said[-1] == ("pointer", 42, 100.0, 50.0)


def test_the_pointer_is_placed_in_logical_coordinates_on_a_scaled_screen():
    """At 200% the picture has twice the pixels of the window's logical
    size, which is what GNOME places the pointer in."""
    w, p, _s = window(props={"size": (400, 300)})
    w.move(100, 50)
    assert p.said[-1] == ("pointer", 42, 50.0, 25.0)


def test_an_absurd_size_from_the_portal_is_not_believed():
    w, p, _s = window(props={"size": (8000, 6000)})
    w.move(100, 50)
    assert p.said[-1] == ("pointer", 42, 100.0, 50.0)


def test_buttons_go_as_evdev_codes():
    w, p, _s = window()
    w.button("left", True)
    w.button("right", False)
    w.button("middle", True)
    w.button("fourth", True)                       # not one of ours: dropped
    assert p.said == [("button", 0x110, True), ("button", 0x111, False),
                      ("button", 0x112, True)]


def test_the_wheel_is_the_right_way_up():
    """The frame's dy is positive for up, as Windows' wheel is; mutter
    scrolls DOWN for a positive step. Up must stay up."""
    w, p, _s = window()
    w.wheel(0, 1)
    w.wheel(0, -2)
    w.wheel(1, 0)
    assert p.said == [("scroll", wg.VERTICAL, -1), ("scroll", wg.VERTICAL, 2),
                      ("scroll", wg.HORIZONTAL, 1)]


def test_keys_go_as_the_evdev_codes_they_are():
    w, p, _s = window()
    w.key(30, True)
    w.key(30, False)
    assert p.said == [("key", 30, True), ("key", 30, False)]


def test_nothing_is_left_held_down_when_it_comes_home():
    """Here the keys and buttons are real ones on this desktop: a Ctrl left
    down would stay down for the person sitting at it."""
    w, p, s = window()
    w.button("left", True)
    w.key(29, True)                                # Ctrl, still down
    w.key(30, True)
    w.key(30, False)                               # A, let go already
    p.said.clear()
    w.show()
    assert sorted(x for x in p.said if x[0] != "close") == [
        ("button", 0x110, False), ("key", 29, False)]
    assert p.said[-1] == ("close",) and s.stopped


def test_the_frames_close_button_ends_the_sharing():
    """No program may close another's window on Wayland; ending the sharing
    is what closing the frame can do, and the frame closes over there."""
    w, p, _s = window()
    w.close()
    assert p.closed.is_set()
    assert not w.alive() and w.capture() is None


def test_it_is_never_hidden_and_never_resized():
    w, p, _s = window()
    assert w.hide() is False
    w.resize(10, 10)
    assert p.said == []


# ------------------------------------------------------------- the module
def test_only_a_window_chosen_in_the_dialog_can_be_asked_for():
    assert wg.why_not("12345") == ("on this desktop the window is chosen in "
                                   "its own dialog")


def test_one_entry_is_offered_since_the_dialog_chooses():
    assert wg.movable() == [(wg.PICK, "a window you choose", "")]


def test_what_the_tray_asked_for_is_kept_as_it_is():
    assert wg.ref("pick-ab12cd34") == "pick-ab12cd34"


def test_after_a_crash_there_is_nothing_to_put_back():
    assert wg.restore_everything(rescue=None) == 0


def test_padding_between_rows_is_taken_out():
    row = bytes(range(8))                          # two pixels
    padded = (row + b"\xee" * 4) * 2               # each row padded to 12
    assert wg.unpad(2, 2, padded) == (2, 2, row * 2)


def test_rows_with_no_padding_are_kept_as_they_are():
    data = bytes(range(16))
    assert wg.unpad(2, 2, data) == (2, 2, data)


def test_a_short_buffer_is_not_a_picture():
    assert wg.unpad(2, 2, bytes(10)) is None
    assert wg.unpad(0, 2, bytes(10)) is None


# ------------------------------------------------- no frame of a frame
def marked(w=64, h=8, at=(40, 7), count=None):
    """A BGRx picture with the frame's mark in it, the fourth bytes odd."""
    from link import winmove
    data = bytearray(w * h * 4)
    mark = winmove.FRAME_MARK[:count] if count else winmove.FRAME_MARK
    x0, y = at
    for i, (r, g, b) in enumerate(mark):
        o = (y * w + x0 + i) * 4
        data[o:o + 4] = bytes((b, g, r, 0x7F))
    return w, h, bytes(data)


def test_a_frame_chosen_in_gnomes_dialog_is_refused():
    """GNOME does not say which window was chosen; a frame - another
    computer's window shown here - is known by its mark, and refused, or it
    would be shown back to its own computer in a frame of a frame."""
    p, s = FakePortal(), FakeStream([marked()])
    with pytest.raises(wg.Declined) as e:
        wg.Window(wg.PICK, portal=p, stream=s)
    assert "from another computer" in str(e.value)
    assert p.closed.is_set() and s.stopped, "and the sharing is ended"


def test_the_mark_is_found_wherever_the_frame_puts_it():
    assert wg.shows_a_frame(marked(at=(0, 0)))
    assert wg.shows_a_frame(marked(at=(56, 7)))


def test_an_ordinary_window_is_not_taken_for_a_frame():
    assert not wg.shows_a_frame(picture(64, 8, fill=0x7F))
    assert not wg.shows_a_frame(marked(count=7)), "seven of the eight is not it"
