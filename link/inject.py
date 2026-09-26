"""Input injection: replay received events into the local OS.

Windows: user32 SendInput-style calls (ctypes, stdlib) - the same proven
approach the Nishro companion already uses.
Linux: a /dev/uinput virtual device via python-evdev. uinput is chosen over
XTEST because it works under BOTH X11 and Wayland. Needs `pip install evdev`
and the user in the `input` group (or run with sudo once to test).
"""
from __future__ import annotations

import sys

from . import keymap

# The name our Linux virtual device advertises. capture_linux.py must refuse to
# read from any device whose name starts with this, or a node reads back its own
# injections (P6). Defined here, next to the UInput() call that uses it, so the
# two can never drift apart.
VIRTUAL_DEVICE_NAME = "Nishro Link Virtual Input"

REPEAT = 2      # evdev's auto-repeat value, as it arrives on the wire

# Keys that must never be bounced to synthesise a repeat - see
# LinuxInjector._repeat. Held modifiers do get repeat events from the OS, and
# releasing one mid-chord would break every combination using it.
MODIFIER_CODES = frozenset({
    29,    # left ctrl
    42,    # left shift
    54,    # right shift
    56,    # left alt
    58,    # caps lock
    97,    # right ctrl
    100,   # right alt
    125,   # left meta
    126,   # right meta
})


class Injector:
    def move(self, dx: int, dy: int) -> None: ...
    def move_abs(self, x: int, y: int) -> None: ...   # absolute (edge-crossing)
    def button(self, name: str, down: bool) -> None: ...
    def wheel(self, dx: int, dy: int) -> None: ...
    def key(self, evdev_code: int, down: bool) -> None: ...
    def close(self) -> None: ...


# ------------------------------------------------------------------- Windows
class WindowsInjector(Injector):
    MOVE = 0x0001
    LDOWN, LUP = 0x0002, 0x0004
    RDOWN, RUP = 0x0008, 0x0010
    MDOWN, MUP = 0x0020, 0x0040
    WHEEL, HWHEEL = 0x0800, 0x1000
    KEYUP = 0x0002

    def __init__(self, origin=(0, 0)):
        import ctypes
        import ctypes.wintypes
        self.c = ctypes
        self.u = ctypes.windll.user32
        # Link positions count from the desktop's corner; SetCursorPos counts
        # from the primary monitor's. See desktop.py.
        self.ox, self.oy = int(origin[0]), int(origin[1])

    def _pos(self):
        pt = self.c.wintypes.POINT()
        self.u.GetCursorPos(self.c.byref(pt))
        return pt.x, pt.y

    def move(self, dx, dy):
        x, y = self._pos()
        self.u.SetCursorPos(int(x + dx), int(y + dy))  # SetCursorPos dodges accel

    def move_abs(self, x, y):
        self.u.SetCursorPos(int(x) + self.ox, int(y) + self.oy)

    def set_origin(self, ox, oy):
        """See WinCapture.set_origin: a monitor change moved the desktop's corner."""
        self.ox, self.oy = int(ox), int(oy)

    def button(self, name, down):
        flags = {
            ("left", True): self.LDOWN, ("left", False): self.LUP,
            ("right", True): self.RDOWN, ("right", False): self.RUP,
            ("middle", True): self.MDOWN, ("middle", False): self.MUP,
        }.get((name, down))
        if flags is not None:
            self.u.mouse_event(flags, 0, 0, 0, 0)

    def wheel(self, dx, dy):
        if dy:
            self.u.mouse_event(self.WHEEL, 0, 0, int(dy) * 120, 0)
        if dx:
            self.u.mouse_event(self.HWHEEL, 0, 0, int(dx) * 120, 0)

    def key(self, evdev_code, down):
        vk = keymap.evdev_to_vk(evdev_code)
        if vk is None:
            return
        # Windows does not auto-repeat injected keys itself, so a repeat (2) has
        # to become another keydown - which is what any truthy value does here.
        self.u.keybd_event(vk, 0, 0 if down else self.KEYUP, 0)

    def close(self):
        pass


# --------------------------------------------------------------------- Linux
class LinuxInjector(Injector):
    def __init__(self, screen=None):
        # screen=(w,h) -> ABSOLUTE device (edge-crossing, drift-free). None ->
        # relative device (L1 / hotkey mode). Mixing REL_X/REL_Y with ABS_X/ABS_Y
        # on one node confuses libinput, so it is one or the other.
        from evdev import UInput, ecodes as e, AbsInfo
        self.e = e
        self.abs = screen is not None
        keys = sorted(set(keymap.E.values())) + [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE]
        if self.abs:
            w, h = screen
            cap = {
                e.EV_KEY: keys,
                e.EV_ABS: [(e.ABS_X, AbsInfo(0, 0, w - 1, 0, 0, 0)),
                           (e.ABS_Y, AbsInfo(0, 0, h - 1, 0, 0, 0))],
                e.EV_REL: [e.REL_WHEEL, e.REL_HWHEEL],
            }
        else:
            cap = {e.EV_KEY: keys, e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL]}
        self.ui = UInput(cap, name=VIRTUAL_DEVICE_NAME)
        self._btn = {"left": e.BTN_LEFT, "right": e.BTN_RIGHT, "middle": e.BTN_MIDDLE}

    def move(self, dx, dy):
        if self.abs:
            return
        e = self.e
        if dx:
            self.ui.write(e.EV_REL, e.REL_X, int(dx))
        if dy:
            self.ui.write(e.EV_REL, e.REL_Y, int(dy))
        self.ui.syn()

    def move_abs(self, x, y):
        if not self.abs:
            return
        e = self.e
        self.ui.write(e.EV_ABS, e.ABS_X, int(x))
        self.ui.write(e.EV_ABS, e.ABS_Y, int(y))
        self.ui.syn()

    def button(self, name, down):
        code = self._btn.get(name)
        if code is not None:
            self.ui.write(self.e.EV_KEY, code, 1 if down else 0)
            self.ui.syn()

    def wheel(self, dx, dy):
        e = self.e
        if dy:
            self.ui.write(e.EV_REL, e.REL_WHEEL, int(dy))
        if dx:
            self.ui.write(e.EV_REL, e.REL_HWHEEL, int(dx))
        self.ui.syn()

    def key(self, evdev_code, down):
        code, value = int(evdev_code), int(down)
        if value == REPEAT:
            return self._repeat(code)
        self.ui.write(self.e.EV_KEY, code, value)
        self.ui.syn()

    def _repeat(self, code: int) -> None:
        """Make one more character appear for a key held on the other machine.

        Passing the kernel's repeat value (2) straight through does NOT work, and
        it took real hardware to find out: libinput discards kernel auto-repeat
        and documents that repeat is the compositor's job, but the compositor
        does not repeat for this device in practice. A key held on the other
        machine produced exactly one character either way.

        So emit a release followed by a press. That is what an application
        actually receives from auto-repeat anyway - X11 and Wayland both deliver
        repeats as further key-press events - and unlike value 2 it cannot be
        filtered on the way through.

        Modifiers are skipped: Windows sends repeats for a held Shift too, and
        bouncing it up and down in the middle of a chord would break Shift+key
        for as long as the finger was down. Nothing needs a repeating modifier.
        """
        if code in MODIFIER_CODES:
            return
        self.ui.write(self.e.EV_KEY, code, 0)
        self.ui.syn()
        self.ui.write(self.e.EV_KEY, code, 1)
        self.ui.syn()

    def close(self):
        try:
            self.ui.close()
        except Exception:
            pass


def make_injector(screen=None, origin=(0, 0)) -> Injector:
    if sys.platform == "win32":
        return WindowsInjector(origin=origin)
    return LinuxInjector(screen=screen)
