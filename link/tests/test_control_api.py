"""The localhost control UI. See control_api.py.

Exercised over a real socket against a real Node, because the parts worth
testing are the ones that only exist once HTTP is involved: the token, what a
browser can and cannot reach, and whether a setting actually lands.
"""
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, __file__.rsplit("test_control_api", 1)[0])

from link import config, control_api, pairing  # noqa: E402
from link.desk import simple                # noqa: E402
from link.node import Node, NodeCore          # noqa: E402
from link.runtime import RunLog               # noqa: E402
from test_node_live import FakeCapture, FakeInjector   # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def api(tmp_path):
    cfg = config.merge(config.DEFAULTS, {
        "node": "laptop", "peer": "aio", "hub": True, "side": "right",
        "pin": "5813", "port": 8770,
    })
    lay = simple("laptop", (1366, 768), "aio", (1920, 1080), "right")
    core = NodeCore("laptop", lay, cfg["policy"], is_hub=True, side="right")
    node = Node(core, FakeCapture(), FakeInjector(), port=8770)
    node.capture.start(node)
    log = RunLog(tmp_path / "link.log", echo=False)
    a = control_api.ControlAPI(node, cfg, log,
                              cfg_path=tmp_path / "config.json", port=free_port())
    assert a.start(), "control UI did not start"
    try:
        yield a
    finally:
        a.stop()
        node.stop()


def alone(a):
    """The fixture's hub has "aio" on its arrangement. A device with devices of
    its own cannot join another group (they would be stranded), so the tests
    of joining start from a device on its own."""
    a.node.core.alone()
    return a


def get(a, path, token=None):
    tok = a.token if token is None else token
    with urllib.request.urlopen(f"http://127.0.0.1:{a.port}{path}?t={tok}",
                                timeout=5) as r:
        return r.status, r.read()


def post(a, path, body, token=None):
    tok = a.token if token is None else token
    req = urllib.request.Request(
        f"http://127.0.0.1:{a.port}{path}?t={tok}",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


# ------------------------------------------------------------- the token
def test_the_page_is_served_without_a_token_and_carries_one(api):
    """You have to be able to open it in the first place, so the page itself is
    unauthenticated - and it is where the token comes from."""
    code, body = get(api, "/", token="")
    assert code == 200
    assert api.token.encode() in body
    assert b"__TOKEN__" not in body, "placeholder was not substituted"


def test_the_api_refuses_a_missing_or_wrong_token(api):
    """Loopback keeps the network out but NOT a web page you have open: any site
    can POST to 127.0.0.1 from your browser, and this API can turn on input
    capture and change the shared PIN."""
    for bad in ("", "not-the-token", api.token + "x"):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(api, "/api/status", token=bad)
        assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        post(api, "/api/disable", {}, token="nope")
    assert e.value.code == 403


def test_a_header_works_as_well_as_the_query(api):
    req = urllib.request.Request(f"http://127.0.0.1:{api.port}/api/status",
                                headers={"X-Nishro-Token": api.token})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


def test_it_listens_on_loopback_only(api):
    """Anything that can inject keystrokes must not be on the LAN."""
    s = socket.socket()
    s.settimeout(2)
    addrs = control_api.my_addresses()
    if not addrs:
        pytest.skip("no non-loopback address to test against")
    try:
        with pytest.raises(OSError):
            s.connect((addrs[0], api.port))
    finally:
        s.close()


# -------------------------------------------------------------- status
def test_status_says_what_is_actually_happening(api):
    code, body = get(api, "/api/status")
    s = json.loads(body)
    assert code == 200
    assert s["node"] == "laptop" and s["hub"] is True
    assert s["enabled"] is True and s["connected"] is False
    assert s["holds"] is True                       # the hub starts out driving
    assert s["cursor"]["screen"] == "laptop"
    assert s["suppress"] == {"mouse": False, "keyboard": False}
    assert s["pin_set"] is True
    assert "5813" not in body.decode(), "the PIN itself must never be served"


def test_status_lists_both_screens_and_which_is_ours(api):
    s = json.loads(get(api, "/api/status")[1])
    mine = [x for x in s["screens"] if x["mine"]]
    assert [x["name"] for x in mine] == ["laptop"]
    assert {x["name"]: (x["w"], x["h"]) for x in s["screens"]} == {
        "laptop": (1366, 768), "aio": (1920, 1080)}


def test_status_includes_the_log_tail(api):
    api.log("something worth seeing")
    s = json.loads(get(api, "/api/status")[1])
    assert any("something worth seeing" in line for line in s["log"])


# ------------------------------------------------------------- control
def test_stopping_linking_does_not_stop_the_process(api):
    """Killing the process would take the UI down with it, and then there would
    be nothing left to start it from."""
    assert post(api, "/api/disable", {})[1]["enabled"] is False
    assert api.node.enabled is False
    s = json.loads(get(api, "/api/status")[1])       # still answering
    assert s["enabled"] is False
    assert s["suppress"] == {"mouse": False, "keyboard": False}

    assert post(api, "/api/enable", {})[1]["enabled"] is True
    assert api.node.enabled is True


def test_release_is_the_failsafe_as_a_button(api):
    """Someone whose mouse has just stopped working should not have to remember
    a key combination."""
    assert post(api, "/api/release", {})[1]["ok"] is True
    assert api.node.core.suppress_mouse() is False


def test_an_unknown_command_is_a_404(api):
    with pytest.raises(urllib.error.HTTPError) as e:
        post(api, "/api/nonsense", {})
    assert e.value.code == 404


def test_malformed_json_is_rejected_not_crashed(api):
    req = urllib.request.Request(
        f"http://127.0.0.1:{api.port}/api/config?t={api.token}",
        data=b"{not json", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400


# -------------------------------------------------------------- settings
def test_a_policy_change_takes_effect_at_once(api):
    r = post(api, "/api/config", {"may_drive": False})[1]
    assert r["applied_now"]["may_drive"] is False
    assert api.node.core.policy["may_drive"] is False
    assert api.node.core.may_drive() is False


def test_a_claim_policy_change_reaches_the_detector(api):
    post(api, "/api/config", {"claim": "click"})
    assert api.node.core.claims.policy == "click"
    # and motion no longer claims
    api.node.core.baton.holder = "aio"
    assert api.node.core.local_pointer("laptop", 500, 500, 99, 0).send == []


def test_settings_are_written_to_disk(api):
    post(api, "/api/config", {"side": "left", "may_be_driven": False})
    saved = config.load(api.cfg_path)
    assert saved["side"] == "left"
    assert saved["policy"]["may_be_driven"] is False


def test_things_needing_a_reconnect_say_so(api):
    """Rather than pretending to have taken effect. A new password does not:
    the hub hands it to the devices connected now."""
    r = post(api, "/api/config", {"pin": "k7qm-2xvp-9hdt", "side": "top"})[1]
    assert "side" in r["needs_reconnect"]
    assert not any("password" in x for x in r["needs_reconnect"])


def test_a_refused_password_changes_nothing_else(api):
    """The password was checked after `side` had already been applied, so a
    refused request still moved the peer to the other side in memory."""
    before = api.node.core.side
    r = post(api, "/api/config", {"pin": "123", "side": "top"})[1]
    assert "error" in r
    assert api.node.core.side == before
    assert config.load(api.cfg_path).get("side") != "top"


@pytest.mark.parametrize("bad", [{"side": "sideways"}, {"claim": "telepathy"}])
def test_nonsense_settings_are_refused(api, bad):
    r = post(api, "/api/config", bad)[1]
    assert "error" in r
    assert config.load(api.cfg_path).get("side") != "sideways"


def test_a_new_pin_reaches_the_node(api):
    post(api, "/api/config", {"pin": "k7qm-2xvp-9hdt"})
    assert api.node.pin == "k7qm2xvp9hdt", "compared without its dashes"
    assert config.load(api.cfg_path)["pin"] == "k7qm-2xvp-9hdt",         "saved as it reads, so the window can show it back"


def test_stopping_releases_the_port(api):
    """shutdown() stops serve_forever but does NOT close the listening socket, so
    without an explicit close the port stays held until the process exits - a
    leak per restart, and flaky tests that bind fresh ports."""
    port = api.port
    api.stop()
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        s.bind(("127.0.0.1", port))          # must be free again
    finally:
        s.close()


def test_stopping_twice_is_harmless(api):
    api.stop()
    api.stop()


# ------------------------------------------------- the connection tab
def test_status_reports_trust_honestly(api):
    """Not connected means not trusted - and the UI must never imply a verified
    peer where there is none."""
    s = json.loads(get(api, "/api/status")[1])
    assert s["trusted_peer"] is None
    assert s["encrypted"] is False, "authenticated is not encrypted, and saying " \
                                    "otherwise would be worse than the gap"


def test_the_device_name_changes_at_once(api):
    """It used to need a restart."""
    r = post(api, "/api/config", {"node": "workshop"})[1]
    assert config.load(api.cfg_path)["node"] == "workshop"
    assert r["needs_reconnect"] == [] and r["applied_now"]["node"] == "workshop"
    core = api.node.core
    assert core.node == "workshop" and "workshop" in core.layout.names()
    assert "laptop" not in core.layout.names() and core.arbiter is not None
    assert core.layout.get("aio"), "and the others are where they were"


@pytest.mark.parametrize("name,why", [
    ("aio", "already"), ("x" * 33, "32"), ("lap/top", "letters"), ("", "empty")])
def test_a_bad_new_name_is_refused(api, name, why):
    r = post(api, "/api/rename", {"new": name})[1]
    assert why in r["error"]
    assert api.node.core.node == "laptop"


def test_the_hub_renames_a_device_that_is_off_at_once(api):
    api.cfg["devices"] = [{"name": "aio", "id": "aio-1"}]
    api.node.names_by_id["aio-1"] = "aio"
    r = post(api, "/api/rename", {"name": "aio", "new": "kitchen"})[1]
    assert r == {"ok": True, "pending": True}
    assert "kitchen" in api.node.core.layout.names()
    assert api.node.pending_names == {"aio-1": "kitchen"}
    assert config.load(api.cfg_path)["devices"][0]["rename_to"] == "kitchen"


def test_rights_are_set_and_kept(api):
    r = post(api, "/api/rights", {"name": "laptop", "may_be_driven": False})[1]
    assert r["ok"] and api.node.core.may_be_driven() is False
    assert config.load(api.cfg_path)["policy"]["may_be_driven"] is False


def test_another_devices_rights_need_it_switched_on(api):
    r = post(api, "/api/rights", {"name": "aio", "may_drive": False})[1]
    assert "switched off" in r["error"]


def test_the_device_list_carries_the_details(api):
    s = json.loads(get(api, "/api/status")[1])
    me = next(d for d in s["devices"] if d["me"])
    assert me["version"] and me["may_drive"] is True and me["id"]
    other = next(d for d in s["devices"] if not d["me"])
    assert "first_seen" in other and "may_be_driven" in other


def test_an_empty_device_name_is_refused(api):
    assert "error" in post(api, "/api/config", {"node": "   "})[1]
    assert config.load(api.cfg_path).get("node") != "   "


def test_the_role_can_be_switched(api):
    r = post(api, "/api/config", {"hub": False})[1]
    assert config.load(api.cfg_path)["hub"] is False
    assert any("waits/connects" in x for x in r["needs_reconnect"])


@pytest.mark.parametrize("bad", ["", "abc", 0, 70000, -1])
def test_a_bad_port_is_refused(api, bad):
    before = config.load(api.cfg_path).get("port")
    assert "error" in post(api, "/api/config", {"port": bad})[1]
    assert config.load(api.cfg_path).get("port") == before


def test_a_good_port_is_taken(api):
    r = post(api, "/api/config", {"port": 9000})[1]
    assert config.load(api.cfg_path)["port"] == 9000
    assert any("port" in x for x in r["needs_reconnect"])


def test_changing_the_password_reaches_the_node_without_a_restart(api):
    """The next handshake uses it, so it does not need the process restarting -
    only the connection."""
    post(api, "/api/config", {"pin": "hunter22"})
    assert api.node.pin == "hunter22"
    assert config.load(api.cfg_path)["pin"] == "hunter22"


def test_polling_does_not_pile_up_threads(api):
    """The UI polls about once a second, for as long as the program runs. With
    HTTP/1.1 keep-alive and no handler timeout, every one of those parked a
    thread in readline() waiting for a request that never came."""
    import threading
    base = threading.active_count()
    for _ in range(60):
        get(api, "/api/status")
    time.sleep(0.4)
    assert threading.active_count() - base <= 2, "connections are not being closed"


def test_each_response_closes_its_connection(api):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", api.port, timeout=5)
    c.request("GET", f"/api/status?t={api.token}")
    r = c.getresponse()
    r.read()
    assert r.getheader("Connection") == "close"
    c.close()


# ------------------------------------------------------- pairing two devices
def test_pairing_as_the_waiting_side(api):
    """"Let another device connect" - this device listens and hands back what to
    type on the other one: its NAME and a password, never an address."""
    r = post(api, "/api/pair", {"mode": "wait"})[1]
    assert r["ok"] is True and r["waiting"] is True
    assert r["port"] == 8770
    assert r["name"] == "laptop", "it must say what to type on the other machine"
    assert pairing.problem(r["pin"]) is None, "a generated password must be sound"
    saved = config.load(api.cfg_path)
    assert saved["hub"] is True and saved["peer_addr"] is None
    assert saved["pin"] == r["pin"]


def test_waiting_again_keeps_the_same_password(api):
    """The other device may already have typed it in."""
    first = post(api, "/api/pair", {"mode": "wait"})[1]["pin"]
    assert post(api, "/api/pair", {"mode": "wait"})[1]["pin"] == first


def test_an_old_short_password_is_replaced_when_waiting(api):
    """5813 predates generated passwords, and found-by-name makes a short one
    guessable offline by anything that answers the search."""
    api.cfg["pin"] = "5813"
    pin = post(api, "/api/pair", {"mode": "wait"})[1]["pin"]
    assert pin != "5813" and pairing.problem(pin) is None


def test_a_new_password_can_be_made(api):
    old = post(api, "/api/password", {})[1]["pin"]
    new = post(api, "/api/password", {"new": True})[1]["pin"]
    assert new != old
    assert api.node.pin == pairing.normalise(new)
    assert config.load(api.cfg_path)["pin"] == new


def test_dialling_by_name(api):
    alone(api)
    r = post(api, "/api/pair", {"mode": "dial", "peer": "aio",
                                "pin": "K7QM 2XVP 9HDT"})[1]
    assert r["ok"] is True and r["waiting"] is False
    assert (api.node.peer_name, api.node.peer_addr) == ("aio", None)
    assert api.node.pin == "k7qm2xvp9hdt", "case and spaces from reading it out"
    saved = config.load(api.cfg_path)
    assert (saved["peer"], saved["peer_addr"], saved["hub"]) == ("aio", None, False)


def test_a_new_pairing_forgets_the_old_devices_id(api):
    """The ID belongs to whatever was paired before; keeping it would make the
    new name look for the old machine."""
    alone(api)
    api.cfg["peer_id"] = "old-device"
    api.node.peer_id = "old-device"
    post(api, "/api/pair", {"mode": "dial", "peer": "desk-pc", "pin": "abcd1234"})
    assert api.node.peer_id is None
    assert config.load(api.cfg_path)["peer_id"] is None


def test_what_a_dial_learns_is_saved(api):
    api.node.on_paired("aio", "a1a1", "192.168.1.20")
    saved = config.load(api.cfg_path)
    assert (saved["peer"], saved["peer_id"], saved["peer_addr"]) == \
        ("aio", "a1a1", "192.168.1.20")


def test_status_carries_names_and_ids_but_never_the_password(api):
    alone(api)
    post(api, "/api/pair", {"mode": "dial", "peer": "aio", "pin": "abcd1234"})
    code, body = get(api, "/api/status")
    s = json.loads(body)
    assert s["peer"] == "aio" and s["device_id"]
    assert "abcd1234" not in body.decode()


def test_pairing_as_the_dialling_side(api):
    alone(api)
    r = post(api, "/api/pair", {"mode": "dial", "pin": "amber-cedar-rowan-42",
                                "peer_addr": "192.168.1.20"})[1]
    assert r["ok"] is True and r["waiting"] is False
    saved = config.load(api.cfg_path)
    assert saved["hub"] is False
    assert saved["peer_addr"] == "192.168.1.20"


def test_pairing_applies_without_a_restart(api):
    """A pairing flow that ended in "now restart the program" would not be a
    pairing flow."""
    assert api.node.core.is_hub is True
    alone(api)
    post(api, "/api/pair", {"mode": "dial", "pin": "abcd1234",
                            "peer_addr": "10.0.0.5"})
    assert api.node.core.is_hub is False, "the role flipped live"
    assert api.node.core.arbiter is None, "and the arbiter went with it"
    assert api.node.peer_addr == "10.0.0.5"
    assert api.node.pin == "abcd1234"
    assert api.node.enabled is True


def test_switching_back_to_waiting_restores_the_arbiter(api):
    alone(api)
    post(api, "/api/pair", {"mode": "dial", "pin": "abcd1234",
                            "peer_addr": "10.0.0.5"})
    post(api, "/api/pair", {"mode": "wait", "pin": "abcd1234"})
    assert api.node.core.is_hub is True
    assert api.node.core.arbiter is not None
    assert api.node.core.holds() is True, "a waiting hub starts out driving"


@pytest.mark.parametrize("bad,why", [
    ({"mode": "sideways", "pin": "abcd1234"}, "mode"),
    ({"mode": "dial", "pin": "abcd1234"}, "name"),
    ({"mode": "dial", "pin": "abcd1234", "peer": "LAPTOP"}, "its own name"),
    ({"mode": "dial", "pin": "", "peer": "aio"}, "password"),
    ({"mode": "wait", "pin": "abc"}, "password"),
    ({"mode": "wait", "pin": "abcd1234", "port": "nope"}, "port"),
    ({"mode": "wait", "pin": "abcd1234", "port": 99999}, "port"),
])
def test_bad_pairing_requests_are_refused(api, bad, why):
    r = post(api, "/api/pair", bad)[1]
    assert "error" in r, f"{bad} should have been refused ({why})"


def test_a_short_password_is_refused_because_it_is_the_only_defence(api):
    r = post(api, "/api/pair", {"mode": "wait", "pin": "1234567"})[1]
    assert "8 characters" in r["error"]


def test_a_device_with_its_own_group_cannot_join_another(api):
    """It would strand the devices connected to it."""
    r = post(api, "/api/pair", {"mode": "dial", "peer": "desk-pc",
                                "pin": "abcd1234"})[1]
    assert "aio" in r["error"] and "Add the other device from here" in r["error"]
    assert api.node.core.is_hub is True


def test_leaving_a_group_makes_a_group_of_one_with_a_new_password(api):
    alone(api)
    post(api, "/api/pair", {"mode": "dial", "pin": "abcd1234",
                            "peer_addr": "10.0.0.5"})
    r = post(api, "/api/forget", {})[1]
    assert r["ok"] is True
    saved = config.load(api.cfg_path)
    assert saved["peer_addr"] is None and saved["peer"] is None
    assert saved["hub"] is True, "on its own, ready to be added again"
    assert pairing.normalise(saved["pin"]) != "abcd1234", \
        "the old group's password stays with the old group"
    assert api.node.enabled is True
    # The live node has to agree with the file. It did not: reconfigure() used
    # None as its "leave this alone" sentinel, so asking it to clear peer_addr -
    # which is literally what forgetting a device is - did nothing, and status
    # went on reporting a paired device that the config no longer had. Caught by
    # driving the shipped binary rather than by any of the unit tests.
    assert api.node.peer_addr is None
    assert api.node.core.is_hub is True
    assert json.loads(get(api, "/api/status")[1])["role"] == "alone"


def test_status_says_whether_anything_is_paired(api):
    s = json.loads(get(api, "/api/status")[1])
    assert s["paired"] is True                 # the fixture is a waiting hub
    api.node.core.is_hub = False
    api.node.peer_addr = None
    assert json.loads(get(api, "/api/status")[1])["paired"] is False


def test_a_second_copy_does_not_bind_the_same_port(api, tmp_path):
    """Two servers on one port is a bug you cannot see from the outside.

    http.server sets SO_REUSEADDR for everyone, and on Windows that permits a
    second bind to a port somebody is already listening on. Both processes then
    appear to work while new connections go to whichever the kernel picks, so
    the window polls one node and shows another node's URL. Found on real
    hardware with two copies running: the token printed at startup was rejected
    by the server on that very port.
    """
    other = control_api.ControlAPI(api.node, api.cfg, api.log,
                                   cfg_path=tmp_path / "other.json",
                                   port=api.port)
    assert other.start()
    try:
        assert other.port != api.port, "second server bound an occupied port"
        assert str(other.port) in other.url
        # Each answers only its own token, which is what proves they are two
        # separate servers rather than one port shared between them.
        assert get(api, "/api/status")[0] == 200
        assert get(other, "/api/status")[0] == 200
        with pytest.raises(urllib.error.HTTPError) as e:
            get(api, "/api/status", token=other.token)
        assert e.value.code == 403
    finally:
        other.stop()


def test_giving_up_when_every_nearby_port_is_taken(api, tmp_path, monkeypatch):
    """A refusal has to be logged, not silent: the window is useless without it."""
    monkeypatch.setattr(control_api, "ThreadingHTTPServer", _Unbindable)
    dead = control_api.ControlAPI(api.node, api.cfg, api.log,
                                  cfg_path=tmp_path / "dead.json", port=api.port)
    assert dead.start() is False
    assert any("control UI not available" in ln for ln in api.log.tail)


class _Unbindable:
    """Stands in for the HTTP server, refusing every port."""

    allow_reuse_address = True

    def __init__(self, *a, **k):
        raise OSError("address in use")


def test_a_restart_gets_the_same_port_back(api, tmp_path):
    """Every poll ends with the server hanging up (Connection: close), which
    leaves the port in TIME_WAIT. Turning SO_REUSEADDR off to stop the Windows
    double-bind made Linux refuse that port for a minute after any restart, so
    the UI moved to the next one with only one copy running - found on the
    Ubuntu box. A bookmarked URL should keep working across a restart."""
    port = api.port
    for _ in range(3):
        assert get(api, "/api/status")[0] == 200     # leave TIME_WAITs behind
    api.stop()
    again = control_api.ControlAPI(api.node, api.cfg, api.log,
                                   cfg_path=tmp_path / "again.json", port=port)
    assert again.start()
    try:
        assert again.port == port, f"moved to {again.port} after a clean restart"
    finally:
        again.stop()


def test_a_refused_post_always_arrives_as_a_403(api):
    """The server used to answer 403 without reading the POST body. Closing a
    socket with unread data makes Windows send a reset instead of a close, and
    the reset could destroy the 403 before the client read it - so the refusal
    arrived as WinError 10053, about one request in 25. The token check itself
    was never fooled; the answer was lost. Repeated so that a ~4% race is all
    but certain to show: 0.96^150 is about 0.2%."""
    for i in range(150):
        try:
            post(api, "/api/disable", {"padding": "x" * 200}, token="wrong")
        except urllib.error.HTTPError as e:
            assert e.code == 403, f"request {i}: got {e.code}"
        else:
            pytest.fail(f"request {i}: a wrong token was accepted")
    assert api.node.enabled is True, "a refused request still took effect"


def test_an_oversized_body_is_refused_not_read(api):
    req = urllib.request.Request(
        f"http://127.0.0.1:{api.port}/api/config?t={api.token}",
        data=b"x", method="POST",
        headers={"Content-Length": str(control_api.MAX_BODY + 1)})
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        assert e.code == 413
    except (ConnectionError, urllib.error.URLError):
        pass            # refusing before reading may reset; either way, not served
    else:
        pytest.fail("an oversized request was served")


# ------------------------------------------------------------ the firewall
def test_status_reports_a_firewall_block(api):
    assert json.loads(get(api, "/api/status")[1])["firewall_blocked"] == []
    api.firewall_blocked = ["nishrolink.exe (TCP, private networks)"]
    assert json.loads(get(api, "/api/status")[1])["firewall_blocked"] == \
        ["nishrolink.exe (TCP, private networks)"]


def test_a_block_found_at_startup_is_logged_in_plain_words(api, monkeypatch):
    monkeypatch.setattr(control_api.runtime, "firewall_blocks",
                        lambda *a: ["nishrolink.exe (TCP, private networks)"])
    api.check_firewall()
    assert any("BLOCKING this program" in x for x in api.log.tail)


def test_the_firewall_can_be_fixed_from_the_window(api, monkeypatch):
    monkeypatch.setattr(control_api.runtime, "allow_through_firewall", lambda *a: True)
    monkeypatch.setattr(control_api.runtime, "firewall_blocks", lambda *a: [])
    api.firewall_blocked = ["x"]
    r = post(api, "/api/firewall", {})[1]
    assert r["ok"] is True and api.firewall_blocked == []


def test_declining_the_prompt_changes_nothing(api, monkeypatch):
    monkeypatch.setattr(control_api.runtime, "allow_through_firewall", lambda *a: False)
    monkeypatch.setattr(control_api.runtime, "firewall_blocks", lambda *a: ["x"])
    r = post(api, "/api/firewall", {})[1]
    assert r["ok"] is False and r["declined"] is True


# ------------------------------------------------------------- devices
def test_a_device_that_joins_is_remembered(api):
    api.node.on_devices("joined", "aio1", {
        "id": "a1", "addr": "192.168.1.20",
        "screens": [{"w": 1920, "h": 1080, "parts": [[0, 0, 1920, 1080]]}]})
    saved = {d["name"]: d for d in config.load(api.cfg_path)["devices"]}
    assert saved["aio1"]["id"] == "a1" and saved["aio1"]["addr"] == "192.168.1.20"
    assert saved["aio1"]["screens"][0]["w"] == 1920 and saved["aio1"]["last_seen"]


def test_the_group_is_described_to_everyone_with_the_hub_first(api):
    api.node.on_devices("joined", "aio1", {"id": "a1"})
    view = api.node.core.devices
    assert view[0]["name"] == "laptop" and view[0]["hub"] is True
    assert {"name": "aio1"}.items() <= view[1].items()
    assert all("addr" not in d for d in view), "addresses stay with the hub"


def test_status_lists_every_machine_with_its_displays(api):
    devs = {d["name"]: d for d in json.loads(get(api, "/api/status")[1])["devices"]}
    assert devs["laptop"]["me"] and devs["laptop"]["hub"] and devs["laptop"]["online"]
    assert devs["aio"]["online"] is False, "not connected in this fixture"
    assert devs["aio"]["displays"] == [[1920, 1080]]


def test_a_device_that_is_offline_can_be_forgotten(api):
    api.node.on_devices("joined", "aio", {"id": "a1"})
    r = post(api, "/api/device/forget", {"name": "aio"})[1]
    assert r["ok"] is True
    assert "aio" not in api.node.core.layout.names()
    assert all(d["name"] != "aio" for d in config.load(api.cfg_path)["devices"])


class FakeChannel:
    def __init__(self):
        self.sent, self.closed = [], False

    def send(self, msg):
        self.sent.append(msg)

    def close(self, flush=0.0):
        self.closed = True


def test_a_connected_device_is_told_it_was_removed(api):
    """It forgets the group; and until it is paired again on purpose, the hub
    refuses it even though it still knows the password."""
    from link.node import _Link
    ch = FakeChannel()
    api.cfg["devices"] = [{"name": "aio", "id": "aio-1"}]
    api.node.links["aio"] = _Link("aio", ch, ("10.0.0.9", 1))
    try:
        r = post(api, "/api/remove", {"name": "aio"})[1]
        assert r["ok"] is True
        for _ in range(100):
            if ch.closed:
                break
            time.sleep(0.01)
        assert ch.sent[-1] == {"t": "removed", "by": "laptop"} and ch.closed
        assert "aio" in api.node._leaving, "forgotten once its link is gone"
        assert "aio-1" in api.node.removed_ids
        assert config.load(api.cfg_path)["removed"] == ["aio-1"]
    finally:
        api.node.links.clear()


def test_only_the_hub_removes_devices(api):
    api.node.core.is_hub = False
    r = post(api, "/api/remove", {"name": "aio"})[1]
    assert "only the hub" in r["error"]


def test_a_member_cannot_change_the_groups_password(api):
    api.node.core.is_hub = False
    api.node.peer_name = "laptop2"
    r = post(api, "/api/password", {"new": True})[1]
    assert "only laptop2" in r["error"]


def test_a_peer_keeps_the_hubs_picture_of_the_group(api):
    api.node.core.is_hub = False
    api.node.on_devices("roster", None, {"devices": [
        {"name": "laptop", "id": "h1", "hub": True, "online": True},
        {"name": "aio2", "id": "a2", "last_seen": 5, "online": False}]})
    saved = {d["name"] for d in config.load(api.cfg_path)["devices"]}
    assert saved == {"aio2"} | ({"laptop"} if api.node.core.node != "laptop" else set())
