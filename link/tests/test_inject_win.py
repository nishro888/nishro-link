"""What injected input actually looks like to Windows (inject.py).

These watch the same low-level hooks Windows itself uses to decide things,
so they check what the SYSTEM sees rather than what we meant to send. Every
key is swallowed by the hook, so nothing is typed into any window; the
pointer is moved a little and put back.

Reported by the maintainer, and both fixed here:

* Avro Keyboard's F12 did not switch between Bangla and English when
  pressed on the other computer's keyboard, and with Bangla on, that
  keyboard still typed English while the laptop's own was fine. A program
  that reads the SCAN CODE to know which physical key was struck could not
  tell, because we sent scan code zero.
* After a restart the pointer was moved and clicked perfectly but no arrow
  was drawn until the laptop's own touchpad was touched: SetCursorPos moves
  the cursor without the system seeing any mouse input at all.
"""
import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

if sys.platform == "win32":
    from link import keymap
    from link.inject import WindowsInjector

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN = 0x0100, 0x0101, 0x0104
WM_MOUSEMOVE = 0x0200
LLKHF_INJECTED, LLKHF_EXTENDED = 0x10, 0x01
MAPVK_VK_TO_VSC_EX = 4


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD),
                ("extra", ctypes.c_void_p)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("extra", ctypes.c_void_p)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class Watcher:
    """A low-level hook on its own thread, as Windows requires."""

    def __init__(self, which):
        self.which = which
        self.seen = []
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SetWindowsHookExW.restype = wt.HHOOK
        self.user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int,
                                               wt.WPARAM, wt.LPARAM]
        self._proc = HOOKPROC(self._on)
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        self._ready.wait(5)
        time.sleep(0.3)                 # the hook needs a moment to be live

    def _on(self, code, wparam, lparam):
        if code == 0:
            if self.which == WH_KEYBOARD_LL and wparam in (WM_KEYDOWN,
                                                           WM_SYSKEYDOWN,
                                                           WM_KEYUP):
                kb = ctypes.cast(lparam,
                                 ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                self.seen.append({"vk": kb.vkCode, "scan": kb.scanCode,
                                  "up": wparam == WM_KEYUP,
                                  "injected": bool(kb.flags & LLKHF_INJECTED),
                                  "extended": bool(kb.flags & LLKHF_EXTENDED)})
                return 1                # swallowed: nothing is typed anywhere
            if self.which == WH_MOUSE_LL and wparam == WM_MOUSEMOVE:
                ms = ctypes.cast(lparam,
                                 ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self.seen.append({"x": ms.pt.x, "y": ms.pt.y})
        return self.user32.CallNextHookEx(None, code, wparam, lparam)

    def _pump(self):
        h = self.user32.SetWindowsHookExW(self.which, self._proc, None, 0)
        self.hooked = bool(h)
        self._ready.set()
        if not h:
            return
        msg = wt.MSG()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass

    def wait_for(self, how_many, timeout=4.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(self.seen) < how_many:
            time.sleep(0.02)
        return list(self.seen)


@pytest.fixture
def keys():
    w = Watcher(WH_KEYBOARD_LL)
    if not getattr(w, "hooked", False):
        pytest.skip("could not watch the keyboard")
    return w


@pytest.fixture
def injector():
    return WindowsInjector()


def hardware_scan(vk):
    u = ctypes.WinDLL("user32")
    u.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
    u.MapVirtualKeyW.restype = wt.UINT
    return u.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC_EX) & 0xFF


# --------------------------------------------------------------- scan codes
@pytest.mark.parametrize("name", ["A", "B", "F12", "1", "SPACE", "ENTER"])
def test_an_injected_key_carries_the_scan_code_the_hardware_would(
        keys, injector, name):
    """The fix for Avro Keyboard. Scan code zero told a program nothing
    about which key was struck."""
    code = keymap.E[name]
    vk = keymap.evdev_to_vk(code)
    injector.key(code, True)
    seen = keys.wait_for(1)
    assert seen, f"{name} was not seen at all"
    first = seen[0]
    assert first["vk"] == vk
    assert first["scan"] == hardware_scan(vk) != 0, \
        f"{name} carried scan {first['scan']}, hardware sends {hardware_scan(vk)}"
    injector.key(code, False)


def test_a_key_release_carries_it_too(keys, injector):
    code = keymap.E["A"]
    injector.key(code, True)
    injector.key(code, False)
    seen = keys.wait_for(2)
    ups = [e for e in seen if e["up"]]
    assert ups, f"no release seen: {seen}"
    assert ups[0]["scan"] == hardware_scan(keymap.evdev_to_vk(code))


@pytest.mark.parametrize("name", ["LEFT", "RIGHT", "HOME", "DELETE", "RIGHTCTRL"])
def test_the_right_hand_and_navigation_keys_are_marked_extended(
        keys, injector, name):
    """Windows tells the left Ctrl from the right one, and an arrow from a
    number-pad key, by this flag and nothing else."""
    code = keymap.E[name]
    injector.key(code, True)
    seen = keys.wait_for(1)
    assert seen, f"{name} was not seen"
    assert seen[0]["extended"], f"{name} arrived without the extended flag"
    injector.key(code, False)


def test_injected_keys_still_say_they_are_injected(keys, injector):
    """P6: a machine must be able to tell its own injections apart, or it
    reads back what it just played."""
    injector.key(keymap.E["B"], True)
    seen = keys.wait_for(1)
    assert seen and seen[0]["injected"] is True
    injector.key(keymap.E["B"], False)


def test_a_key_windows_has_no_name_for_is_simply_dropped(keys, injector):
    injector.key(999999, True)
    time.sleep(0.4)
    assert keys.seen == []


# ------------------------------------------------- the arrow, after a reboot
def test_moving_the_pointer_puts_it_exactly_where_asked(injector):
    """Whatever is done about the arrow, a pointer crossing an edge must
    land on the pixel it was sent to - SendInput's absolute move rounds
    through a 0-65535 grid and lands a pixel out, which is why it cannot be
    used on its own."""
    home = injector._pos()
    try:
        for x, y in ((300, 300), (301, 300), (700, 451), (1365, 767)):
            injector.move_abs(x - injector.ox, y - injector.oy)
            time.sleep(0.05)
            assert injector._pos() == (x, y), \
                f"asked for {x},{y} and landed on {injector._pos()}"
    finally:
        injector.u.SetCursorPos(*home)


def test_the_arrow_is_only_looked_at_now_and_then(injector):
    """The check is a system call on the path every pointer movement takes,
    so it must not happen on every one."""
    looks = []
    injector._arrow_is_drawn = lambda: looks.append(1) or True
    for i in range(50):
        injector.move_abs(400 + i, 400)
    assert len(looks) <= 2, f"looked {len(looks)} times for 50 movements"


def test_when_the_arrow_is_not_drawn_real_mouse_input_is_sent(injector):
    """The fix itself: the system is given movement it counts as mouse
    input, and the pointer is then put back exactly."""
    mouse = Watcher(WH_MOUSE_LL)
    if not getattr(mouse, "hooked", False):
        pytest.skip("could not watch the mouse")
    home = injector._pos()
    try:
        injector._arrow_is_drawn = lambda: False     # as it is after a boot
        injector._looked_at_cursor = 0.0
        mouse.seen.clear()
        injector.move_abs(700 - injector.ox, 450 - injector.oy)
        seen = mouse.wait_for(1)
        assert seen, "the system saw no mouse input, so the arrow stays hidden"
        assert injector._pos() == (700, 450), \
            f"the pointer ended up at {injector._pos()}"
    finally:
        injector.u.SetCursorPos(*home)


def test_nothing_is_sent_while_the_arrow_is_already_drawn(injector):
    mouse = Watcher(WH_MOUSE_LL)
    if not getattr(mouse, "hooked", False):
        pytest.skip("could not watch the mouse")
    home = injector._pos()
    try:
        injector._arrow_is_drawn = lambda: True
        injector._looked_at_cursor = 0.0
        mouse.seen.clear()
        injector.move_abs(650 - injector.ox, 420 - injector.oy)
        time.sleep(0.4)
        assert mouse.seen == [], \
            "mouse input was sent although the arrow was already there"
    finally:
        injector.u.SetCursorPos(*home)
