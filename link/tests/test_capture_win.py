"""Windows capture: deltas, pinning, and the stale-anchor bug. DESIGN.md P1/P6.

The hook itself needs Windows, but everything that decides anything is reachable
by calling _handle_mouse directly with a fake user32 - which is how the bug that
oscillated two real machines is pinned down here instead of on hardware.
"""
import sys
import types

import pytest

# Skipped before the import, not by a mark: the module itself needs Windows'
# ctypes (WINFUNCTYPE), so elsewhere even collecting it fails.
if sys.platform != "win32":
    pytest.skip("Windows hooks", allow_module_level=True)

from link.capture_win import (WM_LDOWN, WM_MOUSEMOVE, WM_MOUSEWHEEL,  # noqa: E402
                              WinCapture)


class FakeUser32:
    def __init__(self, w=1366, h=768):
        self.w, self.h = w, h
        self.moves = []

    def GetSystemMetrics(self, i):
        return self.w if i == 0 else self.h

    def SetCursorPos(self, x, y):
        self.moves.append((x, y))
        return True


class Sink:
    def __init__(self):
        self.pointer, self.motion, self.buttons, self.wheel = [], [], [], []
        self.failsafes = 0

    def on_pointer(self, x, y, dx, dy):
        self.pointer.append((x, y, dx, dy))

    def on_motion(self, dx, dy):
        self.motion.append((dx, dy))

    def on_button(self, name, down):
        self.buttons.append((name, down))

    def on_wheel(self, dx, dy):
        self.wheel.append((dx, dy))

    def on_key(self, code, down):
        pass

    def on_failsafe(self):
        self.failsafes += 1


def cap():
    u = FakeUser32()
    c = WinCapture(u=u)
    c.sink = Sink()
    return c, u, c.sink


def ev(x, y, data=0):
    return types.SimpleNamespace(pt=types.SimpleNamespace(x=x, y=y), mouseData=data)


def move(c, x, y):
    return c._handle_mouse(WM_MOUSEMOVE, ev(x, y))


# ---------------------------------------------------- not suppressing
def test_the_os_pointer_is_reported_with_its_delta():
    c, u, s = cap()
    move(c, 500, 400)
    move(c, 510, 395)
    assert s.pointer[-1] == (510, 395, 10, -5)


def test_nothing_is_swallowed_or_moved_while_free():
    c, u, s = cap()
    assert move(c, 500, 400) is False
    assert u.moves == []


def test_buttons_and_wheel_pass_through():
    c, u, s = cap()
    c._handle_mouse(WM_LDOWN, ev(0, 0))
    c._handle_mouse(WM_MOUSEWHEEL, ev(0, 0, data=120 << 16))
    assert s.buttons == [("left", True)]
    assert s.wheel == [(0, 1)]


# -------------------------------------------------------- suppressing
def test_suppressed_movement_is_swallowed_and_pinned():
    c, u, s = cap()
    c.set_suppress(True, False, cursor_here=True)
    move(c, 500, 400)                      # first event only sets the rest point
    u.moves.clear()
    assert move(c, 530, 380) is True       # swallowed
    assert s.motion == [(30, -20)]         # reported as a delta
    assert u.moves == [(500, 400)]         # and put straight back


def test_it_rests_in_place_when_the_cursor_is_on_this_screen():
    """Someone else is placing that cursor exactly where they want it. Yanking
    it to the middle of the screen would fight them."""
    c, u, s = cap()
    c.set_suppress(True, False, cursor_here=True)
    move(c, 42, 17)
    assert c._anchor == (42, 17)
    assert u.moves == []                   # nothing was moved to park it


def test_it_parks_in_the_middle_when_the_cursor_is_elsewhere():
    """Our pointer means nothing then - but we measure movement as a distance
    from it, so where it rests decides which directions can still be expressed."""
    c, u, s = cap()
    c.set_suppress(True, True, cursor_here=False)
    move(c, 1365, 400)
    assert c._anchor == (683, 384)         # centre of 1366x768
    assert u.moves == [(683, 384)]


def test_the_pointer_never_drifts_while_pinned():
    c, u, s = cap()
    c.set_suppress(True, False, cursor_here=True)
    move(c, 600, 300)
    for i in range(1, 20):                 # from 1: a zero delta re-pins nothing
        move(c, 600 + i * 7, 300 - i * 3)
        assert u.moves[-1] == (600, 300)   # always back to the same place
    assert len(u.moves) == 19


# ------- the bug that let the far cursor travel only one way
def test_resting_on_a_screen_edge_would_kill_one_direction():
    """Reported: "sometimes mouse shifting on the right showing mouse shifting on
    the left when I am controlling from Windows".

    Crossing OFF the right edge leaves the pointer at x=1365, and Windows will
    not let it go further. If the anchor rested there, pushing right would
    measure 1365-1365 = 0 forever and the far cursor could only ever go left.
    Parking in the middle is what keeps both directions expressible.
    """
    c, u, s = cap()
    c.set_suppress(True, True, cursor_here=False)   # cursor has gone to the peer
    move(c, 1365, 400)                              # pointer is at the right edge
    assert c._anchor == (683, 384), "must not rest on the edge it left from"

    s.motion.clear()
    move(c, 713, 384)                               # push right, from the centre
    assert s.motion == [(30, 0)], "pushing right must move the far cursor right"

    s.motion.clear()
    move(c, 653, 384)                               # and left still works
    assert s.motion == [(-30, 0)]


def test_the_cursor_arriving_here_switches_to_resting_in_place():
    """It flips between the two rest points as the cursor comes and goes."""
    c, u, s = cap()
    c.set_suppress(True, True, cursor_here=False)   # cursor away -> parked centre
    move(c, 1365, 400)
    assert c._anchor == (683, 384)

    c.set_suppress(True, False, cursor_here=True)   # ...now it arrives here
    c.note_injected(120, 90)                        # driven there by the peer
    s.motion.clear()
    move(c, 121, 90)
    assert s.motion == [(1, 0)]                     # measured from where it is

    c.set_suppress(True, True, cursor_here=False)   # ...and leaves again
    move(c, 121, 90)
    assert c._anchor == (683, 384)                  # re-parked in the middle


# ------------------ the bug that made two real machines oscillate
def test_injecting_a_position_moves_the_rest_point_with_it():
    """While suppressed we measure every real movement against where we last saw
    the pointer. But when the cursor is on OUR screen and a remote machine is
    driving it, we also inject positions - and SetCursorPos fires no hook, so
    nothing tells the capture the pointer moved.

    Without note_injected the next real event, even a one-pixel touchpad twitch,
    is measured against an anchor hundreds of pixels away and reported as a huge
    deliberate movement - which claims the baton. Two machines doing that to each
    other oscillate forever, which is exactly what the first long hardware run
    did: epoch climbing on its own with nobody touching anything.
    """
    c, u, s = cap()
    c.set_suppress(True, False, cursor_here=True)
    move(c, 600, 300)                      # resting here
    s.motion.clear()

    c.note_injected(120, 90)               # the remote drives the cursor over there
    move(c, 121, 90)                       # then a single-pixel twitch

    assert s.motion == [(1, 0)]            # a twitch, not a 480px leap
    assert max(abs(dx) for dx, _ in s.motion) < 8   # under the claim threshold


def test_without_the_fix_the_twitch_would_have_claimed():
    """Guards the number, not just the direction: 8px is the claim threshold, and
    the stale anchor produced deltas two orders of magnitude past it."""
    c, u, s = cap()
    c.set_suppress(True, False, cursor_here=True)
    move(c, 600, 300)
    stale = (600 - 120, 300 - 90)          # what the delta would have been
    assert abs(stale[0]) > 8 and abs(stale[1]) > 8


def test_a_fresh_suppression_re_reads_the_rest_point():
    c, u, s = cap()
    c.set_suppress(True, False, cursor_here=True)
    move(c, 600, 300)
    c.set_suppress(False, False)
    c.set_suppress(True, False, cursor_here=True)   # suppressed again
    move(c, 50, 50)
    assert c._anchor == (50, 50)


def test_injected_events_are_ignored_entirely():
    """P6 - and on Windows this is the flag, since SendInput sets it."""
    c, u, s = cap()
    c.set_suppress(True, False, cursor_here=True)
    move(c, 600, 300)
    s.motion.clear()
    # _mouse_proc is what applies the flag test; _handle_mouse never sees these.
    from link.capture_win import LLMHF_INJECTED
    assert LLMHF_INJECTED == 0x01


# ------------------------------ holding a key must keep typing
from link import protocol                                          # noqa: E402
from link.capture_win import WM_KEYDOWN, WM_KEYUP                   # noqa: E402


def kb(vk):
    return types.SimpleNamespace(vkCode=vk, scanCode=0, flags=0)


class KeySink(Sink):
    def __init__(self):
        super().__init__()
        self.keys = []

    def on_key(self, code, down):
        self.keys.append((code, down))


def keycap():
    c = WinCapture(u=FakeUser32())
    c.sink = KeySink()
    return c, c.sink


VK_D, EVDEV_D = 0x44, 32          # 'd' -> KEY_D


def test_a_held_key_is_reported_as_auto_repeat_not_as_new_presses():
    """Reported: holding 'd' typed 'dddddd...' on Windows but exactly one 'd' on
    Linux. KBDLLHOOKSTRUCT carries no repeat flag, so Windows sent a stream of
    fresh key-DOWNs - and duplicate presses with no release between them get
    filtered by libinput, each one restarting the compositor's repeat timer so it
    never reaches the repeat delay."""
    c, s = keycap()
    for _ in range(6):                                 # the key is held down
        c._handle_key(WM_KEYDOWN, kb(VK_D))
    c._handle_key(WM_KEYUP, kb(VK_D))

    values = [v for code, v in s.keys if code == EVDEV_D]
    assert values[0] == protocol.KEY_DOWN, "the first one is a real press"
    assert set(values[1:-1]) == {protocol.KEY_REPEAT}, "the rest are repeats"
    assert values[-1] == protocol.KEY_UP


def test_pressing_the_same_key_again_after_release_is_a_fresh_press():
    c, s = keycap()
    for _ in range(2):
        c._handle_key(WM_KEYDOWN, kb(VK_D))
        c._handle_key(WM_KEYUP, kb(VK_D))
    values = [v for code, v in s.keys if code == EVDEV_D]
    assert values == [protocol.KEY_DOWN, protocol.KEY_UP,
                      protocol.KEY_DOWN, protocol.KEY_UP]


def test_two_different_keys_held_together_are_both_fresh_presses():
    """Repeat is tracked per key, not globally - otherwise Shift+D would send D
    as a repeat of Shift."""
    c, s = keycap()
    c._handle_key(WM_KEYDOWN, kb(0xA0))                # Shift
    c._handle_key(WM_KEYDOWN, kb(VK_D))                # then D
    downs = [v for _, v in s.keys]
    assert downs == [protocol.KEY_DOWN, protocol.KEY_DOWN]
