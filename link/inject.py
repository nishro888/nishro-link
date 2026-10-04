"""Input injection: replay received events into the local OS.

Windows: user32 SendInput-style calls (ctypes, stdlib) - the same proven
approach the Nishro companion already uses.
Linux: a /dev/uinput virtual device via python-evdev. uinput is chosen over
XTEST because it works under BOTH X11 and Wayland. Needs `pip install evdev`
and the user in the `input` group (or run with sudo once to test).
"""
from __future__ import annotations

import subprocess
import sys
import threading

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
    def spotlight(self, x: int, y: int) -> None: ...  # show where the pointer is
    def close(self) -> None: ...


# ------------------------------------------------------------------- Windows
class WindowsInjector(Injector):
    MOVE = 0x0001
    LDOWN, LUP = 0x0002, 0x0004
    RDOWN, RUP = 0x0008, 0x0010
    MDOWN, MUP = 0x0020, 0x0040
    WHEEL, HWHEEL = 0x0800, 0x1000
    KEYUP, EXTENDED = 0x0002, 0x0001

    def __init__(self, origin=(0, 0)):
        import ctypes
        import ctypes.wintypes
        self.c = ctypes
        self.u = ctypes.windll.user32
        self._b_lock = threading.Lock()
        self._b_pending, self._b_busy = 0, False
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
        step = keymap.BRIGHTNESS.get(evdev_code)
        if step is not None:
            if down:                      # a press, or a repeat of one held
                self._brightness(step)
            return
        vk = keymap.evdev_to_vk(evdev_code)
        if vk is None:
            return
        # Windows does not auto-repeat injected keys itself, so a repeat (2) has
        # to become another keydown - which is what any truthy value does here.
        flags = 0 if down else self.KEYUP
        if vk in keymap.MEDIA_VKS:
            flags |= self.EXTENDED
        self.u.keybd_event(vk, 0, flags, 0)

    def _brightness(self, step):
        """Windows has no brightness key a program can send: a laptop's are
        read by its firmware. So set the built-in screen's brightness directly,
        through WMI - on a worker, as PowerShell takes a moment, with presses
        that arrive meanwhile added together. (External monitors don't take
        it; Windows shows no slider for it either.)"""
        with self._b_lock:
            self._b_pending += step
            if self._b_busy:
                return
            self._b_busy = True
        threading.Thread(target=self._brightness_worker, daemon=True).start()

    def _brightness_worker(self):
        while True:
            with self._b_lock:
                step, self._b_pending = self._b_pending, 0
                if not step:
                    self._b_busy = False
                    return
            try:
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                                "-Command", brightness_script(step)],
                               capture_output=True, timeout=20,
                               creationflags=0x08000000)       # no window
            except Exception:
                pass

    def spotlight(self, x, y):
        """Darken every screen but a circle round the pointer (spotlight_win)
        - wherever the pointer really is, so (x, y) is only a hint."""
        from .spotlight_win import show
        show()

    def close(self):
        pass


def brightness_script(step: int) -> str:
    """PowerShell that moves the built-in screen's brightness by `step` percent,
    kept within 0-100."""
    step = int(step)
    return ("$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
            "| Select-Object -First 1; "
            f"$n = [Math]::Max(0, [Math]::Min(100, [int]$m.CurrentBrightness + ({step}))); "
            "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
            "| Select-Object -First 1 | Invoke-CimMethod -MethodName WmiSetBrightness "
            "-Arguments @{Timeout = [uint32]0; Brightness = [byte]$n} | Out-Null")


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

    def spotlight(self, x, y):
        """Show where the pointer is - by GNOME's own Locate Pointer: a Ctrl
        pressed and let go on its own sends a ripple out from the pointer. On
        Wayland only the compositor may draw over everything, so this is the
        way that works, and it knows where the pointer really is. Off the
        main path: it asks and sets GNOME's settings, which takes a moment."""
        import threading

        def show():
            if not locate_gnome(self) and getattr(self, "on_log", None):
                self.on_log("find the pointer: GNOME's Locate Pointer could not be "
                            "used here (not GNOME, or no one logged in)")
        threading.Thread(target=show, daemon=True).start()

    def close(self):
        try:
            self.ui.close()
        except Exception:
            pass


_LOCATING = None


def locate_gnome(injector, settle: float = 0.15, restore_after: float = 1.6,
                 run=None) -> bool:
    """GNOME's Locate Pointer, shown once. If the person has it off, it is
    turned on for the moment and back off after, so their setting stands.
    False where there is no GNOME to ask. One at a time."""
    import threading
    import time
    global _LOCATING
    if _LOCATING is None:
        _LOCATING = threading.Lock()
    if not _LOCATING.acquire(blocking=False):
        return False                       # one is showing now
    try:
        if run is None:
            from . import session
            run = session.run
        def gs(verb, *more):
            return run(["gsettings", verb, "org.gnome.desktop.interface",
                        "locate-pointer", *more],
                       capture_output=True, text=True, timeout=3)
        r = gs("get")
        if r.returncode != 0:
            return False                   # not GNOME, or no session to ask
        was_on = r.stdout.strip() == "true"
        if not was_on:
            gs("set", "true")
            time.sleep(settle)             # for the compositor to hear it
        ctrl = keymap.E["LEFTCTRL"]
        injector.key(ctrl, True)
        time.sleep(0.03)
        injector.key(ctrl, False)
        if not was_on:
            time.sleep(restore_after)      # the ripple takes about a second
            gs("set", "false")
        return True
    except Exception:
        return False
    finally:
        _LOCATING.release()


def make_injector(screen=None, origin=(0, 0)) -> Injector:
    if sys.platform == "win32":
        return WindowsInjector(origin=origin)
    return LinuxInjector(screen=screen)
