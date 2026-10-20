"""The frame: a window on THIS computer showing one that lives on another.

docs/moving-windows.md, section 4.3. A moved window's program runs at home;
this is what the person actually looks at and uses, on the away computer. It
is an ordinary window of Nishro Link's - Tk, as the rest of the program - so
it has the away system's own title bar, can be moved and resized there, and
takes input from whatever mouse and keyboard drive that computer, including
another computer's through Nishro Link itself.

Tk is the same on Windows and Linux, so this is one piece of code for both.
It reads the pictures as they arrive (PNG, which Tk takes as it is - see
picture.py) and copies each changed rectangle into place, which costs about
0.01 ms.

ONE Tk, ONE THREAD. Tcl may only be used from the thread that made it, and a
second Tk in another thread crashes the process outright - seen while
building this, as a Windows fatal exception the moment a test made its own.
Up to four windows may be away at once (R8), so every frame is a child of
one shared root, kept by the host below, whose loop runs on a single thread
for the whole program. winmove.AwaySession calls open/draw/retitle/close
from the thread reading its channel; those only put work on a queue, which
the Tk thread picks up. What the person does goes back the other way,
through `events`.
"""
from __future__ import annotations

import queue
import sys
import threading

from . import keymap, picture

POLL_MS = 8                     # how often the Tk loop looks for work
RESIZE_SETTLE_MS = 250          # a drag of the frame's corner is many events
BUTTONS = {1: "left", 2: "middle", 3: "right"}


class _Host:
    """The one Tk root, and the one thread its loop runs on."""

    def __init__(self):
        self.work: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.failed = None
        self.root = None
        self.tk = None
        self._thread = None
        self._lock = threading.Lock()

    def start(self, log) -> bool:
        """Make sure the loop is running. False if there is no display."""
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, args=(log,),
                                                daemon=True, name="window-frames")
                self._thread.start()
        self.ready.wait(15)
        return self.failed is None

    def _run(self, log) -> None:
        try:
            import tkinter as tk
            self.tk = tk
            self.root = tk.Tk()
            self.root.withdraw()          # the root itself is never seen
        except Exception as e:
            self.failed = e
            log(f"no window could be opened here: {e}")
            self.ready.set()
            return
        self.ready.set()
        self.root.after(POLL_MS, self._pump)
        try:
            self.root.mainloop()
        except Exception as e:
            log(f"the window frames stopped: {e!r}")

    def _pump(self) -> None:
        try:
            for _ in range(64):
                job = self.work.get_nowait()
                try:
                    job()
                except Exception as e:      # one frame must not take the rest down
                    _complain(e)
        except queue.Empty:
            pass
        if self.root is not None:
            self.root.after(POLL_MS, self._pump)

    def later(self, job) -> None:
        self.work.put(job)


_HOST = _Host()
_PROBLEMS = []


def _complain(e) -> None:
    _PROBLEMS.append(repr(e))
    del _PROBLEMS[:-20]


def problems():
    """What went wrong in a frame, for the log and the tests."""
    return list(_PROBLEMS)


class Frame:
    """One moved window, shown here.

    `events` carries what the person does, as the very dicts that cross the
    channel: {"t": "in", ...}, {"t": "size", ...}, {"t": "close"},
    {"t": "home"}. `on_gone` is called once the frame has really gone, so
    whoever started it can tidy up.
    """

    def __init__(self, where: str = "", log=None, on_gone=None):
        self.where = where              # the computer the window lives on
        self.log = log or (lambda *_: None)
        self.on_gone = on_gone
        self.events: queue.Queue = queue.Queue()
        self.closed = None              # why, once it has gone
        self._top = None                # our Toplevel
        self._canvas = None
        self._picture = None            # the Tk image the window is drawn on
        self._size = None
        self._up = threading.Event()
        self._gone = False
        self._resize_job = None
        self._last_sent_size = None
        self._unknown_keys = set()
        self._keys_held = set()         # pressed here and not yet let go
        self._title = ""

    # ------------------------------------------------- what the session calls
    def open(self, title: str, w: int, h: int) -> None:
        self._title = title or ""
        self._size = (int(w), int(h))
        if not _HOST.start(self.log):
            # No display: say so, and ask for the window to come home rather
            # than leave it hidden on the other computer waiting for a frame.
            self._up.set()
            self.events.put({"t": "home"})
            return
        _HOST.later(lambda: self._build(int(w), int(h)))

    def draw(self, regions) -> None:
        _HOST.later(lambda: self._do_draw(regions))

    def retitle(self, title: str) -> None:
        self._title = title or ""
        _HOST.later(lambda: self._top and self._top.title(self._title_text()))

    def close(self, why: str = "") -> None:
        self.closed = why
        _HOST.later(lambda: self._take_away(why))

    def wait_until_open(self, timeout=15.0) -> bool:
        self._up.wait(timeout)
        return self._top is not None

    # ------------------------------------------------------------ on Tk's thread
    def _build(self, w, h) -> None:
        tk = _HOST.tk
        top = tk.Toplevel(_HOST.root)
        self._top = top
        top.title(self._title_text())
        top.geometry(f"{w}x{h}")
        top.configure(bg="#101214")
        top.minsize(120, 80)
        # A canvas, not a label: it holds the picture at a fixed place
        # without resizing itself to it, so the frame's size stays what the
        # person chose rather than whatever the far window last sent.
        self._canvas = tk.Canvas(top, highlightthickness=0, bd=0,
                                 bg="#101214", width=w, height=h)
        self._canvas.pack(fill="both", expand=True)
        self._picture = tk.PhotoImage(master=top, width=w, height=h)
        self._canvas.create_image(0, 0, anchor="nw", image=self._picture,
                                  tags="pic")
        # The size it was just told to open at counts as already sent, or the
        # first <Configure> - which arrives simply from the window appearing -
        # would tell home to resize the window to the size it already is.
        self._last_sent_size = (w, h)
        self._bind(top)
        top.protocol("WM_DELETE_WINDOW", self._asked_to_close)
        self._mark(top)
        try:
            top.focus_force()
        except Exception:
            pass
        self._up.set()

    def _mark(self, top) -> None:
        """On Windows, mark the frame as one of ours, so that it is never
        taken for a window of this computer's and moved - another computer's
        window shown back to it, or on, in a frame of a frame
        (window_win.FRAME_PROP)."""
        if sys.platform != "win32":
            return
        try:
            from . import window_win
            top.update_idletasks()
            window_win.mark_frame(int(top.wm_frame(), 16))
        except Exception as e:
            self.log(f"could not mark the frame as Nishro Link's own: {e!r}")

    def _title_text(self) -> str:
        """Always says where the window really lives (N5)."""
        name = self._title or "a window"
        return f"{name} - from {self.where}" if self.where else name

    def _resize_to(self, w, h) -> None:
        if self._size == (w, h) and self._picture is not None:
            return
        self._size = (w, h)
        self._picture = _HOST.tk.PhotoImage(master=self._top, width=w, height=h)
        self._canvas.delete("pic")
        self._canvas.create_image(0, 0, anchor="nw", image=self._picture,
                                  tags="pic")

    def _do_draw(self, regions) -> None:
        if self._top is None:
            return
        for x, y, w, h, codec, data in regions:
            if codec != "png":
                self.log(f"a picture arrived as {codec}, which this frame cannot show")
                continue
            was = picture.size(data)
            if was != (w, h):
                # The header said one size and the bytes are another: draw
                # nothing rather than something misplaced.
                self.log(f"a {w}x{h} piece of the window arrived as {was}")
                continue
            if self._size and (x + w > self._size[0] or y + h > self._size[1]):
                self._resize_to(max(x + w, self._size[0]),
                                max(y + h, self._size[1]))
            patch = _HOST.tk.PhotoImage(master=self._top, data=data)
            # `copy` is Tk's own blit: about 0.01 ms for a square of a
            # window. The patch is only a carrier - once its pixels are in
            # the frame's own image it can go.
            self._picture.tk.call(self._picture, "copy", patch, "-to", x, y)

    def _take_away(self, why) -> None:
        if self._gone:
            return
        self._gone = True
        top, self._top = self._top, None
        self._canvas = self._picture = None
        if top is not None:
            try:
                top.destroy()
            except Exception as e:
                _complain(e)
        self._up.set()
        if self.on_gone:
            try:
                self.on_gone(why)
            except Exception as e:
                _complain(e)

    # -------------------------------------------------------------- the person
    def _bind(self, top) -> None:
        c = self._canvas
        c.bind("<Motion>", self._moved)
        for n in BUTTONS:
            c.bind(f"<Button-{n}>", lambda e, n=n: self._button(e, n, True))
            c.bind(f"<ButtonRelease-{n}>", lambda e, n=n: self._button(e, n, False))
        c.bind("<MouseWheel>", self._wheel)                      # Windows, macOS
        c.bind("<Button-4>", lambda e: self._wheel_x11(e, 1))    # X11 and XWayland
        c.bind("<Button-5>", lambda e: self._wheel_x11(e, -1))
        top.bind("<KeyPress>", self._key_down)
        top.bind("<KeyRelease>", self._key_up)
        top.bind("<FocusIn>", self._focus_gained)
        top.bind("<FocusOut>", self._focus_lost)
        c.bind("<Configure>", self._configured)

    def _moved(self, ev) -> None:
        self.events.put({"t": "in", "how": "move", "x": int(ev.x), "y": int(ev.y)})

    def _button(self, ev, n, down) -> None:
        name = BUTTONS.get(n)
        if not name:
            return
        # The pointer's place goes first: home plays a button wherever the
        # pointer last was, and a click can arrive with no move before it.
        self.events.put({"t": "in", "how": "move", "x": int(ev.x), "y": int(ev.y)})
        self.events.put({"t": "in", "how": "btn", "name": name, "down": bool(down)})
        if down and self._canvas is not None:
            self._canvas.focus_set()

    def _wheel(self, ev) -> None:
        notches = int(ev.delta / 120) if ev.delta else 0
        if notches:
            self.events.put({"t": "in", "how": "wheel", "dx": 0, "dy": notches})

    def _wheel_x11(self, ev, direction) -> None:
        self.events.put({"t": "in", "how": "wheel", "dx": 0, "dy": direction})

    def _key(self, ev, down) -> None:
        code = keymap.tk_to_evdev(ev.keysym)
        if code is None:
            if ev.keysym not in self._unknown_keys:
                self._unknown_keys.add(ev.keysym)
                self.log(f"the key {ev.keysym!r} is not one Nishro Link carries")
            return
        if down:
            self._keys_held.add(int(code))
        else:
            self._keys_held.discard(int(code))
        self.events.put({"t": "in", "how": "key", "code": int(code),
                         "down": bool(down)})

    def _focus_gained(self, ev=None) -> None:
        """The frame has the keyboard: its service is told, so the tray
        knows which window "Send back" means."""
        if ev is not None and getattr(ev, "widget", None) is not self._top:
            return                   # a widget inside it - not news
        self.events.put({"t": "focus", "on": True})

    def _focus_lost(self, ev=None) -> None:
        """The frame lost the keyboard while keys were held - the person
        clicked away with Ctrl down, say: its release now goes to some other
        window, never to this one, and home would hold Ctrl in the window
        for good. So everything still held is let go here."""
        if ev is not None and getattr(ev, "widget", None) is not self._top:
            return                   # a widget inside it - not news
        for code in sorted(self._keys_held):
            self.events.put({"t": "in", "how": "key", "code": code, "down": False})
        self._keys_held.clear()
        self.events.put({"t": "focus", "on": False})

    def _key_down(self, ev) -> None:
        self._key(ev, True)

    def _key_up(self, ev) -> None:
        self._key(ev, False)

    def _configured(self, ev) -> None:
        """The frame was resized. Many events arrive while a corner is
        dragged, so the far window is only told once it has settled."""
        if self._top is None:
            return
        if self._resize_job is not None:
            try:
                self._top.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self._top.after(RESIZE_SETTLE_MS, self._settled)

    def _settled(self) -> None:
        self._resize_job = None
        if self._canvas is None:
            return
        w, h = self._canvas.winfo_width(), self._canvas.winfo_height()
        if (w, h) == self._last_sent_size or w < 20 or h < 20:
            return
        self._last_sent_size = (w, h)
        self.events.put({"t": "size", "w": int(w), "h": int(h)})

    def _asked_to_close(self) -> None:
        """The frame's own close button. The maintainer's choice: it closes
        the program's window, as the same button at home would - so the
        program may put up "save changes?" first."""
        self.events.put({"t": "close"})
