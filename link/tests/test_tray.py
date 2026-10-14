"""The tray icon: what it shows for each state of the service, its menu, its
notifications, and what its clicks do. Asked for: "basic configurations can be
done ... on/off pairing/communicating, and more necessary things, so to operate
basic things should not need to open the main software" - with a devices list,
the state in the icon, notifications, and the Linux dock's right-click menu.

What is decided is pure data (tray.state/menu/changes), so it is tested here
on any system; the Windows menu builder is tested on Windows.
"""
import time
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


# ----------------------------------------------- the menu, with things wrong
class NotesUI(FakeUI):
    """Keeps what each notification said, not only its title."""

    def notify(self, title, text):
        self.notes.append((title, text))


def test_with_no_service_to_be_found_the_tray_says_so():
    ui = FakeUI()
    t = tray.Tray(lambda: None, ui, ["nishro-link"])
    t.poll_once()
    assert ui.shown == [tray.state(None)]


def test_with_the_service_away_its_controls_do_nothing():
    """The menu may still show them for a moment; a click must not fail."""
    ui = NotesUI()
    t = tray.Tray(lambda: None, ui, ["nishro-link"])
    for action in ("sharing", "find", "release"):
        t.act(action)
    assert ui.notes == []


def test_a_control_that_fails_says_so_and_the_tray_goes_on():
    api = FakeAPI(status())

    def gone(path, body):
        raise OSError("the service went away")
    api.command = gone
    ui = NotesUI()
    t = tray.Tray(lambda: api, ui, ["nishro-link"])
    t.poll_once()
    t.act("find")
    assert ui.notes == [("Nishro Link", "That didn't work: the service went away")]
    assert not ui.quit_called


def test_the_tray_lives_through_a_hiccup_and_stops_when_told(monkeypatch):
    monkeypatch.setattr(tray, "POLL_S", 0.01)
    t, _ = make(FakeAPI(status()))
    polls = []

    def poll_once():
        polls.append(1)
        if len(polls) == 1:
            raise RuntimeError("a hiccup")
        if len(polls) == 3:
            t.stop()
    t.poll_once = poll_once
    t.loop()                                   # returns once stopped
    assert len(polls) == 3


# --------------------------------------------------- starting the tray at all
def test_the_window_brings_back_a_hidden_tray(monkeypatch):
    started = []
    monkeypatch.setattr(tray, "spawn", lambda cmd: started.append(cmd))
    tray.start_in_background(["NishroLink.exe"])
    assert started == [["NishroLink.exe", "--tray"]]


def test_a_tray_that_cannot_start_does_not_stop_the_window(monkeypatch):
    def cannot(cmd):
        raise OSError("not found")
    monkeypatch.setattr(tray, "spawn", cannot)
    tray.start_in_background(["missing.exe"])  # no exception


class RunUI(FakeUI):
    """A tray icon whose run() returns at once - as when Hide is chosen."""

    def __init__(self):
        super().__init__()
        self.on_action, self.ran = None, False

    def run(self):
        self.ran = True


@pytest.fixture
def desktop(monkeypatch):
    """tray.run() with the platform's tray, the one-per-person lock and the
    service all pretended."""
    import types

    import link
    from link import service
    d = types.SimpleNamespace(ui=RunUI(), first=True, made=0)

    def make_ui(log):
        d.made += 1
        return d.ui
    fake = types.SimpleNamespace(make=make_ui)
    for name in ("tray_win", "tray_linux"):
        monkeypatch.setitem(sys.modules, "link." + name, fake)
        monkeypatch.setattr(link, name, fake, raising=False)

    class Only:
        def acquire(self):
            return d.first
    monkeypatch.setattr(tray, "_Only", Only)
    monkeypatch.setattr(service, "find", lambda *a, **k: None)
    monkeypatch.setattr(tray, "POLL_S", 0.01)
    return d


def test_the_tray_shows_until_it_is_hidden(desktop):
    assert tray.run(log=lambda *a: None) == 0
    assert desktop.ui.ran
    assert desktop.ui.on_action is not None, "its menu does something"
    assert desktop.ui.shown, "it drew its icon before showing"


def test_a_second_tray_exits_at_once(desktop):
    desktop.first = False
    assert tray.run(log=lambda *a: None) == 0
    assert desktop.made == 0, "no second icon"


def test_a_desktop_without_a_tray_is_not_an_error(desktop, monkeypatch):
    desktop.ui = None
    assert tray.run(log=lambda *a: None) == 0


# ---------------------------------------- moving windows between computers
def with_windows(**extra):
    """A status with the feature switched on and a device online."""
    s = {"enabled": True, "connected": True, "role": "hub",
         "devices": [{"name": "laptop", "me": True, "hub": True, "online": True},
                     {"name": "aio", "me": False, "hub": False, "online": True}],
         "move_windows": True, "windows_here": ["home", "away"]}
    s.update(extra)
    return tray.state(s)


def labels(st):
    return [i.get("label") for i in tray.menu(st)]


def test_nothing_about_windows_until_the_switch_is_on():
    """Someone who has not turned it on should not be asked to wonder about
    it - nothing shown, rather than something greyed out."""
    st = with_windows(move_windows=False)
    assert tray.window_items(st) == []
    assert not any("window" in (lbl or "").lower() for lbl in labels(st))


def test_nothing_about_windows_where_this_computer_cannot_do_it():
    assert tray.window_items(with_windows(windows_here=[])) == []


def test_one_other_computer_gets_a_plain_entry():
    st = with_windows()
    items = tray.window_items(st)
    assert any(i.get("label") == "Move the window in front to aio"
               and i.get("action") == "window:aio" for i in items)
    assert "Move the window in front to aio" in labels(st)


def test_several_computers_get_a_submenu():
    st = with_windows(devices=[
        {"name": "laptop", "me": True, "hub": True, "online": True},
        {"name": "aio", "me": False, "hub": False, "online": True},
        {"name": "desktop-1", "me": False, "hub": False, "online": True}])
    sub = [i for i in tray.window_items(st) if i.get("kind") == "sub"]
    assert sub and sub[0]["label"] == "Move the window in front to"
    assert [i["action"] for i in sub[0]["items"]] == ["window:aio",
                                                      "window:desktop-1"]


def test_a_computer_that_is_offline_is_not_offered():
    st = with_windows(devices=[
        {"name": "laptop", "me": True, "hub": True, "online": True},
        {"name": "aio", "me": False, "hub": False, "online": False}])
    assert not any("aio" in (i.get("label") or "") for i in tray.window_items(st))


def test_windows_already_away_can_be_brought_home():
    st = with_windows(windows_away=[{"which": "us:1", "device": "aio",
                                     "ours": True}])
    items = tray.window_items(st)
    assert any(i.get("label") == "1 window is on another screen" for i in items)
    assert any(i.get("action") == "windows-home" for i in items)


def test_the_count_of_windows_away_reads_properly():
    st = with_windows(windows_away=[{"which": "us:1"}, {"which": "us:2"}])
    assert any(i.get("label") == "2 windows are on another screen"
               for i in tray.window_items(st))


def test_bringing_home_is_offered_even_with_nowhere_left_to_send_one():
    """A window is away and the other computer has since gone offline: the
    person must still be able to ask for it back."""
    st = with_windows(
        devices=[{"name": "laptop", "me": True, "hub": True, "online": True},
                 {"name": "aio", "me": False, "hub": False, "online": False}],
        windows_away=[{"which": "us:1", "device": "aio", "ours": True}])
    assert any(i.get("action") == "windows-home" for i in tray.window_items(st))


def test_a_status_from_an_older_service_says_nothing_about_windows():
    """N6: the tray must not assume the service knows about any of this."""
    st = tray.state({"enabled": True, "connected": True, "role": "hub",
                     "devices": []})
    assert st["move_windows"] is False
    assert tray.window_items(st) == []


# ------------------------- the window entries actually do something when clicked
class WindowAPI(FakeAPI):
    """Answers the window commands, and remembers what it was asked."""

    def __init__(self, s, windows=None, error=None):
        super().__init__(s)
        self.windows = windows if windows is not None else [
            {"which": "111", "title": "Notepad"},
            {"which": "222", "title": "A browser"}]
        self.error = error
        self.asked = []

    def command(self, path, body):
        self.asked.append((path, body))
        if path == "/api/windows":
            return {"windows": self.windows}
        if path == "/api/window/move":
            return {"error": self.error} if self.error else {"ok": True}
        if path == "/api/window/home":
            return {"ok": True, "brought_home": 2}
        return super().command(path, body)


def window_status():
    return {"enabled": True, "connected": True, "role": "hub",
            "move_windows": True, "windows_here": ["home", "away"],
            "devices": [{"name": "laptop", "me": True, "hub": True, "online": True},
                        {"name": "aio", "me": False, "hub": False, "online": True}]}


def test_clicking_move_sends_the_window_in_front():
    """The entry used to be drawn and do nothing at all - the action was
    never wired up."""
    api = WindowAPI(window_status())
    t, ui = make(api)
    t.poll_once()
    t._move_a_window("aio")
    assert ("/api/windows", {}) in api.asked
    assert ("/api/window/move", {"which": "111", "device": "aio"}) in api.asked, \
        f"asked: {api.asked}"
    assert ui.notes, "the person was not told it happened"


def test_the_window_in_front_is_the_first_one_offered():
    """The list comes back frontmost first, and that is the one meant by
    "the window in front"."""
    api = WindowAPI(window_status(), windows=[{"which": "999", "title": "On top"},
                                              {"which": "111", "title": "Behind"}])
    t, ui = make(api)
    t.poll_once()
    t._move_a_window("aio")
    moved = [b for p, b in api.asked if p == "/api/window/move"]
    assert moved == [{"which": "999", "device": "aio"}]


def test_with_no_window_to_move_the_person_is_told_not_left_wondering():
    api = WindowAPI(window_status(), windows=[])
    t, ui = make(api)
    t.poll_once()
    t._move_a_window("aio")
    assert not [p for p, _b in api.asked if p == "/api/window/move"]
    assert ui.notes, "nothing was said"


def test_a_refusal_is_shown_to_the_person():
    api = WindowAPI(window_status(), error="aio is not connected")
    t, ui = make(api)
    t.poll_once()
    t._move_a_window("aio")
    assert ui.notes, "the reason was not shown"


def test_clicking_bring_windows_home_asks_for_them_back():
    api = WindowAPI(window_status())
    t, ui = make(api)
    t.poll_once()
    t.act("windows-home")
    assert ("/api/window/home", {}) in api.asked
    assert ui.notes, "the person was not told"


def test_the_move_action_is_recognised_from_the_menu_item():
    """The menu puts the device's name in the action; act() must take it."""
    api = WindowAPI(window_status())
    t, ui = make(api)
    t.poll_once()
    items = tray.window_items(tray.state(window_status()))
    action = next(i["action"] for i in items if (i.get("action") or "").startswith("window:"))
    assert action == "window:aio"
    t.act(action)
    time.sleep(0.5)                     # act() does the moving on a thread
    assert any(p == "/api/window/move" for p, _b in api.asked), \
        f"the action did nothing: {api.asked}"


# ------------------------------- a window dragged across to another computer
DOOR = {"side": "left", "edge": 0, "device": "aio"}


class AskedAPI:
    """Remembers the whole of what was asked, body and all."""

    def __init__(self, answer=None):
        self.asked, self.answer = [], answer or {"ok": True}

    def status(self):
        return {}

    def command(self, path, body):
        self.asked.append((path, body))
        return self.answer


def test_the_borders_reach_the_tray_from_the_service():
    st = tray.state({"enabled": True, "move_windows": True,
                     "window_doorways": [DOOR]})
    assert st["window_doorways"] == [DOOR]


def test_no_borders_where_the_service_offers_none():
    assert tray.state({"enabled": True})["window_doorways"] == []


def test_with_the_switch_off_no_border_is_offered_to_a_drag():
    """The watcher asks the tray, not the service, so the switch is checked
    here: a drag must not wait on the service while a hand is moving."""
    t, _ui = make(None)
    t.st = tray.state({"enabled": True, "move_windows": False,
                       "window_doorways": [DOOR]})
    assert t.doorways() == []
    t.st = tray.state({"enabled": True, "move_windows": True,
                       "window_doorways": [DOOR]})
    assert t.doorways() == [DOOR]


def test_a_tray_that_has_not_polled_yet_offers_no_border():
    t, _ui = make(None)
    assert t.doorways() == []


def test_throwing_a_window_asks_the_service_by_its_handle():
    t, ui = make(None)
    t.api = AskedAPI()
    assert t.throw_window(12345, "aio") == ""
    assert t.api.asked == [("/api/window/move",
                            {"which": "12345", "device": "aio"})]
    assert ui.notes == ["Nishro Link"]


def test_a_refusal_comes_back_as_words_not_a_notification():
    """The watcher logs it. Someone dragging a window about does not want a
    popup for every border they happen to cross."""
    t, ui = make(None)
    t.api = AskedAPI({"error": "aio is not connected"})
    assert t.throw_window(1, "aio") == "aio is not connected"
    assert ui.notes == []


def test_with_no_service_the_window_simply_stays():
    t, _ui = make(None)
    t.api = None
    assert t.throw_window(1, "aio") == "Nishro Link is not running"


def test_nothing_is_watched_where_windows_cannot_be_watched(monkeypatch):
    """On GNOME one program may not watch another's window being dragged -
    the same wall that stops a window being hidden there."""
    monkeypatch.setattr(tray.sys, "platform", "linux")
    t, _ui = make(None)
    assert tray._watch_drags(t, print) is None


def test_a_watcher_that_will_not_start_does_not_stop_the_tray(monkeypatch):
    monkeypatch.setattr(tray.sys, "platform", "win32")
    said = []

    def boom(*a, **k):
        raise RuntimeError("no hooks here")
    import link.windrag_win as ww
    monkeypatch.setattr(ww, "Watcher", boom)
    t, _ui = make(None)
    assert tray._watch_drags(t, said.append) is None
    assert any("will not be noticed" in s for s in said), said
