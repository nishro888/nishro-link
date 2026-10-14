"""Windows: a real window, dragged by its real title bar (windrag_win.py).

The hook, the measurement in local pixels, and taking the window out of the
move loop - against a Notepad of this test's own. The drag is injected
because there is nobody here to do it by hand; the window manager's move
loop cannot tell the difference, and what is under test is the hook and the
arithmetic, not how the button went down.

The pointer is put back where it was and the button is let go however a test
ends, and the Notepad is closed.
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

if sys.platform == "win32":
    from link import windrag_win
    from link.inject import WindowsInjector
    from link.window_win import movable, pid_of, rect_of

SM_XVIRTUAL, SM_YVIRTUAL, SM_CXVIRTUAL, SM_CYVIRTUAL = 76, 77, 78, 79
SWP_NOZORDER = 0x0004


class Pumped:
    """A Watcher with a message loop, on one thread, as the tray has.

    THE HOOK AND THE PUMP MUST BE THE SAME THREAD. WINEVENT_OUTOFCONTEXT
    posts to the queue of the thread that installed the hook, so installing
    it on one thread and pumping on another means not one event arrives -
    which looks exactly like a hook that does not work.
    """

    def __init__(self, doorways):
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.asked = []
        self.ended = []
        self.said = []
        self.hooked = threading.Event()
        self.ok = False
        self._stop = threading.Event()
        self.watcher = None
        self._doorways = doorways
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="drag-watch")
        self._thread.start()
        self.hooked.wait(10)

    def _run(self):
        self.watcher = windrag_win.Watcher(
            doorways=self._doorways, ask=self._ask, log=self.said.append)
        real_end = self.watcher._end_drag

        def end(hwnd):
            self.ended.append(hwnd)
            real_end(hwnd)
        self.watcher._end_drag = end
        self.watcher.drags.end_drag = end
        self.ok = self.watcher.start()
        self.hooked.set()
        msg = wt.MSG()
        while not self._stop.is_set():
            while self.u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                self.u.TranslateMessage(ctypes.byref(msg))
                self.u.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)

    def _ask(self, hwnd, device):
        self.asked.append((hwnd, device))
        return ""

    def wait_for_asking(self, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self.asked:
            time.sleep(0.02)
        return list(self.asked)

    def stop(self):
        self._stop.set()
        if self.watcher is not None:
            self.watcher.stop()


def desktop():
    u = ctypes.WinDLL("user32")
    return (u.GetSystemMetrics(SM_XVIRTUAL), u.GetSystemMetrics(SM_YVIRTUAL),
            u.GetSystemMetrics(SM_CXVIRTUAL), u.GetSystemMetrics(SM_CYVIRTUAL))


def put(hwnd, x, y, w, h):
    ctypes.WinDLL("user32").SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER)
    time.sleep(0.6)


@pytest.fixture
def notepad():
    """A Notepad of our own, closed afterwards."""
    proc = subprocess.Popen(["notepad.exe"])
    hwnd = None
    try:
        for _ in range(40):
            for h, _t, _c in movable():
                if pid_of(h) == proc.pid:
                    hwnd = h
            if hwnd:
                break
            time.sleep(0.3)
        if not hwnd:
            pytest.skip("Notepad would not open on this computer")
        time.sleep(1.2)
        yield hwnd
    finally:
        subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)],
                       capture_output=True)


@pytest.fixture
def pointer():
    """The pointer, put back where the person left it."""
    inj = WindowsInjector()
    home = inj._pos()
    try:
        yield inj
    finally:
        try:
            inj.button("left", False)
        except Exception:
            pass
        inj.u.SetCursorPos(*home)


@pytest.fixture
def watching():
    """A pumped watcher, taken down however the test ends."""
    made = []

    def make(doorways):
        w = Pumped(doorways)
        made.append(w)
        if not w.ok:
            pytest.skip("windows being dragged cannot be watched here")
        return w
    try:
        yield make
    finally:
        for w in made:
            w.stop()


def on_top_at(hwnd, x, y) -> bool:
    """Is `hwnd` really the window at this point on the screen?

    The drag presses a point and trusts the title bar to be under it. Run on
    its own that is true; run with the whole suite it is not - other tests
    leave windows of their own about, and a press meant for this Notepad's
    title bar landed on one of those, so no drag began and the hook had
    nothing to report. Asked rather than assumed.
    """
    u = ctypes.WinDLL("user32")
    u.WindowFromPoint.restype = wt.HWND
    u.WindowFromPoint.argtypes = [ctypes.c_longlong]
    at = u.WindowFromPoint((y << 32) | (x & 0xFFFFFFFF))
    if not at:
        return False
    u.GetAncestor.restype = wt.HWND
    root = u.GetAncestor(at, 2)                  # GA_ROOT
    return int(root or at) == int(hwnd)


def to_the_front(hwnd, tries=4) -> bool:
    u = ctypes.WinDLL("user32")
    for _ in range(tries):
        u.SetForegroundWindow(hwnd)
        u.BringWindowToTop(hwnd)
        time.sleep(0.4)
        x, y, w, _h = rect_of(hwnd)
        if on_top_at(hwnd, x + w // 2, y + 16):
            return True
    return False


def drag_right(inj, hwnd, steps=25, stop_when=None):
    """Take the window by the middle of its title bar and drag it right.

    EVERY movement is real mouse input (SendInput, absolute), because that is
    what a mouse does and it is the only thing a window manager's move loop
    reliably follows. SetCursorPos was tried first and is not enough: the
    move loop began - the hook said so - and then the window did not follow
    the pointer at all, 0 pixels. It happened to work run on its own and
    failed under the whole suite, which is the worst way for a test to be
    wrong. move_abs is no good here either: it sends real input only on the
    first movement of a spell (the arrow fix) and SetCursorPos for the rest.
    """
    if not to_the_front(hwnd):
        pytest.skip("another window is over the title bar, so a drag of this "
                    "one cannot be pressed - nothing to do with the code "
                    "under test")
    x, y, w, _h = rect_of(hwnd)
    gx, gy = x + w // 2, y + 16
    inj._as_real_mouse_input(gx, gy)
    inj.u.SetCursorPos(gx, gy)              # the 0-65535 grid rounds
    time.sleep(0.4)
    inj.button("left", True)
    try:
        for step in range(1, steps + 1):
            inj._as_real_mouse_input(gx + step * 20, gy)
            time.sleep(0.06)
            if stop_when is not None and stop_when():
                break
    finally:
        inj.button("left", False)
    time.sleep(0.3)


def test_a_window_dragged_across_the_border_is_offered_to_that_computer(
        notepad, pointer, watching):
    ox, oy, dw, _dh = desktop()
    # the right-hand side of the whole desktop, in LOCAL pixels
    watch = watching(lambda: [{"side": "right", "edge": dw, "device": "aio"}])
    put(notepad, ox + dw - 700, oy + 200, 500, 360)
    was = rect_of(notepad)
    began = []
    real_began = watch.watcher.drags.began

    def began_(hwnd):
        out = real_began(hwnd)
        began.append((hwnd, out))
        return out
    watch.watcher.drags.began = began_

    drag_right(pointer, notepad, stop_when=lambda: bool(watch.asked))
    asked = watch.wait_for_asking()
    # Everything worth knowing in the message: this is a real window being
    # really dragged, so a bare "it did not happen" is no help at all.
    assert asked, (
        f"the drag was not noticed. drags the hook reported: {began}; "
        f"the window went from {was} to {rect_of(notepad)}; "
        f"the border asked for was right of {dw}; log: {watch.said}")
    assert asked[0] == (notepad, "aio")
    # Out of the move loop FIRST, so the window is not left hanging off the
    # edge of the screen under the person's hand.
    assert watch.ended == [notepad]
    now = rect_of(notepad)
    assert abs(now[0] - was[0]) < 60 and now[2:] == was[2:], (
        f"the window was left at {now}, not back at {was}")


def test_a_border_nothing_is_on_is_not_crossed(notepad, pointer, watching):
    """Dragged right while the only other computer is to the LEFT."""
    ox, oy, dw, _dh = desktop()
    watch = watching(lambda: [{"side": "left", "edge": 0, "device": "aio"}])
    put(notepad, ox + dw - 700, oy + 200, 500, 360)
    drag_right(pointer, notepad, steps=12)
    assert watch.asked == [], watch.asked


def test_a_drag_that_crosses_nothing_is_still_begun_and_ended(
        notepad, pointer, watching):
    ox, oy, _dw, _dh = desktop()
    seen = []
    watch = watching(lambda: [{"side": "left", "edge": 0, "device": "aio"}])
    drags = watch.watcher.drags
    real_began, real_ended = drags.began, drags.ended

    def began(hwnd):
        out = real_began(hwnd)
        seen.append(("began", hwnd))
        return out

    def ended():
        seen.append(("ended", None))
        return real_ended()
    drags.began, drags.ended = began, ended

    put(notepad, ox + 400, oy + 200, 500, 360)
    drag_right(pointer, notepad, steps=6)
    end = time.monotonic() + 5
    while time.monotonic() < end and not any(e[0] == "ended" for e in seen):
        time.sleep(0.02)
    assert ("began", notepad) in seen, seen
    assert ("ended", None) in seen, seen
    assert drags.hwnd is None


def test_a_window_is_measured_in_local_pixels_not_windows_own(
        notepad, pointer, watching):
    """With a monitor to the left of this one, Windows' x is negative over
    there and local x counts from the corner of the whole desktop. Measured
    on the laptop: the desktop is 3286x1080 and starts at (-1920, -148).
    Reading the window's place without that correction would put every
    border out by the whole of a screen.
    """
    ox, oy, dw, dh = desktop()
    watch = watching(lambda: [])
    x, y, w, h = ox + 100, oy + 100, 400, 300
    put(notepad, x, y, w, h)
    assert watch.watcher._rect(notepad) == (100, 100, w, h)
    # and the desktop itself is 0,0 to its own size in those pixels
    assert (dw, dh) > (0, 0)
    assert rect_of(notepad)[:2] == (x, y)
