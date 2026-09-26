"""Dialling by name instead of address. See discovery.py and Node._target.

The live tests keep discovery on loopback (conftest switches broadcasting off
for the whole suite), with the hub's responder the only one on the port - in
a real deployment each machine has its own port 8770, but here both ends share
one machine.
"""
import time

import pytest

from link import protocol
from link.discovery import Found
from link.desk import simple
from link.node import Node, NodeCore

from test_node_live import FakeCapture, FakeInjector, Pair


def wait(cond, what, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {what}")


def by_name(name="laptop"):
    """A Pair whose peer knows the hub by NAME only - no address at all."""
    p = Pair()
    p.aio.peer_addr = None
    p.aio.peer_name = name
    p.aio.discover_hints = ("127.0.0.1",)
    p.aio._start_responder = lambda: None       # the port's one responder: the hub's
    p.learned = []
    p.aio.on_paired = lambda *a: p.learned.append(a)
    return p


# ------------------------------------------------------------------ live
def test_a_device_is_found_and_dialled_by_name_alone():
    p = by_name()
    try:
        p.start().connected()
        wait(lambda: p.learned, "the pairing to be learned")
        name, dev_id, addr = p.learned[-1]
        assert name == "laptop"
        assert dev_id == p.hub.device_id, "the ID is what survives a rename"
        assert addr == "127.0.0.1", "and the address is remembered for next time"
        assert (p.aio.peer_id, p.aio.peer_addr) == (dev_id, addr)
    finally:
        p.stop()


def test_the_name_is_matched_whatever_its_case():
    p = by_name("LAPTOP")
    try:
        p.start().connected()
    finally:
        p.stop()


def test_a_name_nobody_answers_to_never_connects():
    p = by_name("kitchen-pc")
    lines = []
    p.aio._log = lines.append
    try:
        p.start()
        wait(lambda: any("cannot find 'kitchen-pc'" in x for x in lines),
             "it to say it cannot find the device")
        assert p.aio.ch is None
    finally:
        p.stop()


def test_a_hub_says_who_it_is_before_anything_else():
    msg = protocol.auth("laptop", "n0nce", "dev-1")
    assert (msg["node"], msg["id"]) == ("laptop", "dev-1")


# ------------------------------------------------------ choosing a target
def dialler(**kw):
    lay = simple("aio", (1920, 1080), "laptop", (1366, 768), "right")
    n = Node(NodeCore("aio", lay, {}, is_hub=False), FakeCapture(),
             FakeInjector(), port=8770, **kw)
    n.lines = []
    n._log = n.lines.append
    return n


def found(addr, waiting=True, name="laptop", dev_id="lap-1"):
    return Found(name, dev_id, addr, 8770, waiting)


def test_the_remembered_address_is_tried_first():
    """It is usually still right, and trying it costs nothing."""
    n = dialler(peer_name="laptop", peer_addr="192.168.1.10")
    n.find = lambda q: pytest.fail("searched when the address was never tried")
    assert n._target() == ("192.168.1.10", 8770)


def test_after_two_misses_a_search_takes_over():
    """A DHCP lease that moved will not move back, however long we wait."""
    n = dialler(peer_name="laptop", peer_addr="192.168.1.10")
    n.backoff.on_failure()
    n.backoff.on_failure()
    asked = []
    n.find = lambda q: asked.append(q) or [found("192.168.1.30")]
    assert n._target() == ("192.168.1.30", 8770)
    assert any("found 'laptop' at 192.168.1.30" in x for x in n.lines)


def test_a_known_device_is_looked_up_by_id_not_name():
    """So renaming it does not lose it."""
    n = dialler(peer_name="laptop", peer_id="lap-1")
    asked = []
    n.find = lambda q: asked.append(q) or [found("10.0.0.5")]
    n._target()
    assert asked == ["lap-1"]


def test_a_device_that_is_not_waiting_is_not_dialled():
    n = dialler(peer_name="laptop")
    n.find = lambda q: [found("10.0.0.5", waiting=False)]
    assert n._target() is None


def test_a_bare_address_still_works_when_that_is_all_there_is():
    """The Advanced fallback, for a network that drops the search."""
    n = dialler(peer_addr="192.168.1.10")
    n.find = lambda q: pytest.fail("nothing to search for")
    assert n._target() == ("192.168.1.10", 8770)


class OneMessage:
    """A channel that says one thing and records what is sent back."""

    def __init__(self, msg):
        self.msg, self.sent = msg, []

    def recv(self):
        m, self.msg = self.msg, None
        return m

    def send(self, m):
        self.sent.append(m)


def test_a_different_device_at_the_remembered_address_is_refused():
    """DHCP gave the address to another machine running Nishro Link. That must
    end at 'wrong machine', before any proof is sent, and the stale address
    must be dropped so the next attempt searches by name."""
    n = dialler(peer_name="laptop", peer_id="lap-1", peer_addr="192.168.1.10")
    ch = OneMessage(protocol.auth("desk-pc", "n0nce", "desk-9"))
    assert n._say_hello(ch, "192.168.1.10") is False
    assert ch.sent == [], "answered a machine it was not paired with"
    assert n.peer_addr is None
    assert any("is now 'desk-pc', not 'laptop'" in x for x in n.lines)


def test_a_renamed_device_is_still_accepted_by_its_id():
    n = dialler(peer_name="laptop", peer_id="lap-1", peer_addr="192.168.1.10")
    assert n._expected("alex-laptop", "lap-1") is True
    assert n._expected("laptop", "someone-else") is False


def test_the_responder_answers_with_this_devices_identity():
    n = dialler(device_id="aio-7")
    me = n.identity()
    assert (me["name"], me["id"], me["port"], me["waiting"]) == \
        ("aio", "aio-7", 8770, False)
