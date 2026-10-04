"""Clipboard sharing: lazy, chunked, and loop-free.

Copy on one machine, paste on the other. Works under Wayland, which Input Leap
still cannot do at all (their README says so outright).

LAZY, not eager. The old code pushed the whole clipboard across on every single
hand-over. Instead we announce that something changed (`clipmeta`) and send the
data only if the other side asks for it (`clipget` -> `clipdata`). Copying a
20 MB image must not stall a screen switch.

LOOP-FREE. Any machine may originate a change, and setting the clipboard from a
remote update would otherwise look exactly like a fresh local copy and bounce
straight back. `origin` stops the echo, and remembering what we just wrote stops
us re-announcing it.

NEVER ON THE HOOK THREAD. get() shells out on Linux, and P1 says a capture
thread may not block for a microsecond longer than arithmetic. Everything here
runs on Node's own clipboard worker. (Windows is read through user32 directly
- clip_win.py - since PowerShell flashed a console window on every Ctrl+C.)

NO WINDOWS ON GNOME. GNOME under Wayland gives background programs no way to
the clipboard, so wl-clipboard gets there by opening a tiny window for a moment
- which the dock shows, every time. Read every 400ms, that was reported as the
Ubuntu dock's Trash icon "jumping down and up repeatedly" while linked. So on
GNOME the X11 tools come first: they reach the same clipboard through XWayland
without a window. (Elsewhere wl-clipboard uses a proper protocol and is best.)
And on Linux the clipboard is read only when the pointer has just left this
machine, not on a timer - see Node._clipboard.

TEXT AND IMAGES. A clipboard value is either text (a str) or an image,
("image/png", bytes): PNG is what both systems' clipboards speak. Text wins
when both are offered - a range of cells copied is text, even if a picture
of it comes along. An image travels base64 in the same chunks, paced so that
the pointer and the keys never queue behind it (Node._clipboard). Files are
not shared yet.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys

from . import protocol

# Capped: this rides the input channel, and a runaway paste buffer should be
# refused rather than chunked into ten thousand frames.
MAX_BYTES = 1024 * 1024               # text
MAX_IMAGE_BYTES = 16 * 1024 * 1024    # an image, as PNG
IMAGE = "image/png"


READ = {"wl": ["wl-paste", "-n", "--type", "text"],
        "xclip": ["xclip", "-selection", "clipboard", "-o"],
        "xsel": ["xsel", "-b", "-o"]}
WRITE = {"wl": ["wl-copy", "--type", "text/plain"],
         "xclip": ["xclip", "-selection", "clipboard", "-i"],
         "xsel": ["xsel", "-b", "-i"]}
_BINARY = {"wl": "wl-paste", "xclip": "xclip", "xsel": "xsel"}
# What each tool offers, and how to read and write an image. xsel cannot.
TYPES = {"wl": ["wl-paste", "--list-types"],
         "xclip": ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]}
READ_IMAGE = {"wl": ["wl-paste", "--type", IMAGE],
              "xclip": ["xclip", "-selection", "clipboard", "-t", IMAGE, "-o"]}
WRITE_IMAGE = {"wl": ["wl-copy", "--type", IMAGE],
               "xclip": ["xclip", "-selection", "clipboard", "-t", IMAGE, "-i"]}
TEXT_TYPES = frozenset({"text/plain", "text/plain;charset=utf-8", "UTF8_STRING",
                        "STRING", "TEXT"})


def order(env=None) -> list:
    """The Linux clipboard tools to use, best first, for this session."""
    env = os.environ if env is None else env
    wayland = bool(env.get("WAYLAND_DISPLAY"))
    x11 = bool(env.get("DISPLAY"))
    gnome = "gnome" in env.get("XDG_CURRENT_DESKTOP", "").lower()
    x = ["xclip", "xsel"] if x11 else []
    wl = ["wl"] if wayland else []
    if wayland and gnome:
        return x + wl          # no window through XWayland; wl-clipboard flashes one
    if wayland:
        return wl + x
    return x


@functools.lru_cache(maxsize=1)
def _user_tools() -> tuple:
    return tuple(t for t in order() if shutil.which(_BINARY[t]))


def _tools() -> tuple:
    """The tools to try, and - as the system service - the session they run
    in. The service is root and outside every session; the clipboard belongs
    to whoever is logged in (session.py)."""
    from . import session
    if not session.running_as_root():
        return _user_tools()
    s = session.active()
    if s is None:
        return ()                     # the login screen: no clipboard to share
    env = dict(s["env"], XDG_CURRENT_DESKTOP="GNOME" if s["type"] == "wayland"
               else "")
    return tuple(t for t in order(env) if shutil.which(_BINARY[t]))


def _run(cmd, **kw):
    from . import session
    return session.run(cmd, **kw)


last_error = None      # why the last get() or set() failed, for the log


def get():
    """The clipboard: its text, ("image/png", bytes) for an image, or "" -
    or None when another program held it just then: look again."""
    global last_error
    last_error = None
    try:
        if sys.platform == "win32":
            from . import clip_win
            text = clip_win.get_text()
            if text is None or text:
                return text
            png = clip_win.get_image()
            return (IMAGE, png) if png else ""
        for tool in _tools():
            if tool in TYPES:
                t = _run(TYPES[tool], capture_output=True, text=True, timeout=5)
                offered = frozenset(t.stdout.split()) if t.returncode == 0 else frozenset()
                if IMAGE in offered and not offered & TEXT_TYPES:
                    r = _run(READ_IMAGE[tool], capture_output=True, timeout=10)
                    if r.returncode == 0 and r.stdout.startswith(b"\x89PNG"):
                        return (IMAGE, r.stdout)
            r = _run(READ[tool], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return r.stdout
    except Exception as e:
        last_error = repr(e)
    return ""


def set(value) -> bool:
    """Put text (a str) or an image (("image/png", bytes)) on the clipboard."""
    if isinstance(value, tuple):
        return _set_image(value[1])
    text = value
    try:
        if sys.platform == "win32":
            from . import clip_win
            return clip_win.set_text(text)
        for tool in _tools():
            # Not captured: wl-copy and xclip stay behind to hold the clipboard,
            # and a pipe held open by that child made run() wait out the whole
            # timeout on every paste that arrived.
            r = _run(WRITE[tool], input=text, text=True, timeout=5,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return True
    except Exception:
        pass
    return False


def _set_image(png: bytes) -> bool:
    global last_error
    last_error = None
    try:
        if sys.platform == "win32":
            from . import clip_win
            return clip_win.set_image(png)
        for tool in _tools():
            if tool not in WRITE_IMAGE:
                continue
            r = _run(WRITE_IMAGE[tool], input=png, timeout=10,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return True
    except Exception as e:
        last_error = repr(e)
    return False


def to_wire(value, spool):
    """A clipboard value for a JSON message: text as itself, an image as the
    path of a file holding it - the agent's channel takes lines of 128 KB at
    most, and the service and its agent share the disk (agent.py). `spool` is
    a function giving that path: asked only when there is an image."""
    if isinstance(value, tuple):
        path = pathlib.Path(spool())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value[1])
        return {"fmt": value[0], "file": str(path)}
    return value


def from_wire(value):
    """The other half of to_wire. The file goes once read: a copied image can
    be anything, and nothing needs it on disk."""
    if isinstance(value, dict) and value.get("file"):
        path = pathlib.Path(value["file"])
        try:
            return (value.get("fmt") or IMAGE, path.read_bytes())
        except OSError:
            return ""
        finally:
            try:
                path.unlink()
            except OSError:
                pass
    return value


def sequence():
    """A cheap "has the clipboard changed?" counter, or None if unavailable.

    Windows gives us one for the cost of a function call, which means we can
    check several times a second and read the clipboard only when something
    actually changed.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            return int(ctypes.windll.user32.GetClipboardSequenceNumber())
        except Exception:
            return None
    return None                       # Linux: no equivalent, so poll get()


class ClipboardSync:
    """The lazy-clipboard state machine. I/O is injected, so it is testable."""

    def __init__(self, node: str, read=get, write=set, seq_fn=sequence,
                 max_bytes: int = MAX_BYTES, max_image_bytes: int = MAX_IMAGE_BYTES):
        self.node = node
        self._read = read
        self._write = write
        self._seq_fn = seq_fn
        self.max_bytes = max_bytes
        self.max_image_bytes = max_image_bytes
        self._seq = 0
        self._native = None           # last OS clipboard sequence number
        self._last = None             # what we last knew of, either direction
        self._offer = None            # (seq, format, data) we have announced
        self._incoming = {}           # seq -> {index: chunk}
        # Said in the log: what was asked for, sent, and whether it landed -
        # never what it was. An image that did not cross left no trace at all.
        self.log = lambda line: None

    def cheap(self) -> bool:
        """Is looking at the clipboard nearly free here? True where the OS keeps
        a change counter (Windows); False where a look runs a program (Linux)."""
        if not hasattr(self, "_cheap"):
            self._cheap = self._seq_fn() is not None
        return self._cheap

    # ---- our side changed ----
    def poll(self, force: bool = False) -> list:
        """Check the local clipboard. Returns messages to send (usually none).

        Called on a worker thread a few times a second. `force` reads it even
        when the change counter says nothing changed: the pointer has just
        left - the moment a copy can next be pasted elsewhere - and a counter
        that is missing or wrong must not keep a copy back.
        """
        native = self._seq_fn()
        if native is not None:
            if native == self._native and not force:
                return []             # provably unchanged, and we paid almost nothing
            self._native = native
        value = self._read()
        if value is None:             # held by another program: look again
            self._native = None
            return []
        if isinstance(value, tuple):
            fmt, data = value
            size = len(data or b"")
        else:
            fmt, data = "text", value
            size = len((data or "").encode("utf-8"))
        if not data or _key(fmt, data) == self._last:
            return []
        self._last = _key(fmt, data)
        if size > (self.max_image_bytes if fmt == IMAGE else self.max_bytes):
            return []                 # too big to offer: nothing announced
        self._seq += 1
        self._offer = (self._seq, fmt, data)
        return [protocol.clipmeta(self.node, self._seq, [fmt], size)]

    # ---- the other side's ----
    def on_message(self, msg: dict) -> list:
        t = msg.get("t")
        if t == "clipmeta":
            return self._on_meta(msg)
        if t == "clipget":
            return self._on_get(msg)
        if t == "clipdata":
            return self._on_data(msg)
        return []

    def _on_meta(self, msg) -> list:
        if msg.get("origin") == self.node:
            return []                 # our own announcement, come back to us
        formats = msg.get("formats") or ["text"]
        fmt = "text" if "text" in formats else IMAGE if IMAGE in formats else None
        if fmt is None:
            return []                 # nothing we can take
        who, size = msg.get("origin") or msg.get("from") or "another device", msg.get("bytes", 0)
        if size > (self.max_image_bytes if fmt == IMAGE else self.max_bytes):
            self.log(f"clipboard: {who} copied {_what(fmt)} of {_size(size)} - "
                     f"too big to bring across")
            return []                 # too big to be worth the input channel
        self.log(f"clipboard: {who} copied {_what(fmt)} ({_size(size)}) - asking for it")
        return [protocol.clipget(msg["seq"], fmt)]

    def _on_get(self, msg) -> list:
        if not self._offer or self._offer[0] != msg.get("seq"):
            return []                 # they want something we no longer hold
        seq, fmt, data = self._offer
        if msg.get("format", "text") != fmt:
            return []                 # an older 1.x asks for text, whatever is offered
        body = data if fmt == "text" else base64.b64encode(data).decode("ascii")
        parts = [body[i:i + protocol.CLIP_CHUNK]
                 for i in range(0, len(body), protocol.CLIP_CHUNK)] or [""]
        self.log(f"clipboard: sending {_what(fmt)} ({_size(_bytes(fmt, data))}) "
                 f"to {msg.get('from') or 'another device'}")
        return [protocol.clipdata(seq, fmt, i, len(parts), part)
                for i, part in enumerate(parts)]

    def _on_data(self, msg) -> list:
        seq, i, n = msg["seq"], msg["i"], msg["n"]
        buf = self._incoming.setdefault(seq, {})
        buf[i] = msg.get("v", "")
        if len(buf) < n:
            return []
        body = "".join(buf[k] for k in sorted(buf))
        self._incoming.pop(seq, None)
        fmt = msg.get("format") or "text"
        if fmt == IMAGE:
            try:
                value = (IMAGE, base64.b64decode(body))
            except ValueError:
                return []
            key = _key(IMAGE, value[1])
        elif fmt == "text":
            value = key = body
        else:
            return []
        # Remember it BEFORE writing: the write is what our own poll() would
        # otherwise see as a brand new local copy and announce straight back.
        self._last = key
        self._native = None           # force one real read to resync the counter
        ok = self._write(value)
        self.log(f"clipboard: {_what(fmt)} from {msg.get('from') or 'another device'} "
                 f"({_size(_bytes(fmt, value[1] if fmt == IMAGE else value))}) "
                 + ("is on this computer's clipboard" if ok is not False else
                    "could NOT be put on this computer's clipboard"))
        return []


def _what(fmt) -> str:
    return "an image" if fmt == IMAGE else "text"


def _bytes(fmt, data) -> int:
    return len(data) if fmt == IMAGE else len((data or "").encode("utf-8"))


def _size(n: int) -> str:
    n = int(n or 0)
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _key(fmt, data):
    """What a clipboard value is known by, to tell a new copy from one we have
    already seen: text by itself, an image by its digest."""
    return data if fmt == "text" else (fmt, hashlib.sha256(data).hexdigest())
