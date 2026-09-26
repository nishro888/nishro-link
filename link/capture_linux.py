"""Linux input capture via evdev: reads /dev/input, grabs (EVIOCGRAB) to suppress.

The Wayland-safe counterpart to capture_win.py, and the same contract: report
local physical input to a Node sink, swallow it when told to, decide nothing.

Needs `pip install evdev` and read access to /dev/input/* - add the user to the
`input` group (`sudo usermod -aG input $USER`) and log back in. Unlike injection,
there is no portal-free alternative to this.

P6 lives here: should_capture() refuses our own uinput device. Every node
captures AND injects at once, so without that check a node reads back its own
injections, claims the baton, and feeds back forever at wire speed.

Unlike Windows there is no absolute pointer to read - evdev gives relative
deltas - so on_pointer() is synthesised from a position this module tracks
itself while unsuppressed.
"""
from __future__ import annotations

import glob
import selectors
import threading

from .inject import VIRTUAL_DEVICE_NAME

KEY_LEFTCTRL, KEY_RIGHTCTRL = 29, 97
KEY_A, BTN_LEFT, REL_X = 30, 272, 0   # for should_capture, which takes no evdev


def is_own_virtual_device(name: str) -> bool:
    """P6: is this the uinput device WE created?

    Prefix, not equality, because the kernel may suffix a name when a stale
    device from a crashed run still holds the original.
    """
    return (name or "").startswith(VIRTUAL_DEVICE_NAME)


def should_capture(name: str, key_codes, rel_codes) -> bool:
    """Is this device a real keyboard or mouse we should read?

    Pure, so P6 is testable on any machine - unlike the bug it prevents.
    """
    if is_own_virtual_device(name):
        return False
    return KEY_A in key_codes or REL_X in rel_codes or BTN_LEFT in key_codes


class LinuxCapture:
    def __init__(self, screen=(1920, 1080)):
        self.sink = None
        self.sw, self.sh = screen
        self._sup_mouse = False
        self._sup_kb = False
        self._x, self._y = self.sw // 2, self.sh // 2   # our own idea of the pointer
        self._devs = []
        self._grabbed = False
        self._held = set()
        self._perm_denied = False
        self._sel = selectors.DefaultSelector()
        self._stop = False
        self._btn = {272: "left", 273: "right", 274: "middle"}

    # ---- lifecycle ----
    def start(self, sink):
        self.sink = sink
        self._open_devices()
        if not self._devs:
            if self._perm_denied:
                raise PermissionError(
                    "Can't read /dev/input/* - add your user to the 'input' group "
                    "(sudo usermod -aG input $USER) and log back in.")
            raise RuntimeError("No keyboard or mouse input devices found.")
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop = True

    def set_suppress(self, mouse: bool, keyboard: bool,
                     cursor_here: bool = False):
        # cursor_here is unused here: evdev grabs whole devices and forwards
        # raw deltas, so there is no anchor to place and no screen edge to be
        # pinned against. Windows needs it; this does not.
        self._sup_mouse = bool(mouse)
        self._sup_kb = bool(keyboard)
        # evdev grabs a whole device, so we can only take everything or nothing.
        # Grab when either stream needs swallowing and re-inject nothing: the
        # keyboard-only case (cursor here, someone else driving) still needs the
        # mouse grabbed, and NodeCore already routes the keys back to us.
        self._set_grab(self._sup_mouse or self._sup_kb)

    def note_injected(self, x: int, y: int) -> None:
        """We just placed the pointer ourselves; track it, or the next real
        movement is measured from a position the pointer left long ago. Same
        hazard as on Windows - see WinCapture.note_injected."""
        self._x, self._y = int(x), int(y)

    # ---- internals ----
    def _open_devices(self):
        # glob directly rather than evdev.list_devices(), which silently hides
        # nodes we cannot read - making a permission problem invisible.
        from evdev import InputDevice, ecodes as ec
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                d = InputDevice(path)
            except PermissionError:
                self._perm_denied = True
                continue
            except OSError:
                continue
            try:
                caps = d.capabilities()
                if should_capture(d.name, caps.get(ec.EV_KEY, []), caps.get(ec.EV_REL, [])):
                    self._devs.append(d)
                    self._sel.register(d, selectors.EVENT_READ)
                else:
                    d.close()
            except Exception:
                try:
                    d.close()
                except Exception:
                    pass

    def _set_grab(self, on):
        if on and not self._grabbed:
            for d in self._devs:
                try:
                    d.grab()
                except Exception:
                    pass
            self._grabbed = True
        elif not on and self._grabbed:
            for d in self._devs:
                try:
                    d.ungrab()
                except Exception:
                    pass
            self._grabbed = False

    def _run(self):
        from evdev import ecodes as ec
        while not self._stop:
            for k, _ in self._sel.select(timeout=0.2):
                try:
                    for e in k.fileobj.read():
                        self._handle(e, ec)
                except OSError:
                    # unplugged - drop it, or select() spins at 100% CPU
                    try:
                        self._sel.unregister(k.fileobj)
                        self._devs.remove(k.fileobj)
                    except (KeyError, ValueError):
                        pass
                except Exception:
                    pass                                   # P4: fail open
        self._set_grab(False)

    def _handle(self, e, ec):
        if e.type == ec.EV_KEY:
            code, val = e.code, e.value          # 1 down, 2 autorepeat, 0 up
            if code in self._btn:
                if val != 2:                     # buttons do not auto-repeat
                    self.sink.on_button(self._btn[code], val == 1)
                return
            if val == 1:
                self._held.add(code)
            elif val == 0:
                self._held.discard(code)
            # Only a FRESH press can trip the failsafe; otherwise holding both
            # Ctrls would fire it over and over as the repeats arrived.
            if val == 1 and {KEY_LEFTCTRL, KEY_RIGHTCTRL} <= self._held:
                self.sink.on_failsafe()
                return
            # val passes through as itself, repeats included: dropping them meant
            # a key held on THIS machine also typed only one character over there.
            self.sink.on_key(code, val)

        elif e.type == ec.EV_REL:
            if e.code == ec.REL_X:
                self._motion(e.value, 0)
            elif e.code == ec.REL_Y:
                self._motion(0, e.value)
            elif e.code == ec.REL_WHEEL:
                self.sink.on_wheel(0, e.value)
            elif e.code == ec.REL_HWHEEL:
                self.sink.on_wheel(e.value, 0)

    def _motion(self, dx, dy):
        if self._sup_mouse:
            self.sink.on_motion(dx, dy)
            return
        # Not suppressing: the compositor is moving the real pointer and we
        # cannot read where it put it, so we track it ourselves. Clamping here is
        # what lets NodeCore see movement the screen edge ate, which is what
        # carries the cursor onto the next machine.
        self._x = max(0, min(self.sw - 1, self._x + dx))
        self._y = max(0, min(self.sh - 1, self._y + dy))
        self.sink.on_pointer(self._x, self._y, dx, dy)
