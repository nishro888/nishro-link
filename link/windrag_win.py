"""Windows: watch windows being dragged, so one can be thrown at another
computer.

Stage 4's other half (docs/moving-windows.md section 4.6). Windows says when
any window starts and stops being dragged - EVENT_SYSTEM_MOVESIZESTART and
EVENT_SYSTEM_MOVESIZEEND, through SetWinEventHook - but nothing in between,
so where the window has got to is read off it while the drag lasts. The
decision is windrag.Drags, which knows nothing of Windows.

The hook must be installed on a thread with a message loop (WINEVENT_
OUTOFCONTEXT delivers through its message queue), so this is started on the
tray's thread, which has one. It holds no window of anyone's and changes
nothing: it reads where a window is, and when a window has been thrown it
asks the node, exactly as the tray's menu does.

P1 still applies: nothing here may block or slow the person's own input. A
hook callback that raised would otherwise go back into Windows, so every one
of them is wrapped - a watcher that has given up is better than a tray that
has died.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time

from . import windrag

EVENT_SYSTEM_MOVESIZESTART = 0x000A
EVENT_SYSTEM_MOVESIZEEND = 0x000B
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0
WM_CANCELMODE = 0x001F
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77

# How often the dragged window's place is read. Windows says nothing between
# the start and the end of a drag, and a hook on every location change fires
# for every window on the machine. Sixty milliseconds is far below what a
# hand can do and costs one call to GetWindowRect.
LOOK_EVERY_S = 0.06

WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND,
                                  wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)


class Watcher:
    """Windows' side of throwing a window at another computer.

    `doorways()` and `ask(hwnd, device)` are windrag.Drags's and are given
    straight to it. Nothing is watched until start() is called, and start()
    must be called on a thread with a message loop.
    """

    def __init__(self, doorways, ask, log=None):
        self.log = log or (lambda *_: None)
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.u.SetWinEventHook.restype = wt.HANDLE
        self.u.GetWindowRect.argtypes = [wt.HWND, ctypes.c_void_p]
        self.drags = windrag.Drags(doorways=doorways, ask=ask,
                                   end_drag=self._end_drag, log=self.log)
        self._proc = WINEVENTPROC(self._event)      # kept: Windows calls it
        self._hook = None
        self._looking = None
        self._stop = threading.Event()

    # ----------------------------------------------------------- the hook
    def start(self) -> bool:
        """Install the hook. Must be on a thread with a message loop."""
        if self._hook:
            return True
        self._hook = self.u.SetWinEventHook(
            EVENT_SYSTEM_MOVESIZESTART, EVENT_SYSTEM_MOVESIZEEND,
            None, self._proc, 0, 0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
        if not self._hook:
            self.log("could not watch for windows being dragged, so a window "
                     "can only be sent from the tray or by Win+Shift+arrow")
            return False
        return True

    def stop(self) -> None:
        self._stop.set()
        hook, self._hook = self._hook, None
        if hook:
            try:
                self.u.UnhookWinEvent(hook)
            except Exception:
                pass

    def _event(self, _hook, event, hwnd, obj, _child, _thread, _time):
        """Called by Windows on the tray's thread. Must never raise."""
        try:
            if obj != OBJID_WINDOW or not hwnd:
                return
            if event == EVENT_SYSTEM_MOVESIZESTART:
                if self.drags.began(int(hwnd)):
                    self._look()
            elif event == EVENT_SYSTEM_MOVESIZEEND:
                self.drags.ended()
        except Exception as e:
            try:
                self.log(f"watching a window being dragged went wrong: {e!r}")
            except Exception:
                pass

    # ------------------------------------------------- while it is dragged
    def _look(self) -> None:
        """Read where the window is until the drag ends.

        On a thread of its own: this must not be done in the hook callback,
        which runs on the tray's thread between its own messages, and a
        sleep there would stop the tray answering.
        """
        if self._looking is not None and self._looking.is_alive():
            return
        self._looking = threading.Thread(target=self._keep_looking,
                                         daemon=True, name="window-drag")
        self._looking.start()

    def _keep_looking(self) -> None:
        try:
            while not self._stop.is_set():
                hwnd = self.drags.hwnd
                if hwnd is None:
                    return
                place = self._rect(hwnd)
                if place is None:
                    return                   # the window has gone
                if self.drags.moved(place):
                    return                   # it is on its way
                time.sleep(LOOK_EVERY_S)
        except Exception as e:
            self.log(f"following a dragged window went wrong: {e!r}")

    # ---------------------------------------------------------- the system
    def _rect(self, hwnd):
        """Where the window is, in LOCAL pixels.

        Windows counts from the primary screen's corner, so with a monitor
        to the left of this one every x there is negative; local coordinates
        - the ones the wire, the desk and inject.py all speak - count from
        the corner of the whole desktop. Measured on the laptop: two
        monitors, the virtual desktop 3286x1080 starting at (-1920, -148).
        Without this the borders would be out by the whole of a screen.
        """
        r = wt.RECT()
        if not self.u.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
            return None
        ox = self.u.GetSystemMetrics(SM_XVIRTUALSCREEN)
        oy = self.u.GetSystemMetrics(SM_YVIRTUALSCREEN)
        return (r.left - ox, r.top - oy, r.right - r.left, r.bottom - r.top)

    def _end_drag(self, hwnd) -> None:
        """Take the window out of the window manager's move loop.

        WM_CANCELMODE, which is what Windows itself sends to end a modal
        loop; the window goes back to where it was picked up rather than
        being left hanging off the edge of the screen. Escape would do it
        too, but Escape is a key the program would also see.
        """
        self.u.SendMessageW(wt.HWND(hwnd), WM_CANCELMODE, 0, 0)
