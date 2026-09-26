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
