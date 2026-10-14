"""Moving windows between computers: the window channel.

The full design is docs/moving-windows.md. This module holds the one piece
that is the same on every system - the window channel's transport - so it can
be built and tested on its own, before any of the system-specific capture,
hiding or framing is written.

A moved window's pictures must never queue in front of the pointer, so they
travel on a connection of their own, dialled and proved exactly like the link
(node.py does the handshake, as it does for the files channel). Once proved,
the connection is handed to a BinChannel here: sealed binary frames, length
first, so a picture pays no base64 and no 128 KB line cap.
"""
from __future__ import annotations

import json
import os
import pathlib
import queue
import socket
import struct
import threading
import time

from .secure import SecureError

# A frame bigger than this is broken or hostile, not a picture: a whole
# 1920x1080 picture raw is ~8 MB, and pictures travel compressed. The cap is
# generous so a burst of changed regions still fits, but bounded so a bad
# length cannot make us try to read gigabytes.
MAX_FRAME = 48 * 1024 * 1024
_LEN = struct.Struct(">I")              # a frame's length: 4 bytes, big-endian
_HDR = struct.Struct(">H")              # the header's length inside a frame

MAX_SIDE = 8192                 # pixels; no real window is wider or taller
MAX_REGIONS = 256               # changed rectangles in one picture, at most
AHEAD = 2                       # pictures sent and not yet shown, at most
MOST_AWAY = 4                   # windows on another screen at once (R8)
TILE = 64                       # pictures are compared in squares this big
CODECS = ("jpeg", "png", "raw")

MAX_FPS = 25.0                  # pictures a second, at most
IDLE_S = 0.2                    # how often to look again while nothing changes
KEYFRAME_S = 10.0               # the whole picture again this often, regardless
READY_S = 20.0                  # how long home waits for the frame to appear

# Why a moved window stopped being away - what the node logs and the tray says.
HOME = "home"                   # asked for, by either side
GONE = "gone"                   # the program's window ended
DROPPED = "dropped"             # the channel or the link broke
STOPPED = "stopped"             # Nishro Link is shutting down, or the switch went off
FAILED = "failed"               # something went wrong; the window comes home


class ChannelError(Exception):
    """The window channel broke: a bad length, a frame that would not
    decrypt, or the far end hung up. Always ends the channel (and with it the
    moved window, which comes home)."""


class BadMessage(ChannelError):
    """The far end sent something impossible - a region longer than the bytes
    that came, a header that is not an object, a picture before the size.

    Kept apart from a channel that merely broke, because the two mean
    different things to whoever reads the log: this is a peer that is newer,
    or has a bug, not a network that went away. Both bring the window home.
    """


# ------------------------------------------------------------------ messages
# Every frame is a 2-byte header length, a small JSON header, then the
# payload as it is - so a picture's bytes are never escaped into JSON (which
# would cost base64 again) and a header stays cheap to read.
#
# home -> away   meta (title, size), pic (a picture's changed regions), gone
# away -> home   ready, ack, in (input), size, close, home
#
# Unknown message types and unknown header fields are ignored by both ends,
# which is what lets a later 1.x add to this without breaking an older one.

def pack(header: dict, payload: bytes = b"") -> bytes:
    """One frame's bytes, for BinChannel.send."""
    head = json.dumps(header, separators=(",", ":")).encode("utf-8")
    if len(head) > 0xFFFF:
        raise ChannelError(f"a window message header is {len(head)}B, over 65535B")
    return _HDR.pack(len(head)) + head + payload


def unpack(frame: bytes):
    """(header, payload) from one frame. Raises ChannelError on anything
    malformed - the far end is proved, but a proved peer can still be a
    newer or buggier version, and a short frame must not raise IndexError
    deep inside a session."""
    if len(frame) < _HDR.size:
        raise BadMessage("a window message with no header length")
    (n,) = _HDR.unpack(frame[:_HDR.size])
    end = _HDR.size + n
    if len(frame) < end:
        raise BadMessage(f"a window message says its header is {n}B, "
                           f"but only {len(frame) - _HDR.size}B followed")
    try:
        header = json.loads(frame[_HDR.size:end].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise BadMessage(f"a window message header that will not read: {e}") from None
    if not isinstance(header, dict):
        raise BadMessage("a window message header that is not an object")
    return header, frame[end:]


def pic(n: int, regions) -> bytes:
    """A picture frame. `regions` is (x, y, w, h, codec, bytes) for each
    changed rectangle; their bytes go in the payload one after another, and
    the header says how long each is."""
    if len(regions) > MAX_REGIONS:
        raise ChannelError(f"{len(regions)} changed regions, over {MAX_REGIONS}")
    out, parts = [], []
    for x, y, w, h, codec, data in regions:
        if codec not in CODECS:
            raise ChannelError(f"unknown codec {codec!r}")
        out.append([int(x), int(y), int(w), int(h), codec, len(data)])
        parts.append(bytes(data))
    return pack({"t": "pic", "n": int(n), "r": out}, b"".join(parts))


def regions(header: dict, payload: bytes):
    """The regions of a picture frame, as (x, y, w, h, codec, bytes).

    Every length and size is checked against the payload actually received:
    a header that claims more than arrived, or a rectangle outside any real
    window, is refused rather than trusted."""
    rows = header.get("r")
    if not isinstance(rows, list):
        raise BadMessage("a picture with no regions")
    if len(rows) > MAX_REGIONS:
        raise BadMessage(f"a picture with {len(rows)} regions, over {MAX_REGIONS}")
    out, at = [], 0
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise BadMessage("a picture region that is not x, y, w, h, codec, length")
        x, y, w, h, codec, length = row[:6]
        try:
            x, y, w, h, length = int(x), int(y), int(w), int(h), int(length)
        except (TypeError, ValueError):
            raise BadMessage("a picture region with numbers that are not numbers") \
                from None
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > MAX_SIDE or y + h > MAX_SIDE:
            raise BadMessage(f"a picture region outside any real window: "
                               f"{w}x{h} at {x},{y}")
        if codec not in CODECS:
            raise BadMessage(f"a picture region in an unknown codec {codec!r}")
        if length < 0 or at + length > len(payload):
            raise BadMessage("a picture region longer than the bytes that arrived")
        out.append((x, y, w, h, codec, payload[at:at + length]))
        at += length
    if at != len(payload):
        raise BadMessage(f"a picture with {len(payload) - at}B more than its "
                           f"regions account for")
    return out


def changed(old: bytes, new: bytes, w: int, h: int, tile: int = TILE, was=None):
    """The rectangles covering every pixel that differs between two pictures.

    Both are BGRA, top row first, 4 bytes a pixel, w*h*4 long. Compared in
    squares of `tile` pixels, because comparing and sending pixel-perfect
    rectangles costs more than sending a few unchanged pixels along with the
    changed ones. Neighbouring changed squares are joined into as few
    rectangles as possible: a run across a row first, then a run of identical
    runs down the rows, so a changed line of text becomes one rectangle
    rather than twenty.

    `old` empty means everything changed: the whole picture comes back as one
    rectangle. So does a resize - pass `was`, the (width, height) `old` was
    captured at, and the whole picture is sent whenever it differs. Length
    alone cannot tell: a window resized from 64x16 to 32x32 has a picture of
    exactly the same number of bytes, and comparing those tile by tile would
    report nothing changed and leave the frame showing the old shape for
    ever. The length check stays as a cheap second guard.
    """
    need = w * h * 4
    if w <= 0 or h <= 0:
        return []
    if len(new) < need:
        raise ValueError(f"a {w}x{h} picture needs {need}B, got {len(new)}B")
    if was is not None and tuple(was) != (w, h):
        return [(0, 0, w, h)]
    if len(old) != len(new):
        return [(0, 0, w, h)]
    if old == new:
        # By far the commonest case - a window nobody is touching. One
        # comparison of the whole picture, done in C, instead of walking
        # every square of it.
        return []
    stride = w * 4
    cols = (w + tile - 1) // tile
    rows = (h + tile - 1) // tile
    # Plain bytes slices, not memoryview: slicing copies, but then `==` is a
    # memcmp, where comparing two memoryviews goes a slower way round -
    # measured four times slower here, and this is the hot path of the whole
    # feature (a band of rows: 0.27 ms against 1.05 ms).
    o, n = bytes(old), bytes(new)
    out = []
    open_runs: dict = {}          # (first col, last col + 1) -> index in out
    for r in range(rows):
        y0, y1 = r * tile, min((r + 1) * tile, h)
        # The whole band of rows at once first: typing changes one band out
        # of ten, and skipping the other nine whole saves most of the work.
        if o[y0 * stride:y1 * stride] == n[y0 * stride:y1 * stride]:
            open_runs = {}
            continue
        # And within the band, which rows differ - a whole row at a time,
        # again in C. Typing changes a line or two of the sixty-four in a
        # band, and only those rows are then looked at square by square.
        hot = [y for y in range(y0, y1)
               if o[y * stride:(y + 1) * stride] != n[y * stride:(y + 1) * stride]]
        dirty = [False] * cols
        for c in range(cols):
            a, b = c * tile * 4, min((c + 1) * tile, w) * 4
            for y in hot:
                base = y * stride
                if o[base + a:base + b] != n[base + a:base + b]:
                    dirty[c] = True
                    break
        runs, c = [], 0
        while c < cols:
            if dirty[c]:
                start = c
                while c < cols and dirty[c]:
                    c += 1
                runs.append((start, c))
            else:
                c += 1
        still_open = {}
        for key in runs:
            c0, c1 = key
            x = c0 * tile
            rect_w = min(c1 * tile, w) - x
            if key in open_runs:
                i = open_runs[key]
                rx, ry, rw, _ = out[i]
                out[i] = (rx, ry, rw, y1 - ry)     # the same run again: grow it down
                still_open[key] = i
            else:
                out.append((x, y0, rect_w, y1 - y0))
                still_open[key] = len(out) - 1
        open_runs = still_open
    return out


class Pacer:
    """How far ahead home may get of the window the person is actually
    looking at.

    A moved window must never become a queue of old pictures: if the network
    or the away computer cannot keep up, the right thing is a slower but
    current window. So home sends a picture only while fewer than `ahead` of
    them are unacknowledged, and away acknowledges each one it has shown.
    Picture numbers only ever rise; an acknowledgement for one already
    acknowledged, or for one never sent, changes nothing.
    """

    def __init__(self, ahead: int = AHEAD):
        self.ahead = max(1, int(ahead))
        self.sent = 0                 # the last picture number handed to the channel
        self.shown = 0                # the last one away said it had shown

    def may_send(self) -> bool:
        return self.sent - self.shown < self.ahead

    def next_number(self) -> int:
        """The number for the picture about to be sent."""
        self.sent += 1
        return self.sent

    def acked(self, n) -> None:
        try:
            n = int(n)
        except (TypeError, ValueError):
            return
        if self.shown < n <= self.sent:
            self.shown = n

    @property
    def waiting(self) -> int:
        return self.sent - self.shown


class Rescue:
    """What is needed to put a hidden window back, written down before it is
    hidden.

    R6, never lose a window: hiding a window is a change to someone else's
    program, and if Nishro Link dies between hiding and showing - crash,
    power cut, killed - that window is invisible with nothing left running
    that knows how to bring it back. So home writes the undo down first, and
    at its next start puts back everything the file still lists.

    A plain JSON file, written whole and renamed over the old one, so a
    half-written one is never read. Keyed by whatever the system uses to name
    a window (on Windows, the handle as a string), with whatever that system
    needs to undo the hiding.
    """

    def __init__(self, path, log=None):
        self.path = pathlib.Path(path)
        self.log = log or (lambda *_: None)

    def _read(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                was = json.load(f)
            return was if isinstance(was, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            # Unreadable: say so and start again, rather than refuse to move
            # any window ever again.
            self.log(f"the record of moved windows could not be read ({e}); starting it again")
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".new")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def remember(self, key, undo: dict) -> None:
        """Before hiding. `undo` is whatever show() will need."""
        data = self._read()
        data[str(key)] = undo
        self._write(data)

    def forget(self, key) -> None:
        """Once the window is back. Removing the last one removes the file,
        so a computer with no moved windows has nothing lying about."""
        data = self._read()
        if data.pop(str(key), None) is None:
            return
        if data:
            self._write(data)
        else:
            try:
                os.unlink(self.path)
            except OSError:
                pass

    def pending(self) -> dict:
        """What was still hidden when Nishro Link last stopped."""
        return self._read()

    def clear(self) -> None:
        try:
            os.unlink(self.path)
        except OSError:
            pass


class _Plain:
    """Sealing that does not seal, for a channel that needs none.

    Only ever used on a loopback connection between Nishro Link's own
    processes on one computer - the service, which holds the network
    connection and the password, and the part of it that sits in the
    signed-in session, where the screen is. The network side of a moved
    window is sealed as everything else is; adding a second layer between
    two processes of the same program, on the same machine, would only cost
    time. The loopback connection is protected the way the rest of that
    channel is: nothing but this computer can reach it, and the other end
    proves a token (agent.py).
    """

    def seal_bytes(self, plain: bytes) -> bytes:
        return bytes(plain)

    def open_bytes(self, raw: bytes) -> bytes:
        return bytes(raw)


def plain_channel(sock) -> "BinChannel":
    """A window channel between two of this computer's own processes."""
    return BinChannel(sock, _Plain(), _Plain())


# A frame from the part in the session that is a line for the log, not
# something to pass on. Recognised by its first bytes rather than by
# unpacking every frame: pictures go this way too, and their headers are the
# biggest thing on the channel. pack() writes "t" first, so this is exactly
# what one of these frames begins with.
LOG_FRAME = b'{"t":"host_log"'


def host_log(message: str) -> bytes:
    """A line for the service's log, from the part of Nishro Link that has a
    screen. It runs in the person's session, where its own output goes to the
    system log at best and nowhere at all from a windowed build - which left
    two real failures findable only with journalctl."""
    return pack({"t": "host_log", "msg": str(message)[:400]})


def relay(a: "BinChannel", b: "BinChannel", log=None) -> None:
    """Pass frames between two window channels until either ends.

    This is how a moved window reaches the screen on Windows, and will on
    Linux: the service owns the network connection, because it holds the
    password and the group, but it runs in a session with no desktop at all.
    The part in the signed-in session owns the window and the frame. So the
    service does the proving and the sealing and then simply passes frames
    along, which on loopback costs a copy and nothing else.
    """
    log = log or (lambda *_: None)

    def pump(src, dst, which):
        try:
            while True:
                frame = src.recv()
                if which == "out" and frame[_HDR.size:_HDR.size + len(LOG_FRAME)]                         == LOG_FRAME:
                    try:
                        log(f"the window's own part: "
                            f"{unpack(frame)[0].get('msg', '')}")
                    except ChannelError:
                        pass
                    continue
                dst.send(frame)
        except ChannelError as e:
            # Said, not swallowed: which way stopped first, and why, is the
            # only thing that distinguishes "the other computer let go" from
            # "the part with the screen did", and both look the same from
            # the far end once both channels are closed.
            log(f"the window's frames stopped going {which}: {e}")
        except Exception as e:
            log(f"passing a window's frames {which} stopped: {e!r}")
        finally:
            for ch in (a, b):
                ch.close()

    there = threading.Thread(target=pump, args=(a, b, "in"), daemon=True,
                             name="window-relay-in")
    there.start()
    pump(b, a, "out")
    there.join(2)


class HomeSession:
    """Home's end of one moved window: send what it looks like, play back what
    the person does to it away.

    The system-specific half comes in as `window`, which must offer:

        alive()                 is the program's window still there?
        title()                 its title now (it can change: a browser tab)
        size()                  (width, height) of what capture() returns
        capture()               the picture, BGRA, top row first - or None if
                                the window has gone
        resize(w, h)            the frame was resized; match it
        close()                 ask the program's window to close (the frame's
                                close button does this - the maintainer's
                                choice; the program may ask to save first)
        move(x, y)              play a pointer move, in window coordinates
        button(name, down)      play a button
        wheel(dx, dy)           play the wheel
        key(code, down)         play a key, as an evdev code

    and `encode(raw, w, h)` must return (codec, bytes) for one rectangle's
    worth of BGRA. Nothing here touches a screen or a socket directly, so the
    whole session can be driven by fakes.
    """

    def __init__(self, ch, window, encode, log=None, pacer=None, clock=time.monotonic):
        self.ch = ch
        self.window = window
        self.encode = encode
        self.log = log or (lambda *_: None)
        self.pacer = pacer or Pacer()
        self.clock = clock
        self.ended = None             # one of HOME/GONE/DROPPED/STOPPED/FAILED
        self._stop = False
        self._ready = threading.Event()
        self._last = b""              # the picture last sent
        self._last_wh = None          # the size it was captured at
        self._keyed = 0.0             # when the whole picture last went
        self._title = None

    # ----------------------------------------------------------------- public
    def stop(self, why=STOPPED) -> None:
        """Bring the window home. Safe from any thread, and from a tray
        click; never waits for the network."""
        if self.ended is None:
            self.ended = why
        self._stop = True
        self._ready.set()

    def run(self) -> str:
        """Hold the window away until it comes home. Returns why it ended.

        The window is NOT hidden here: the caller hides it once this says the
        frame is up (`wait_ready`), and shows it again when this returns - so
        a session that never gets a frame cannot leave an invisible window.
        """
        reader = threading.Thread(target=self._read, daemon=True,
                                  name="window-home-read")
        reader.start()
        try:
            self._send_meta()
            if not self._ready.wait(READY_S):
                self.stop(FAILED)
                self.log("the other computer never showed the window's frame")
            self._capture_loop()
        except BadMessage as e:
            self.stop(FAILED)
            self.log(f"the other computer sent something we cannot read: {e}")
        except ChannelError as e:
            self.stop(DROPPED)
            self.log(f"the window channel ended: {e}")
        except Exception as e:                       # never leave a window away
            self.stop(FAILED)
            self.log(f"moving the window failed: {e!r}")
        finally:
            self._farewell()
        return self.ended or HOME

    def wait_ready(self, timeout=READY_S) -> bool:
        """True once away has the frame up (or the session has ended)."""
        self._ready.wait(timeout)
        return self.ended is None and self._ready.is_set()

    # ---------------------------------------------------------------- sending
    def _send_meta(self) -> None:
        self._title = self.window.title()
        w, h = self.window.size()
        self.ch.send(pack({"t": "meta", "title": self._title, "w": int(w),
                           "h": int(h)}))

    def _farewell(self) -> None:
        """Tell away why, if the channel still lives. Best effort: the window
        is coming home either way."""
        try:
            self.ch.send(pack({"t": "gone", "why": self.ended or HOME}))
        except ChannelError:
            pass

    def _capture_loop(self) -> None:
        while not self._stop:
            if not self.window.alive():
                self.stop(GONE)
                break
            if not self.pacer.may_send():
                # Away has not caught up. Wait for an acknowledgement rather
                # than building a queue of pictures nobody will ever see.
                time.sleep(0.01)
                continue
            started = self.clock()
            if not self._send_picture():
                break
            self._retitle()
            spent = self.clock() - started
            time.sleep(max(1.0 / MAX_FPS - spent, 0.0))

    def _send_picture(self) -> bool:
        """One picture, or False if the window has gone."""
        w, h = self.window.size()
        raw = self.window.capture()
        if raw is None:
            self.stop(GONE)
            return False
        now = self.clock()
        if now - self._keyed >= KEYFRAME_S:
            # The whole picture now and then, so a region lost to a bug
            # cannot leave a stale patch on the frame for ever.
            self._last, self._last_wh = b"", None
            self._keyed = now
        rects = changed(self._last, raw, w, h, was=self._last_wh)
        self._last, self._last_wh = raw, (w, h)
        if not rects:
            time.sleep(IDLE_S)        # nothing moved: look again soon, not at once
            return True
        stride = w * 4
        out = []
        for x, y, rw, rh in rects:
            cut = b"".join(raw[(y + row) * stride + x * 4:
                               (y + row) * stride + (x + rw) * 4]
                           for row in range(rh))
            codec, data = self.encode(cut, rw, rh)
            out.append((x, y, rw, rh, codec, data))
        self.ch.send(pic(self.pacer.next_number(), out))
        return True

    def _retitle(self) -> None:
        title = self.window.title()
        if title != self._title:
            self._title = title
            self.ch.send(pack({"t": "meta", "title": title}))

    # --------------------------------------------------------------- receiving
    def _read(self) -> None:
        try:
            while not self._stop:
                self._on(*unpack(self.ch.recv()))
        except BadMessage as e:
            self.stop(FAILED)
            self.log(f"the other computer sent something we cannot read: {e}")
        except ChannelError as e:
            if not self._stop:
                self.stop(DROPPED)
                self.log(f"the window channel dropped: {e}")
        except Exception as e:
            self.stop(FAILED)
            self.log(f"the window channel went wrong: {e!r}")
        finally:
            self._ready.set()         # never leave run() waiting on a dead channel

    def _on(self, header: dict, _payload: bytes) -> None:
        t = header.get("t")
        if t == "ready":
            self._ready.set()
        elif t == "ack":
            self.pacer.acked(header.get("n"))
        elif t == "in":
            self._input(header)
        elif t == "size":
            w, h = _side(header.get("w")), _side(header.get("h"))
            if w and h:
                # The next capture is a different size from the one kept, and
                # changed() is told the size the kept one was taken at, so it
                # sends the whole window by itself - nothing to reset here.
                self.window.resize(w, h)
        elif t == "close":
            # The frame's close button closes the program's window, as the
            # same button at home would. The program may refuse, or ask to
            # save; if it does close, alive() goes false and we send "gone".
            self.window.close()
        elif t == "home":
            self.stop(HOME)
        # anything else: a newer peer talking about something we do not know

    def _input(self, header: dict) -> None:
        """One thing the person did in the frame, played into the window.

        Every number is checked: this comes from the other computer, and a
        coordinate far outside the window would otherwise be played at some
        unrelated place on this screen (P6 still applies - what we inject is
        never read back as the person's own input)."""
        how = header.get("how")
        if how == "move":
            w, h = self.window.size()
            x, y = _within(header.get("x"), w), _within(header.get("y"), h)
            if x is not None and y is not None:
                self.window.move(x, y)
        elif how == "btn":
            name, down = header.get("name"), bool(header.get("down"))
            if name in ("left", "right", "middle"):
                self.window.button(name, down)
        elif how == "wheel":
            dx, dy = _step(header.get("dx")), _step(header.get("dy"))
            if dx or dy:
                self.window.wheel(dx, dy)
        elif how == "key":
            code = header.get("code")
            if isinstance(code, int) and 0 <= code < 1024:
                self.window.key(code, bool(header.get("down")))


def _is_move(event) -> bool:
    return (isinstance(event, dict) and event.get("t") == "in"
            and event.get("how") == "move")


def _side(v):
    """A window side from a peer: an int within reason, else None."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if 0 < v <= MAX_SIDE else None


def _within(v, limit):
    """A coordinate inside a window of this size, else None."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if 0 <= v < max(int(limit), 1) else None


def _step(v):
    """A wheel step, clamped - a peer cannot ask for a thousand notches."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return 0
    return max(-20, min(20, v))


# ---------------------------------- dragging a moved window back home
# The frame showing a moved window is sent home by dragging it across the
# edge of the screen that the window's own computer lies beyond - the same
# edge the pointer crosses to drive that computer, so the gesture matches
# the rest of Nishro Link. The way back by the tray and the taskbar stays.
#
# The frame's PLACE is what is watched, never the pointer. The pointer
# reaching the edge of the screen is the link's own doorway: the baton
# would go to the other computer in the middle of the drag, and the frame
# would be left half-thrown. Dragging a window partly off the edge leaves
# the pointer well inside the screen, so the two never meet.
THROW_SIDES = ("left", "right", "top", "bottom")
THROW_BACK = 0.4            # how much of the frame must be past the edge
THROW_SETTLE_MS = 400       # quiet for this long counts as letting go


def past_edge(side, place, screen) -> float:
    """How much of a frame has gone past one edge of this screen.

    `side` is the edge the window's own computer lies beyond - one of
    THROW_SIDES - `place` is the frame as (x, y, w, h) in screen pixels,
    and `screen` is (width, height). Returns the fraction of the frame
    past that edge: 0.0 if none of it is, 1.0 if all of it is.

    A fraction rather than pixels, so the same push of the hand sends a
    window home on a small laptop screen and on a big monitor.

    Nothing is trusted: these numbers are read off a window manager, which
    answers oddly while a window is being mapped or taken away, and a frame
    must not be thrown home on the strength of that.
    """
    if side not in THROW_SIDES:
        return 0.0
    try:
        x, y, w, h = (int(v) for v in place)
        sw, sh = int(screen[0]), int(screen[1])
    except (TypeError, ValueError, IndexError):
        return 0.0
    if w <= 0 or h <= 0:
        return 0.0
    if side == "left":
        over, span = -x, w
    elif side == "right":
        over, span = (x + w) - sw, w
    elif side == "top":
        over, span = -y, h
    else:
        over, span = (y + h) - sh, h
    return max(0.0, min(1.0, over / span))


class AwaySession:
    """Away's end of one moved window: show it, and send on what the person
    does to it.

    The system-specific half comes in as `frame`, which must offer:

        open(title, w, h)       put the frame on this screen
        draw(regions)           (x, y, w, h, codec, bytes) rectangles
        retitle(title)
        close(why)              take the frame away

    and `frame.events` is a queue the frame puts the person's doings on, as
    the same dicts that cross the channel ({"t": "in", "how": "move", ...},
    {"t": "size", ...}, {"t": "close"}, {"t": "home"}). The frame belongs to
    whatever draws windows on this system, so it is never touched from this
    thread beyond those calls.
    """

    def __init__(self, ch, frame, log=None):
        self.ch = ch
        self.frame = frame
        self.log = log or (lambda *_: None)
        self.ended = None
        self.title = None
        self.size = None
        self._stop = False
        self._open = False

    def stop(self, why=STOPPED) -> None:
        if self.ended is None:
            self.ended = why
        self._stop = True

    def run(self) -> str:
        """Show the window until it goes home. Returns why it ended."""
        sender = threading.Thread(target=self._send_events, daemon=True,
                                  name="window-away-send")
        sender.start()
        try:
            while not self._stop:
                self._on(*unpack(self.ch.recv()))
        except BadMessage as e:
            self.stop(FAILED)
            self.log(f"the other computer sent something we cannot read: {e}")
        except ChannelError as e:
            self.stop(DROPPED)
            self.log(f"the window channel ended: {e}")
        except Exception as e:
            self.stop(FAILED)
            self.log(f"showing the window failed: {e!r}")
        finally:
            self._stop = True
            if self._open:
                self.frame.close(self.ended or HOME)
        return self.ended or HOME

    def _on(self, header: dict, payload: bytes) -> None:
        t = header.get("t")
        if t == "meta":
            self._meta(header)
        elif t == "pic":
            if not self._open:
                raise BadMessage("a picture before the window's size was known")
            self.frame.draw(regions(header, payload))
            # Acknowledged only once it is actually shown: that is what paces
            # home, so a slow frame gets a slower but current window.
            self.ch.send(pack({"t": "ack", "n": int(header.get("n") or 0)}))
        elif t == "gone":
            self.stop(str(header.get("why") or GONE)[:32])

    def _meta(self, header: dict) -> None:
        title = header.get("title")
        if isinstance(title, str):
            self.title = title[:256]
        w, h = _side(header.get("w")), _side(header.get("h"))
        if w and h:
            self.size = (w, h)
        if not self._open:
            if not self.size:
                raise BadMessage("the window's size never came")
            self.frame.open(self.title or "", *self.size)
            self._open = True
            self.ch.send(pack({"t": "ready"}))
        else:
            self.frame.retitle(self.title or "")

    def _send_events(self) -> None:
        """What the person does in the frame, on to home.

        Pointer moves are collapsed as the link itself collapses them: a
        position is absolute, so when several are waiting only the newest can
        matter, and sending the older ones would just make home act on where
        the pointer used to be. Everything else goes in order, and a move is
        always kept if anything else follows it, because home plays a button
        wherever the pointer last was.
        """
        try:
            while not self._stop:
                try:
                    event = self.frame.events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if event is None:
                    break
                while _is_move(event):
                    try:
                        nxt = self.frame.events.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None:
                        self.ch.send(pack(event))
                        return
                    if not _is_move(nxt):
                        self.ch.send(pack(event))   # the one the button needs
                    event = nxt
                self.ch.send(pack(event))
        except ChannelError:
            self.stop(DROPPED)
        except Exception as e:
            self.stop(FAILED)
            self.log(f"sending what you did to the window failed: {e!r}")


class BinChannel:
    """Sealed length-prefixed frames over a socket, one window channel's worth.

    node.py does the line-based handshake (hello/ok, proof both ways, key
    exchange) on the raw socket, then hands it here with the Sealer/Opener it
    derived and whatever bytes it had already read past the last line
    (protocol.LineChannel.detach). From then on every frame is
    `seal_bytes(payload)` with a 4-byte length in front - the Sealer's frame
    numbering carries straight on from the handshake, so the first binary
    frame is simply the next number.

    Not thread-safe for concurrent senders: one thread sends, one receives,
    as the sessions use it.
    """

    def __init__(self, sock: socket.socket, sealer, opener, rest: bytes = b""):
        self.sock = sock
        self._sealer = sealer
        self._opener = opener
        self._buf = bytearray(rest)         # bytes read during the handshake but past its last line
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def send(self, payload: bytes) -> None:
        """Seal and send one frame. Raises ChannelError if the socket is gone,
        so the caller brings the window home rather than sending blind."""
        sealed = self._sealer.seal_bytes(payload)
        try:
            self.sock.sendall(_LEN.pack(len(sealed)) + sealed)
        except OSError as e:
            raise ChannelError(f"the window channel could not send: {e}") from None

    def _fill(self, n: int) -> None:
        """Read until the buffer holds at least n bytes, or the peer hangs up."""
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(65536)
            except OSError as e:
                raise ChannelError(f"the window channel dropped: {e}") from None
            if not chunk:
                raise ChannelError("the other end closed the window channel")
            self._buf += chunk

    def recv(self) -> bytes:
        """The next frame's payload. Raises ChannelError on a bad length, a
        frame that fails to decrypt (altered, replayed or reordered - the seal
        catches it), or a hang-up."""
        self._fill(_LEN.size)
        (length,) = _LEN.unpack(self._buf[:_LEN.size])
        if length > MAX_FRAME:
            raise ChannelError(f"a window frame says it is {length}B, over the "
                               f"{MAX_FRAME}B cap - broken or hostile")
        self._fill(_LEN.size + length)
        sealed = bytes(self._buf[_LEN.size:_LEN.size + length])
        del self._buf[:_LEN.size + length]
        try:
            return self._opener.open_bytes(sealed)
        except SecureError as e:
            raise ChannelError(str(e)) from None

    def close(self) -> None:
        for step in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                step()
            except OSError:
                pass
