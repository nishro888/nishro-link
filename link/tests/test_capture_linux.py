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


# ---- devices that appear later: hot-plug ----
class FakeDev:
    opened = []

    def __init__(self, path):
        spec = FakeEvdev.nodes[path]
        if spec is PermissionError:
            raise PermissionError(path)
        self.path, self.name, self._caps = path, spec[0], spec[1]
        self.grabbed = False
        FakeDev.opened.append(path)

    def capabilities(self):
        return self._caps

    def grab(self):
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False

    def close(self):
        pass


class FakeEvdev:
    """Stands in for the evdev module: nodes are path -> (name, capabilities)."""
    nodes = {}
    inodes = {}
    InputDevice = FakeDev

    class ecodes:
        EV_KEY, EV_REL = 1, 2


class FakeSelector:
    def __init__(self):
        self.fds = []

    def register(self, d, _):
        self.fds.append(d)

    def unregister(self, d):
        self.fds.remove(d)


KBD = ("Wireless Keyboard", {1: [KEY_A, 29]})
MSE = ("USB Mouse", {1: [BTN_LEFT], 2: [REL_X]})
LID = ("Lid Switch", {5: [0]})


@pytest.fixture
def cap(monkeypatch):
    import sys
    from link import capture_linux
    FakeEvdev.nodes, FakeEvdev.inodes, FakeDev.opened = {}, {}, []
    monkeypatch.setitem(sys.modules, "evdev", FakeEvdev)
    monkeypatch.setattr(capture_linux.glob, "glob", lambda _: list(FakeEvdev.nodes))
    monkeypatch.setattr(capture_linux, "_inode", lambda p: FakeEvdev.inodes.get(p))
    c = capture_linux.LinuxCapture()
    c._sel = FakeSelector()
    return c


def plug(path, spec, inode):
    FakeEvdev.nodes[path], FakeEvdev.inodes[path] = spec, inode


def unplug(path):
    del FakeEvdev.nodes[path]
    FakeEvdev.inodes.pop(path, None)


def test_a_keyboard_that_appears_later_is_read_from_the_next_look(cap):
    """Reported: typing on the AIO did nothing on the laptop. Its keyboard had
    appeared after the service started, and devices were opened only once."""
    plug("/dev/input/event3", MSE, 3)
    cap._open_devices()
    assert [d.name for d in cap._devs] == ["USB Mouse"]
    plug("/dev/input/event7", KBD, 7)            # the wireless keyboard wakes
    cap._open_devices()
    assert sorted(d.name for d in cap._devs) == ["USB Mouse", "Wireless Keyboard"]
    assert len(cap._sel.fds) == 2


def test_one_arriving_while_input_goes_elsewhere_is_taken_at_once(cap):
    """Grabbed now, not at the next change of control - or its first keys
    would type here while the pointer is on the other computer."""
    plug("/dev/input/event3", MSE, 3)
    cap._open_devices()
    cap._set_grab(True)
    plug("/dev/input/event7", KBD, 7)
    cap._open_devices()
    assert all(d.grabbed for d in cap._devs)


def test_a_device_that_is_not_ours_is_looked_at_once_not_every_time(cap):
    plug("/dev/input/event0", LID, 100)
    cap._open_devices()
    cap._open_devices()
    cap._open_devices()
    assert FakeDev.opened == ["/dev/input/event0"] and cap._devs == []


def test_a_new_device_at_an_old_path_is_looked_at_again(cap):
    """udev reuses eventN for a new device; the node itself is new."""
    plug("/dev/input/event4", LID, 100)
    cap._open_devices()
    unplug("/dev/input/event4")
    plug("/dev/input/event4", KBD, 101)
    cap._open_devices()
    assert [d.name for d in cap._devs] == ["Wireless Keyboard"]


def test_a_keyboard_unplugged_and_plugged_back_is_read_again(cap):
    plug("/dev/input/event7", KBD, 7)
    cap._open_devices()
    cap._drop(cap._devs[0])                       # read() failed: it went away
    assert cap._devs == [] and cap._sel.fds == [] and cap._paths == {}
    plug("/dev/input/event7", KBD, 8)
    cap._open_devices()
    assert [d.name for d in cap._devs] == ["Wireless Keyboard"]


def test_a_node_it_may_not_read_is_reported_not_skipped_for_good(cap):
    plug("/dev/input/event9", PermissionError, 9)
    cap._open_devices()
    assert cap._perm_denied and cap._devs == []
    plug("/dev/input/event9", KBD, 9)             # access fixed, same node
    cap._open_devices()
    assert [d.name for d in cap._devs] == ["Wireless Keyboard"]


def test_the_reading_loop_itself_looks_again(cap, monkeypatch):
    import threading
    import time
    from link import capture_linux
    monkeypatch.setattr(capture_linux, "RESCAN_S", 0.05)
    cap._sel.select = lambda timeout: time.sleep(0.01) or []
    plug("/dev/input/event3", MSE, 3)
    cap._open_devices()
    t = threading.Thread(target=cap._run, daemon=True)
    t.start()
    try:
        plug("/dev/input/event7", KBD, 7)
        end = time.monotonic() + 2
        while time.monotonic() < end and len(cap._devs) < 2:
            time.sleep(0.01)
        assert sorted(d.name for d in cap._devs) == ["USB Mouse", "Wireless Keyboard"]
    finally:
        cap.stop()
        t.join(1)


# ---- media keys follow the pointer ----
def test_a_keyboards_media_keys_are_read_too():
    """Reported: with the AIO's keyboard, volume up and down acted on the AIO
    even with the pointer on the laptop. Those keys come on a device of their
    own, with no letter keys, which was never read."""
    consumer = [113, 114, 115, 163, 164, 165, 224, 225]
    assert should_capture("SINO WEALTH Gaming KB Consumer Control", consumer, []) is True


def test_never_a_device_that_carries_the_power_button():
    """Grabbed while the pointer is elsewhere, this computer's power and sleep
    keys would go with it."""
    assert should_capture("Gaming KB System Control", [116, 142, 143], []) is False
    assert should_capture("Intel HID events", [113, 114, 115, 116], []) is False



def test_a_keyboards_media_device_listing_power_and_sleep_is_read():
    """Asked for: the volume dial on the AIO's keyboard should act on the
    computer the pointer is on. Cheap keyboards declare the whole consumer
    range on their media device, power and sleep included, with no such keys
    on the keyboard; that device was skipped, so its keys always acted here.
    A keyboard's media device is read now; its power, sleep and wake keys
    stay on this computer (NodeCore.local_key)."""
    consumer = [113, 114, 115, 116, 142, 163, 164, 165]
    assert should_capture("SINO WEALTH Gaming KB Consumer Control", consumer, []) is True
    assert should_capture("SINO WEALTH Gaming KB  Consumer Control", consumer, []) is True


def test_the_power_button_devices_are_still_left_alone():
    """Not a keyboard's media collection: the machine's own buttons."""
    assert should_capture("Power Button", [116], []) is False
    assert should_capture("Sleep Button", [142], []) is False
    assert should_capture("Gaming KB System Control", [116, 142, 143, 113, 114, 115], []) is False
    assert should_capture("Intel HID events", [113, 114, 115, 116], []) is False
