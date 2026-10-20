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
        self.said = []                    # (title, text), so a test can read it

    def show(self, st, items):
        self.shown.append(st)

    def notify(self, title, text):
        self.notes.append(title)
        self.said.append((title, text))

    def quit(self):
        self.quit_called = True


def make(api, in_front=None):
    """A tray, with the window it would move answered for.

    The real one looks at this computer's own windows; a test says what it
    would have found, so no test depends on what happens to be on screen.
    """
    ui = FakeUI()
    t = tray.Tray(lambda: api, ui, ["nishro-link"])
    t.look_for_window = lambda: in_front
    return t, ui


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
         "move_windows": True, "windows_here": ["home", "away"],
         "current_window": {"which": "111", "title": "Notepad"}}
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


def test_one_other_computer_still_gets_the_list_not_a_plain_entry():
    """The shape should not change when a third computer appears."""
    st = with_windows()
    sub = [i for i in tray.window_items(st) if i.get("kind") == "sub"]
    assert sub and sub[0]["label"] == "Move the current window to"
    assert [i["action"] for i in sub[0]["items"]] == ["window:aio"]
    assert "Move the current window to" in labels(st)


def test_several_computers_get_a_submenu():
    st = with_windows(devices=[
        {"name": "laptop", "me": True, "hub": True, "online": True},
        {"name": "aio", "me": False, "hub": False, "online": True},
        {"name": "desktop-1", "me": False, "hub": False, "online": True}])
    sub = [i for i in tray.window_items(st) if i.get("kind") == "sub"]
    assert sub and sub[0]["label"] == "Move the current window to"
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
            if self.error:
                return {"error": self.error}
            # It really arrives, so the tray may say so.
            self.s = dict(self.s, windows_away=[
                {"which": f"laptop:{body.get('which')}",
                 "device": body.get("device"), "ours": True}])
            return {"ok": True}
        if path == "/api/window/home":
            return {"ok": True, "brought_home": 2}
        return super().command(path, body)


def window_status(**extra):
    s = {"enabled": True, "connected": True, "role": "hub",
         "move_windows": True, "windows_here": ["home", "away"],
         "current_window": {"which": "111", "title": "Notepad"},
         "devices": [{"name": "laptop", "me": True, "hub": True, "online": True},
                     {"name": "aio", "me": False, "hub": False, "online": True}]}
    s.update(extra)
    return s


def test_clicking_move_sends_the_window_in_front():
    """The entry used to be drawn and do nothing at all - the action was
    never wired up. The window is now the one the TRAY saw in front, so it
    is the same one the menu named."""
    api = WindowAPI(window_status())
    t, ui = make(api, in_front={"which": "111", "title": "Notepad"})
    t.poll_once()
    t._move_a_window("aio")
    assert ("/api/window/move", {"which": "111", "device": "aio"}) in api.asked, (
        f"asked: {api.asked}")
    assert ui.notes, "the person was not told it happened"


def test_where_the_tray_cannot_see_windows_it_asks_the_service():
    """On Linux the tray cannot look at the windows itself, so the part of
    Nishro Link that can is asked - and its list is frontmost first, which
    is what "the window in front" means."""
    api = WindowAPI(window_status(current_window=None),
                    windows=[{"which": "999", "title": "On top"},
                             {"which": "111", "title": "Behind"}])
    t, ui = make(api)
    t.poll_once()
    t._move_a_window("aio")
    assert ("/api/windows", {}) in api.asked
    moved = [b for p, b in api.asked if p == "/api/window/move"]
    assert moved == [{"which": "999", "device": "aio"}]


def test_with_no_window_to_move_the_person_is_told_not_left_wondering():
    api = WindowAPI(window_status(current_window=None), windows=[])
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
    t, ui = make(api, in_front={"which": "111", "title": "Notepad"})
    t.poll_once()
    items = tray.window_items(tray.state(window_status()))
    inside = [i for it in items for i in (it.get("items") or [])]
    action = next(i["action"] for i in items + inside
                  if (i.get("action") or "").startswith("window:"))
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














# ------------------- "Move the current window to", and telling the truth
TWO = {"enabled": True, "move_windows": True, "windows_here": ["home", "away"],
       "devices": [{"name": "laptop", "online": True, "me": True, "hub": True},
                   {"name": "aio", "online": True, "me": False, "hub": False},
                   {"name": "desk-pc", "online": True, "me": False,
                    "hub": False}],
       "current_window": {"which": "4242", "title": "report.txt - Notepad"}}


def items_of(st, label):
    """The submenu under a label, or None."""
    for it in tray.menu(st):
        if it.get("label") == label:
            return it
    for it in tray.menu(st):
        for sub in it.get("items") or []:
            if sub.get("label") == label:
                return sub
    return None


def test_the_window_in_front_is_named_in_the_menu():
    """The maintainer: "sometimes i am not sure which windo is chosen"."""
    st = tray.state(TWO)
    labels = [i.get("label") for i in tray.menu(st)]
    assert any("report.txt - Notepad" in (l or "") for l in labels), labels


def test_moving_is_always_a_list_of_devices_even_with_one():
    """There will be more than two computers, so the shape should not change
    when a third appears."""
    one = dict(TWO, devices=[d for d in TWO["devices"]
                             if d["name"] in ("laptop", "aio")])
    sub = items_of(tray.state(one), "Move the current window to")
    assert sub is not None, tray.menu(tray.state(one))
    assert sub["kind"] == "sub"
    assert [i["label"] for i in sub["items"]] == ["aio"]


def test_every_other_computer_is_offered():
    sub = items_of(tray.state(TWO), "Move the current window to")
    assert [i["label"] for i in sub["items"]] == ["aio", "desk-pc"]
    assert all(i["action"].startswith("window:") for i in sub["items"])


def test_this_computer_is_not_offered_to_itself():
    sub = items_of(tray.state(TWO), "Move the current window to")
    assert "laptop" not in [i["label"] for i in sub["items"]]


def test_with_no_window_in_front_there_is_nothing_to_move():
    st = tray.state(dict(TWO, current_window=None))
    assert items_of(st, "Move the current window to") is None


# These two reach into window_win, which does ctypes.WinDLL("user32") when
# it is imported - so they cannot even be collected off Windows, let alone
# run. Pretending the platform is win32 is not enough; the module behind it
# has to exist. CI's five Linux jobs found this in 25 seconds, which no
# amount of running the suite on this laptop would have.
@pytest.mark.skipif(sys.platform != "win32",
                    reason="window_win loads user32 at import")
def test_the_tray_sees_the_window_in_front_itself(monkeypatch):
    """Asked of this computer, not of the service: the tray runs where the
    windows are, and asking the service starts a helper process - far too
    much to do every two seconds to put a name in a menu."""
    monkeypatch.setattr(tray.sys, "platform", "win32")
    import link.window_win as ww
    monkeypatch.setattr(ww, "in_front",
                        lambda own_pid=None: (4242, "report.txt - Notepad"))
    monkeypatch.setattr(ww, "handle", lambda h: int(h))
    assert tray.current_window() == {"which": "4242",
                                     "title": "report.txt - Notepad"}


@pytest.mark.skipif(sys.platform != "win32",
                    reason="window_win loads user32 at import")
def test_a_frame_in_front_means_nothing_to_move(monkeypatch):
    """Not the window underneath it: that is not the one anybody meant."""
    monkeypatch.setattr(tray.sys, "platform", "win32")
    import link.window_win as ww
    monkeypatch.setattr(ww, "in_front", lambda own_pid=None: (
        None, "that is a window from another computer, shown here"))
    assert tray.current_window() is None


@pytest.mark.skipif(sys.platform != "win32",
                    reason="window_win loads user32 at import")
def test_nothing_in_front_is_not_an_error(monkeypatch):
    monkeypatch.setattr(tray.sys, "platform", "win32")
    import link.window_win as ww
    monkeypatch.setattr(ww, "in_front", lambda own_pid=None: (None, ""))
    assert tray.current_window() is None


def test_where_windows_cannot_be_seen_it_simply_says_so(monkeypatch):
    monkeypatch.setattr(tray.sys, "platform", "linux")
    assert tray.current_window() is None


# ------------------------------------------------- asking is not arriving
class MovingAPI:
    """A service that takes the request and may or may not deliver."""

    def __init__(self, arrives=True, error=None, trouble=None):
        self.arrives, self.error, self.trouble = arrives, error, trouble
        self.asked = []
        self.away = []

    def status(self):
        return {"enabled": True, "move_windows": True,
                "windows_here": ["home", "away"], "devices": TWO["devices"],
                "windows_away": self.away, "window_trouble": self.trouble}

    def command(self, path, body):
        self.asked.append((path, body))
        if self.error:
            return {"error": self.error}
        if self.arrives:
            self.away = [{"which": f"laptop:{body.get('which')}",
                          "device": body.get("device"), "ours": True}]
        return {"ok": True}


def test_it_says_the_window_arrived_only_once_it_has():
    t, ui = make(None)
    t.api = MovingAPI(arrives=True)
    t.st = tray.state(TWO)
    t._move_a_window("aio")
    assert t.api.asked == [("/api/window/move",
                            {"which": "4242", "device": "aio"})]
    assert ui.notes == ["Nishro Link"]
    assert any("is now on aio" in text for _title, text in ui.said), ui.said


def test_a_window_that_never_turns_up_is_not_announced_as_arrived():
    """The bug the maintainer reported: "moving a window in laptop shows
    notification that window in aio, but actually not". /api/window/move
    answers as soon as the request is on its way - on a hub it has only
    asked the other computer to dial in - so the tray announced success the
    instant it was clicked, and the other computer refused a second later.
    """
    t, ui = make(None)
    t.api = MovingAPI(arrives=False)
    t.st = tray.state(TWO)
    t._move_a_window("aio")
    assert not any("is now on" in text for _title, text in ui.said), ui.said
    assert any("could not" in text for _title, text in ui.said), ui.said


def test_the_other_computers_reason_is_passed_on_if_it_gave_one():
    t, ui = make(None)
    t.api = MovingAPI(arrives=False,
                      trouble={"device": "aio",
                               "why": "nobody is signed in on aio"})
    t.st = tray.state(TWO)
    t._move_a_window("aio")
    assert any("nobody is signed in on aio" in text
               for _title, text in ui.said), ui.said


def test_a_refusal_from_this_computer_is_said_at_once():
    t, ui = make(None)
    t.api = MovingAPI(error="aio cannot show a window from here")
    t.st = tray.state(TWO)
    t._move_a_window("aio")
    assert any("cannot show a window" in text for _title, text in ui.said), \
        ui.said


# ------------------------------------------- GNOME: its own dialog chooses
def test_where_gnome_chooses_the_window_the_menu_offers_to_show_one():
    """No program may see which window is in front on GNOME, so nothing is
    named: the computer is chosen here, the window in GNOME's dialog."""
    st = with_windows(current_window=None, windows_pick=True)
    sub = [i for i in tray.window_items(st) if i.get("kind") == "sub"]
    assert sub and sub[0]["label"] == "Show a window on"
    assert [i["action"] for i in sub[0]["items"]] == ["window:aio"]
    assert not any((i.get("label") or "").startswith("In front:")
                   for i in tray.window_items(st))


def test_a_status_without_the_pick_flag_offers_nothing_new():
    """An older service says nothing about picking: as before, no window in
    front means nothing to move."""
    st = with_windows(current_window=None)
    assert not any(i.get("kind") == "sub" for i in tray.window_items(st))


def test_each_window_chosen_in_gnomes_dialog_is_asked_for_by_a_name_of_its_own():
    """Two windows shown at once must not be taken for one: the service keeps
    them apart by what the tray asked for."""
    t, ui = make(None)
    t.api = MovingAPI(arrives=True)
    t.st = tray.state(dict(TWO, windows_pick=True))
    t._move_a_window("aio")
    t._move_a_window("aio")
    asked = [b["which"] for p, b in t.api.asked if p == "/api/window/move"]
    assert len(asked) == 2 and all(w.startswith("pick-") for w in asked)
    assert asked[0] != asked[1]


def test_the_person_is_told_to_choose_and_then_where_it_went():
    t, ui = make(None)
    t.api = MovingAPI(arrives=True)
    t.st = tray.state(dict(TWO, windows_pick=True))
    t._move_a_window("aio")
    texts = [text for _title, text in ui.said]
    assert any("Choose the window to show on aio" in x for x in texts), texts
    assert any("is now on aio, and still here too" in x for x in texts), texts


def test_the_tray_waits_as_long_as_gnomes_dialog_may_take(monkeypatch):
    """Choosing takes the person a while: the short wait for an ordinary
    window would give up on them before they had chosen."""
    monkeypatch.setattr(tray, "WINDOW_WAIT_S", 0.05)
    monkeypatch.setattr(tray, "PICK_WAIT_S", 0.6)
    t, ui = make(None)
    t.api = MovingAPI(arrives=False)
    t.st = tray.state(dict(TWO, windows_pick=True))
    started = time.monotonic()
    t._move_a_window("aio")
    assert time.monotonic() - started >= 0.55, "it gave up as for any window"
    assert any("could not" in text for _title, text in ui.said), ui.said
