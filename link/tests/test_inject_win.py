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

# Skipped before anything else runs, not by a mark: HOOKPROC below needs
# ctypes.WINFUNCTYPE, which does not exist off Windows, so elsewhere even
# COLLECTING this file fails with an AttributeError. A pytestmark would not
# help - the module is imported before any mark is consulted. CI's Linux
# jobs found this; running the suite on the laptop never could.
if sys.platform != "win32":
    pytest.skip("Windows hooks", allow_module_level=True)

from link import keymap                                      # noqa: E402
from link.inject import WindowsInjector                      # noqa: E402

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN = 0x0100, 0x0101, 0x0104
WM_MOUSEMOVE = 0x0200
LLKHF_INJECTED, LLKHF_EXTENDED = 0x10, 0x01
LLMHF_INJECTED = 0x01
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
        self._stop = threading.Event()
        self._handle = None
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
                injected = bool(kb.flags & LLKHF_INJECTED)
                self.seen.append({"vk": kb.vkCode, "scan": kb.scanCode,
                                  "up": wparam == WM_KEYUP,
                                  "injected": injected,
                                  "extended": bool(kb.flags & LLKHF_EXTENDED)})
                # ONLY our own injections are swallowed. A hook swallowing
                # everything leaves the person unable to type at all if this
                # process ever outlives the test - which happened: a test run
                # left hanging in the background ate every keystroke on the
                # machine until it was restarted. A key the person actually
                # pressed must always pass through, whatever goes wrong here.
                return 1 if injected else self.user32.CallNextHookEx(
                    None, code, wparam, lparam)
            if self.which == WH_MOUSE_LL and wparam == WM_MOUSEMOVE:
                ms = ctypes.cast(lparam,
                                 ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self.seen.append({
                    "x": ms.pt.x, "y": ms.pt.y,
                    "injected": bool(ms.flags & LLMHF_INJECTED)})
        return self.user32.CallNextHookEx(None, code, wparam, lparam)

    def _pump(self):
        h = self.user32.SetWindowsHookExW(self.which, self._proc, None, 0)
        self.hooked = bool(h)
        self._handle = h
        self._ready.set()
        if not h:
            return
        msg = wt.MSG()
        while not self._stop.is_set() and                 self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1) >= 0:
            if self._stop.wait(0.02):
                break
        self.unhook()

    def unhook(self):
        """Taken down the moment a test is done with it, not left to the
        process exiting."""
        h, self._handle = getattr(self, "_handle", None), None
        if h:
            try:
                self.user32.UnhookWindowsHookEx(h)
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        self.unhook()

    def ours(self):
        """Only what Nishro Link injected.

        These hooks watch the WHOLE machine, so anything the person does
        while the suite runs arrives here too - and did: seven of these
        tests failed in one run because the maintainer was typing at the
        time, one of them (`a key Windows has no name for`) simply because
        it asserted that NO key had been seen. A test that watches the real
        system must look only at its own input, or using the computer breaks
        the build.
        """
        return [e for e in self.seen if e.get("injected")]

    def wait_for(self, how_many, timeout=4.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(self.ours()) < how_many:
            time.sleep(0.02)
        return self.ours()


@pytest.fixture
def keys():
    w = Watcher(WH_KEYBOARD_LL)
    if not getattr(w, "hooked", False):
        w.stop()
        pytest.skip("could not watch the keyboard")
    try:
        yield w
    finally:
        w.stop()


@pytest.fixture
def mouse():
    w = Watcher(WH_MOUSE_LL)
    if not getattr(w, "hooked", False):
        w.stop()
        pytest.skip("could not watch the mouse")
    try:
        yield w
    finally:
        w.stop()


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
    assert keys.ours() == [], (
        f"nothing should have been injected; saw {keys.ours()}")


def test_what_the_person_does_while_this_runs_is_ignored(keys, injector):
    """These hooks watch the WHOLE machine, so a key the person presses
    arrives here too. Seven of these tests failed in one run because the
    maintainer was typing to the assistant at the time; one of them only
    because it asserted no key had been seen at all. Using the computer must
    not break the build.
    """
    keys.seen.append({"vk": 0x41, "scan": 30, "up": False,
                      "injected": False, "extended": False})
    injector.key(keymap.E["B"], True)
    try:
        seen = keys.wait_for(1)
        assert seen, "our own key was not seen"
        assert all(e["injected"] for e in seen), seen
        assert seen[0]["vk"] == keymap.evdev_to_vk(keymap.E["B"])
    finally:
        injector.key(keymap.E["B"], False)


# ------------------------------------------------- the arrow, after a reboot
def test_moving_the_pointer_puts_it_exactly_where_asked(injector):
    """Whatever is done about the arrow, a pointer crossing an edge must
    land on the pixel it was sent to - SendInput's absolute move rounds
    through a 0-65535 grid and lands a pixel out, which is why it cannot be
    used on its own.

    The places are worked out from THIS screen, not written down. They were
    written down - (1365, 767), the corner of the maintainer's laptop - and
    CI's Windows runner is 1024 wide, so the pointer clamped to 1023 and the
    test failed for a reason that had nothing to do with what it checks.
    """
    u = ctypes.WinDLL("user32")
    w = u.GetSystemMetrics(78) or 1024      # SM_CXVIRTUALSCREEN
    h = u.GetSystemMetrics(79) or 768
    # The far corner matters most: that is where the 0-65535 grid's rounding
    # is worst. The two adjacent pixels catch an off-by-one.
    places = [(w // 4, h // 4), (w // 4 + 1, h // 4),
              (w // 2, h // 2 + 1), (w - 1, h - 1)]
    home = injector._pos()
    try:
        for x, y in places:
            injector.move_abs(x - injector.ox, y - injector.oy)
            time.sleep(0.05)
            assert injector._pos() == (x, y), (
                f"asked for {x},{y} and landed on {injector._pos()} "
                f"(this desktop is {w}x{h})")
    finally:
        injector.u.SetCursorPos(*home)


def test_the_first_movement_of_a_spell_sends_real_mouse_input(injector, mouse):
    """The fix: whenever driving begins, the system is given movement it
    counts as mouse input, so the arrow is drawn - and the pointer is then
    put back on the exact pixel.

    Windows is NOT asked whether the arrow is showing. It said yes while it
    plainly was not drawing one, so the wake never happened, and after the
    first attempt at this fix the maintainer still had no arrow until a
    CLICK arrived from the other computer's mouse - a click being mouse
    input where our movement was not.
    """
    home = injector._pos()
    try:
        mouse.seen.clear()
        injector.move_abs(700 - injector.ox, 450 - injector.oy)
        assert mouse.wait_for(1),             "the system saw no mouse input, so the arrow stays hidden"
        assert injector._pos() == (700, 450),             f"the pointer ended up at {injector._pos()}"
    finally:
        injector.u.SetCursorPos(*home)


def test_driving_on_and_on_does_not_keep_sending_it(injector, mouse):
    """It costs two extra system calls on the path every movement of the
    pointer takes, so it happens once a spell, not once a movement."""
    home = injector._pos()
    try:
        injector.move_abs(500 - injector.ox, 400 - injector.oy)
        mouse.wait_for(1)
        mouse.seen.clear()
        for i in range(60):
            injector.move_abs(500 + i - injector.ox, 400 - injector.oy)
        time.sleep(0.4)
        ours = mouse.ours()
        assert len(ours) <= 1, (
            f"sent mouse input {len(ours)} times for 60 movements")
    finally:
        injector.u.SetCursorPos(*home)


def test_it_is_sent_again_after_a_pause(injector, mouse):
    """A pause means the pointer went back to the other computer, and the
    arrow may well need waking when it returns - after a lock, for one."""
    home = injector._pos()
    try:
        injector.move_abs(500 - injector.ox, 400 - injector.oy)
        mouse.wait_for(1)
        injector._last_move_at -= injector.WAKE_AFTER_IDLE_S + 0.5
        mouse.seen.clear()
        injector.move_abs(520 - injector.ox, 400 - injector.oy)
        assert mouse.wait_for(1), "nothing was sent when driving began again"
    finally:
        injector.u.SetCursorPos(*home)
