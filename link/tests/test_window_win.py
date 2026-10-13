"""Windows' half of a moved window (window_win.py), against a real window.

The window is a plain Windows one this test starts itself
(win32_subject.py), in a subprocess of its own, with Windows' own message
loop - which is what every real program is underneath. Nothing of the
person's is ever touched, and if a test fails half-way, killing that
subprocess takes its window with it, hidden or not.

A toolkit window would not do: Tk ignores posted clicks even when it is on
the screen, so it cannot tell a broken click from a Tk quirk. Measured
against real programs instead (TESTLOG 2026-10-13): Edge takes posted
clicks, the wheel and typing, shown or hidden, without the focus ever
moving.

Skipped off Windows, and where there is no desktop to put a window on.
"""
import json
import pathlib
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

if sys.platform == "win32":
    from link import keymap, picture, winmove
    from link.window_win import (GWL_EXSTYLE, WS_EX_LAYERED, Window, movable,
                                 restore_everything, user32, why_not)

SUBJECT = pathlib.Path(__file__).with_name("win32_subject.py")
TITLE = "NL window under test"


class Subject:
    """A window of our own, and the handle to it."""

    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(SUBJECT), TITLE],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            creationflags=0x08000000)              # no console window flashing up
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("the window never started")
        # A plain number: window_win takes either, and the tests then do too.
        self.hwnd = int(json.loads(line)["hwnd"])
        time.sleep(0.6)                            # let it draw itself once

    def tell(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        time.sleep(0.3)

    def saw(self):
        """Everything that has arrived since the last time we asked."""
        self.proc.stdin.write("report\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def close(self):
        self.proc.kill()
        self.proc.wait(10)


@pytest.fixture
def subject():
    try:
        s = Subject()
    except Exception as e:
        pytest.skip(f"could not put a window on the screen: {e}")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def rescue(tmp_path):
    return winmove.Rescue(tmp_path / "moved.json")


@pytest.fixture
def hidden(subject, rescue):
    """The window, hidden - and put back however the test ends."""
    win = Window(subject.hwnd, rescue=rescue)
    assert win.hide() is True
    subject.saw()                                  # forget the hiding itself
    try:
        yield win
    finally:
        win.show()


def key_of(ch):
    return keymap.E[ch.upper()]


# -------------------------------------------------------------- what it sees
def test_a_window_of_ours_is_found_and_can_be_moved(subject):
    assert why_not(subject.hwnd) == ""
    assert Window(subject.hwnd).title() == TITLE
    assert any(t == TITLE for _h, t, _c in movable())


def test_its_size_is_the_whole_window_frame_and_all(subject):
    w, h = Window(subject.hwnd).size()
    assert w >= 320 and h >= 240, "the frame is included"
    assert w < 400 and h < 340, "but not wildly so"


def test_a_window_that_has_gone_is_not_movable(subject):
    hwnd = subject.hwnd
    subject.close()
    time.sleep(1.0)
    assert "gone" in why_not(hwnd)
    assert Window(hwnd).alive() is False
    assert Window(hwnd).capture() is None


def test_nishro_links_own_windows_are_refused(subject):
    assert "Nishro Link's own" in why_not(subject.hwnd, own_pid=subject.proc.pid)


def test_a_minimized_window_is_refused_because_it_stops_drawing(subject):
    user32.ShowWindow(subject.hwnd, 6)             # SW_MINIMIZE
    time.sleep(0.8)
    try:
        assert "minimized" in why_not(subject.hwnd)
    finally:
        user32.ShowWindow(subject.hwnd, 9)         # SW_RESTORE


# ----------------------------------------------------------------- capturing
def test_it_captures_a_picture_of_the_right_size(subject):
    win = Window(subject.hwnd)
    w, h = win.size()
    raw = win.capture()
    assert raw is not None and len(raw) == w * h * 4


def test_the_capture_follows_what_the_window_shows(subject):
    """What makes a moved window live rather than a photograph."""
    win = Window(subject.hwnd)
    before = win.capture()
    subject.tell("colour 2060c0")
    time.sleep(0.5)
    after = win.capture()
    assert before != after, "the capture did not follow the window"
    w, h = win.size()
    assert winmove.changed(before, after, w, h, was=(w, h)), \
        "and the change must come out as rectangles to send"


def test_a_captured_picture_encodes_as_a_png_of_that_size(subject):
    """The whole path home takes: capture -> what goes on the wire."""
    win = Window(subject.hwnd)
    w, h = win.size()
    codec, data = picture.encode(win.capture(), w, h)
    assert codec == "png" and picture.size(data) == (w, h)


# ------------------------------------------------------ hiding, and coming back
def test_hiding_takes_it_off_the_screen_but_it_keeps_drawing(subject, rescue):
    """The property the whole feature rests on - measured before anything was
    built on it. Moved off-screen or minimized, a window stops drawing; made
    layered and click-through, it does not."""
    win = Window(subject.hwnd, rescue=rescue)
    assert win.hide() is True
    try:
        assert str(int(subject.hwnd)) in rescue.pending(), \
            "the undo must be written down BEFORE it is hidden"
        a = win.capture()
        subject.tell("colour 20c060")
        time.sleep(0.5)
        b = win.capture()
        assert a and b and a != b, "a hidden window stopped drawing"
    finally:
        win.show()
    assert rescue.pending() == {}, "and is forgotten once it is back"


def test_the_window_looks_as_it_did_after_it_comes_home(subject, rescue):
    before = user32.GetWindowLongW(subject.hwnd, GWL_EXSTYLE)
    win = Window(subject.hwnd, rescue=rescue)
    win.hide()
    assert user32.GetWindowLongW(subject.hwnd, GWL_EXSTYLE) != before
    win.show()
    assert user32.GetWindowLongW(subject.hwnd, GWL_EXSTYLE) == before


def test_a_window_left_hidden_is_put_back_at_the_next_start(subject, rescue):
    """R6: Nishro Link dies between hiding and showing, so nothing is left
    running that knows about the window - except what was written down."""
    win = Window(subject.hwnd, rescue=rescue)
    win.hide()
    del win                                        # as if the process had gone
    assert rescue.pending(), "something must be left to find"
    assert restore_everything(rescue) == 1
    assert rescue.pending() == {}
    assert not user32.GetWindowLongW(subject.hwnd, GWL_EXSTYLE) & WS_EX_LAYERED


def test_a_record_for_a_window_that_has_gone_is_simply_dropped(subject, rescue):
    win = Window(subject.hwnd, rescue=rescue)
    win.hide()
    subject.close()
    time.sleep(1.0)
    assert restore_everything(rescue) == 0
    assert rescue.pending() == {}


def test_a_handle_that_now_belongs_to_another_window_is_left_alone(subject, rescue):
    """Windows reuses handles, so the record keeps the title too: a handle
    that has become some other window must not be meddled with."""
    rescue.remember(int(subject.hwnd), {
        "exstyle": 0, "alpha": None, "flags": None,
        "title": "a window that was something else entirely"})
    said = []
    assert restore_everything(rescue, log=said.append) == 0
    assert any("leaving it alone" in m for m in said)


# ------------------------------------------------------------------- driving
def test_typing_reaches_a_hidden_window_without_taking_the_focus(subject, hidden):
    """Home is a computer the person is still using: their focus must not
    move because something was typed on the other screen."""
    was_in_front = user32.GetForegroundWindow()
    for ch in "hey":
        hidden.key(key_of(ch), True)
        hidden.key(key_of(ch), False)
    time.sleep(0.4)
    got = subject.saw()
    downs = [e for e in got if e[0] == "key-down"]
    assert len(downs) == 3, f"three keys expected, got {got}"
    assert user32.GetForegroundWindow() == was_in_front, "the focus moved"


def test_a_click_reaches_the_place_it_was_aimed_at(subject, hidden):
    """A click posted to the top-level window of a program with child
    controls reaches none of them, so the deepest child at the point is
    found first - and the coordinates must survive that."""
    w, h = hidden.size()
    hidden.move(w // 2, h // 2)
    hidden.button("left", True)
    hidden.button("left", False)
    time.sleep(0.4)
    got = subject.saw()
    kinds = [e[0] for e in got]
    assert "left-down" in kinds and "left-up" in kinds, f"got {got}"
    down = next(e for e in got if e[0] == "left-down")
    move = next(e for e in got if e[0] == "move")
    assert down[1:] == move[1:], "the click landed somewhere else than the pointer"
    assert 0 < down[1] < w and 0 < down[2] < h, "and inside the window"


@pytest.mark.parametrize("name", ["left", "right", "middle"])
def test_every_button_arrives_as_itself(subject, hidden, name):
    hidden.move(40, 40)
    hidden.button(name, True)
    hidden.button(name, False)
    time.sleep(0.4)
    kinds = [e[0] for e in subject.saw()]
    assert f"{name}-down" in kinds and f"{name}-up" in kinds


def test_the_wheel_arrives_with_its_direction(subject, hidden):
    hidden.move(40, 40)
    hidden.wheel(0, -2)
    hidden.wheel(0, 1)
    time.sleep(0.4)
    wheels = [e for e in subject.saw() if e[0] == "wheel"]
    assert len(wheels) == 2, f"got {wheels}"
    assert wheels[0][1] < 0 < wheels[1][1], "the directions came out the wrong way"


def test_nothing_is_left_held_down_when_it_comes_home(subject, rescue):
    """A key or a button still held when the window comes back is something
    the person cannot undo, so showing it lets go of everything first."""
    win = Window(subject.hwnd, rescue=rescue)
    win.hide()
    win.move(40, 40)
    win.button("left", True)                       # and never let go
    win.key(key_of("a"), True)
    subject.saw()
    win.show()
    time.sleep(0.4)
    kinds = [e[0] for e in subject.saw()]
    assert "left-up" in kinds, "the button was left held down"
    assert "key-up" in kinds, "the key was left held down"
    assert win._held == 0 and win._down == set()


def test_resizing_resizes_the_real_window(subject, rescue):
    win = Window(subject.hwnd, rescue=rescue)
    before = win.size()
    win.resize(before[0] + 60, before[1] + 40)
    time.sleep(0.6)
    after = win.size()
    assert after == (before[0] + 60, before[1] + 40)
    assert len(win.capture()) == after[0] * after[1] * 4


def test_closing_asks_the_program_to_close_its_window(subject):
    """The frame's close button closes the program's window, as the same
    button at home would - the program is asked, not killed, so it can still
    put up "save changes?"."""
    win = Window(subject.hwnd)
    assert win.alive()
    win.close()
    # Not asked for a report afterwards: this window obeys the close and
    # exits, so the pipe to it is gone - that it closed at all is the proof
    # the message arrived and was acted on, rather than killed from outside.
    for _ in range(40):
        if not win.alive():
            break
        time.sleep(0.1)
    assert not win.alive(), "the window did not close"
