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


@pytest.fixture(autouse=True)
def no_real_window(monkeypatch):
    """No test here may open a real window: on a computer with Nishro Link
    installed, a launcher test gone wrong once found the real service and
    opened a window onto it. A test that needs a window brings a fake one."""
    from link import nishro_link
    monkeypatch.setattr(nishro_link, "load_window",
                        lambda log: pytest.fail("a test opened a real window"))


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


# ------------------------------------- start and stop, underneath Quit and Open
class FakeSc:
    """sc.exe for a pretend Windows service. `answer` is what start and stop
    return; the service reaches the new state `after` queries later."""

    def __init__(self, state, answer=0, after=0):
        self.state, self.answer, self.after = state, answer, after
        self.calls, self._to, self._left = [], None, 0

    def __call__(self, *args, timeout=30):
        self.calls.append(args[0])
        if args[0] == "query":
            if self._to and self._left <= 0:
                self.state, self._to = self._to, None
            self._left -= 1
            if self.state is None:
                return 1060, "[SC] OpenService FAILED 1060: no such service"
            return 0, (f"SERVICE_NAME: NishroLink TYPE : 10 WIN32_OWN_PROCESS "
                       f"STATE : 4 {self.state} (STOPPABLE, NOT_PAUSABLE)")
        if self.answer in (0, 1056, 1062):
            self._to = "RUNNING" if args[0] == "start" else "STOPPED"
            self._left = self.after
        return self.answer, f"[SC] {args[0]} FAILED {self.answer}: it did not work"


@pytest.fixture
def windows(monkeypatch):
    """control() as on Windows; sc.exe and the elevation prompt are pretended,
    and every elevation prompt is recorded."""
    import subprocess
    import types
    monkeypatch.setattr(service, "sys", types.SimpleNamespace(platform="win32"))
    prompts = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: prompts.append(cmd))
    return prompts


def test_stopping_waits_until_the_service_has_stopped(windows, monkeypatch):
    sc = FakeSc("RUNNING", after=2)
    monkeypatch.setattr(service, "_sc", sc)
    assert service.control("stop") == {"ok": True}
    assert sc.calls[:2] == ["query", "stop"]
    assert sc.state == "STOPPED"
    assert windows == [], "no prompt: signed-in users may stop it"


def test_a_service_already_there_is_left_alone(windows, monkeypatch):
    sc = FakeSc("STOPPED")
    monkeypatch.setattr(service, "_sc", sc)
    assert service.control("stop") == {"ok": True}
    assert sc.calls == ["query"]


def test_without_the_right_to_stop_it_windows_asks_the_person(windows, monkeypatch):
    """The setup gives signed-in users that right. Where it is missing (an
    older install), Windows' own elevation prompt asks instead."""
    import subprocess
    sc = FakeSc("RUNNING", answer=5)
    monkeypatch.setattr(service, "_sc", sc)

    def prompt_answered_yes(cmd, **k):
        windows.append(cmd)
        sc.state = "STOPPED"
    monkeypatch.setattr(subprocess, "run", prompt_answered_yes)
    assert service.control("stop") == {"ok": True}
    assert len(windows) == 1
    assert "-Verb RunAs" in windows[0][-1]
    assert "'stop','NishroLink'" in windows[0][-1]


def test_a_declined_prompt_leaves_it_running_and_says_so(windows, monkeypatch):
    monkeypatch.setattr(service, "_sc", FakeSc("RUNNING", answer=5))
    assert service.control("stop", timeout=0.5) == {
        "ok": False, "error": "the service is running"}
    assert len(windows) == 1


@pytest.mark.parametrize("state, action, answer", [
    ("STOP_PENDING", "stop", 1062),       # "not started": it is on its way down
    ("START_PENDING", "start", 1056),     # "already running": on its way up
])
def test_already_on_its_way_counts_as_going(windows, monkeypatch, state, action, answer):
    monkeypatch.setattr(service, "_sc", FakeSc(state, answer=answer))
    assert service.control(action) == {"ok": True}


def test_an_error_from_sc_is_passed_on(windows, monkeypatch):
    monkeypatch.setattr(service, "_sc", FakeSc("STOPPED", answer=1053))
    r = service.control("start")
    assert r["ok"] is False
    assert "FAILED 1053" in r["error"]
    assert windows == []


def test_a_service_that_never_gets_there_is_said(windows, monkeypatch):
    monkeypatch.setattr(service, "_sc", FakeSc("RUNNING", after=10 ** 6))
    assert service.control("stop", timeout=0.5) == {
        "ok": False, "error": "the service is running"}


def test_a_service_that_disappears_is_said_missing(windows, monkeypatch):
    sc = FakeSc("RUNNING")

    def removed(*args, timeout=30):
        if args[0] == "stop":
            sc.state = None
            return 0, ""
        return sc(*args, timeout=timeout)
    monkeypatch.setattr(service, "_sc", removed)
    assert service.control("stop", timeout=0.5) == {
        "ok": False, "error": "the service is missing"}


def test_the_state_is_read_from_what_sc_prints(monkeypatch):
    for out, state in [("STATE : 4 RUNNING", "RUNNING"), ("STATE : 1 STOPPED", "STOPPED"),
                       ("STATE : 2 START_PENDING", "START_PENDING"),
                       ("STATE : 3 STOP_PENDING", "STOP_PENDING"),
                       ("something else", "UNKNOWN")]:
        monkeypatch.setattr(service, "_sc", lambda *a, out=out, **k: (0, out))
        assert service._win_state() == state, out
    monkeypatch.setattr(service, "_sc", lambda *a, **k: (1060, "no such service"))
    assert service._win_state() == ""


def test_sc_runs_without_a_console_window(monkeypatch):
    """Anything started from the desk agent must not flash a console window
    (seen with Ctrl+C, when the clipboard went through PowerShell)."""
    import subprocess
    import types
    seen = {}

    def run(cmd, **k):
        seen.update(k, cmd=cmd)
        return types.SimpleNamespace(returncode=0, stdout="  STATE  :  4  RUNNING \r\n")
    monkeypatch.setattr(subprocess, "run", run)
    assert service._sc("query") == (0, "STATE : 4 RUNNING")
    assert seen["cmd"] == ["sc", "query", "NishroLink"]
    assert seen["creationflags"] == 0x08000000


def test_no_sc_is_an_answer_not_a_crash(monkeypatch):
    import subprocess

    def run(cmd, **k):
        raise FileNotFoundError("sc")
    monkeypatch.setattr(subprocess, "run", run)
    assert service._sc("query")[0] == -1


def test_installed_on_windows_means_sc_knows_the_service(monkeypatch):
    import types
    monkeypatch.setattr(service, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(service, "_sc", lambda *a, **k: (0, "STATE : 4 RUNNING"))
    assert service.installed() is True
    monkeypatch.setattr(service, "_sc", lambda *a, **k: (1060, "no such service"))
    assert service.installed() is False


@pytest.fixture
def linux(monkeypatch):
    import types
    monkeypatch.setattr(service, "sys", types.SimpleNamespace(platform="linux"))


def test_on_linux_systemctl_does_it(linux, monkeypatch):
    import subprocess
    import types
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: ran.append(cmd) or
                        types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert service.control("stop") == {"ok": True}
    assert ran == [["systemctl", "stop", "nishro-link.service"]]


def test_on_linux_a_refusal_is_passed_on(linux, monkeypatch):
    """polkit too old for the .deb's rule, and the password prompt cancelled."""
    import subprocess
    import types
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: types.SimpleNamespace(
        returncode=1, stdout="", stderr="Interactive authentication required.\n"))
    assert service.control("start") == {
        "ok": False, "error": "Interactive authentication required."}


def test_on_linux_without_systemctl_it_says_so(linux, monkeypatch):
    import subprocess

    def run(cmd, **k):
        raise FileNotFoundError("systemctl")
    monkeypatch.setattr(subprocess, "run", run)
    r = service.control("start")
    assert r["ok"] is False and "systemctl" in r["error"]


def test_a_quit_that_cannot_tell_the_tray_still_quits(monkeypatch, tmp_path):
    """The service has stopped - that is the quit. Not being able to leave the
    note for the tray is a warning, not a failure."""
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    monkeypatch.setattr(service, "_quit_marker", lambda: blocker / "nested" / "quit")
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(service, "control", lambda action: {"ok": True})
    r = service.quit_everything()
    assert r["ok"] is True
    assert "could not tell the tray" in r["warning"]


# ------------------------------------------------ the launcher, around the service
def test_a_service_that_started_but_never_got_ready_is_said(monkeypatch):
    import types
    from link import nishro_link
    clock = {"t": 0.0}

    def monotonic():
        clock["t"] += 5.0
        return clock["t"]
    monkeypatch.setattr(nishro_link, "time", types.SimpleNamespace(
        monotonic=monotonic, sleep=lambda s: None))
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(service, "published", lambda *a: False)
    monkeypatch.setattr(service, "control", lambda action: {"ok": True})
    monkeypatch.setattr(service, "find", lambda *a, **k: pytest.fail(
        "went looking for a service that never got ready"))
    said = []
    monkeypatch.setattr(nishro_link, "_not_started", lambda e: said.append(e) or 5)
    args = nishro_link.build_parser().parse_args([])
    assert nishro_link.attach(args, {"port": _free_port()}) == 5
    assert said == ["it started, but was not ready in time"]


def test_could_not_start_is_said_in_a_message_box_too(monkeypatch, capsys):
    """Opened from a menu there is no console: the reason must be on screen."""
    tkinter = pytest.importorskip("tkinter")
    import tkinter.messagebox
    from link import nishro_link
    shown = []

    class Root:
        def withdraw(self):
            pass

        def destroy(self):
            pass
    monkeypatch.setattr(tkinter, "Tk", Root)
    monkeypatch.setattr(tkinter.messagebox, "showerror", lambda title, msg: shown.append((title, msg)))
    assert nishro_link._not_started("access denied") == 5
    assert shown[0][0] == "Nishro Link"
    assert "access denied" in shown[0][1]
    assert "restart the computer" in shown[0][1]
    assert "access denied" in capsys.readouterr().err
    nishro_link._not_started(None)
    assert "no reason given" in shown[1][1]


def test_opening_brings_the_tray_back_and_add_goes_straight_to_adding(monkeypatch):
    """The window of an installed service: the person's tray comes back with
    it (hidden, or never started after an install), and --add (the dock's
    "Add a device") opens on adding one."""
    import types
    from link import autostart, nishro_link, tray
    port = _free_port()
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(service, "published", lambda *a: True)
    monkeypatch.setattr(service, "find", lambda *a, **k: (port, "tok"))
    trays = []
    monkeypatch.setattr(tray, "start_in_background", lambda cmd: trays.append(cmd))
    windows = []

    class Root:
        def __init__(self):
            self.later = []

        def after(self, ms, fn):
            self.later.append((ms, fn))

    class App:
        def __init__(self, api, remote=False):
            self.remote, self.root, self.ran = remote, Root(), False
            windows.append(self)

        def show(self):
            pass

        def _add_device(self):
            pass

        def run(self):
            self.ran = True
    monkeypatch.setattr(nishro_link, "load_window", lambda log: types.SimpleNamespace(App=App))
    args = nishro_link.build_parser().parse_args(["--add"])
    assert nishro_link.attach(args, {"port": port}) == 0
    w = windows[0]
    assert w.remote is True, "a window onto the service, not an engine"
    assert w.ran
    assert w.root.later == [(400, w._add_device)]
    assert trays == [autostart.launcher()]


def test_the_tray_and_one_shot_switches_reach_their_handlers(monkeypatch):
    from link import nishro_link, tray
    monkeypatch.setattr(sys, "setswitchinterval", lambda s: None)
    monkeypatch.setattr(tray, "run", lambda log: 7)
    monkeypatch.setattr(sys, "argv", ["nishro-link", "--tray"])
    assert nishro_link.main() == 7
    monkeypatch.setattr(nishro_link, "one_shot", lambda args: 8)
    for flags in (["--sharing", "off"], ["--find-pointer"], ["--release-input"]):
        monkeypatch.setattr(sys, "argv", ["nishro-link", *flags])
        assert nishro_link.main() == 8, flags


def test_a_one_shot_without_access_to_the_service_says_so(monkeypatch, capsys):
    from link import nishro_link

    def denied(*a, **k):
        raise PermissionError("not this account")
    monkeypatch.setattr(service, "find", denied)
    args = nishro_link.build_parser().parse_args(["--find-pointer"])
    assert nishro_link.one_shot(args) == 3
    assert "can't reach" in capsys.readouterr().err
