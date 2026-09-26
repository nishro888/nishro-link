"""Finding devices by name. See discovery.py.

Driven over loopback with broadcast off, so a test run never sprays the real
network - the laptop and the AIO are on it, and a stray answer from a real
device would make these tests depend on what else is switched on.
"""
import socket
import time

import pytest

from link import discovery


def free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def aio():
    """A device called "aio", waiting for a connection, answering on loopback."""
    me = {"name": "aio", "id": "a1a1a1a1", "port": 8770, "waiting": True}
    r = discovery.Responder(free_udp_port(), lambda: me)
    assert r.start()
    r.me = me
    yield r
    r.stop()


def ask(r, q, my_id="laptop-id"):
    return discovery.find(q, r.port, my_id=my_id, timeout=0.6,
                          hints=["127.0.0.1"], broadcast=False)


# ------------------------------------------------------------- messages
def test_a_question_and_an_answer_round_trip():
    q = discovery.parse(discovery.question("aio", "x1"))
    assert (q["t"], q["q"], q["from"]) == ("who", "aio", "x1")
    a = discovery.parse(discovery.answer("aio", "a1", 8770, True))
    assert (a["t"], a["name"], a["id"], a["port"], a["waiting"]) == \
        ("here", "aio", "a1", 8770, True)


@pytest.mark.parametrize("junk", [b"", b"hello", b"\xff\xfe", b"[1,2]",
                                  b'{"t":"who","q":"aio"}',          # not ours
                                  b"x" * 5000])
def test_anything_else_on_the_port_is_ignored(junk):
    assert discovery.parse(junk) is None


@pytest.mark.parametrize("q,ok", [("aio", True), ("AIO", True), (" aio ", True),
                                  ("a1a1", True), ("*", True), ("laptop", False),
                                  ("", False), ("ai", False)])
def test_matching_by_name_id_or_everyone(q, ok):
    assert discovery.matches(q, "aio", "a1a1") is ok


# ------------------------------------------------------------ over UDP
def test_a_device_is_found_by_its_name(aio):
    found = ask(aio, "aio")
    assert [(f.name, f.id, f.addr, f.waiting) for f in found] == \
        [("aio", "a1a1a1a1", "127.0.0.1", True)]


def test_the_name_is_not_case_sensitive(aio):
    assert [f.name for f in ask(aio, "AIO")] == ["aio"]


def test_a_device_is_found_by_its_id(aio):
    """How a paired peer is looked up: the ID survives a rename."""
    assert [f.name for f in ask(aio, "a1a1a1a1")] == ["aio"]


def test_a_different_name_gets_no_answer(aio):
    assert ask(aio, "laptop") == []


def test_everyone_answers_a_star(aio):
    assert [f.name for f in ask(aio, "*")] == ["aio"]


def test_a_device_does_not_answer_its_own_question(aio):
    """Its own broadcast comes back to it; answering would list itself."""
    assert ask(aio, "*", my_id="a1a1a1a1") == []


def test_a_rename_is_answered_at_once(aio):
    aio.me["name"] = "kitchen-pc"
    assert [f.name for f in ask(aio, "kitchen-pc")] == ["kitchen-pc"]
    assert ask(aio, "aio") == []


def test_it_says_whether_it_is_waiting_for_a_connection(aio):
    aio.me["waiting"] = False
    assert [f.waiting for f in ask(aio, "aio")] == [False]


def test_one_dead_address_does_not_stop_the_search(aio):
    """Windows reports an ICMP 'port unreachable' from one send on the NEXT
    receive. A hint pointing at nothing must not end the search early."""
    dead = free_udp_port()
    found = discovery.find("aio", aio.port, my_id="x", timeout=0.6,
                           hints=["127.0.0.1"], broadcast=False)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b"nothing here", ("127.0.0.1", dead))   # provoke the ICMP
    s.close()
    assert [f.name for f in found] == ["aio"]
    assert [f.name for f in ask(aio, "aio")] == ["aio"]   # and it still answers


def test_the_responder_survives_garbage(aio):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for junk in (b"", b"\x00" * 900, b"{", b'{"app":"nishro-link","t":"who"}'):
        s.sendto(junk, ("127.0.0.1", aio.port))
    s.close()
    time.sleep(0.1)
    assert [f.name for f in ask(aio, "aio")] == ["aio"]


def test_waiting_devices_are_listed_first():
    ports = []
    rs = []
    for name, waiting in (("zeta", True), ("alpha", False), ("mid", True)):
        me = {"name": name, "id": name + "-id", "port": 8770, "waiting": waiting}
        r = discovery.Responder(free_udp_port(), lambda me=me: me)
        assert r.start()
        rs.append(r)
        ports.append(r.port)
    try:
        found = []
        for p in ports:
            found += discovery.find("*", p, my_id="x", timeout=0.4,
                                    hints=["127.0.0.1"], broadcast=False)
        order = sorted(found, key=lambda f: (not f.waiting, f.name.casefold()))
        assert [f.name for f in order] == ["mid", "zeta", "alpha"]
    finally:
        for r in rs:
            r.stop()


def test_a_second_responder_cannot_take_the_port(aio):
    """Two copies answering on one port would give two answers for one
    device - or, on Windows, the answer from whichever the kernel picked."""
    other = discovery.Responder(aio.port, lambda: aio.me)
    assert other.start() is False


def test_local_addresses_exclude_loopback_and_link_local():
    for a in discovery.local_ipv4s():
        assert not a.startswith(("127.", "169.254.", "0."))


# ------------------------------------------- asking from the responder's port
def test_asking_from_the_responders_own_port_gets_the_answer_there(aio):
    """Why questions are sent from 8770: the answer is addressed to wherever the
    question came from, and ufw drops an answer to a random port because Linux
    cannot match it to a question that went to a broadcast address. Asking from
    the responder means the answer lands on the responder's port - here, the
    only place it can be collected."""
    me = {"name": "laptop", "id": "l1", "port": 8770, "waiting": True}
    asker = discovery.Responder(free_udp_port(), lambda: me)
    assert asker.start()
    try:
        found = asker.ask("aio", aio.port, "l1", timeout=0.6,
                          hints=["127.0.0.1"], broadcast=False)
        assert [(f.name, f.addr) for f in found] == [("aio", "127.0.0.1")]
    finally:
        asker.stop()


def test_a_responder_still_answers_while_it_is_asking(aio):
    """Asking and answering share one socket; neither may starve the other."""
    me = {"name": "laptop", "id": "l1", "port": 8770, "waiting": True}
    asker = discovery.Responder(free_udp_port(), lambda: me)
    assert asker.start()
    try:
        import threading
        t = threading.Thread(target=lambda: asker.ask(
            "nobody", aio.port, "l1", timeout=0.8, hints=["127.0.0.1"],
            broadcast=False))
        t.start()
        time.sleep(0.1)
        got = discovery.find("laptop", asker.port, my_id="x", timeout=0.4,
                             hints=["127.0.0.1"], broadcast=False)
        t.join()
        assert [f.name for f in got] == ["laptop"]
    finally:
        asker.stop()


def test_a_stopped_responder_falls_back_to_a_plain_search(aio):
    me = {"name": "laptop", "id": "l1", "port": 8770, "waiting": True}
    asker = discovery.Responder(free_udp_port(), lambda: me)
    found = asker.ask("aio", aio.port, "l1", timeout=0.6, hints=["127.0.0.1"],
                      broadcast=False)                 # never started
    assert [f.name for f in found] == ["aio"]
