"""Windows input capture via low-level hooks (WH_MOUSE_LL / WH_KEYBOARD_LL).

Reports local physical input to a Node sink and, when told to, swallows it so it
does not also drive this machine. It decides nothing: no edges, no baton, no
protocol. That all lives in NodeCore, which is testable without a second computer.

P1 GOVERNS THIS FILE. Windows freezes ALL mouse input system-wide for as long as
a WH_MOUSE_LL callback runs, so nothing in a hook may block: no network send, no
print, no clipboard, no lock that a slow thread might hold. The sink callbacks
obey the same rule - they do arithmetic and one queued put.

P6: injected events carry LLMHF_INJECTED / LLKHF_INJECTED and are dropped here.
Without that a node reads back its own injections and claims the baton forever.

P4: any exception in a hook falls through to CallNextHookEx, passing the event
on. Failing open means a bug costs you a lost feature, not a dead keyboard.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading

from . import keymap, protocol

# DPI awareness BEFORE any metric or hook use. On a scaled display (125/150%)
# GetSystemMetrics returns LOGICAL pixels while the hook reports PHYSICAL ones,
# so every coordinate would be wrong by the scale factor.
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

WH_MOUSE_LL, WH_KEYBOARD_LL = 14, 13
WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x0200, 0x020A, 0x020E
WM_LDOWN, WM_LUP = 0x0201, 0x0202
WM_RDOWN, WM_RUP = 0x0204, 0x0205
WM_MDOWN, WM_MUP = 0x0207, 0x0208
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLMHF_INJECTED, LLKHF_INJECTED = 0x00000001, 0x00000010
VK_LCTRL, VK_RCTRL = 0xA2, 0xA3

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t

_BUTTONS = {WM_LDOWN: ("left", True), WM_LUP: ("left", False),
            WM_RDOWN: ("right", True), WM_RUP: ("right", False),
            WM_MDOWN: ("middle", True), WM_MUP: ("middle", False)}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class WinCapture:
    def __init__(self, u=None, origin=(0, 0)):
        self.sink = None
        self._u = u or ctypes.windll.user32
        # Positions go to the link relative to the DESKTOP's corner (see
        # desktop.py): with a monitor to the left of the primary, the OS reports
        # negative x there, and the link's screen starts at 0.
        self._ox, self._oy = int(origin[0]), int(origin[1])
        # The primary monitor, for parking only: its centre is always on a real
        # monitor, which the centre of the whole desktop need not be.
        self.sw = self._u.GetSystemMetrics(0) or 1920
        self.sh = self._u.GetSystemMetrics(1) or 1080
        self._sup_mouse = False
        self._sup_kb = False
        self._cursor_here = False   # is the shared cursor on OUR screen?
        self._anchor = None          # where the pointer is pinned while suppressed
        self._last = None            # last seen position, for deltas
        self._down_ctrl = set()
        self._down_keys = set()      # to tell a repeat from a fresh press
        self._mhook = self._khook = None
        self._tid = None
        self._mproc = HOOKPROC(self._mouse_proc)   # keep refs or they are GC'd
        self._kproc = HOOKPROC(self._key_proc)

    # ---- lifecycle ----
    def start(self, sink):
        self.sink = sink
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        if self._tid:
            self._u.PostThreadMessageW(self._tid, 0x0012, 0, 0)   # WM_QUIT

    def set_suppress(self, mouse: bool, keyboard: bool, cursor_here: bool = False):
        """Called several times a second, including from the P2 watchdog tick.
        Must stay cheap and must never block a hook.

        `cursor_here` says whether the shared cursor is on one of OUR screens.
        It decides where the pointer rests while suppressed, and the two cases
        want opposite things - see _handle_mouse.
        """
        was_mouse, was_here = self._sup_mouse, self._cursor_here
        self._sup_mouse = bool(mouse)
        self._sup_kb = bool(keyboard)
        self._cursor_here = bool(cursor_here)
        if not mouse:
            self._anchor = None
        elif (not was_mouse) or (was_here and not cursor_here):
            # Starting to suppress, or the cursor has just left our screen and
            # the pointer is now parked rather than driven. Re-choose where it
            # rests on the next event.
            self._anchor = None

    def set_origin(self, ox: int, oy: int) -> None:
        """The desktop's corner moved - a monitor was added or removed on the
        LEFT or ABOVE, which shifts where Windows starts counting. Positions
        reported from now on count from the new corner."""
        self._ox, self._oy = int(ox), int(oy)
        self._anchor = None

    def note_injected(self, x: int, y: int) -> None:
        """We just moved the pointer ourselves - that is its new rest position.

        Without this the anchor silently goes stale. While suppressed we hold
        the pointer at the anchor and measure every real movement against it,
        but when the cursor is on OUR screen and a remote machine is driving it,
        we also inject positions - and SetCursorPos fires no hook, so nothing
        tells us the pointer has moved. The next real event, even a touchpad
        twitch of one pixel, then measures against an anchor hundreds of pixels
        away, reports that as genuine movement, and claims the baton. Two
        machines doing that to each other oscillate forever, which is exactly
        what the first long hardware run did.
        """
        self._anchor = (int(x) + self._ox, int(y) + self._oy)   # link -> OS
        self._last = self._anchor

    # ---- hooks ----
    def _run(self):
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        u = self._u
        u.SetWindowsHookExW.restype = wt.HHOOK
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
        u.CallNextHookEx.restype = LRESULT
        u.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
        self._mhook = u.SetWindowsHookExW(WH_MOUSE_LL, self._mproc, None, 0)
        self._khook = u.SetWindowsHookExW(WH_KEYBOARD_LL, self._kproc, None, 0)
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        for h in (self._mhook, self._khook):
            if h:
                u.UnhookWindowsHookEx(h)

    def _mouse_proc(self, nCode, wParam, lParam):
        try:
            if nCode == 0:
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (ms.flags & LLMHF_INJECTED):      # P6
                    if self._handle_mouse(wParam, ms):
                        return 1
        except Exception:
            pass                                          # P4: fail open
        return self._u.CallNextHookEx(None, nCode, wParam, lParam)

    def _handle_mouse(self, msg, ms) -> bool:
        """True to swallow the event."""
        if msg == WM_MOUSEMOVE:
            if not self._sup_mouse:
                last = self._last or (ms.pt.x, ms.pt.y)
                self._last = (ms.pt.x, ms.pt.y)
                self.sink.on_pointer(ms.pt.x - self._ox, ms.pt.y - self._oy,
                                     ms.pt.x - last[0], ms.pt.y - last[1])
                return False
            if self._anchor is None:
                if self._cursor_here:
                    # The cursor is on this screen and someone else is placing
                    # it. Rest wherever it already is; moving it would fight
                    # them, and note_injected keeps this current as they drive.
                    self._anchor = (ms.pt.x, ms.pt.y)
                else:
                    # Our pointer is parked and means nothing - the cursor is on
                    # another machine. Park it in the MIDDLE, because we measure
                    # movement as a distance from the anchor and then pin the
                    # pointer back: resting against a screen edge makes the
                    # direction pointing off-screen produce a delta of zero
                    # forever, so the far cursor could only ever travel the
                    # other way. Crossing out leaves the pointer exactly there,
                    # on the edge, so this is the common case, not a corner one.
                    self._anchor = (self.sw // 2, self.sh // 2)
                    self._u.SetCursorPos(*self._anchor)
                self._last = self._anchor
                return True
            ax, ay = self._anchor
            dx, dy = ms.pt.x - ax, ms.pt.y - ay
            if dx or dy:
                self._u.SetCursorPos(ax, ay)              # pin it back
                self.sink.on_motion(dx, dy)
            return True

        if msg in _BUTTONS:
            name, down = _BUTTONS[msg]
            self.sink.on_button(name, down)
        elif msg == WM_MOUSEWHEEL:
            self.sink.on_wheel(0, _wheel_delta(ms.mouseData))
        elif msg == WM_MOUSEHWHEEL:
            self.sink.on_wheel(_wheel_delta(ms.mouseData), 0)
        return self._sup_mouse

    def _key_proc(self, nCode, wParam, lParam):
        try:
            if nCode == 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (kb.flags & LLKHF_INJECTED):      # P6
                    if self._handle_key(wParam, kb):
                        return 1
        except Exception:
            pass                                          # P4: fail open
        return self._u.CallNextHookEx(None, nCode, wParam, lParam)

    def _handle_key(self, msg, kb) -> bool:
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        vk = kb.vkCode
        if vk in (VK_LCTRL, VK_RCTRL):
            self._down_ctrl.add(vk) if down else self._down_ctrl.discard(vk)
            if len(self._down_ctrl) == 2:                 # failsafe: both Ctrls
                self.sink.on_failsafe()
                return False                              # never swallow the escape
        # KBDLLHOOKSTRUCT carries no "this is a repeat" flag, so we work it out:
        # a keydown for a key we already hold IS the auto-repeat. Forwarding those
        # as fresh presses is what made a held key type exactly one character on
        # the other machine.
        if down:
            value = protocol.KEY_REPEAT if vk in self._down_keys else protocol.KEY_DOWN
            self._down_keys.add(vk)
        else:
            value = protocol.KEY_UP
            self._down_keys.discard(vk)

        code = keymap.vk_to_evdev(vk)
        if code is not None:
            self.sink.on_key(code, value)
        return self._sup_kb


def _wheel_delta(mouse_data: int) -> int:
    hi = (mouse_data >> 16) & 0xFFFF
    if hi >= 0x8000:
        hi -= 0x10000
    return hi // 120
