"""GNOME's half of a moved window: one window's picture and input, through
the desktop portal.

docs/moving-windows.md, section 4.2 (stage 5). Wayland lets no program see,
move or hide another program's window, so nothing here does. GNOME's own
dialog asks the person which window to share and whether the other computer
may use it - the RemoteDesktop portal, with a ScreenCast source on the same
session - and from then on:

* the window's pictures come from PipeWire, through GStreamer
  (pipewiresrc -> videoconvert -> an appsink, BGRx: the same four bytes a
  pixel as Windows' BGRA, so everything after the capture is shared);
* the other computer's pointer and keys go in through that same session,
  the pointer in the window's own coordinates.

What follows from Wayland's rules, and is said plainly where it shows:

* **The window is not hidden here**: it shows on both screens (the
  maintainer's choice, 2026-10-13). Clicks are real pointer events on this
  screen, so they land on it only where nothing here covers it.
* **Keys go to whichever window has the focus here** - a click in the frame
  first gives the window the focus, as a click here would.
* **Its title is not known**: the portal does not say.
* **The frame cannot resize or close it**: no program may do either to
  another's window. Closing the frame ends the sharing instead.

Only what the package depends on: python3-gi for D-Bus (Gio), GStreamer's
bindings with its PipeWire element (gir1.2-gstreamer-1.0,
gstreamer1.0-pipewire, gstreamer1.0-plugins-base). Not gst-plugins-base's own
Python bindings (GstApp, GstVideo), which the AIO did not have: the appsink
is driven by its action signals, which core GStreamer's bindings reach.

Everything that talks to GNOME is in Portal and Stream; Window, which is
what winmove.HomeSession drives, only uses them - so the tests replace those
two and run anywhere, and what only GNOME can answer is checked on the AIO.
"""
from __future__ import annotations

import os
import re
import threading

PICK = "pick"                # the window is chosen in GNOME's dialog, not here
CHOOSE_S = 180.0             # how long the person has to choose
FIRST_PICTURE_S = 10.0       # and the first picture to come, after that
CALL_S = 10.0                # any other call to the portal

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
RD = "org.freedesktop.portal.RemoteDesktop"
SC = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"

KEYBOARD, POINTER = 1, 2                 # RemoteDesktop's device types
WINDOW = 2                               # ScreenCast's source types: one window
CURSOR_HIDDEN = 1                        # the other computer draws its own
CANCELLED = 1                            # a Response: the person said no
# Pointer buttons as evdev's BTN_LEFT, BTN_RIGHT, BTN_MIDDLE: the portal
# takes evdev codes, and so does everything else in Nishro Link.
BUTTONS = {"left": 0x110, "right": 0x111, "middle": 0x112}
# NotifyPointerAxisDiscrete's axes. In mutter a positive step scrolls DOWN
# (or right) and a negative one up (or left) - read in its source
# (discrete_steps_to_scroll_direction) - while the frame's dy is positive
# for UP, as Windows' wheel is. So the vertical steps are turned over.
VERTICAL, HORIZONTAL = 0, 1


class Declined(Exception):
    """The window was not shared: the person said no in GNOME's dialog, did
    not answer, or GNOME would not. The message says which, for the person."""


def ref(which):
    """`which` as this module takes it. GNOME's dialog chooses the window, so
    there is nothing to look up: "pick", or "pick-<something>" to tell two
    shared at once apart (window_host passes what the tray asked for)."""
    return str(which)


# ------------------------------------------------------------ can it be done
def _gi():
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gio, GLib, Gst
    return Gio, GLib, Gst


def possible() -> bool:
    """Whether this system could share a window at all, as far as can be
    told WITHOUT the person's session: the service asks, and it runs as root
    outside every session, with no portal to talk to. Cheap - it loads no
    library. The real answer comes from why_not(), in the session."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        gi.require_version("Gst", "1.0")
        return True
    except (ImportError, ValueError):
        return False


def why_not(which, own_pid=None) -> str:
    """Why a window cannot be shared from here, or "" if it can. Asked in the
    person's session, where the portal is. Every reason names what is
    missing, so the person can do something about it."""
    if not str(which).startswith(PICK):
        return "on this desktop the window is chosen in its own dialog"
    try:
        Gio, GLib, Gst = _gi()
    except (ImportError, ValueError):
        return ("GStreamer's part for Python is not installed here "
                "(gir1.2-gstreamer-1.0)")
    if not Gst.is_initialized():
        Gst.init(None)
    for element, package in (("pipewiresrc", "gstreamer1.0-pipewire"),
                             ("videoconvert", "gstreamer1.0-plugins-base"),
                             ("appsink", "gstreamer1.0-plugins-base")):
        if Gst.ElementFactory.find(element) is None:
            return f"GStreamer's {element} is not installed here ({package})"
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        sources = _property(bus, SC, "AvailableSourceTypes")
        devices = _property(bus, RD, "AvailableDeviceTypes")
    except Exception:
        return ("this desktop has no screen-sharing portal "
                "(xdg-desktop-portal with its GNOME part)")
    if not int(sources) & WINDOW:
        return "this desktop's portal cannot share a single window"
    if int(devices) & (KEYBOARD | POINTER) != (KEYBOARD | POINTER):
        return "this desktop's portal cannot pass on a pointer and keys"
    return ""


def _property(bus, iface, name):
    from gi.repository import Gio, GLib
    answer = bus.call_sync(BUS, PATH, "org.freedesktop.DBus.Properties", "Get",
                           GLib.Variant("(ss)", (iface, name)),
                           GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE,
                           int(CALL_S * 1000), None)
    return answer.unpack()[0]


_MARK = None


def shows_a_frame(frame) -> bool:
    """Is this picture one of Nishro Link's own frames - another computer's
    window, shown here? GNOME's dialog does not say which window was
    chosen, so the frame's mark (winmove.FRAME_MARK) is looked for in the
    picture itself: BGRx, the fourth byte of each pixel left open."""
    global _MARK
    if _MARK is None:
        from . import winmove
        _MARK = re.compile(b"".join(re.escape(bytes((b, g, r))) + b"."
                                    for r, g, b in winmove.FRAME_MARK), re.S)
    return frame is not None and _MARK.search(frame[2]) is not None


def movable(own_pid=None):
    """What can be offered from here: one entry, since GNOME's dialog picks
    the window - (which, title, class) like window_win's."""
    return [(PICK, "a window you choose", "")]


def restore_everything(rescue, log=None) -> int:
    """Nothing to put back after a crash: no window is ever hidden here."""
    return 0


# ------------------------------------------------------------------ GNOME
class Portal:
    """One RemoteDesktop session with a ScreenCast source on it, over the
    session bus: everything this module asks of GNOME.

    The calls that wait - for GNOME, or for the person - answer through a
    Request object, whose Response signal carries the result. Signals are
    delivered on a GLib main loop, which runs on a thread of its own here.
    The Response is subscribed to BEFORE the call is made, on the path the
    portal will use (it is made from our name and a token we choose), so an
    answer that comes back at once is never missed.
    """

    def __init__(self, log=None):
        Gio, GLib, _ = _gi()
        self.Gio, self.GLib = Gio, GLib
        self.log = log or (lambda *_: None)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._me = (self.bus.get_unique_name() or "").lstrip(":").replace(".", "_")
        self._n = 0
        self._loop = GLib.MainLoop()
        # Wait until the loop really runs: a quit() that came before run()
        # would be lost, and the loop would then run on for ever (the AIO's
        # check found it not yet running the moment after it was started).
        running = threading.Event()
        GLib.idle_add(lambda: running.set() or False)
        threading.Thread(target=self._loop.run, daemon=True,
                         name="portal-signals").start()
        running.wait(2.0)
        self.session = None
        self.closed = threading.Event()       # by us, by GNOME, or the person
        self._closed_sub = None

    def _token(self) -> str:
        self._n += 1
        return f"nishro{os.getpid()}_{self._n}"

    def _ask(self, iface, method, signature, args, options, timeout):
        """Call `method`, whose answer comes later, and wait for it: the
        results, or Declined."""
        V = self.GLib.Variant
        token = self._token()
        path = f"{PATH}/request/{self._me}/{token}"
        got, done = {}, threading.Event()

        def answered(_bus, _sender, _path, _iface, _signal, params, *_):
            got["code"], got["results"] = params.unpack()
            done.set()

        sub = self.bus.signal_subscribe(BUS, REQUEST, "Response", path, None,
                                        self.Gio.DBusSignalFlags.NONE, answered)
        try:
            options = dict(options, handle_token=V("s", token))
            self.bus.call_sync(BUS, PATH, iface, method,
                               V(signature, (*args, options)),
                               self.GLib.VariantType.new("(o)"),
                               self.Gio.DBusCallFlags.NONE,
                               int(CALL_S * 1000), None)
            if not done.wait(timeout):
                self._close(path, REQUEST)
                raise Declined("GNOME's dialog was not answered in time")
        finally:
            self.bus.signal_unsubscribe(sub)
        if got["code"] == CANCELLED:
            raise Declined("the window was not shared - GNOME's dialog was "
                           "cancelled")
        if got["code"] != 0:
            raise Declined("GNOME would not share the window")
        return got["results"]

    def open(self, parent: str = ""):
        """Ask, in GNOME's dialog, for one window and the use of it. Blocks
        until the person answers. (node, its properties, devices granted)."""
        V = self.GLib.Variant
        got = self._ask(RD, "CreateSession", "(a{sv})", (),
                        {"session_handle_token": V("s", self._token())}, CALL_S)
        self.session = str(got["session_handle"])
        self._closed_sub = self.bus.signal_subscribe(
            BUS, SESSION, "Closed", self.session, None,
            self.Gio.DBusSignalFlags.NONE, lambda *_: self.closed.set())
        self._ask(RD, "SelectDevices", "(oa{sv})", (self.session,),
                  {"types": V("u", KEYBOARD | POINTER)}, CALL_S)
        self._ask(SC, "SelectSources", "(oa{sv})", (self.session,),
                  {"types": V("u", WINDOW), "multiple": V("b", False),
                   "cursor_mode": V("u", CURSOR_HIDDEN)}, CALL_S)
        got = self._ask(RD, "Start", "(osa{sv})", (self.session, parent), {},
                        CHOOSE_S)
        streams = got.get("streams") or []
        if not streams:
            raise Declined("GNOME shared no window")
        node, props = streams[0]
        return int(node), dict(props or {}), int(got.get("devices") or 0)

    def pipewire(self) -> int:
        """A connection to PipeWire that can see the shared window: a file
        descriptor, passed over the bus (ScreenCast.OpenPipeWireRemote)."""
        V = self.GLib.Variant
        answer, fds = self.bus.call_with_unix_fd_list_sync(
            BUS, PATH, SC, "OpenPipeWireRemote", V("(oa{sv})", (self.session, {})),
            self.GLib.VariantType.new("(h)"), self.Gio.DBusCallFlags.NONE,
            int(CALL_S * 1000), None, None)
        return fds.get(answer.unpack()[0])

    # Input: sent and not waited for, in order - nothing a person does may
    # wait on the bus. Dropped once the session has ended.
    def _tell(self, method, signature, *args):
        if self.session is None or self.closed.is_set():
            return
        self.bus.call(BUS, PATH, RD, method,
                      self.GLib.Variant(signature, (self.session, {}, *args)),
                      None, self.Gio.DBusCallFlags.NONE, -1, None, None, None)

    def pointer(self, node: int, x: float, y: float) -> None:
        self._tell("NotifyPointerMotionAbsolute", "(oa{sv}udd)", node,
                   float(x), float(y))

    def button(self, code: int, down: bool) -> None:
        self._tell("NotifyPointerButton", "(oa{sv}iu)", code, 1 if down else 0)

    def scroll(self, axis: int, steps: int) -> None:
        self._tell("NotifyPointerAxisDiscrete", "(oa{sv}ui)", axis, steps)

    def key(self, code: int, down: bool) -> None:
        self._tell("NotifyKeyboardKeycode", "(oa{sv}iu)", code, 1 if down else 0)

    def _close(self, path, iface):
        try:
            self.bus.call_sync(BUS, path, iface, "Close", None, None,
                               self.Gio.DBusCallFlags.NONE,
                               int(CALL_S * 1000), None)
        except Exception:
            pass                      # already gone: that is what was wanted

    def close(self) -> None:
        """End the sharing. GNOME's indicator in the top bar goes with it."""
        if self.session is not None and not self.closed.is_set():
            self._close(self.session, SESSION)
        self.closed.set()
        if self._closed_sub is not None:
            self.bus.signal_unsubscribe(self._closed_sub)
            self._closed_sub = None
        self._loop.quit()


class Stream:
    """The shared window's pictures, from PipeWire: always the newest one,
    never a queue (the appsink keeps one and drops the rest, as winmove's
    pacing wants)."""

    def __init__(self, fd: int, node: int, log=None):
        _, _, Gst = _gi()
        if not Gst.is_initialized():
            Gst.init(None)
        self.Gst = Gst
        self.log = log or (lambda *_: None)
        self.fd = int(fd)
        self.pipeline = Gst.parse_launch(
            f"pipewiresrc fd={self.fd} path={int(node)} "
            f"! videoconvert ! video/x-raw,format=BGRx "
            f"! appsink name=sink max-buffers=1 drop=true sync=false")
        self.sink = self.pipeline.get_by_name("sink")
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.stop()
            raise Declined("the window's pictures could not be started")
        self.ended = None

    def newest(self, wait: float = 0.0):
        """(w, h, BGRx bytes, top row first) of a picture newer than the last
        one asked for - or None if there is none yet. Waits up to `wait`."""
        Gst = self.Gst
        sample = self.sink.emit("try-pull-sample", int(wait * Gst.SECOND))
        self._watch()
        return _frame(sample) if sample is not None else None

    def _watch(self) -> None:
        bus = self.pipeline.get_bus()
        while self.ended is None:
            msg = bus.pop_filtered(self.Gst.MessageType.ERROR
                                   | self.Gst.MessageType.EOS)
            if msg is None:
                return
            if msg.type == self.Gst.MessageType.EOS:
                self.ended = "the window was closed, or the sharing stopped"
            else:
                err, _debug = msg.parse_error()
                self.ended = f"the window's pictures stopped: {err.message}"

    def stop(self) -> None:
        """Stop the pictures and close our end of PipeWire - once: closed
        twice, the number could by then be some other file of this process
        (pipewiresrc works on a copy of it, so ours is ours to close)."""
        try:
            self.pipeline.set_state(self.Gst.State.NULL)
        finally:
            fd, self.fd = self.fd, -1
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _frame(sample):
    """A GStreamer sample as (w, h, BGRx bytes) with no padding between rows,
    or None if it is not a picture we can use."""
    s = sample.get_caps().get_structure(0)
    w, h = int(s.get_value("width") or 0), int(s.get_value("height") or 0)
    buf = sample.get_buffer()
    data = buf.extract_dup(0, buf.get_size())
    return unpad(w, h, bytes(data))


def unpad(w: int, h: int, data: bytes):
    """Rows of `w` pixels, four bytes each, from rows that may be padded."""
    row = w * 4
    if w <= 0 or h <= 0 or len(data) < row * h:
        return None
    stride = len(data) // h
    if stride == row:
        return w, h, data[:row * h]
    return w, h, b"".join(data[y * stride:y * stride + row] for y in range(h))


# ------------------------------------------------------------------ the window
class Window:
    """A window of another program on this screen, shared through the portal
    - what winmove.HomeSession needs of a window, as far as Wayland allows.

    Made, it opens GNOME's dialog and waits for the person (window_host does
    this before saying it is ready, so a "no" reaches both computers as the
    reason). `portal` and `stream` are for the tests: things that act as
    Portal and Stream do.
    """

    def __init__(self, which=PICK, rescue=None, log=None, portal=None,
                 stream=None):
        self.log = log or (lambda *_: None)
        self.portal = portal if portal is not None else Portal(log=self.log)
        self.stream = None
        try:
            self.node, props, devices = self.portal.open()
            if devices & (KEYBOARD | POINTER) != (KEYBOARD | POINTER):
                self.log("GNOME shares the window's picture but not the use "
                         "of it, so it can be watched over there, not used")
            self.stream = (stream if stream is not None
                           else Stream(self.portal.pipewire(), self.node,
                                       log=self.log))
            self._frame = self.stream.newest(wait=FIRST_PICTURE_S)
            if self._frame is None:
                raise Declined("no picture came from the shared window")
            if shows_a_frame(self._frame):
                raise Declined("that is a window from another computer, shown "
                               "here - it can only go back there")
        except BaseException:
            if self.stream is not None:
                self.stream.stop()
            self.portal.close()
            raise
        # The pointer is placed in the stream's LOGICAL coordinates, which
        # are the picture's pixels divided by this screen's scale (200%:
        # half). The scale does not change as the window is resized, so it
        # is worked out once, from the size GNOME gave and the first picture.
        size = props.get("size") or ()
        self._scale = 1.0
        if len(size) == 2 and size[0] and self._frame[0]:
            scale = float(size[0]) / float(self._frame[0])
            if 0.2 <= scale <= 5.0:
                self._scale = scale
        self.hidden = False
        self._held = set()            # buttons and keys the far side holds
        self._held_lock = threading.Lock()

    # --------------------------------------------------------------- looking
    def alive(self) -> bool:
        return not self.portal.closed.is_set() and self.stream.ended is None

    def title(self) -> str:
        return "a window"             # the portal does not say which one

    def size(self):
        """The size of the picture capture() gives next.

        HomeSession asks for the size, then for the picture, and the two must
        agree - the picture is cut into squares by that size. The newest
        picture is taken in capture(), on the session's own thread, so asking
        for the size from another thread (the pointer's bounds) cannot slip a
        picture of another size in between."""
        return self._frame[0], self._frame[1]

    def capture(self):
        """The newest picture, as BGRx - the same size size() just said. A
        picture of a new size (the window was resized here) is kept for the
        next round: size() says it then, and this one goes out as it was."""
        if not self.alive():
            return None
        newer = self.stream.newest()
        if newer is not None:
            if newer[:2] == self._frame[:2]:
                self._frame = newer
            else:
                was = self._frame
                self._frame = newer
                return was[2]
        return self._frame[2]

    # ------------------------------------------------------- hiding and back
    def hide(self) -> bool:
        """Wayland lets no program hide another's window: it stays on this
        screen as well (window_host says so in the log)."""
        return False

    def show(self) -> None:
        """The window comes home - here, it never left: let go of whatever
        the other computer holds down, and end the sharing."""
        self._let_go()
        try:
            self.portal.close()
        finally:
            self.stream.stop()

    # --------------------------------------------------------------- driving
    def resize(self, w, h) -> None:
        """No program may resize another's window on Wayland; the frame
        shows the window at the size it has here."""

    def close(self) -> None:
        """No program may close another's window on Wayland. The frame's
        close button ends the sharing instead: alive() goes false and the
        frame closes over there."""
        self.portal.close()

    def move(self, x, y) -> None:
        self.portal.pointer(self.node, x * self._scale, y * self._scale)

    def button(self, name, down) -> None:
        code = BUTTONS.get(name)
        if code is not None:
            self._hold(("btn", code), down)
            self.portal.button(code, bool(down))

    def wheel(self, dx, dy) -> None:
        if dy:
            self.portal.scroll(VERTICAL, -int(dy))      # see VERTICAL
        if dx:
            self.portal.scroll(HORIZONTAL, int(dx))

    def key(self, code, down) -> None:
        """A key, as the evdev code Nishro Link speaks everywhere - and the
        code GNOME takes. It goes to the window that has the focus here."""
        self._hold(("key", int(code)), down)
        self.portal.key(int(code), bool(down))

    def _hold(self, what, down) -> None:
        with self._held_lock:
            if down:
                self._held.add(what)
            else:
                self._held.discard(what)

    def _let_go(self) -> None:
        """Nothing may be left held down when the sharing ends: here the keys
        and buttons are real ones, of this computer's own desktop."""
        with self._held_lock:
            held, self._held = list(self._held), set()
        for kind, code in held:
            try:
                if kind == "btn":
                    self.portal.button(code, False)
                else:
                    self.portal.key(code, False)
            except Exception:
                pass
