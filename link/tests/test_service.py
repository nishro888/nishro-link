"""Running as a system service: the session it borrows, the handle it leaves,
and the window that attaches to it.

Asked for: "after restart I want these two connected automatically, even at
the Linux or Windows lock screen". A per-user program starts only after
someone logs in; the service starts with the computer.
"""
import json
import socket
import sys
import urllib.error
import urllib.request

import pytest

from link import config, control_api, service, session
from link.desk import simple
from link.node import Node, NodeCore
from link.runtime import RunLog

from test_node_live import FakeCapture, FakeInjector


# ------------------------------------------------------------ the session
def test_a_persons_wayland_session_is_described(tmp_path):
    rt = tmp_path / "run"
    (rt / "1000").mkdir(parents=True)
    (rt / "1000" / "wayland-0").write_text("")
    (rt / "1000" / ".mutter-Xwaylandauth.ABC123").write_text("")
    xs = tmp_path / "x11"
    xs.mkdir()
    (xs / "X0").write_text("")
    s = session.describe({"Class": "user", "Type": "wayland", "User": "1000",
                          "Name": "alex", "Display": ""},
                         runtime=str(rt), xsockets=str(xs))
    assert (s["user"], s["uid"], s["type"]) == ("alex", 1000, "wayland")
    assert s["env"]["WAYLAND_DISPLAY"] == "wayland-0"
    assert s["env"]["DISPLAY"] == ":0", "GNOME's XWayland"
    assert s["env"]["XAUTHORITY"].endswith(".mutter-Xwaylandauth.ABC123")


@pytest.mark.parametrize("props", [
    {"Class": "greeter", "Type": "wayland", "User": "120", "Name": "gdm"},
    {"Class": "user", "Type": "tty", "User": "1000", "Name": "alex"},
    {"Class": "user", "Type": "x11", "User": "0", "Name": "root"},
])
def test_the_login_screen_a_console_or_root_is_nobodys_desktop(props):
    assert session.describe(props) is None


def test_loginctl_properties_are_read():
    assert session.parse_props("Name=alex\nUser=1000\nType=wayland\n") == \
        {"Name": "alex", "User": "1000", "Type": "wayland"}


def test_a_command_runs_as_the_sessions_user_with_its_display():
    s = {"user": "alex", "uid": 1000, "type": "x11",
         "env": {"DISPLAY": ":0", "XDG_RUNTIME_DIR": "/run/user/1000"}}
    cmd = session.command(["xclip", "-o"], s)
    assert cmd[:5] == ["runuser", "-u", "alex", "--", "env"]
    assert "DISPLAY=:0" in cmd and cmd[-2:] == ["xclip", "-o"]


# ------------------------------------------------------------ the service
@pytest.fixture
def running_api(tmp_path):
    cfg = config.merge(config.DEFAULTS, {"node": "aio", "hub": True,
                                         "pin": "tiger-lemon-coral-radio"})
    node = Node(NodeCore("aio", simple("aio", (1920, 1080), "laptop", (1366, 768),
                                       "right"), {}, is_hub=True),
                FakeCapture(), FakeInjector(), pin=cfg["pin"])
    api = control_api.ControlAPI(node, cfg, RunLog(tmp_path / "l.log", echo=False),
                                 cfg_path=tmp_path / "c.json", port=_free())
    api.service = True
    assert api.start()
    yield api
    api.stop()
    node.stop()


def _free():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_the_handle_is_published_and_found(running_api, tmp_path):
    h = service.publish(running_api.port, running_api.token, tmp_path / "api.json")
    assert json.loads(h.read_text())["port"] == running_api.port
    if sys.platform != "win32":
        assert (h.stat().st_mode & 0o777) == 0o640, "root and the input group"
    assert service.find(h) == (running_api.port, running_api.token)


def test_a_handle_left_by_a_crash_is_not_followed(tmp_path):
    h = service.publish(_free(), "stale", tmp_path / "api.json")
    assert service.find(h) is None


def test_no_handle_means_no_service(tmp_path):
    assert service.find(tmp_path / "nothing.json") is None


def test_the_window_drives_the_service_through_the_same_two_calls(running_api):
    remote = service.RemoteAPI(running_api.port, running_api.token)
    s = remote.status()
    assert s["node"] == "aio" and s["service"] is True
    assert s["autostart"]["available"] is False, "it starts with the computer"
    r = remote.command("/api/rename", {"new": "kitchen"})
    assert r == {"ok": True} and running_api.node.core.node == "kitchen"
    assert "error" in remote.command("/api/rename", {"new": ""})


def test_the_service_page_needs_the_token(running_api):
    """Its controls include the group's password; the page must not hand its
    token to any local program that asks."""
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{running_api.port}/", timeout=3)
    assert e.value.code == 403
    ok = urllib.request.urlopen(
        f"http://127.0.0.1:{running_api.port}/?t={running_api.token}", timeout=3)
    assert ok.status == 200


def test_a_bad_token_gets_nothing(running_api):
    remote = service.RemoteAPI(running_api.port, "wrong")
    with pytest.raises(urllib.error.HTTPError):
        remote.status()


# --------------------- the launcher, with a service it cannot reach
class FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        self.t += 0.5
        return self.t

    def sleep(self, s):
        self.t += s


def test_a_service_that_does_not_answer_is_said_not_joined(monkeypatch):
    """Reported: the window did not open. The launcher found no answer from
    the service, started an engine of its own, found the lock taken by the
    service, and exited with nothing on screen. Now it waits a little, then
    says so - and never starts a second engine beside a running service."""
    from link import nishro_link, runtime
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1] + 1                   # the engine lock is port - 1
    s.close()
    held = runtime.SingleInstance(port - 1)
    assert held.acquire(), "the service's engine lock"
    calls = []
    try:
        monkeypatch.setattr(nishro_link.time, "monotonic", FakeClock().monotonic)
        monkeypatch.setattr(nishro_link.time, "sleep", lambda s: None)
        monkeypatch.setattr(service, "find", lambda *a, **k: calls.append(1))
        monkeypatch.setattr(service, "published", lambda *a: True)
        monkeypatch.setattr(service, "problem", "it did not answer on port 8771")
        said = []
        monkeypatch.setattr(nishro_link, "_not_answering",
                            lambda problem: said.append(problem) or 4)
        args = nishro_link.build_parser().parse_args([])
        assert nishro_link.attach(args, {"port": port}) == 4
        assert said == ["it did not answer on port 8771"]
        assert len(calls) > 1, "it tried again before saying so"
    finally:
        held.release()


def test_a_handle_left_by_a_stopped_service_is_not_an_error(monkeypatch):
    """No service holds the engine lock: the handle is stale (a crash on
    Windows leaves it). Then this copy may run the link itself, as before."""
    from link import nishro_link
    monkeypatch.setattr(nishro_link.time, "sleep", lambda s: None)
    monkeypatch.setattr(nishro_link.time, "monotonic", FakeClock().monotonic)
    monkeypatch.setattr(service, "find", lambda *a, **k: None)
    monkeypatch.setattr(service, "published", lambda *a: True)
    monkeypatch.setattr(nishro_link, "_not_answering",
                        lambda problem: (_ for _ in ()).throw(AssertionError(problem)))
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1] + 1
    s.close()
    args = nishro_link.build_parser().parse_args([])
    assert nishro_link.attach(args, {"port": port}) is None


def test_find_says_why_it_found_nothing(tmp_path):
    handle = tmp_path / "api.json"
    assert service.find(handle) is None and service.problem is None, "no service"
    handle.write_text("{not json")
    assert service.find(handle) is None and "could not be read" in service.problem
    handle.write_text('{"port": 1, "token": "x"}')
    assert service.find(handle, timeout=0.5) is None
    assert "did not answer on port 1" in service.problem


# ------------------------------------------------------------- Quit, and Open
def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_opening_after_a_quit_starts_the_service(monkeypatch):
    """Quit stops the service. Opening Nishro Link must start it again - not
    run a second engine here from this person's old settings, which is what
    opening did with the service stopped."""
    from link import nishro_link
    running, asked = {"yes": False}, []

    def control(action):
        asked.append(action)
        running["yes"] = True
        return {"ok": True}
    port = _free_port()
    monkeypatch.setattr(nishro_link.time, "sleep", lambda s: None)
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(service, "published", lambda *a: running["yes"])
    monkeypatch.setattr(service, "control", control)
    monkeypatch.setattr(service, "find", lambda *a, **k: (port, "t") if running["yes"] else None)
    monkeypatch.setattr(nishro_link, "load_window", lambda log: None)   # no Tk here
    args = nishro_link.build_parser().parse_args([])
    assert nishro_link.attach(args, {"port": port}) == 0, "attached to the service"
    assert asked == ["start"]


def test_a_quit_holds_at_login(monkeypatch):
    """Started at login (--background), it does not undo a Quit."""
    from link import nishro_link
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(service, "published", lambda *a: False)
    monkeypatch.setattr(service, "find", lambda *a, **k: None)
    monkeypatch.setattr(service, "control", lambda action: (_ for _ in ()).throw(
        AssertionError("started the service")))
    args = nishro_link.build_parser().parse_args(["--background"])
    assert nishro_link.attach(args, {"port": _free_port()}) is None


def test_a_service_that_will_not_start_is_said(monkeypatch):
    from link import nishro_link
    said = []
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(service, "published", lambda *a: False)
    monkeypatch.setattr(service, "control", lambda action: {"ok": False,
                                                            "error": "access denied"})
    monkeypatch.setattr(nishro_link, "_not_started", lambda e: said.append(e) or 5)
    args = nishro_link.build_parser().parse_args([])
    assert nishro_link.attach(args, {"port": _free_port()}) == 5
    assert said == ["access denied"]


def test_quitting_stops_the_service_then_tells_the_tray(monkeypatch, tmp_path):
    import time
    marker = tmp_path / "quit"
    monkeypatch.setattr(service, "_quit_marker", lambda: marker)
    monkeypatch.setattr(service, "installed", lambda: True)
    asked = []
    monkeypatch.setattr(service, "control",
                        lambda action: asked.append(action) or {"ok": True})
    before = time.time() - 1
    assert not service.quit_asked_since(before)
    assert service.quit_everything() == {"ok": True}
    assert asked == ["stop"]
    assert service.quit_asked_since(before)
    assert not service.quit_asked_since(time.time() + 1), "a tray started later stays"


def test_a_quit_that_could_not_stop_the_service_closes_nothing(monkeypatch, tmp_path):
    marker = tmp_path / "quit"
    monkeypatch.setattr(service, "_quit_marker", lambda: marker)
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(service, "control", lambda action: {"ok": False, "error": "no"})
    assert service.quit_everything() == {"ok": False, "error": "no"}
    assert not marker.exists(), "the tray and windows stay: nothing stopped"
