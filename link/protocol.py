"""Nishro Link wire protocol v2. See DESIGN.md section 6.

Newline-delimited JSON over a persistent TCP socket. Chosen deliberately: input
volume is tiny (~1 KB/s while moving), and reading the wire with `nc` during a
2 a.m. debugging session is worth more than the bytes a binary frame would save.

Canonical key codes on the wire are **Linux evdev KEY_ codes** (see keymap.py).
Windows maps its VK codes to and from these; Linux uses them as-is. So one
machine's keyboard layout never leaks into the protocol.

KEY NAMING. Hot-path messages - positions, buttons, keys, sent hundreds of times
a second - use short keys ("s", "x", "e"). Control messages, sent rarely, spell
their fields out ("screen", "holder", "epoch"). Terseness where it is measured,
readability where it is read.

EPOCHS. Every input message carries "e", the baton epoch it was produced under.
A receiver discards anything whose epoch is not current (is_stale), which is what
makes a handover atomic: events still in flight from the previous holder cannot
land after the baton has moved.

Message shapes:
  handshake  {"t":"auth","v":2,"node":..,"nonce":..}          listener speaks first
             {"t":"hello","v":2,"node":..,"screens":[..],"policy":{..},
              "proof":..,"nonce":..,"resume":<session id>}
             {"t":"welcome","v":2,"node":..,"epoch":N,"holder":..,"layout":{..},
              "screen":..,"x":..,"y":..,"resumed":bool,"session":..}
             {"t":"err","msg":..,"code":..}   code: auth | removed | busy | name
  joining    hello with "join":true      a pairing someone just asked for, not a
                                         reconnect - lets a removed device back in
  inviting   hello with "invite":true    "join MY group": answered by
             {"t":"invite_ok","node":..,"proof":..}, then
             {"t":"invite","hub":..,"hub_id":..,"addr":..,"port":N,"secret":{..}}
             {"t":"invite_done","ok":bool}
  leaving    {"t":"leave"}   peer -> hub          {"t":"removed","by":..} hub -> peer
  checking   hello with "probe":true    "is this the password?" - answered by
             {"t":"probe_ok","node":..,"proof":..} and nothing else: nobody
             joins, so a wrong guess changes nothing on either side
  baton      {"t":"claim","node":..,"reason":"motion"|"click"|"hotkey"}
             {"t":"baton","holder":..,"epoch":N,"screen":..,"x":..,"y":..,
              "held":[..],"toggles":{..}}
             {"t":"release","node":..}
  input      {"t":"p","e":N,"s":<screen>,"x":0..1,"y":0..1}   absolute, normalised
             {"t":"b","e":N,"k":"left"|"right"|"middle","d":1|0}
             {"t":"w","e":N,"x":dx,"y":dy}                    wheel notches
             {"t":"k","e":N,"c":<evdev code>,"d":1|0|2}  2 = auto-repeat
  health     {"t":"ping","i":N} / {"t":"pong","i":N}
  geometry   {"t":"geom","screens":[..]}
  clipboard  {"t":"clipmeta","origin":..,"seq":N,"formats":[..],"bytes":N}
             {"t":"clipget","seq":N,"format":..}
             {"t":"clipdata","seq":N,"format":..,"i":N,"n":N,"v":..}
"""
from __future__ import annotations

import hashlib
import hmac
import json
import queue
import secrets
import socket
import threading

VERSION = 4
# v4: passwords are compared without their dashes (pairing.normalise), and a
# device can be added from either side (invite). A v3 peer would prove a
# different secret and fail as "wrong password" - refused as a version instead.
# v3: the layout is machines with POSITIONS and displays (desk.py), and the
# pointer crosses wherever displays touch. A v2 peer would read that layout as
# screens with no links and could never cross, so it is refused outright, with
# a message saying to update both sides, instead of half working.

# A peer that sends more than this without a newline is not slow, it is broken or
# hostile: drop the connection rather than buffer it. (Input Leap caps at 4 MiB
# and disconnects; the principle is theirs, the number is ours, because our
# messages are small and bulk data does not travel on this channel.)
MAX_LINE = 64 * 1024

# Clipboard chunk payload, in CHARACTERS. Far under MAX_LINE because JSON
# escaping inflates text badly: json.dumps defaults to ensure_ascii, so a BMP
# character costs 6 bytes (\uXXXX) and an astral one - any emoji - costs 12, as a
# surrogate pair, despite being a single Python character. 4096 * 12 plus frame
# overhead still fits a 64 KiB line with room to spare. Sizing this by len() of
# the raw string would overflow the cap on the first emoji anyone copied.
CLIP_CHUNK = 4096


class ProtocolError(Exception):
    """The peer broke the protocol. Always fatal to the connection."""


def encode(event: dict) -> bytes:
    line = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
    if len(line) > MAX_LINE:
        raise ProtocolError(f"outbound frame is {len(line)}B, over the {MAX_LINE}B cap")
    return line


def decode(line: bytes) -> dict:
    return json.loads(line.decode("utf-8"))


# ------------------------------------------------------------- negotiation
def check_version(msg: dict) -> int:
    """Validate a peer's hello/welcome. Raises rather than failing later.

    Strict equality: a mismatch is a clean, explainable error instead of a
    puzzling parse failure three messages in. Widen this to a range only when
    there is a real older version worth speaking.
    """
    v = msg.get("v")
    if v != VERSION:
        raise ProtocolError(
            f"protocol version mismatch: peer speaks v{v}, we speak v{VERSION}")
    return v


def is_stale(msg: dict, epoch: int) -> bool:
    """Should this message be discarded because the baton has since moved?

    Control messages carry no epoch and are never stale - they are how the epoch
    changes in the first place.
    """
    e = msg.get("e")
    return e is not None and e != epoch


def _tag(ev: dict, epoch) -> dict:
    if epoch is not None:
        ev["e"] = int(epoch)
    return ev


# -------------------------------------------------------------- the secret
# The shared secret is never sent. Each side proves it knows the secret by
# answering the other's nonce, which means a passive listener on the LAN learns
# nothing they can replay or reuse - and, just as important, the answer goes BOTH
# ways, so a machine will not accept injected keystrokes from something that
# cannot prove it knows the password.
#
# The node names are folded into what gets signed. Without them, a proof made by
# the hub could be reflected straight back at the hub as if the attacker had
# made it.
#
# This is authentication, NOT encryption. Once the two sides trust each other the
# input stream still travels in clear, so this is a trusted-LAN tool until TLS
# lands (DESIGN.md section 8). Saying otherwise would be worse than the gap.


def nonce() -> str:
    return secrets.token_hex(16)


def proof(secret: str, challenge: str, prover: str, verifier: str) -> str:
    """Prove to `verifier` that we know the secret, for this challenge only."""
    msg = f"{challenge}|{prover}|{verifier}".encode()
    return hmac.new((secret or "").encode(), msg, hashlib.sha256).hexdigest()


def verify(secret: str, challenge: str, prover: str, verifier: str,
           given: str) -> bool:
    expect = proof(secret, challenge, prover, verifier)
    return hmac.compare_digest(expect, given or "")


def auth(node: str, chal: str, dev_id: str = None) -> dict:
    """The listener speaks first, so the caller has something to answer.

    `id` is the device's permanent ID. The caller checks it before going any
    further: an address found by name, or remembered from last time, may since
    have been handed to a different machine, and connecting to the wrong one
    should end here rather than at a password failure nobody can explain.
    """
    ev = {"t": "auth", "v": VERSION, "node": node, "nonce": chal}
    if dev_id:
        ev["id"] = dev_id
    return ev


# --------------------------------------------------------------- handshake
def hello(node: str, screens, policy: dict = None, pin: str = "",
          resume: str = None, proof: str = None, chal: str = None,
          dev_id: str = None, join: bool = False, invite: bool = False,
          probe: bool = False) -> dict:
    """Both peers send this; neither is 'the client'.

    `proof` answers the listener's challenge; `chal` is our own challenge for it
    to answer in return. `pin` is the old plaintext field, kept only so a v2 peer
    that has not been updated still gets a clear rejection rather than a
    confusing hang.

    `resume` is a session id from a previous connection. It gates BULK state
    only - file offsets, the last clipboard announcement. Input state is never
    resumed.
    """
    ev = {"t": "hello", "v": VERSION, "node": node, "screens": list(screens)}
    if dev_id:
        ev["id"] = dev_id
    if policy:
        ev["policy"] = dict(policy)
    if proof:
        ev["proof"] = proof
    if chal:
        ev["nonce"] = chal
    if pin:
        ev["pin"] = pin
    if resume:
        ev["resume"] = resume
    if join:
        ev["join"] = True
    if invite:
        ev["invite"] = True
    if probe:
        ev["probe"] = True
    return ev


def welcome(node: str, epoch: int, holder: str, layout: dict, screen: str,
            x: int = 0, y: int = 0, resumed: bool = False,
            session: str = None, proof: str = None) -> dict:
    ev = {"t": "welcome", "v": VERSION, "node": node, "epoch": int(epoch),
          "holder": holder, "layout": layout, "screen": screen,
          "x": int(x), "y": int(y), "resumed": bool(resumed)}
    if session:
        ev["session"] = session
    if proof:
        ev["proof"] = proof          # our answer to the caller's challenge
    return ev


def err(msg: str, code: str = None) -> dict:
    """A refusal. `code` is for the program - "auth" is a wrong password, which
    the dialling side must stop retrying and ask a person about - and `msg` is
    for the person."""
    ev = {"t": "err", "msg": msg}
    if code:
        ev["code"] = code
    return ev


# ----------------------------------------------------- joining and leaving
# Adding a device from EITHER side. "Join" is the simple one: the new device
# types the group's password and dials the hub. "Invite" is the other way
# round: a device already in a group types the NEW device's password, proves
# it, and tells the new device where the group is and what its password is.
#
# That password must not cross the network in the clear - anything that heard
# it could join the group. So it is wrapped under a key only the two ends can
# make: the new device's password, which both know and never send, mixed with
# both challenges from this handshake. An eavesdropper has the challenges and
# the proofs, which let it guess the new device's password offline - the same
# exposure every handshake has, and the reason passwords are generated (59
# bits). HMAC-SHA256 as a keystream and as a MAC: the standard library only.

def _wrap_key(pin: str, chal_a: str, chal_b: str) -> bytes:
    msg = f"nishro-link wrap|{chal_a}|{chal_b}".encode()
    return hmac.new((pin or "").encode(), msg, hashlib.sha256).digest()


def _stream(key: bytes, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(key, b"ks" + i.to_bytes(4, "big"), hashlib.sha256).digest()
        i += 1
    return out[:n]


def wrap(secret: str, pin: str, chal_a: str, chal_b: str) -> dict:
    """`secret` sealed under `pin` and this handshake's two challenges."""
    key = _wrap_key(pin, chal_a, chal_b)
    data = (secret or "").encode("utf-8")
    ct = bytes(x ^ y for x, y in zip(data, _stream(key, len(data))))
    tag = hmac.new(key, b"tag" + ct, hashlib.sha256).hexdigest()
    return {"ct": ct.hex(), "tag": tag}


def unwrap(box: dict, pin: str, chal_a: str, chal_b: str) -> str:
    """The secret, or ValueError if it was not sealed with this pin."""
    try:
        ct = bytes.fromhex(str(box["ct"]))
        tag = str(box["tag"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"not a wrapped secret: {e}") from None
    key = _wrap_key(pin, chal_a, chal_b)
    want = hmac.new(key, b"tag" + ct, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, tag):
        raise ValueError("the secret was not sealed with this password")
    return bytes(x ^ y for x, y in zip(ct, _stream(key, len(ct)))).decode("utf-8")


def invite_ok(node: str, proof: str) -> dict:
    return {"t": "invite_ok", "v": VERSION, "node": node, "proof": proof}


def invite(hub: str, hub_id: str, addr: str, port: int, secret: dict) -> dict:
    return {"t": "invite", "hub": hub, "hub_id": hub_id, "addr": addr,
            "port": int(port), "secret": secret}


def invite_done(ok: bool, msg: str = "") -> dict:
    ev = {"t": "invite_done", "ok": bool(ok)}
    if msg:
        ev["msg"] = msg
    return ev


def removed(by: str) -> dict:
    return {"t": "removed", "by": by}


def leave() -> dict:
    return {"t": "leave"}


# ------------------------------------------------------------------- baton
def claim(node: str, reason: str = "motion") -> dict:
    return {"t": "claim", "node": node, "reason": reason}


def baton(holder: str, epoch: int, screen: str, x: int = 0, y: int = 0,
          held=(), toggles: dict = None) -> dict:
    """The hub's authoritative answer to who is driving.

    Carries the held-key set so the new holder makes its state match rather than
    inferring it from a stream whose start it may have missed - the self-healing
    form of P3.
    """
    ev = {"t": "baton", "holder": holder, "epoch": int(epoch), "screen": screen,
          "x": int(x), "y": int(y), "held": [int(c) for c in held]}
    if toggles:
        ev["toggles"] = dict(toggles)
    return ev


def release(node: str) -> dict:
    """Hand the baton back voluntarily (failsafe, sleep, policy change)."""
    return {"t": "release", "node": node}


# ------------------------------------------------------------------- input
def pos(screen: str, nx: float, ny: float, epoch=None) -> dict:
    """Absolute cursor position, normalised to the destination screen.

    Normalised rather than pixels so a peer that changes resolution mid-session
    lands somewhere sane instead of off-screen. Absolute rather than a delta
    because LineChannel drops under pressure by design, and a dropped delta is a
    permanent offset while a dropped position costs one stale frame.
    """
    return _tag({"t": "p", "s": screen,
                 "x": round(float(nx), 5), "y": round(float(ny), 5)}, epoch)


def button(k: str, down: bool, epoch=None) -> dict:
    return _tag({"t": "b", "k": k, "d": 1 if down else 0}, epoch)


def wheel(dx: int, dy: int, epoch=None) -> dict:
    return _tag({"t": "w", "x": int(dx), "y": int(dy)}, epoch)


KEY_UP, KEY_DOWN, KEY_REPEAT = 0, 1, 2


def key(code: int, down, epoch=None) -> dict:
    """`down` is a bool, or KEY_REPEAT for auto-repeat.

    Auto-repeat has to travel as its own value, not as another KEY_DOWN. Holding
    a key on Windows produces a stream of key-DOWN events, and forwarding those
    verbatim gave Linux duplicate presses with no release between them: libinput
    filters them, and each one restarts the compositor's repeat timer so it never
    reaches the repeat delay. Holding "d" typed exactly one d.

    int() over both bool and int: True is 1 and False is 0 in Python, so one
    field carries all three states without callers having to care which they hold.
    """
    return _tag({"t": "k", "c": int(code), "d": int(down)}, epoch)


# ------------------------------------------------------------------ health
def ping(i: int) -> dict:
    return {"t": "ping", "i": int(i)}


def pong(i: int) -> dict:
    return {"t": "pong", "i": int(i)}


def layout_msg(layout: dict, placement=None) -> dict:
    """The arrangement, from the hub, which keeps the one true copy.

    Sent after the handshake and again whenever the arrangement changes. It used
    to travel only in the handshake, so a change made mid-session reached the
    hub and not the peer - and whichever machine held the baton decided where
    the cursor crossed, using its own copy. The user dragged the AIO to the left,
    pressed Apply, and pushing right still went to the AIO.
    """
    return {"t": "layout", "layout": layout, "placement": list(placement or [])}


def roster(online, devices=None) -> dict:
    """Who is connected, from the hub, to everyone - on every join and leave.

    A peer talks only to the hub, so without this it could not know which
    other machines are there: pushing the pointer at a device that had left
    would freeze it, and one that had just joined could not be reached.
    """
    ev = {"t": "roster", "online": sorted(online)}
    if devices is not None:
        ev["devices"] = list(devices)
    return ev


def geom(screens) -> dict:
    """This machine's displays changed - a monitor plugged in or unplugged."""
    return {"t": "geom", "screens": list(screens)}


# Messages only the hub may originate. A peer that sends one is not relayed
# and not believed: every member of the group knows the password, but only the
# hub decides who holds control, what the arrangement is and who is here.
HUB_ONLY = frozenset({"baton", "layout", "roster", "welcome", "removed",
                      "rekey"})

# Answered on the link they arrived on, never routed or relayed.
HOP_LOCAL = frozenset({"ping", "pong", "err"})


def arrange(placement) -> dict:
    """A machine that is not the hub asks the hub to rearrange the desk."""
    return {"t": "arrange", "placement": list(placement)}


# --------------------------------------------------------------- clipboard
def clipmeta(origin: str, seq: int, formats, nbytes: int) -> dict:
    """Announce a clipboard change. The data itself is only sent if asked for.

    `origin` and `seq` stop a change echoing back to the machine it came from,
    which matters now that any node may originate one.
    """
    return {"t": "clipmeta", "origin": origin, "seq": int(seq),
            "formats": list(formats), "bytes": int(nbytes)}


def clipget(seq: int, fmt: str = "text") -> dict:
    return {"t": "clipget", "seq": int(seq), "format": fmt}


def clipdata(seq: int, fmt: str, i: int, n: int, v: str) -> dict:
    return {"t": "clipdata", "seq": int(seq), "format": fmt,
            "i": int(i), "n": int(n), "v": v}


class LineChannel:
    """Buffered newline-delimited JSON over a socket.

    send() NEVER blocks the calling thread and never touches the socket
    directly: it enqueues onto a bounded queue that a single dedicated sender
    thread drains. This matters because capture calls send() from inside a
    WH_MOUSE_LL hook callback - if that callback blocks on real network I/O (a
    stalled Wi-Fi link, a slow receiver, a reconnect), Windows freezes ALL mouse
    input system-wide for as long as the callback takes to return. Queueing
    removes that class of bug entirely: a stalled connection can only fill the
    queue and start dropping the newest events (acceptable - a stale position is
    corrected by the next one; a frozen real mouse is not), never block the
    caller. That is P1.
    """

    def __init__(self, sock: socket.socket, queue_size: int = 512):
        self.sock = sock
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._buf = b""
        self._q: queue.Queue = queue.Queue(maxsize=queue_size)
        self._dropped = 0
        self._sender = threading.Thread(target=self._send_loop, daemon=True)
        self._sender.start()

    @property
    def dropped(self) -> int:
        """How many events the queue has shed. The control API reports this -
        a rising number is the honest answer to 'why does it feel laggy'."""
        return self._dropped

    def send(self, event: dict) -> None:
        """Queue an event. Never blocks, and never keeps stale frames over fresh
        ones.

        When the queue is full the link is not keeping up, and what we discard
        matters. Dropping the INCOMING event leaves a backlog of stale positions
        to deliver, so the cursor arrives late and keeps arriving late - the
        queue becomes a latency buffer. Since positions are absolute, the newest
        frame is the only accurate one, so we make room by discarding the OLDEST
        instead. Queue depth then bounds how far behind we can ever be rather
        than how much lag we accumulate.
        """
        try:
            self._q.put_nowait(event)
            return
        except queue.Full:
            pass
        try:
            self._q.get_nowait()        # evict the oldest, almost always a position
            self._dropped += 1
        except queue.Empty:
            pass
        try:
            self._q.put_nowait(event)
        except queue.Full:
            self._dropped += 1          # a drainer beat us to it; let this one go

    def _send_loop(self) -> None:
        while True:
            event = self._q.get()
            if event is None:          # shutdown sentinel from close()
                return
            try:
                self.sock.sendall(encode(event))
            except ProtocolError:
                self._dropped += 1     # our own bug: oversized frame. Don't die for it.
            except OSError:
                pass  # connection is broken; recv() elsewhere will notice and clean up

    def recv(self) -> dict | None:
        """Return the next event, or None when the peer closes the connection.

        Raises ProtocolError on a frame that is oversized or undecodable. Without
        the cap a peer that never sends a newline grows _buf until the process
        dies - a free denial of service against a machine that owns a mouse.
        """
        while b"\n" not in self._buf:
            if len(self._buf) > MAX_LINE:
                raise ProtocolError(
                    f"peer sent {len(self._buf)}B with no frame end; cap is {MAX_LINE}B")
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        if len(line) > MAX_LINE:
            raise ProtocolError(f"frame is {len(line)}B, over the {MAX_LINE}B cap")
        try:
            return decode(line)
        except (ValueError, UnicodeDecodeError) as e:
            raise ProtocolError(f"undecodable frame: {e}") from None

    def close(self, flush: float = 0.0) -> None:
        """Close. With `flush`, first give what is queued up to that many
        seconds to go out - a last "you were removed" or "I am leaving" is
        worthless if the socket closes under it."""
        try:
            if flush:
                self._q.put(None, timeout=flush)
                self._sender.join(flush)
            else:
                self._q.put_nowait(None)   # best-effort: wake the sender to exit
        except queue.Full:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
