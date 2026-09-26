"""Finding a device by NAME on the local network, so nobody types an IP address.

Addresses on a home network come from DHCP and change: a lease runs out, a
router restarts, a laptop moves between access points. So a pairing is recorded
as a name and a permanent device ID, and the address is looked up whenever it is
needed - tried from memory first, because it usually has not changed, and found
again here when it has.

WHY NOT ARP OR PING. Both answer the wrong question. ARP maps an address to a
hardware address; a ping says a machine is there. Neither says what it is
called or whether it runs Nishro Link, which is the only thing worth knowing.
Only the device itself can say that, so each one answers a short question:

    who   {"t":"who","q":"aio","from":<our id>}      broadcast + multicast
    here  {"t":"here","name":"aio","id":..,"port":8770,"waiting":true,
           "group":"laptop","alone":false}                      unicast

`group` is the name of the hub of the group the device is in (its own name if
it is the hub), and `alone` says it is a group of one - which is what decides
what "add this device" means: one on its own is invited into this group; one
already in a group is joined.

`q` is a name (case does not matter), a device ID, or "*" for everyone. The
question goes to the broadcast address, to each local /24's broadcast address,
and to a site-local multicast group, three times over about a second because UDP
may drop any one of them. Answers come straight back to the asker.

UDP port = the link's TCP port, 8770, so there is one number to open in a
firewall rather than two. Questions are also SENT from 8770 whenever this
device is running its responder, so answers come back to 8770 too. From a
random port they did not survive ufw: Linux connection tracking cannot match an
answer from 192.168.1.10 to a question that went to 255.255.255.255, so the
answer was dropped as unsolicited - and no rule for 8770 could let it in,
because it was addressed to the random port. Found on the Ubuntu box.

TRUST. An answer is not trusted and cannot be: anything on the network can
claim to be "aio". It does not need to be trusted, because the link handshake
that follows still requires proof of the password, and nothing is injected into
a machine that has not proved it. What a fake CAN do is collect one proof and
guess passwords offline - which is why generated passwords are long.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from dataclasses import dataclass

from . import runtime

GROUP = "239.255.87.70"     # administratively scoped: routers keep it on site
# Off switch for broadcasting, flipped by the test suite: tests run on the same
# network as the real laptop and AIO, and must never ask them anything.
NETWORK = True
MAX_DATAGRAM = 1024
APP = "nishro-link"


@dataclass(frozen=True)
class Found:
    name: str
    id: str
    addr: str
    port: int
    waiting: bool
    group: str = None                 # its hub's name; its own if it is the hub
    alone: bool = False               # a group of one: it can be invited


# ---------------------------------------------------------------- messages
def question(q: str, sender_id: str) -> bytes:
    return _pack({"t": "who", "app": APP, "q": str(q), "from": str(sender_id)})


def answer(name: str, dev_id: str, port: int, waiting: bool, group: str = None,
           alone: bool = False) -> bytes:
    d = {"t": "here", "app": APP, "name": str(name), "id": str(dev_id),
         "port": int(port), "waiting": bool(waiting), "alone": bool(alone)}
    if group:
        d["group"] = str(group)
    return _pack(d)


def parse(data: bytes):
    """A message of ours, or None for anything else that reached the port."""
    if not data or len(data) > MAX_DATAGRAM:
        return None
    try:
        msg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("app") != APP:
        return None
    return msg


def matches(q: str, name: str, dev_id: str) -> bool:
    q = (q or "").strip()
    if not q:
        return False
    return q == "*" or q == dev_id or q.casefold() == (name or "").casefold()


def _pack(d: dict) -> bytes:
    return json.dumps(d, separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------- answering
class Responder:
    """Answers "who is ...?" for this device. Runs for as long as the program.

    `identity` is called for every question rather than captured once, so a
    rename, a new port or a change of role is answered correctly at once.
    """

    def __init__(self, port: int, identity, log=None):
        self.port = int(port)
        self.identity = identity          # () -> {name, id, port, waiting}
        self.log = log or (lambda *_: None)
        self._sock = None
        self._stop = False
        self._last = {}                   # source -> when we last answered it
        # A search in progress on this socket: answers are routed here by the
        # receive loop. One at a time; a second waits for the first.
        self._asking = None
        self._ask_lock = threading.Lock()

    def start(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            runtime.exclusive(s)
            s.bind(("", self.port))
        except OSError as e:
            s.close()
            self.log(f"discovery: cannot listen on UDP {self.port} ({e}) - other "
                     f"devices will not find this one by name")
            return False
        _join_group(s)
        s.settimeout(0.5)
        self._sock = s
        self._stop = False
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def stop(self) -> None:
        self._stop = True
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def _run(self) -> None:
        s = self._sock
        while not self._stop and s is not None:
            try:
                data, src = s.recvfrom(MAX_DATAGRAM + 1)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows: a previous answer met a closed port, and the ICMP
                # that came back is reported on the NEXT receive. It says
                # nothing about this socket; carry on.
                continue
            except OSError:
                return                        # closed by stop()
            self._handle(s, data, src)

    def ask(self, q: str, port: int, my_id: str, timeout: float = 1.2,
            hints=(), broadcast: bool = True, rounds: int = 3) -> list:
        """find(), sent from this responder's own port - see the module notes."""
        s = self._sock
        if s is None:
            return find(q, port, my_id, timeout, hints, broadcast, rounds)
        with self._ask_lock:
            found = {}
            self._asking = (my_id, found)
            try:
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                except OSError:
                    pass
                targets = _targets(port, hints, broadcast)
                pkt = question(q, my_id)
                gap = timeout / max(1, rounds)
                for _ in range(max(1, rounds)):
                    for tgt in targets:
                        try:
                            s.sendto(pkt, tgt)
                        except OSError:
                            pass
                    time.sleep(gap)
            finally:
                self._asking = None
        return _ranked(found)

    def _handle(self, s, data, src) -> None:
        msg = parse(data)
        if msg and msg.get("t") == "here":
            asking = self._asking
            if asking is not None:
                _record(asking[1], msg, src[0], asking[0])
            return
        if not msg or msg.get("t") != "who":
            return
        me = self.identity()
        if msg.get("from") == me["id"]:
            return                            # our own question, looped back
        if not matches(msg.get("q", ""), me["name"], me["id"]):
            return
        now = time.monotonic()
        if now - self._last.get(src, 0.0) < 0.1:
            return                            # three copies of one question
        self._last[src] = now
        if len(self._last) > 256:
            self._last = {k: v for k, v in self._last.items() if now - v < 5}
        try:
            s.sendto(answer(me["name"], me["id"], me["port"], me["waiting"],
                            me.get("group"), me.get("alone", False)), src)
        except OSError:
            pass


# ------------------------------------------------------------------ asking
def find(q: str, port: int, my_id: str = "", timeout: float = 1.2,
         hints=(), broadcast: bool = True, rounds: int = 3) -> list:
    """Every device answering to `q`, as Found, best first.

    `hints` are addresses to ask directly as well - the last address a peer
    had, which still reaches it on a network that drops broadcasts.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        except OSError:
            pass
        s.bind(("", 0))
        targets = _targets(port, hints, broadcast)
        pkt = question(q, my_id)
        found = {}
        deadline = time.monotonic() + timeout
        gap = timeout / max(1, rounds)
        for i in range(max(1, rounds)):
            for t in targets:
                try:
                    s.sendto(pkt, t)
                except OSError:
                    pass                      # one unreachable target is not fatal
            _collect(s, found, my_id, min(deadline,
                                          time.monotonic() + gap))
        _collect(s, found, my_id, deadline)
    finally:
        s.close()
    return _ranked(found)


def _targets(port: int, hints, broadcast: bool) -> list:
    targets = [(h, port) for h in hints if h]
    if broadcast and NETWORK:
        targets += [("255.255.255.255", port), (GROUP, port)]
        targets += [(_broadcast24(a), port) for a in local_ipv4s()]
    return list(dict.fromkeys(targets))


def _ranked(found: dict) -> list:
    # Devices waiting for a connection first, since those are the ones that can
    # be connected to; then by name.
    return sorted(found.values(), key=lambda f: (not f.waiting, f.name.casefold()))


def _record(found: dict, msg: dict, addr: str, my_id: str) -> None:
    if msg.get("id") in (None, my_id):
        return
    try:
        f = Found(str(msg["name"]), str(msg["id"]), addr,
                  int(msg.get("port") or 0), bool(msg.get("waiting")),
                  str(msg["group"]) if msg.get("group") else None,
                  bool(msg.get("alone")))
    except (KeyError, TypeError, ValueError):
        return
    found.setdefault(f.id, f)


def _collect(s, found: dict, my_id: str, until: float) -> None:
    while True:
        left = until - time.monotonic()
        if left <= 0:
            return
        s.settimeout(left)
        try:
            data, (addr, _) = s.recvfrom(MAX_DATAGRAM + 1)
        except socket.timeout:
            return
        except ConnectionResetError:
            continue                          # Windows: see Responder._run
        except OSError:
            return
        msg = parse(data)
        if msg and msg.get("t") == "here":
            _record(found, msg, addr, my_id)


# ----------------------------------------------------------------- network
def local_ipv4s() -> list:
    """This machine's IPv4 addresses, the one with the default route first."""
    out = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # connect() on UDP sends nothing; it only asks the OS which
            # interface it would use, which is the one other devices share.
            probe.connect(("192.0.2.1", 9))
            out.append(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for fam, _, _, _, sa in socket.getaddrinfo(socket.gethostname(), None):
            if fam == socket.AF_INET:
                out.append(sa[0])
    except OSError:
        pass
    return [a for a in dict.fromkeys(out)
            if not a.startswith(("127.", "169.254.", "0."))]


def _broadcast24(addr: str) -> str:
    """The /24 broadcast address. Most home networks are /24, and where one is
    not, the 255.255.255.255 broadcast sent alongside still covers it."""
    a, b, c, _ = addr.split(".")
    return f"{a}.{b}.{c}.255"


def _join_group(s) -> None:
    """Receive the multicast question too, on every interface we can. Best
    effort: broadcast already reaches most networks, multicast covers the ones
    that filter broadcast but pass multicast."""
    for iface in ["0.0.0.0"] + local_ipv4s():
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                         struct.pack("4s4s", socket.inet_aton(GROUP),
                                     socket.inet_aton(iface)))
        except OSError:
            pass
