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
import os
import selectors
import threading
import time

from .inject import VIRTUAL_DEVICE_NAME

KEY_LEFTCTRL, KEY_RIGHTCTRL = 29, 97
KEY_A, BTN_LEFT, REL_X = 30, 272, 0   # for should_capture, which takes no evdev
# A keyboard's media keys - mute, volume, play, next, previous, stop, and
# brightness with them - usually come on a device of their own ("... Consumer
# Control"), with no letter keys. Not read, they always acted on THIS
# computer, wherever the pointer was. Reported on the AIO. Such a device is
# known by its sound and playback keys: brightness alone is the display's own
# device ("Video Bus"), which is not a keyboard and is left alone.
MEDIA_KEYS = frozenset({113, 114, 115, 163, 164, 165, 166})
# ...but never a device that also carries power, sleep or wake: grabbed while
# the pointer is elsewhere, this computer's power button would go with it.
SYSTEM_KEYS = frozenset({116, 142, 143})      # KEY_POWER, KEY_SLEEP, KEY_WAKEUP
RESCAN_S = 2.0      # how often to look for a keyboard or mouse that has just appeared


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
    if KEY_A in key_codes or REL_X in rel_codes or BTN_LEFT in key_codes:
        return True
    keys = set(key_codes)
    return bool(keys & MEDIA_KEYS) and not keys & SYSTEM_KEYS


class LinuxCapture:
    def __init__(self, screen=(1920, 1080)):
        self.sink = None
        self.sw, self.sh = screen
        self._sup_mouse = False
        self._sup_kb = False
        self._x, self._y = self.sw // 2, self.sh // 2   # our own idea of the pointer
        self._devs = []
        self._paths = {}          # /dev/input/eventN -> the device open there
        self._skipped = {}        # path -> inode: looked at, not a keyboard or mouse
        self._lock = threading.Lock()   # the device list: the reader vs set_suppress
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
        """Open every keyboard and mouse not open already: at start, and every
        RESCAN_S after it. Once was not enough - a keyboard that appears later
        (a wireless one waking, a Bluetooth one connecting after login, one
        plugged back in) was never read, so its keys typed here while the
        pointer was on another computer. Seen on the AIO.

        glob directly rather than evdev.list_devices(), which silently hides
        nodes we cannot read - making a permission problem invisible."""
        from evdev import InputDevice, ecodes as ec
        present = sorted(glob.glob("/dev/input/event*"))
        for path in list(self._skipped):
            if path not in present:
                del self._skipped[path]
        for path in present:
            if path in self._paths:
                continue
            ino = _inode(path)
            if ino is not None and self._skipped.get(path) == ino:
                continue                 # the same node as last time: still not ours
            try:
                d = InputDevice(path)
            except PermissionError:
                self._perm_denied = True
                continue
            except OSError:
                continue
            try:
                caps = d.capabilities()
                wanted = should_capture(d.name, caps.get(ec.EV_KEY, []),
                                        caps.get(ec.EV_REL, []))
            except Exception:
                wanted = False
            if not wanted:
                self._skipped[path] = ino
                _close(d)
                continue
            with self._lock:
                self._devs.append(d)
                self._paths[path] = d
                self._sel.register(d, selectors.EVENT_READ)
                if self._grabbed:        # arrived while input is going elsewhere
                    try:
                        d.grab()
                    except Exception:
                        pass

    def _drop(self, d):
        """A device that went away. Left registered, select() spins at 100% CPU."""
        with self._lock:
            try:
                self._sel.unregister(d)
            except (KeyError, ValueError):
                pass
            if d in self._devs:
                self._devs.remove(d)
            for path, dev in list(self._paths.items()):
                if dev is d:
                    del self._paths[path]
        _close(d)

    def _set_grab(self, on):
        with self._lock:
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
        next_scan = time.monotonic() + RESCAN_S
        while not self._stop:
            for k, _ in self._sel.select(timeout=0.2):
                try:
                    for e in k.fileobj.read():
                        self._handle(e, ec)
                except OSError:
                    self._drop(k.fileobj)                  # unplugged
                except Exception:
                    pass                                   # P4: fail open
            if time.monotonic() >= next_scan:
                next_scan = time.monotonic() + RESCAN_S
                try:
                    self._open_devices()
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


def _inode(path):
    """Which device node this is: udev makes a new one for a new device, even
    at a path an old one used."""
    try:
        return os.stat(path).st_ino
    except OSError:
        return None


def _close(d):
    try:
        d.close()
    except Exception:
        pass
