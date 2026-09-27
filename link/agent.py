"""The desk agent: the Windows service's hands on whichever screen is showing.

Windows keeps the lock screen and the login screen on a desktop ordinary
programs cannot touch, and a service lives in a session with no screen at all.
So the service keeps the link - the network, the group, the password - and
starts this small agent in the console session, as SYSTEM, on the desktop that
is showing right now: the normal one, the lock screen, the login screen, a UAC
prompt. The agent reads the keyboard and mouse there and plays remote input
there. When the showing desktop changes, the agent says where to and exits; the
service starts a new one there. The link itself never drops.

    service (session 0)                         agent (console session)
      Node ── AgentCapture / AgentInjector ──  WinCapture, WindowsInjector,
             AgentHub  <── loopback, token ──> clipboard, desktop detection

Everything crosses one loopback socket as JSON lines; the agent proves itself
with a token only the service gave it. Input from the agent goes straight into
the node's usual callbacks; the node's commands go straight back. Capture's
hook thread only ever queues (P1), exactly as before.
"""
from __future__ import annotations

import os
import secrets
import socket
import threading
import time

from . import protocol

HELLO_TIMEOUT = 5.0


# ================================================================ service side
class AgentHub:
    """Accepts the agent, relaunches it, and relays between it and the node."""

    def __init__(self, launch=None, log=None):
        self.launch = launch              # (port, token, desktop name) -> proc
        self.log = log or (lambda *_: None)
        self.token = secrets.token_hex(16)
        self.sink = None
        self.suppress = (False, False, False)
        self._ch = None
        self._lock = threading.Lock()
        self._desk = None                 # the agent's desktop geometry
        self._desk_name = "Default"       # where the next agent should start
        self._proc = None
        self._stop = False
        self._calls = {}                  # rid -> [Event, value]
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(2)
        self.port = self._srv.getsockname()[1]
        self.connected = threading.Event()

    # ---------------------------------------------------------- lifecycle
    def start(self) -> None:
        if getattr(self, "_started", False):
            return                        # started early by the service, again by
        self._started = True              # the node: once is enough
        threading.Thread(target=self._accept, daemon=True).start()
        if self.launch:
            threading.Thread(target=self._supervise, daemon=True).start()

    def stop(self) -> None:
        self._stop = True
        self.send({"t": "bye"})
        try:
            self._srv.close()
        except OSError:
            pass
        self._kill()

    def _kill(self) -> None:
        p, self._proc = self._proc, None
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def _supervise(self) -> None:
        """Keep one agent running, on the desktop that is showing."""
        while not self._stop:
            p = self._proc
            if p is None or not p.alive():
                self.connected.clear()
                try:
                    self._proc = self.launch(self.port, self.token, self._desk_name)
                    self.log(f"agent started on the {self._desk_name} desktop")
                except Exception as e:
                    self._proc = None
                    self.log(f"agent not started ({e}) - trying again")
                    time.sleep(2.0)
                    continue
                self.connected.wait(HELLO_TIMEOUT)
            time.sleep(0.25)

    def _accept(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn) -> None:
        ch = protocol.LineChannel(conn)
        try:
            conn.settimeout(HELLO_TIMEOUT)
            hello = ch.recv()
            if not hello or not secrets.compare_digest(str(hello.get("token", "")),
                                                       self.token):
                return                          # not ours: say nothing
            conn.settimeout(None)
            with self._lock:
                old, self._ch = self._ch, ch     # the newest agent wins
            if old is not None:
                old.close()
            self._desk_name = hello.get("desk") or self._desk_name
            ch.send({"t": "sup", "a": list(self.suppress)})
            self.connected.set()
            while not self._stop:
                msg = ch.recv()
                if msg is None:
                    break
                self._on(msg)
        except (OSError, protocol.ProtocolError):
            pass
        finally:
            with self._lock:
                if self._ch is ch:
                    self._ch = None
            ch.close()

    # ------------------------------------------------------------ from it
    def _on(self, msg: dict) -> None:
        t = msg.get("t")
        if t == "ev":
            sink, a = self.sink, msg.get("a") or []
            if sink is not None:
                getattr(sink, "on_" + msg.get("k", ""), lambda *_: None)(*a)
        elif t == "desk":
            from .desktop import Desktop
            self._desk = Desktop(int(msg["x"]), int(msg["y"]), int(msg["w"]),
                                 int(msg["h"]),
                                 tuple(tuple(p) for p in msg.get("parts") or ()))
        elif t == "moving":
            self._desk_name = msg.get("to") or "Default"
            self.log(f"the {self._desk_name} desktop is showing - moving there")
        elif t == "ret":
            box = self._calls.get(msg.get("rid"))
            if box is not None:
                box[1] = msg.get("v")
                box[0].set()

    # -------------------------------------------------------------- to it
    def send(self, msg: dict) -> bool:
        with self._lock:
            ch = self._ch
        if ch is None:
            return False                  # between agents: dropped, like a gap
        ch.send(msg)
        return True

    def call(self, what: str, timeout: float, **args):
        rid = secrets.token_hex(4)
        box = [threading.Event(), None]
        self._calls[rid] = box
        try:
            if not self.send(dict(args, t=what, rid=rid)):
                return None
            box[0].wait(timeout)
            return box[1]
        finally:
            self._calls.pop(rid, None)

    def desktop(self):
        return self._desk

    # the clipboard lives in the console session too
    def clip_seq(self):
        v = self.call("clip_seq", 1.0)
        return 0 if v is None else v      # a number: see ClipboardSync.cheap()

    def clip_read(self) -> str:
        return self.call("clip_get", 6.0) or ""

    def clip_write(self, text: str) -> bool:
        return bool(self.call("clip_set", 6.0, v=text))


class AgentCapture:
    """What the node sees as its capture: events come from the agent."""

    def __init__(self, hub: AgentHub):
        self.hub = hub

    def start(self, sink) -> None:
        self.hub.sink = sink
        self.hub.start()

    def stop(self) -> None:
        self.hub.stop()

    def set_suppress(self, mouse, keyboard, cursor_here=False) -> None:
        s = (bool(mouse), bool(keyboard), bool(cursor_here))
        if s != self.hub.suppress:
            self.hub.suppress = s
            self.hub.send({"t": "sup", "a": list(s)})

    def set_origin(self, x, y) -> None:
        pass                              # the agent measures its own desktop


class AgentInjector:
    """What the node sees as its injector: input is played by the agent."""

    def __init__(self, hub: AgentHub):
        self.hub = hub

    def _do(self, kind, *a):
        self.hub.send({"t": "inj", "k": kind, "a": list(a)})

    def move_abs(self, x, y):
        self._do("move_abs", x, y)

    def move(self, dx, dy):
        self._do("move", dx, dy)

    def button(self, name, down):
        self._do("button", name, bool(down))

    def wheel(self, dx, dy):
        self._do("wheel", dx, dy)

    def key(self, code, down):
        self._do("key", code, int(down))

    def spotlight(self, x, y):
        self._do("spotlight", int(x), int(y))

    def set_origin(self, x, y):
        pass

    def close(self):
        pass


# ================================================================== agent side
class _Relay:
    """The capture's sink, in the agent: every callback becomes a message."""

    def __init__(self, ch):
        self.ch = ch

    def __getattr__(self, name):
        if not name.startswith("on_"):
            raise AttributeError(name)
        kind = name[3:]
        return lambda *a: self.ch.send({"t": "ev", "k": kind, "a": list(a)})


def run(port: int, token: str, capture=None, injector=None, detect=None,
        desk_name=None, showing=None, clip_get=None, clip_set=None, clip_seq=None,
        watch_every: float = 0.25, stop: threading.Event = None) -> int:
    """The agent's life: connect, relay, and leave when the desktop changes.
    Everything it touches is injectable, so the relay is testable anywhere.
    Returns 2 when it left because another desktop is showing."""
    from . import clip as _clip
    from .desktop import detect as _detect
    detect = detect or _detect
    desk_name = desk_name or (lambda: "Default")
    showing = showing or desk_name
    clip_get = clip_get or _clip.get
    clip_set = clip_set or _clip.set
    clip_seq = clip_seq or _clip.sequence
    stop = stop or threading.Event()

    sock = socket.create_connection(("127.0.0.1", int(port)), timeout=5)
    sock.settimeout(None)
    ch = protocol.LineChannel(sock)
    mine = desk_name()
    ch.send({"t": "hello", "token": token, "desk": mine, "pid": os.getpid()})
    d = detect()
    ch.send({"t": "desk", "x": d.x, "y": d.y, "w": d.w, "h": d.h,
             "parts": [list(p) for p in d.parts]})
    if injector is None:
        from .inject import make_injector
        injector = make_injector(screen=d.size, origin=(d.x, d.y))
    if capture is None:
        from .capture_win import WinCapture
        capture = WinCapture(origin=(d.x, d.y))
    capture.start(_Relay(ch))
    note = getattr(capture, "note_injected", None)
    result = {"code": 0}

    def reader():
        try:
            while not stop.is_set():
                msg = ch.recv()
                if msg is None:
                    break
                t = msg.get("t")
                if t == "inj":
                    k, a = msg.get("k"), msg.get("a") or []
                    getattr(injector, k)(*a)
                    if k == "move_abs" and note:
                        note(*a)
                elif t == "sup":
                    capture.set_suppress(*msg.get("a", [False, False, False]))
                elif t in ("clip_seq", "clip_get", "clip_set"):
                    rid = msg.get("rid")

                    def answer(t=t, rid=rid, v=msg.get("v")):
                        value = (clip_seq() if t == "clip_seq" else
                                 clip_get() if t == "clip_get" else clip_set(v))
                        ch.send({"t": "ret", "rid": rid, "v": value})
                    threading.Thread(target=answer, daemon=True).start()
                elif t == "bye":
                    break
        except (OSError, protocol.ProtocolError):
            pass
        stop.set()

    threading.Thread(target=reader, daemon=True).start()
    last = d
    ticks = 0
    try:
        while not stop.wait(watch_every):
            now = showing()
            if now and now != mine:
                # The lock screen, the login screen or a UAC prompt came up (or
                # went): say where, and make way for an agent there.
                ch.send({"t": "moving", "to": now})
                result["code"] = 2
                break
            ticks += 1
            if ticks % max(1, int(3.0 / watch_every)) == 0:
                try:
                    d = detect()
                except Exception:
                    continue
                if d != last:
                    last = d
                    ch.send({"t": "desk", "x": d.x, "y": d.y, "w": d.w, "h": d.h,
                             "parts": [list(p) for p in d.parts]})
                    for part in (capture, injector):
                        f = getattr(part, "set_origin", None)
                        if f:
                            f(d.x, d.y)
    finally:
        stop.set()
        try:
            capture.set_suppress(False, False)   # never leave input dead
            capture.stop()
        except Exception:
            pass
        ch.close(flush=0.5)
    return result["code"]
