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

NEVER ON THE HOOK THREAD. get() shells out - PowerShell on Windows takes roughly
200ms - and P1 says a capture thread may not block for a microsecond longer than
arithmetic. Everything here runs on Node's own clipboard worker.

NO WINDOWS ON GNOME. GNOME under Wayland gives background programs no way to
the clipboard, so wl-clipboard gets there by opening a tiny window for a moment
- which the dock shows, every time. Read every 400ms, that was reported as the
Ubuntu dock's Trash icon "jumping down and up repeatedly" while linked. So on
GNOME the X11 tools come first: they reach the same clipboard through XWayland
without a window. (Elsewhere wl-clipboard uses a proper protocol and is best.)
And on Linux the clipboard is read only when the pointer has just left this
machine, not on a timer - see Node._clipboard.
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys

from . import protocol

# Text only for now, and capped: this rides the input channel, and a runaway
# paste buffer should be refused rather than chunked into ten thousand frames.
MAX_BYTES = 1024 * 1024


READ = {"wl": ["wl-paste", "-n", "--type", "text"],
        "xclip": ["xclip", "-selection", "clipboard", "-o"],
        "xsel": ["xsel", "-b", "-o"]}
WRITE = {"wl": ["wl-copy", "--type", "text/plain"],
         "xclip": ["xclip", "-selection", "clipboard", "-i"],
         "xsel": ["xsel", "-b", "-i"]}
_BINARY = {"wl": "wl-paste", "xclip": "xclip", "xsel": "xsel"}


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
def _tools() -> tuple:
    return tuple(t for t in order() if shutil.which(_BINARY[t]))


def get() -> str:
    try:
        if sys.platform == "win32":
            out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                                 capture_output=True, text=True, timeout=5)
            return out.stdout.rstrip("\r\n")
        for tool in _tools():
            r = subprocess.run(READ[tool], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return r.stdout
    except Exception:
        pass
    return ""


def set(text: str) -> bool:
    try:
        if sys.platform == "win32":
            subprocess.run(["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
                           input=text, text=True, timeout=5, capture_output=True)
            return True
        for tool in _tools():
            # Not captured: wl-copy and xclip stay behind to hold the clipboard,
            # and a pipe held open by that child made run() wait out the whole
            # timeout on every paste that arrived.
            r = subprocess.run(WRITE[tool], input=text, text=True, timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return True
    except Exception:
        pass
    return False


def sequence():
    """A cheap "has the clipboard changed?" counter, or None if unavailable.

    Windows gives us one for the cost of a function call, which means we can
    check several times a second and only pay for the expensive PowerShell read
    when something actually changed. Without it we would be spawning a shell
    twice a second forever on a laptop that cannot spare it.
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
                 max_bytes: int = MAX_BYTES):
        self.node = node
        self._read = read
        self._write = write
        self._seq_fn = seq_fn
        self.max_bytes = max_bytes
        self._seq = 0
        self._native = None           # last OS clipboard sequence number
        self._last = None             # last text we know about, either direction
        self._offer = None            # (seq, text) we have announced
        self._incoming = {}           # seq -> {index: chunk}

    def cheap(self) -> bool:
        """Is looking at the clipboard nearly free here? True where the OS keeps
        a change counter (Windows); False where a look runs a program (Linux)."""
        if not hasattr(self, "_cheap"):
            self._cheap = self._seq_fn() is not None
        return self._cheap

    # ---- our side changed ----
    def poll(self) -> list:
        """Check the local clipboard. Returns messages to send (usually none).

        Called on a worker thread a few times a second.
        """
        native = self._seq_fn()
        if native is not None:
            if native == self._native:
                return []             # provably unchanged, and we paid almost nothing
            self._native = native
        text = self._read()
        if text == self._last or not text:
            return []
        self._last = text
        self._seq += 1
        self._offer = (self._seq, text)
        return [protocol.clipmeta(self.node, self._seq, ["text"],
                                  len(text.encode("utf-8")))]

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
        if msg.get("bytes", 0) > self.max_bytes:
            return []                 # too big to be worth the input channel
        return [protocol.clipget(msg["seq"], "text")]

    def _on_get(self, msg) -> list:
        if not self._offer or self._offer[0] != msg.get("seq"):
            return []                 # they want something we no longer hold
        seq, text = self._offer
        parts = [text[i:i + protocol.CLIP_CHUNK]
                 for i in range(0, len(text), protocol.CLIP_CHUNK)] or [""]
        return [protocol.clipdata(seq, "text", i, len(parts), part)
                for i, part in enumerate(parts)]

    def _on_data(self, msg) -> list:
        seq, i, n = msg["seq"], msg["i"], msg["n"]
        buf = self._incoming.setdefault(seq, {})
        buf[i] = msg.get("v", "")
        if len(buf) < n:
            return []
        text = "".join(buf[k] for k in sorted(buf))
        self._incoming.pop(seq, None)
        # Remember it BEFORE writing: the write is what our own poll() would
        # otherwise see as a brand new local copy and announce straight back.
        self._last = text
        self._native = None           # force one real read to resync the counter
        self._write(text)
        return []
