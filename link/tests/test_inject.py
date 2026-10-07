"""Injection: the parts that are decidable without a real device.

LinuxInjector normally needs /dev/uinput, but the interesting logic - what a
repeat actually turns into - is reachable by standing in a fake uinput in place
of the real one.
"""
import sys
import types

import pytest

from link.inject import MODIFIER_CODES, REPEAT, LinuxInjector


class FakeUInput:
    def __init__(self):
        self.writes = []

    def write(self, etype, code, value):
        self.writes.append((etype, code, value))

    def syn(self):
        self.writes.append(("syn",))

    def close(self):
        pass


EV_KEY = 1
KEY_G, KEY_LEFTSHIFT = 34, 42


def linux_injector():
    """A LinuxInjector without evdev: only .key() is exercised here."""
    inj = LinuxInjector.__new__(LinuxInjector)
    inj.e = types.SimpleNamespace(EV_KEY=EV_KEY)
    inj.ui = FakeUInput()
    inj.abs = True
    inj._btn = {}
    return inj, inj.ui


def keys(ui):
    return [(c, v) for w in ui.writes if len(w) == 3 for _, c, v in [w]]


# ------------------------------------------ a held key must keep typing
def test_a_repeat_becomes_a_release_then_a_press():
    """Reported twice: holding 'g' printed one 'g' on the other machine.

    Passing evdev's repeat value (2) through does not work. libinput discards
    kernel auto-repeat and documents repeat as the compositor's job, but the
    compositor does not repeat for this device in practice. A release followed by
    a press cannot be filtered, and is what an application receives from
    auto-repeat anyway.
    """
    inj, ui = linux_injector()
    inj.key(KEY_G, REPEAT)
    assert keys(ui) == [(KEY_G, 0), (KEY_G, 1)]


def test_each_half_of_the_repeat_is_flushed_separately():
    """One syn per event, or the release and press arrive in the same frame and
    the application sees no change at all."""
    inj, ui = linux_injector()
    inj.key(KEY_G, REPEAT)
    assert ui.writes == [(EV_KEY, KEY_G, 0), ("syn",), (EV_KEY, KEY_G, 1), ("syn",)]


def test_a_held_key_produces_one_character_per_repeat():
    inj, ui = linux_injector()
    inj.key(KEY_G, 1)
    for _ in range(5):
        inj.key(KEY_G, REPEAT)
    inj.key(KEY_G, 0)
    presses = [v for c, v in keys(ui) if c == KEY_G and v == 1]
    assert len(presses) == 6, "one real press plus one per repeat"


# ------------------------------------------------ but never a modifier
def test_a_repeating_modifier_is_ignored():
    """Windows sends repeats for a held Shift too. Bouncing it up and down mid
    chord would break Shift+key for as long as the finger was down."""
    inj, ui = linux_injector()
    inj.key(KEY_LEFTSHIFT, REPEAT)
    assert ui.writes == []


@pytest.mark.parametrize("code", sorted(MODIFIER_CODES))
def test_no_modifier_is_ever_bounced(code):
    inj, ui = linux_injector()
    inj.key(code, REPEAT)
    assert ui.writes == []


def test_modifiers_still_press_and_release_normally():
    inj, ui = linux_injector()
    inj.key(KEY_LEFTSHIFT, 1)
    inj.key(KEY_LEFTSHIFT, 0)
    assert keys(ui) == [(KEY_LEFTSHIFT, 1), (KEY_LEFTSHIFT, 0)]


# ------------------------------------------------- ordinary presses
def test_press_and_release_pass_straight_through():
    inj, ui = linux_injector()
    inj.key(KEY_G, True)
    inj.key(KEY_G, False)
    assert keys(ui) == [(KEY_G, 1), (KEY_G, 0)]


def test_bools_and_ints_mean_the_same_thing():
    inj, ui = linux_injector()
    inj.key(KEY_G, True)
    inj.key(KEY_G, 1)
    assert keys(ui) == [(KEY_G, 1), (KEY_G, 1)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows injector")
def test_windows_treats_a_repeat_as_another_keydown():
    """Windows does not auto-repeat injected keys itself, so a repeat has to be
    another keydown - and any truthy value already is one."""
    from link.inject import WindowsInjector
    sent = []
    inj = WindowsInjector.__new__(WindowsInjector)
    inj.u = types.SimpleNamespace(keybd_event=lambda vk, sc, fl, x: sent.append((vk, fl)))
    inj.KEYUP = WindowsInjector.KEYUP
    inj.key(KEY_G, REPEAT)
    inj.key(KEY_G, True)
    inj.key(KEY_G, False)
    assert sent[0][1] == 0 and sent[1][1] == 0, "repeat and press are both keydowns"
    assert sent[2][1] == WindowsInjector.KEYUP



# ------------------------------------------------------------ media keys
def test_media_keys_travel_both_ways():
    """Volume, mute and playback keys map to Windows' and back; brightness has
    no Windows key at all - it is set instead (see the next test)."""
    from link import keymap
    for vk, code in ((0xAD, 113), (0xAE, 114), (0xAF, 115), (0xB0, 163),
                     (0xB1, 165), (0xB2, 166), (0xB3, 164)):
        assert keymap.vk_to_evdev(vk) == code and keymap.evdev_to_vk(code) == vk
    assert keymap.evdev_to_vk(224) is None and keymap.evdev_to_vk(225) is None
    assert set(keymap.BRIGHTNESS) == {224, 225}


def test_linux_can_play_media_keys():
    """Its virtual device must declare them, or uinput drops them."""
    from link import keymap
    assert {113, 114, 115, 163, 164, 165, 166, 224, 225} <= set(keymap.E.values())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows injector")
def test_windows_plays_media_keys_as_extended_keys_and_sets_brightness():
    import threading
    from link.inject import WindowsInjector
    sent, steps = [], []
    inj = WindowsInjector.__new__(WindowsInjector)
    inj.u = types.SimpleNamespace(keybd_event=lambda vk, sc, fl, x: sent.append((vk, fl)))
    inj._b_lock = threading.Lock()
    inj._b_pending, inj._b_busy = 0, False
    inj._brightness = lambda step: steps.append(step)
    inj.key(115, 1)                                   # volume up
    inj.key(115, 0)
    assert sent == [(0xAF, WindowsInjector.EXTENDED),
                    (0xAF, WindowsInjector.EXTENDED | WindowsInjector.KEYUP)]
    inj.key(225, 1)
    inj.key(225, 2)                                   # held: a repeat
    inj.key(225, 0)                                   # its release does nothing
    inj.key(224, 1)
    assert steps == [10, 10, -10]


def test_brightness_stays_within_its_range():
    from link.inject import brightness_script
    s = brightness_script(-10)
    assert "[Math]::Max(0, [Math]::Min(100" in s and "+ (-10)" in s
    assert "WmiSetBrightness" in s


@pytest.mark.skipif(sys.platform != "win32", reason="Windows injector")
def test_brightness_presses_that_arrive_together_are_added_up(monkeypatch):
    import threading
    from link import inject
    ran = []
    gate = threading.Event()

    def run(cmd, **kw):
        gate.wait(2)
        ran.append(cmd[-1])
    monkeypatch.setattr(inject.subprocess, "run", run)
    inj = inject.WindowsInjector.__new__(inject.WindowsInjector)
    inj._b_lock = threading.Lock()
    inj._b_pending, inj._b_busy = 0, False
    inj._brightness(10)                               # starts the worker
    import time
    time.sleep(0.05)
    inj._brightness(10)                               # while PowerShell runs
    inj._brightness(10)
    gate.set()
    for _ in range(100):
        if not inj._b_busy:
            break
        time.sleep(0.02)
    assert len(ran) == 2 and "+ (10)" in ran[0] and "+ (20)" in ran[1]



def test_linux_can_press_power_sleep_and_wake_here():
    """They stay on this computer while its keyboard is held for another
    (NodeCore.local_key), so its virtual device must declare them."""
    from link import keymap
    assert {116, 142, 143} <= keymap.SYSTEM
    assert keymap.SYSTEM <= set(keymap.linux_keys())
