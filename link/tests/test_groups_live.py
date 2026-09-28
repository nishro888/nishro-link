"""Groups over real sockets: adding a device from EITHER side, a wrong password,
removing a device, leaving, and a new password reaching everyone.

Reported: add and remove were "not fully user friendly", and after typing a
name and a password "I don't see peer connected or not". These pin down that
each of those ends in a state the window can show - connected, or the reason
not - and never in silence or an endless retry.

Every device here listens on its own port on loopback, as each would on its own
machine; discovery is bypassed by giving the address.
"""
import threading
import time

import pytest

from link import pairing
from link.desk import Desk
from link.node import Node, NodeCore

from test_node_live import FakeCapture, FakeInjector, free_port

LAPTOP_PW = "k7qm-2xvp-9hdt"
AIO_PW = "b3nr-8wzc-4tyh"


def device(name, pin, hub=True, peer=None, port=None, w=1920, h=1080):
    d = Desk(name)
    d.add(name, w, h)
    n = Node(NodeCore(name, d, {}, is_hub=hub), FakeCapture(), FakeInjector(),
             port=port or free_port(), pin=pin,
             peer_addr="127.0.0.1" if peer else None, peer_name=peer)
    n.lines = []
    n._log = n.lines.append
    n._start_responder = lambda: None
    n.events = []
    n.on_event = lambda kind, info: n.events.append((kind, info.get("name")))
    return n


def accept_invites(n):
    """What ControlAPI._on_invited does, without the config file."""
    def cb(hub, hub_id, addr, port, secret, by):
        def go():
            time.sleep(0.2)
            n.reconfigure(hub=False, peer_addr=addr, peer_name=hub, peer_id=hub_id,
                          port=port, pin=secret, joining=True)
            with n._lock:
                n.core.alone()
        threading.Thread(target=go, daemon=True).start()
        return True
    n.on_invited = cb


def wait(cond, what, nodes=(), timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    tail = "\n".join(f"  {n.core.node}: {x}" for n in nodes for x in n.lines[-10:])
    pytest.fail(f"timed out waiting for {what}\n{tail}")


def listening(n, timeout=5.0):
    wait(lambda: any("listening on" in x for x in n.lines),
         f"{n.core.node} to listen", (n,), timeout)


@pytest.fixture
def running():
    nodes = []

    def start(*ns):
        for n in ns:
            nodes.append(n)
            threading.Thread(target=n.run, daemon=True).start()
        # Every hub listening before the test talks to it. Windows retries a
        # refused local connect for a second, which hid this race; Linux
        # refuses at once.
        for n in ns:
            if n.core.is_hub:
                listening(n)
        return ns
    yield start
    for n in nodes:
        n.stop()


# ------------------------------------------------------------ inviting
def test_the_hub_adds_a_device_that_is_on_its_own(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", AIO_PW)
    accept_invites(aio)
    running(laptop, aio)
    r = laptop.invite("aio", "B3NR 8WZC 4TYH", addr="127.0.0.1", port=aio.port)
    assert r == {"ok": True, "reason": None, "detail": "aio"}, laptop.adding
    assert laptop.adding["phase"] == "connected"
    wait(lambda: "aio" in laptop.links and aio.connected(), "aio to join",
         (laptop, aio))
    assert aio.pin == pairing.normalise(LAPTOP_PW), "it now has the group's password"
    assert aio.core.is_hub is False and aio.peer_name == "laptop"
    assert {"laptop", "aio"} <= set(aio.core.layout.names())
    assert ("invited", "laptop") in aio.events


def test_a_member_adds_a_device_to_its_hubs_group(running):
    """From the second AIO's window, not the laptop's: it goes to the laptop."""
    laptop = device("laptop", LAPTOP_PW)
    aio1 = device("aio1", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    aio2 = device("aio2", AIO_PW)
    accept_invites(aio2)
    running(laptop, aio1, aio2)
    wait(lambda: aio1.connected(), "aio1 to join first", (laptop, aio1))
    r = aio1.invite("aio2", AIO_PW, addr="127.0.0.1", port=aio2.port)
    assert r["ok"], aio1.adding
    wait(lambda: {"aio1", "aio2"} <= set(laptop.links), "both in the group",
         (laptop, aio1, aio2))
    assert aio2.peer_name == "laptop", "joined the hub, not the one who asked"


def test_a_wrong_password_is_said_and_nothing_changes(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", AIO_PW)
    accept_invites(aio)
    running(laptop, aio)
    r = laptop.invite("aio", "zzzz-zzzz-zzzz", addr="127.0.0.1", port=aio.port)
    assert (r["ok"], r["reason"]) == (False, "wrong_password")
    assert aio.core.is_hub and aio.pin == pairing.normalise(AIO_PW)
    assert ("rejected", "laptop") in aio.events, "and the other side says so too"


def test_a_device_with_its_own_group_is_not_taken_from_it(running):
    laptop = device("laptop", LAPTOP_PW)
    desk = device("desk", AIO_PW)
    desk.core.layout.add("tablet", 1280, 800, owner="tablet", x=1920, y=0)
    accept_invites(desk)
    running(laptop, desk)
    r = laptop.invite("desk", AIO_PW, addr="127.0.0.1", port=desk.port)
    assert (r["ok"], r["reason"]) == (False, "busy")
    assert "tablet" in r["detail"]
    assert desk.core.is_hub


def test_nobody_listening_is_unreachable_not_a_hang(running):
    laptop = device("laptop", LAPTOP_PW)
    running(laptop)
    t = time.monotonic()
    r = laptop.invite("aio", AIO_PW, addr="127.0.0.1", port=free_port())
    assert r["reason"] == "unreachable" and time.monotonic() - t < 5


# -------------------------------------------------------------- joining
def test_joining_says_connected(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", "K7QM2XVP9HDT", hub=False, peer="laptop",
                 port=laptop.port)
    aio.joining = True
    running(laptop, aio)
    wait(lambda: aio.dial["phase"] == "connected", "the dial to say connected",
         (laptop, aio))
    assert aio.joining is False, "only the first hello is a pairing"
    assert ("connected", "laptop") in aio.events
    assert ("joined", "aio") in laptop.events


def test_a_wrong_password_stops_the_dialling_and_says_why(running):
    """It used to retry forever, and the window said "looking for it"."""
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", "zzzz-zzzz-zzzz", hub=False, peer="laptop",
                 port=laptop.port)
    running(laptop, aio)
    wait(lambda: aio.dial["phase"] == "failed", "the dial to fail", (laptop, aio))
    assert (aio.dial["reason"], aio.blocked) == ("wrong_password",
                                                 "wrong_password")
    time.sleep(1.0)
    refusals = [x for x in laptop.lines if "wrong password" in x]
    assert len(refusals) == 1, "tried once, not over and over"
    # A new pairing - the right password this time - tries again.
    aio.reconfigure(pin=LAPTOP_PW, joining=True)
    wait(lambda: aio.dial["phase"] == "connected", "the second try", (laptop, aio))


# -------------------------------------------------------------- removing
def test_a_removed_device_is_told_and_forgotten(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    told = []
    aio.on_removed = told.append
    running(laptop, aio)
    wait(lambda: "aio" in laptop.links and aio.connected(), "aio to connect",
         (laptop, aio))
    assert laptop.remove("aio") is None
    wait(lambda: told == ["laptop"], "aio to hear it", (laptop, aio))
    wait(lambda: "aio" not in laptop.core.layout.names(), "the hub to forget it",
         (laptop, aio))
    assert aio.blocked == "removed", "and it does not dial back in"


def test_a_device_removed_while_off_is_refused_until_paired_again(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    laptop.removed_ids.add(aio.device_id)
    told = []
    aio.on_removed = told.append
    running(laptop, aio)
    wait(lambda: told == ["laptop"], "aio to be refused", (laptop, aio))
    assert "aio" not in laptop.links
    # Paired again on purpose: in.
    aio.reconfigure(joining=True)
    wait(lambda: "aio" in laptop.links, "aio to be let back in", (laptop, aio))
    assert aio.device_id not in laptop.removed_ids


def test_a_device_that_leaves_is_taken_off_the_hub(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    running(laptop, aio)
    wait(lambda: "aio" in laptop.links and aio.connected(), "aio to connect",
         (laptop, aio))
    aio.leave()
    aio.go_alone(pairing.new_password())
    wait(lambda: "aio" not in laptop.core.layout.names(), "the hub to forget it",
         (laptop, aio))
    assert ("left_group", "aio") in laptop.events
    assert aio.core.is_hub and aio.core.layout.names() == ["aio"]


# ------------------------------------------------------------- passwords
def test_a_new_password_reaches_the_devices_connected_now(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    running(laptop, aio)
    wait(lambda: "aio" in laptop.links and aio.connected(), "aio to connect",
         (laptop, aio))
    new = pairing.new_password()
    assert laptop.rekey(new) == 1
    wait(lambda: aio.pin == pairing.normalise(new), "aio to have it", (laptop, aio))
    # And it works: drop the link, and aio reconnects with the new one.
    laptop._close_links()
    wait(lambda: aio.dial["phase"] == "connected" and "aio" in laptop.links,
         "a reconnect with the new password", (laptop, aio))


# ------------------------------------------------- checking before joining
def test_the_password_is_checked_with_the_hub_before_anything_changes(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", AIO_PW)
    running(laptop, aio)
    r = aio.probe("laptop", "K7QM2XVP9HDT", addr="127.0.0.1", port=laptop.port)
    assert r["ok"] and r["hub"] == "laptop" and r["hub_id"] == laptop.device_id
    assert aio.adding["phase"] == "checked"
    time.sleep(0.2)
    assert not laptop.links and not [e for e in laptop.events if e[0] == "joined"]
    assert aio.core.is_hub, "nothing switched: that is the caller's decision"


def test_a_wrong_password_found_by_checking_changes_nothing(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", AIO_PW)
    running(laptop, aio)
    r = aio.probe("laptop", "wrong-wrong-wrong", addr="127.0.0.1",
                  port=laptop.port)
    assert (r["ok"], r["reason"]) == (False, "wrong_password")
    assert aio.core.is_hub and aio.pin == pairing.normalise(AIO_PW)


def test_naming_a_member_finds_its_hub():
    """Pick aio1 from the list; aio1 is in laptop's group; laptop is dialled."""
    from link.discovery import Found
    n = device("aio2", AIO_PW)
    asked = []

    def find(q):
        asked.append(q)
        return {"aio1": [Found("aio1", "a1", "10.0.0.7", 8770, False, "laptop")],
                "laptop": [Found("laptop", "l1", "10.0.0.5", 8770, True, "laptop")]
                }.get(q, [])
    n.find = find
    n.probe("aio1", LAPTOP_PW, port=free_port())     # nothing listens: unreachable
    assert asked == ["aio1", "laptop"]
    assert n.adding["target"] == "laptop" and n.adding["detail"]


def test_joining_through_the_window_ends_in_connected(tmp_path, running):
    """The whole path the Add dialog takes: check, switch, connect."""
    from link import config, control_api
    from link.runtime import RunLog
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", AIO_PW)
    running(laptop, aio)
    cfg = config.merge(config.DEFAULTS, {"node": "aio", "hub": True,
                                         "pin": AIO_PW, "port": aio.port})
    api = control_api.ControlAPI(aio, cfg, RunLog(tmp_path / "l.log", echo=False),
                                 cfg_path=tmp_path / "c.json", port=0)
    r = api.command("/api/join", {"name": "laptop", "pin": LAPTOP_PW,
                                  "addr": "127.0.0.1", "port": laptop.port})
    assert r == {"ok": True, "started": True}
    wait(lambda: api.status()["adding"]["phase"] == "connected", "connected",
         (laptop, aio))
    s = api.status()
    assert (s["role"], s["group"]) == ("member", "laptop")
    saved = config.load(tmp_path / "c.json")
    assert (saved["peer"], saved["hub"]) == ("laptop", False)
    assert any(e["text"] == "Joined laptop's group" for e in s["events"])


def test_a_failed_join_leaves_the_device_as_it_was(tmp_path, running):
    from link import config, control_api
    from link.runtime import RunLog
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", AIO_PW)
    running(laptop, aio)
    cfg = config.merge(config.DEFAULTS, {"node": "aio", "hub": True,
                                         "pin": AIO_PW, "port": aio.port})
    api = control_api.ControlAPI(aio, cfg, RunLog(tmp_path / "l.log", echo=False),
                                 cfg_path=tmp_path / "c.json", port=0)
    api.command("/api/join", {"name": "laptop", "pin": "nope-nope-nope",
                              "addr": "127.0.0.1", "port": laptop.port})
    wait(lambda: api.status()["adding"]["phase"] == "failed", "the refusal",
         (laptop, aio))
    s = api.status()
    assert s["adding"]["reason"] == "wrong_password"
    assert s["role"] == "alone" and aio.pin == pairing.normalise(AIO_PW)


# -------------------------------------------------------------- renaming
def test_the_hub_renames_itself_and_everyone_follows(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    running(laptop, aio)
    wait(lambda: "aio" in laptop.links and aio.connected(), "aio to connect",
         (laptop, aio))
    laptop.rename("workshop")
    wait(lambda: aio.connected() and aio.trusted_peer == "workshop",
         "aio to reconnect to the renamed hub", (laptop, aio))
    assert "workshop" in aio.core.layout.names()
    assert "laptop" not in aio.core.layout.names()


def test_a_member_renames_itself_and_keeps_its_place(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    running(laptop, aio)
    wait(lambda: "aio" in laptop.links and aio.connected(), "aio to connect",
         (laptop, aio))
    x, y = laptop.core.layout.get("aio").x, laptop.core.layout.get("aio").y
    aio.rename("kitchen")
    wait(lambda: "kitchen" in laptop.links, "kitchen to reconnect", (laptop, aio))
    lay = laptop.core.layout
    assert "aio" not in lay.names(), "the same machine, not a second one"
    assert (lay.get("kitchen").x, lay.get("kitchen").y) == (x, y)
    assert ("renamed", "kitchen") in laptop.events


def test_the_hub_renames_a_device_that_is_on(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    aio.on_rename = aio.rename
    running(laptop, aio)
    wait(lambda: "aio" in laptop.links and aio.connected(), "aio to connect",
         (laptop, aio))
    assert laptop.rename_other("aio", "kitchen") is None
    wait(lambda: "kitchen" in laptop.links, "it to come back renamed",
         (laptop, aio))
    assert aio.core.node == "kitchen"


def test_a_device_renamed_while_off_takes_the_name_when_it_returns(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    aio.on_rename = aio.rename
    running(laptop)
    laptop.core.layout.add("aio", 1920, 1080, owner="aio", x=1920, y=0)
    laptop.names_by_id[aio.device_id] = "aio"
    assert laptop.rename_other("aio", "kitchen") is None
    assert "kitchen" in laptop.core.layout.names(), "renamed here at once"
    running(aio)
    wait(lambda: "kitchen" in laptop.links, "it to return renamed", (laptop, aio))
    assert aio.core.node == "kitchen" and not laptop.pending_names


# ----------------------------------------------------------------- rights
def test_a_device_that_may_not_be_driven_is_a_wall_for_everyone(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    running(laptop, aio)
    wait(lambda: "aio" in laptop.links and aio.connected(), "aio to connect",
         (laptop, aio))
    assert "aio" in laptop.core.reachable()
    assert aio.set_rights("aio", True, False) is None
    wait(lambda: "aio" not in laptop.core.reachable(), "the hub to wall it off",
         (laptop, aio))


def test_the_hub_sets_a_devices_rights_and_it_keeps_them(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    kept = []
    aio.on_policy = kept.append
    running(laptop, aio)
    wait(lambda: "aio" in laptop.links and aio.connected(), "aio to connect",
         (laptop, aio))
    assert laptop.set_rights("aio", False, True) is None
    wait(lambda: kept and kept[-1]["may_drive"] is False, "aio to apply it",
         (laptop, aio))
    assert aio.core.may_drive() is False
    wait(lambda: laptop.core.rights.get("aio", {}).get("may_drive") is False,
         "the hub to hear it back", (laptop, aio))


def test_every_device_learns_the_others_versions_and_rights(running):
    laptop = device("laptop", LAPTOP_PW)
    aio = device("aio", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    running(laptop, aio)
    wait(lambda: "aio" in laptop.links and aio.connected(), "aio to connect",
         (laptop, aio))
    assert laptop.versions.get("aio") == aio.version
    laptop.core.devices = [{"name": "laptop", "hub": True}, {"name": "aio"}]
    laptop._broadcast_group()
    wait(lambda: any(d.get("name") == "laptop" and d.get("version")
                     for d in aio.core.devices), "the roster", (laptop, aio))
    assert aio.core.rights["laptop"] == {"may_drive": True, "may_be_driven": True}


# --------------------------------------------- managing from any device
def member_api(tmp_path, node, **cfg_extra):
    from link import config, control_api
    from link.runtime import RunLog
    cfg = config.merge(config.DEFAULTS, dict({"node": node.core.node,
                                              "pin": LAPTOP_PW}, **cfg_extra))
    return control_api.ControlAPI(node, cfg, RunLog(tmp_path / f"{node.core.node}.log",
                                                    echo=False),
                                  cfg_path=tmp_path / f"{node.core.node}.json", port=0)


def trio_up(running):
    laptop = device("laptop", LAPTOP_PW)
    aio1 = device("aio1", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    aio2 = device("aio2", LAPTOP_PW, hub=False, peer="laptop", port=laptop.port)
    aio2.on_rename = aio2.rename
    running(laptop, aio1, aio2)
    wait(lambda: {"aio1", "aio2"} <= set(laptop.links) and aio1.connected()
         and aio2.connected(), "all three",
         (laptop, aio1, aio2))
    return laptop, aio1, aio2


def test_a_member_renames_another_device_through_the_hub(tmp_path, running):
    laptop, aio1, aio2 = trio_up(running)
    hub_api = member_api(tmp_path, laptop, hub=True)
    api1 = member_api(tmp_path, aio1)
    r = api1.command("/api/rename", {"name": "aio2", "new": "kitchen"})
    assert r["ok"], r
    wait(lambda: "kitchen" in laptop.links, "aio2 to come back as kitchen",
         (laptop, aio1, aio2))
    assert hub_api                                      # it is what answered


def test_a_member_removes_another_device_through_the_hub(tmp_path, running):
    laptop, aio1, aio2 = trio_up(running)
    member_api(tmp_path, laptop, hub=True)
    told = []
    aio2.on_removed = told.append
    api1 = member_api(tmp_path, aio1)
    r = api1.command("/api/remove", {"name": "aio2"})
    assert r["ok"], r
    wait(lambda: told == ["laptop"], "aio2 to be told", (laptop, aio1, aio2))


def test_a_member_sets_another_devices_rights_through_the_hub(tmp_path, running):
    laptop, aio1, aio2 = trio_up(running)
    member_api(tmp_path, laptop, hub=True)
    api1 = member_api(tmp_path, aio1)
    r = api1.command("/api/rights", {"name": "aio2", "may_be_driven": False})
    assert r["ok"], r
    wait(lambda: aio2.core.may_be_driven() is False, "aio2 to apply it",
         (laptop, aio1, aio2))
    wait(lambda: "aio2" not in aio1.core.reachable(), "aio1 to see the wall",
         (laptop, aio1, aio2))


def test_a_refusal_from_the_hub_reaches_the_member(tmp_path, running):
    laptop, aio1, aio2 = trio_up(running)
    member_api(tmp_path, laptop, hub=True)
    api1 = member_api(tmp_path, aio1)
    r = api1.command("/api/rename", {"name": "aio2", "new": "laptop"})
    assert "already a device called laptop" in r["error"]


def test_a_member_renames_the_hub(tmp_path, running):
    laptop, aio1, aio2 = trio_up(running)
    member_api(tmp_path, laptop, hub=True)
    api1 = member_api(tmp_path, aio1)
    assert api1.command("/api/rename", {"name": "laptop", "new": "desk"})["ok"]
    wait(lambda: aio1.trusted_peer == "desk" and aio1.connected(),
         "everyone to reconnect to the renamed hub", (laptop, aio1, aio2))


def test_the_hub_cannot_be_removed_by_a_member(tmp_path, running):
    laptop, aio1, aio2 = trio_up(running)
    api1 = member_api(tmp_path, aio1)
    r = api1.command("/api/remove", {"name": "laptop"})
    assert "keeps the group" in r["error"]
