"""The tray icon: what it shows for each state of the service, its menu, its
notifications, and what its clicks do. Asked for: "basic configurations can be
done ... on/off pairing/communicating, and more necessary things, so to operate
basic things should not need to open the main software" - with a devices list,
the state in the icon, notifications, and the Linux dock's right-click menu.

What is decided is pure data (tray.state/menu/changes), so it is tested here
on any system; the Windows menu builder is tested on Windows.
"""
import sys

import pytest

from link import service, tray


@pytest.fixture(autouse=True)
def own_quit_marker(monkeypatch, tmp_path):
    """Never this computer's real one: a test that quits writes it."""
    monkeypatch.setattr(service, "_quit_marker", lambda: tmp_path / "quit")


def status(**kw):
    s = {"enabled": True, "role": "hub", "connected": True, "group": "laptop",
         "setup": None, "notify": True,
         "devices": [{"name": "laptop", "online": True, "me": True, "hub": True},
                     {"name": "aio", "online": True, "me": False, "hub": False,
                      "rtt_ms": 1.8},
                     {"name": "desktop", "online": False, "me": False, "hub": False}]}
    s.update(kw)
    return s


# ------------------------------------------------------------------ the icon
@pytest.mark.parametrize("s, icon, header", [
    (None, "alert", "Nishro Link isn't running"),
    (status(setup={"problems": ["x"]}), "alert", "Setup needed - open Nishro Link"),
    (status(enabled=False), "paused", "Sharing is off"),
    (status(role="alone", devices=[]), "ok", "Ready - add a device to start"),
    (status(), "ok", "Connected · 2 of 3 online"),
    (status(connected=False), "alert", "Waiting for the other devices"),
    (status(role="member", connected=False), "alert", "Not connected to laptop"),
])
def test_the_icon_and_its_first_line_say_the_state(s, icon, header):
    st = tray.state(s)
    assert (st["icon"], st["header"]) == (icon, header)
    assert st["tooltip"].endswith(header)


# ------------------------------------------------------------------ the menu
def flat(items):
    for it in items:
        yield it
        yield from flat(it.get("items", []))


def test_the_menu_holds_the_everyday_controls():
    items = tray.menu(tray.state(status()))
    labels = [it.get("label") for it in items if it["kind"] != "sep"]
    assert labels == ["Connected · 2 of 3 online", "Sharing", "Find the pointer",
                      "Release input", "Devices", "Open Nishro Link", "Hide this icon",
                      "Quit Nishro Link"]
    sharing = next(it for it in items if it.get("action") == "sharing")
    assert sharing["kind"] == "check" and sharing["checked"]
    assert next(it for it in items if it.get("default"))["action"] == "open"
    assert not items[0]["enabled"], "the first line is a statement, not a button"


def test_sharing_off_shows_unticked():
    items = tray.menu(tray.state(status(enabled=False)))
    assert not next(it for it in items if it.get("action") == "sharing")["checked"]


def test_the_devices_are_listed_with_whether_they_are_online():
    devices = next(it for it in tray.menu(tray.state(status())) if it["kind"] == "sub")
    labels = [it["label"] for it in devices["items"] if it["kind"] != "sep"]
    assert labels == ["laptop  (hub)  ·  this device", "● aio  ·  online",
                      "○ desktop  ·  offline", "Add a device…"]
    assert devices["items"][-1]["action"] == "add"


def test_a_changing_round_trip_does_not_redraw_the_menu():
    """Linux rebuilds the menu on every change - and one rebuilt every two
    seconds closes under the person reading it."""
    a = tray.state(status())
    b = tray.state(status(devices=[
        {"name": "laptop", "online": True, "me": True, "hub": True},
        {"name": "aio", "online": True, "me": False, "hub": False, "rtt_ms": 7.3},
        {"name": "desktop", "online": False, "me": False, "hub": False}]))
    assert a == b


def test_with_no_service_only_what_can_work_is_offered():
    items = tray.menu(tray.state(None))
    for it in flat(items):
        if it.get("action") in ("sharing", "find", "release", "add"):
            assert not it["enabled"], it["label"]
    assert next(it for it in items if it.get("action") == "open")["enabled"]


# --------------------------------------------------------------- notifications
def test_devices_connecting_and_dropping_out_are_announced():
    before = tray.state(status())
    after = tray.state(status(devices=[
        {"name": "laptop", "online": True, "me": True, "hub": True},
        {"name": "aio", "online": False, "me": False, "hub": False},
        {"name": "desktop", "online": True, "me": False, "hub": False}]))
    assert tray.changes(before, after) == [
        ("Device disconnected", "aio is no longer connected."),
        ("Device connected", "desktop is connected.")]


def test_nothing_is_announced_on_the_first_look_or_when_the_service_comes_and_goes():
    assert tray.changes(None, tray.state(status())) == []
    assert tray.changes(tray.state(status()), tray.state(None)) == []
    assert tray.changes(tray.state(None), tray.state(status())) == []


# ------------------------------------------------------------------ the tray
class FakeAPI:
    def __init__(self, s):
        self.s, self.sent, self.fail = s, [], False

    def status(self):
        if self.fail:
            raise OSError("service restarting")
        return self.s

    def command(self, path, body):
        self.sent.append(path)
        if path == "/api/disable":
            self.s = dict(self.s, enabled=False)
        if path == "/api/enable":
            self.s = dict(self.s, enabled=True)
        return {"ok": True}


class FakeUI:
    def __init__(self):
        self.shown, self.notes, self.quit_called = [], [], False

    def show(self, st, items):
        self.shown.append(st)

    def notify(self, title, text):
        self.notes.append(title)

    def quit(self):
        self.quit_called = True


def make(api):
    ui = FakeUI()
    return tray.Tray(lambda: api, ui, ["nishro-link"]), ui


def test_it_redraws_only_when_something_changed():
    api = FakeAPI(status())
    t, ui = make(api)
    t.poll_once()
    t.poll_once()
    assert len(ui.shown) == 1
    api.s = status(enabled=False)
    t.poll_once()
    assert len(ui.shown) == 2 and ui.shown[-1]["icon"] == "paused"


def test_notifications_follow_the_setting():
    api = FakeAPI(status())
    t, ui = make(api)
    t.poll_once()
    api.s = status(devices=[{"name": "laptop", "online": True, "me": True, "hub": True},
                            {"name": "aio", "online": False, "me": False, "hub": False},
                            {"name": "desktop", "online": False, "me": False,
                             "hub": False}])
    t.poll_once()
    assert ui.notes == ["Device disconnected"]
    api.s = dict(status(), notify=False)
    t.poll_once()
    assert ui.notes == ["Device disconnected"], "switched off in Settings"


def test_a_restarted_service_is_found_again():
    apis = [FakeAPI(status()), FakeAPI(status(enabled=False))]
    ui = FakeUI()
    t = tray.Tray(lambda: apis[0] if t.api is None and not apis[0].fail else apis[1],
                  ui, ["nishro-link"])
    t.poll_once()
    apis[0].fail = True
    t.poll_once()
    assert ui.shown[-1]["icon"] == "paused", "the new service's state"


def test_the_clicks_do_what_they_say(monkeypatch):
    spawned = []
    monkeypatch.setattr(tray, "spawn", lambda cmd: spawned.append(cmd))
    monkeypatch.setattr(tray.threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": lambda s: target()})())
    api = FakeAPI(status())
    t, ui = make(api)
    t.poll_once()
    t.act("sharing")
    assert api.sent == ["/api/disable"] and ui.shown[-1]["sharing"] is False
    t.act("sharing")
    assert api.sent[-1] == "/api/enable"
    t.act("find")
    t.act("release")
    assert api.sent[-2:] == ["/api/find", "/api/release"]
    t.act("open")
    t.act("add")
    assert spawned == [["nishro-link"], ["nishro-link", "--add"]]
    t.act("hide")
    assert ui.quit_called


def test_quit_stops_everything_then_the_tray_goes(monkeypatch):
    """Asked for: "an option in the app and the taskbar to quit Nishro Link
    fully" - after closing processes by hand in Task Manager."""
    monkeypatch.setattr(tray.threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": lambda s: target()})())
    asked = []
    monkeypatch.setattr(service, "quit_everything",
                        lambda: asked.append("quit") or {"ok": True})
    t, ui = make(FakeAPI(status()))
    t.act("quit")
    assert asked == ["quit"] and ui.quit_called


def test_a_quit_that_failed_keeps_the_tray_and_says_why(monkeypatch):
    monkeypatch.setattr(tray.threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": lambda s: target()})())
    monkeypatch.setattr(service, "quit_everything",
                        lambda: {"ok": False, "error": "access denied"})
    t, ui = make(FakeAPI(status()))
    t.act("quit")
    assert not ui.quit_called and ui.notes == ["Nishro Link"]


def test_a_quit_from_the_window_closes_the_tray(monkeypatch):
    t, ui = make(FakeAPI(status()))
    t.poll_once()
    assert not ui.quit_called
    monkeypatch.setattr(service, "installed", lambda: False)
    import time
    time.sleep(0.05)                           # Windows' clock ticks every 16 ms
    service.quit_everything()                  # what the window's Quit does
    t.poll_once()
    assert ui.quit_called


def test_a_tray_started_after_a_quit_stays(monkeypatch):
    monkeypatch.setattr(service, "installed", lambda: False)
    service.quit_everything()
    import time
    time.sleep(0.05)
    t, ui = make(FakeAPI(status()))            # Open Nishro Link, after the Quit
    t.poll_once()
    assert not ui.quit_called and ui.shown


# ---------------------------------------------------------- the command line
def test_the_one_shot_controls_ask_the_service(monkeypatch, capsys):
    from link import nishro_link, service
    api = FakeAPI(status())
    monkeypatch.setattr(service, "find", lambda *a, **k: (8771, "t"))
    monkeypatch.setattr(service, "RemoteAPI", lambda *a, **k: api)
    p = nishro_link.build_parser()
    assert nishro_link.one_shot(p.parse_args(["--sharing", "off"])) == 0
    assert nishro_link.one_shot(p.parse_args(["--sharing", "toggle"])) == 0
    assert nishro_link.one_shot(p.parse_args(["--find-pointer"])) == 0
    assert nishro_link.one_shot(p.parse_args(["--release-input"])) == 0
    assert api.sent == ["/api/disable", "/api/enable", "/api/find", "/api/release"]
    assert "sharing off" in capsys.readouterr().out


def test_a_one_shot_with_no_service_says_so(monkeypatch, capsys):
    from link import nishro_link, service
    monkeypatch.setattr(service, "find", lambda *a, **k: None)
    monkeypatch.setattr(service, "problem", None)
    args = nishro_link.build_parser().parse_args(["--find-pointer"])
    assert nishro_link.one_shot(args) == 1
    assert "not running" in capsys.readouterr().err


# ------------------------------------------------------------ Windows' menu
@pytest.mark.skipif(sys.platform != "win32", reason="the Windows tray")
def test_the_windows_menu_is_built_from_the_same_data():
    from link import tray_win
    tray_win._types()
    ids = {}
    h, default = tray_win.build_menu(tray.menu(tray.state(status())), ids)
    try:
        assert h and default
        assert ids[default] == "open"
        assert set(a for a in ids.values() if a) == {
            "sharing", "find", "release", "add", "open", "hide", "quit"}
    finally:
        tray_win.u32.DestroyMenu(h)


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows tray")
def test_every_tray_icon_state_is_there_at_every_scale():
    from link.icon import TRAY
    assert set(TRAY) == {"ok", "paused", "alert"}
    for sizes in TRAY.values():
        assert set(sizes) == {16, 20, 24, 32}


def test_a_public_network_turns_the_icon_amber_and_says_why():
    st = tray.state(status(connected=False, network_public=["Home-WiFi"]))
    assert st["icon"] == "alert" and "Windows blocks" in st["header"]
    assert tray.state(status(network_public=["Home-WiFi"]))["icon"] == "ok", \
        "connected all the same: nothing to say"
    assert tray.state(status(enabled=False, network_public=["Home-WiFi"]))["icon"] == \
        "paused", "sharing off comes first"


def test_what_the_tray_starts_is_its_own_instance(monkeypatch):
    """The one-file Windows build: a copy it starts reuses its unpacked folder,
    deleted when the first copy exits - closing the window gutted its tray."""
    started = []
    monkeypatch.setattr(tray.subprocess, "Popen",
                        lambda cmd, **kw: started.append((cmd, kw)))
    tray.spawn(["nishro-link", "--tray"])
    (cmd, kw), = started
    assert cmd == ["nishro-link", "--tray"]
    assert kw["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
