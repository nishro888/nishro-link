"""Linux device selection - specifically P6. See DESIGN.md section 4.

Only the pure parts. Opening real /dev/input nodes needs evdev and a Linux box;
deciding WHICH ones to open does not, and that is where the dangerous bug lives.
"""
import pytest

from link.capture_linux import (BTN_LEFT, KEY_A, REL_X, is_own_virtual_device,
                                should_capture)
from link.inject import VIRTUAL_DEVICE_NAME

KEYBOARD = ([KEY_A, 29, 42], [])
MOUSE = ([BTN_LEFT], [REL_X, 1])


# ---- P6: never read back our own injections ----
def test_our_own_virtual_device_is_refused():
    """The whole peer model turns on this. Both nodes capture AND inject at the
    same time, so reading our own uinput node back means: the peer injects here,
    we see it as local input, we claim the baton - forever, at wire speed."""
    assert should_capture(VIRTUAL_DEVICE_NAME, *MOUSE) is False
    assert should_capture(VIRTUAL_DEVICE_NAME, *KEYBOARD) is False


def test_a_stale_device_from_a_crashed_run_is_also_refused():
    """The kernel may suffix the name if the original node still exists."""
    assert is_own_virtual_device(VIRTUAL_DEVICE_NAME + " 1") is True
    assert should_capture(VIRTUAL_DEVICE_NAME + " 1", *MOUSE) is False


def test_another_vendors_device_is_not_mistaken_for_ours():
    assert is_own_virtual_device("Logitech USB Receiver") is False
    assert is_own_virtual_device("") is False
    assert is_own_virtual_device(None) is False


def test_a_name_merely_containing_ours_is_still_refused_only_by_prefix():
    """Prefix, not substring: someone else's 'My Nishro Link Tester' is theirs."""
    assert is_own_virtual_device("My Nishro Link Virtual Input") is False


# ---- ordinary device selection ----
def test_real_keyboards_and_mice_are_captured():
    assert should_capture("AT Translated Set 2 keyboard", *KEYBOARD) is True
    assert should_capture("Logitech USB Optical Mouse", *MOUSE) is True


def test_a_mouse_is_recognised_by_its_button_alone():
    """Some pointing devices report buttons without REL_X."""
    assert should_capture("Some Touchpad", [BTN_LEFT], []) is True


@pytest.mark.parametrize("name,keys,rel", [
    ("Power Button", [116], []),
    ("Video Bus", [224, 225], []),
    ("HDA Intel HDMI", [], []),
    ("Lid Switch", [], []),
])
def test_things_that_are_not_input_devices_are_skipped(name, keys, rel):
    assert should_capture(name, keys, rel) is False


def test_the_device_name_is_the_single_source_of_truth():
    """capture_linux imports the name from inject, so the check can never drift
    away from the UInput() call it is guarding against."""
    import link.capture_linux as cl
    import link.inject as inj
    assert cl.VIRTUAL_DEVICE_NAME is inj.VIRTUAL_DEVICE_NAME
