"""The window: its pages, sizing, the look, and that it shows what is true.

Driven headless against a real Tk. Most of these are things that were wrong in
an earlier version and invisible until someone opened it on a small screen or a
dark desktop, or with the machines in a state nobody had tried.
"""
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" and not (os.environ.get("DISPLAY")
                                     or os.environ.get("WAYLAND_DISPLAY")),
    reason="no display for Tk")

import tkinter as tk                                       # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))

from link import config, control_api, ui_theme, ui_tk      # noqa: E402
from link.desk import simple                               # noqa: E402
from link.node import Node, NodeCore                       # noqa: E402
from link.runtime import RunLog                            # noqa: E402
from link.ui_kit import Button                             # noqa: E402
from test_node_live import FakeCapture, FakeInjector       # noqa: E402


@pytest.fixture(scope="module")
def tk_root(tk_session):
    """The one shared interpreter - see conftest.py. Each App gets a Toplevel."""
    return tk_session


@pytest.fixture
def app(tmp_path, tk_root):
    cfg = config.merge(config.DEFAULTS, {
        "node": "laptop", "peer": "aio", "hub": True, "side": "right",
        "pin": "k7qm-2xvp-9hdt", "port": 8770})
    lay = simple("laptop", (1366, 768), "aio", (1920, 1080), "right")
    core = NodeCore("laptop", lay, cfg["policy"], is_hub=True, side="right")
    node = Node(core, FakeCapture(), FakeInjector(), port=8770)
    node.capture.start(node)
    log = RunLog(tmp_path / "link.log", echo=False)
    for i in range(40):
        log(f"line {i}")
    log("peer connected: aio (password verified both ways)")
    log("rejected x at y: wrong password")
    api = control_api.ControlAPI(node, cfg, log, cfg_path=tmp_path / "c.json",
                                 port=0)
    a = ui_tk.App(api, root=tk.Toplevel(tk_root))
    a._nearby_search = lambda: {"devices": []}     # never the real network
    a.root.update()
    try:
        yield a
    finally:
        a.close()
        node.stop()


def all_text(widget, out=None):
    out = [] if out is None else out
    try:
        t = widget.cget("text")
        if t:
            out.append(str(t))
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        all_text(child, out)
    return out


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


# ------------------------------------------------------------------ pages
def test_one_navigation_with_settings_and_help_at_its_foot(app):
    """Reported: a menu bar AND five tabs looked a mess - most things were
    there twice. One pane now, as in Windows' own Settings."""
    assert set(app.nav) == {"overview", "devices", "arrange", "activity",
                            "settings", "help"}
    assert app.page == "overview"
    assert not hasattr(app, "menubar")
    ys = {n: app.nav[n]["row"].winfo_y() for n in app.nav}
    top = max(ys[n] for n in ("overview", "devices", "arrange", "activity"))
    assert ys["settings"] > top and ys["help"] > ys["settings"]


def test_the_page_showing_is_marked(app):
    app.show_page("devices")
    app.root.update()
    assert app.nav["devices"]["bar"].winfo_manager() == "place"
    assert not app.nav["overview"]["bar"].winfo_manager()


def test_switching_pages_shows_one_at_a_time(app):
    for name in app.nav:
        app.show_page(name)
        app.root.update()
        shown = [n for n, p in app.pages.items() if p.winfo_manager()]
        assert shown == [name]
        assert app.title.cget("text") == dict(
            (n, t) for n, t, _ in ui_tk.PAGES)[name]


def test_keyboard_shortcuts_are_bound_for_every_page(app):
    for i in range(1, len(ui_tk.PAGES) + 1):
        assert app.root.bind(f"<Control-Key-{i}>")


# ----------------------------------------------------------------- sizing
def test_the_window_fits_the_screen(app):
    """The first version asked for 780 pixels of height on a 768-pixel laptop."""
    assert app.root.winfo_height() <= app.root.winfo_screenheight()
    assert app.root.winfo_width() <= app.root.winfo_screenwidth()


def test_the_minimum_fits_a_small_laptop(app):
    w, h = app.root.minsize()
    assert w <= 800 and h <= 600


def test_a_narrow_window_folds_the_sidebar_to_icons(app):
    app.root.geometry("600x500")
    app.root.update()
    app._reflow()
    assert int(app.sidebar.cget("width")) == 64
    assert not app.nav["devices"]["text"].winfo_manager()
    app.root.geometry("1000x650")
    app.root.update()
    app._reflow()
    assert int(app.sidebar.cget("width")) == 200
    assert app.nav["devices"]["text"].winfo_manager()


@pytest.mark.parametrize("size", [(600, 460), (800, 600), (1200, 800)])
def test_text_wraps_to_the_window_at_any_size(app, size):
    """Labels do not wrap on their own: without this every explanation is
    clipped when narrow and one long line when wide."""
    w, h = size
    app.root.geometry(f"{w}x{h}")
    app.root.update()
    app._reflow()
    for lb in (app.hero_sub, app.arr_info):
        wl = int(lb.cget("wraplength"))
        assert 0 < wl <= w, f"{wl} does not fit {w}"


# ------------------------------------------------------------------ the look
def test_one_theme_everywhere(app):
    C = ui_theme.palette()
    assert app.root.cget("bg") == C["bg"]
    assert app.arranger.C["surface"] == C["surface"]
    assert app.log.cget("background") == C["surface"]


def test_both_themes_have_every_colour_and_are_readable(tk_root):
    """Light and Dark name the same colours, so no page can work in one and
    break in the other - and text stands out from what it is written on."""
    assert set(ui_theme.DARK) == set(ui_theme.LIGHT)
    L = lambda c: ui_theme.luminance(tk_root, c)            # noqa: E731
    for C, dark in ((ui_theme.DARK, True), (ui_theme.LIGHT, False)):
        assert C["dark"] is dark
        assert (L(C["bg"]) < 0.15) if dark else (L(C["bg"]) > 0.9)
        for surface in ("bg", "panel", "card", "sidebar"):
            assert abs(L(C["ink"]) - L(C[surface])) > 0.75, surface
            assert abs(L(C["dim"]) - L(C[surface])) > 0.35, surface
        assert abs(L(C["accent_ink"]) - L(C["accent"])) > 0.4
    assert ui_theme.luminance(tk_root, "not-a-colour") == 1.0


def test_fonts_are_chosen_from_what_the_machine_has(tk_root):
    F = ui_theme.fonts(tk_root)
    for role in ("h1", "body", "small", "mono", "metric"):
        assert role in F


def test_a_button_does_its_job_and_can_be_disabled(app):
    hits = []
    b = Button(app.main, app.kit, "Press", lambda: hits.append(1))
    b.invoke()
    b.set_enabled(False)
    b.invoke()
    assert hits == [1]


# --------------------------------------------------------------- overview
def test_the_overview_says_who_has_control(app):
    app._render(app.api.status())
    assert app.hero_name.cget("text") == "laptop"       # the hub starts in control
    assert app.hero_pill.cget("text") == "You"
    assert "online" in app.m_online.caption.cget("text")


def test_the_header_says_whether_sharing_is_on(app):
    """A switch showing the state, not a button naming its opposite."""
    app._render(app.api.status())
    assert app.chip.cget("text") == "Waiting"
    assert app.sharing.get() is True
    assert app.sharing_label.cget("text") == "Sharing on"
    app.api.node.set_enabled(False)
    app._render(app.api.status())
    assert app.chip.cget("text") == "Paused"
    assert app.sharing.get() is False
    assert app.sharing_label.cget("text") == "Sharing off"


def test_flipping_the_switch_turns_sharing_off_and_on(app):
    app._render(app.api.status())
    app.sharing_switch._flip()
    assert app.api.node.enabled is False
    app.sharing_switch._flip()
    assert app.api.node.enabled is True


def test_the_preview_is_a_picture_not_an_editor(app):
    assert app.preview.readonly is True
    assert not app.preview.canvas.bind("<ButtonPress-1>")


# ---------------------------------------------------------------- devices
def test_every_other_machine_gets_a_card_with_its_displays(app):
    app.show_page("devices")
    app._render(app.api.status())
    text = all_text(app.dev_list)
    assert "aio" in text and "laptop" not in text, "this device has its own card"
    assert any("1920×1080" in t for t in text)
    assert any("offline" in t for t in text), "the AIO is not connected here"


def test_this_device_shows_what_another_needs_to_add_it(app):
    """Its name and its password, big - with the dashes, and a note that they
    do not matter. Reported: "password contains -, not sure that has to be
    entered or not"."""
    app.show_page("devices")
    app._render(app.api.status())
    assert app.me_name.cget("text") == "laptop"
    assert app.me_pill.cget("text") == "Hub"
    assert app.me_password.cget("text") == app.api.command("/api/password", {})["pin"]
    assert "dashes are ignored" in app.me_hint.tip.text, "one hover away"


def test_the_hub_can_remove_any_other_device(app):
    app._render(app.api.status())
    buttons = [w for w in _walk(app.dev_list)
               if isinstance(w, Button) and w.cget("text") == "Remove"]
    assert len(buttons) == 1, "the AIO's card - this device is not in the list"


def test_removing_a_device_asks_first_then_takes_it_off_the_page(app):
    app._render(app.api.status())
    asked = []
    app._confirm = lambda title, text: asked.append(text) or False
    app._remove_device("aio")
    assert asked and "aio" in app.api.node.core.layout.names(), "declined: kept"
    app._confirm = lambda title, text: True
    app._remove_device("aio")
    assert "aio" not in app.api.node.core.layout.names()
    assert "aio" not in all_text(app.dev_list)


def test_a_device_on_its_own_says_how_to_add_one(app):
    app.api.node.core.alone()
    app.show_page("overview")
    app._render(app.api.status())
    assert app.chip.cget("text") == "Ready"
    assert app.start_card.winfo_manager() == "pack", "the first thing on Overview"
    assert "No other devices yet" in all_text(app.dev_list)


def test_a_member_can_leave_but_not_change_the_password(app):
    app.api.node.core.set_hub(False)
    app.api.node.peer_name = "desk"
    app._render(app.api.status())
    assert app.btn_leave.winfo_manager() == "pack"
    assert app.btn_new_pw.enabled is False
    assert "Only desk" in app.new_pw_tip.text


def test_a_rejected_password_is_shown_with_the_way_to_fix_it(app):
    app.api.node.core.set_hub(False)
    app.api.node.peer_name = "desk"
    app.api.node.dial = {"phase": "failed", "reason": "wrong_password"}
    app._render(app.api.status())
    assert app.me_problem.winfo_manager() == "pack"
    assert app.me_problem_text.cget("text") == "desk rejected this device's password"
    assert "changed" in app.me_problem_info.tip.text
    assert app.me_problem_btn.winfo_manager() == "pack"
    assert app.chip.cget("text") == "Not connected"


def test_what_happens_is_shown_once_as_a_notice(app):
    app._render(app.api.status())                  # the first look: nothing old
    app.api._on_event("joined", {"name": "aio", "first": True})
    app._render(app.api.status())
    assert len(app._toasts) == 1
    assert "aio joined" in all_text(app.toasts)
    app._render(app.api.status())
    assert len(app._toasts) == 1, "once"


def test_cards_are_not_rebuilt_every_poll(app):
    app._render(app.api.status())
    first = app.dev_list.winfo_children()
    app._render(app.api.status())
    assert app.dev_list.winfo_children() == first, "a rebuild every poll flickers"


# ------------------------------------------------------------ arrangement
def _move_aio_left(app):
    a = app.arranger
    aio, lap = a.desk.get("aio"), a.desk.get("laptop")
    a.desk.move("aio", lap.x - aio.w, lap.y)       # to the laptop's LEFT
    a.dirty = True
    return {b["name"]: (b["x"], b["y"]) for b in a.boxes}


def test_a_drop_is_in_force_at_once_with_no_apply(app):
    """Reported: "changing arrangement in one device instantly isn't synced".
    A drop reaches the program - and so every device - straight away."""
    app._render(app.api.status())
    wanted = _move_aio_left(app)
    app._arranged(app.arranger.boxes)
    assert app._apply_id is not None, "applied after a short pause, not on Apply"
    app._apply_arrangement()
    sides = {(c.a, c.side) for c in app.api.node.core.layout.crossings()}
    assert sides == {("laptop", "left")} or sides == {("aio", "right")}, sides
    assert "Applied" in app.arr_saved.cget("text")
    app._render(app.api.status())                  # the echo
    assert {b["name"]: (b["x"], b["y"]) for b in app.arranger.boxes} == wanted
    assert app.arranger.dirty is False, "following the program again"


def test_undo_puts_back_the_arrangement_before(app):
    app._render(app.api.status())
    before = {b["name"]: (b["x"], b["y"]) for b in app.arranger.boxes}
    _move_aio_left(app)
    app._apply_arrangement()
    assert app.btn_undo.enabled
    app._undo_arrangement()
    app._render(app.api.status())
    assert {b["name"]: (b["x"], b["y"])
            for b in app.api.node.core.placement} == before
    assert not app.btn_undo.enabled


def test_a_refused_arrangement_snaps_back_and_says_why(app):
    app._render(app.api.status())
    before = app.api.node.core.placement
    a = app.arranger
    a.desk.move("aio", a.desk.get("laptop").x, a.desk.get("laptop").y)  # on top
    a.dirty = True
    app._apply_arrangement()
    assert app.api.node.core.placement == before
    assert app.arr_saved.cget("text")
    assert a.dirty is False


def test_problems_with_the_arrangement_are_spelled_out(app):
    app._render(app.api.status())
    m = app.arranger.desk.get("aio")
    app.arranger.desk.move("aio", m.x + 9000, m.y)
    app._arr_text()
    assert "aio does not touch anything" in app.arr_problems.cget("text")


# --------------------------------------------------------------- activity
def test_the_log_is_shown_and_coloured_by_what_happened(app):
    app._render(app.api.status())
    body = app.log.get("1.0", "end-1c")
    assert "line 39" in body
    assert app.log.tag_ranges("ok") and app.log.tag_ranges("bad"), \
        "a connection in green, a rejection in red"


def test_the_log_says_where_the_file_is(app):
    app._render(app.api.status())
    assert "link.log" in app.logpath.cget("text")


# --------------------------------------------------------------- settings
def test_settings_are_filled_from_status(app):
    app._render(app.api.status())
    assert app.node_name.get() == "laptop"
    assert app.port.get() == "8770"
    assert app.claim.get() == "motion"
    assert app.password.cget("show") == "•"


def test_a_field_being_edited_is_not_overwritten_by_a_poll(app):
    """Status arrives every 700ms and must not fight the keyboard."""
    app._render(app.api.status())
    app.node_name.delete(0, "end")
    app.node_name.insert(0, "half-typed-na")
    app.touched.add("node")
    app._render(app.api.status())
    assert app.node_name.get() == "half-typed-na"


def test_saving_a_setting_applies_it(app):
    app._render(app.api.status())
    app.claim.set("click")
    app.touched.add("claim")
    app._save()
    assert app.api.node.core.policy["claim"] == "click"
    assert app.touched == set()


# ----------------------------------------------------------------- safety
def test_a_firewall_block_is_shown_with_a_way_out(app):
    """The failure that otherwise looks exactly like a device switched off."""
    s = app.api.status()
    app._render(dict(s, firewall_blocked=[]))
    assert app.fw_box.winfo_manager() == ""
    app._render(dict(s, firewall_blocked=["nishrolink.exe (TCP, private networks)"]))
    assert app.fw_box.winfo_manager() == "pack"
    assert "Allow" in app.fw_btn.cget("text")
    app._render(dict(s, firewall_blocked=[]))
    assert app.fw_box.winfo_manager() == "", "and goes once the block is gone"


def test_release_is_always_in_reach(app):
    """The failsafe as a button, in the sidebar, on every page."""
    assert app.btn_release.cget("text") == "Release input"
    app.btn_release.invoke()
    assert app.api.node.core.suppress_mouse() is False


def test_the_window_stops_polling_when_it_closes(app):
    """An after() callback landing after destroy prints 'invalid command name'
    from inside Tcl - noise that reads like a crash."""
    app._alive = True
    app.close()
    assert app._alive is False
    assert app._poll_id is None


def test_a_disconnect_is_not_shown_as_good_news():
    """It contains the word "connected"."""
    assert ui_tk._tone("aio2 disconnected - 1 still connected") == "warn"
    assert ui_tk._tone("peer connected: aio1 (password verified both ways)") == "ok"
    assert ui_tk._tone("rejected x: wrong password") == "bad"


def test_a_device_card_draws_its_real_monitor_layout(app):
    s = app.api.status()
    lap = next(d for d in s["devices"] if d["name"] == "laptop")
    assert lap["rects"] == [[0, 0, 1366, 768]]


# ------------------------------------------------------------ Linux setup
def test_missing_permissions_are_shown_with_the_fix(app):
    s = app.api.status()
    app._render(dict(s, setup={"ok": False, "relogin": False, "fixable": True,
                               "problems": ["It cannot read this computer's "
                                            "keyboard and mouse."]}))
    assert app.setup_box.winfo_manager() == "pack"
    assert app.setup_text.cget("text") == "Keyboard and mouse access needed"
    assert "cannot read" in app.setup_info.tip.text
    assert app.setup_btn.winfo_manager() == "pack"
    assert app.chip.cget("text") == "Setup needed"
    app._render(dict(s, setup=None))
    assert app.setup_box.winfo_manager() == "", "and goes once it is done"


def test_after_setup_it_asks_for_a_new_login_not_the_button_again(app):
    s = app.api.status()
    app._render(dict(s, setup={"ok": False, "relogin": True, "fixable": False,
                               "problems": ["Setup is done - log out and back "
                                            "in once to finish it."]}))
    assert app.setup_text.cget("text").startswith("Almost ready")
    assert app.setup_btn.winfo_manager() == ""


# ---------------------------------------------------- start when I log in
@pytest.fixture
def login_store(monkeypatch):
    """Stands in for the Run key / autostart folder: tests must not change
    what really starts at login."""
    from link import autostart
    stored = {}

    def switch(on, cmd=None, where=None):
        if on:
            stored["cmd"] = list(cmd)
        else:
            stored.pop("cmd", None)

    def state(cmd=None, where=None):
        return {"available": True, "on": "cmd" in stored,
                "current": stored.get("cmd") == list(cmd or [])}
    monkeypatch.setattr(autostart, "switch", switch)
    monkeypatch.setattr(autostart, "state", state)
    return stored


def test_the_login_switch_applies_at_once(app, login_store):
    app.show_page("settings")
    assert app.autostart is not None
    app._render(app.api.status())
    assert app.autostart.get() is False
    assert app.autostart_note.cget("text") == ""

    app.autostart.set(True)
    app._set_autostart()
    assert "--background" in login_store["cmd"]
    app._render(app.api.status())
    assert app.autostart.get() is True
    assert app.autostart_note.cget("text") == "starts hidden"

    app.autostart.set(False)
    app._set_autostart()
    assert "cmd" not in login_store


def test_a_login_entry_for_another_copy_is_pointed_out(app, login_store):
    login_store["cmd"] = ["C:/old/NishroLink.exe", "--background"]
    app._render(app.api.status())
    assert app.autostart.get() is True
    assert "older copy" in app.autostart_note.cget("text")


def test_the_login_command_keeps_this_runs_config_file(app, login_store):
    app.autostart.set(True)
    app._set_autostart()
    cmd = login_store["cmd"]
    assert cmd[cmd.index("--config") + 1] == str(app.api.cfg_path)


# ---------------------------------------------------------------- editing
def test_this_device_is_renamed_where_its_name_is_shown(app):
    app.show_page("devices")
    app._render(app.api.status())
    app._rename_me()
    app.f_me_name.delete(0, "end")
    app.f_me_name.insert(0, "workshop")
    app._rename_me_save()
    assert app.api.node.core.node == "workshop"
    app._render(app.api.status())
    assert app.me_name.cget("text") == "workshop"
    assert app.me_name.winfo_manager() == "pack", "back from editing"


def test_a_bad_name_keeps_the_editor_open_and_says_why(app):
    app._render(app.api.status())
    app._rename_me()
    app.f_me_name.delete(0, "end")
    app.f_me_name.insert(0, "aio")
    app._rename_me_save()
    assert app._renaming
    assert any("already a device called aio" in t for t in all_text(app.toasts))
    app._rename_me_done()


def test_each_card_opens_its_details(app):
    app._render(app.api.status())
    d = app._details("aio")
    try:
        assert d.name == "aio"
    finally:
        d.close()


def test_a_device_with_limits_says_so_on_its_card(app):
    app.api.node.core.rights["aio"] = {"may_drive": True, "may_be_driven": False}
    app._render(app.api.status())
    assert "cannot be controlled" in all_text(app.dev_list)


def test_nearby_devices_are_listed_with_what_adding_means(app):
    app._nearby_search = lambda: {"devices": [
        {"name": "kitchen", "waiting": True, "alone": True, "group": "kitchen"},
        {"name": "aio", "waiting": False, "group": "laptop"}]}
    app.show_page("devices")
    app._render(app.api.status())
    import time
    end = time.monotonic() + 3
    while time.monotonic() < end and "kitchen" not in all_text(app.nearby_list):
        app.root.update()
        time.sleep(0.02)
    text = all_text(app.nearby_list)
    assert "kitchen" in text and "  On its own" in text
    assert "aio" not in text, "already in this group: not 'nearby'"
    buttons = [w for w in _walk(app.nearby_list)
               if isinstance(w, Button) and w.cget("text") == "Add"]
    assert len(buttons) == 1


def test_a_card_lights_up_under_the_pointer(app):
    app.show_page("devices")
    app._render(app.api.status())
    app.root.update()
    card = app.dev_list.winfo_children()[0]
    card.event_generate("<Enter>")
    app.root.update()
    assert card.cget("highlightbackground") == app.C["accent"]


def test_a_right_click_offers_everything_that_can_be_done(app):
    app._render(app.api.status())
    card = app.dev_list.winfo_children()[0]

    class E:
        x_root = y_root = 0
    app._card_menu(E(), "aio")
    m = app._menu.items
    try:
        items = {label: m.state(label) for label in m.labels()}
        assert items["Details…"] == "normal" and items["Rename…"] == "normal"
        assert items["Remove from the group…"] == "normal"
        assert items["Control rights…"] == "disabled", "aio is switched off"
        assert card
    finally:
        app._menu.close()


# ------------------------------------------------------------ the copy
# Reported: "placing long sentence as description isn't good. Should be
# optimal, professional and impressive." Labels are short; the reasons are
# one hover away.

def _labels(w, out=None):
    out = [] if out is None else out
    try:
        t = w.cget("text")
        if t and isinstance(w, tk.Label):
            out.append(str(t))
    except tk.TclError:
        pass
    for c in w.winfo_children():
        _labels(c, out)
    return out


@pytest.mark.parametrize("page", ["overview", "devices", "arrange", "settings"])
def test_no_page_reads_like_a_paragraph(app, page):
    app.show_page(page)
    app._render(app.api.status())
    long = [t for t in _labels(app.pages[page]) if len(t) > 70]
    assert long == [], long


def test_the_overview_says_its_status_in_a_few_words(app):
    app._render(app.api.status())
    values = {k: row[1].cget("text") for k, row in app.status_rows.items()}
    assert values == {"sharing": "On", "connection": "Waiting for devices",
                      "security": "Password set", "control": "This device"}
    assert "Encrypted with a fresh key" in app.status_rows["security"][2].tip.text


def test_a_tooltip_shows_on_hover_and_goes(app):
    app.show_page("devices")
    app._render(app.api.status())
    app.root.update()
    tip = app.me_hint.tip
    tip.show()
    assert tip._tip is not None and tip._tip.winfo_exists()
    tip.hide()
    assert tip._tip is None


def test_a_placeholder_is_shown_but_never_read(tk_root):
    from link import ui_theme
    from link.ui_kit import Kit, field, placeholder
    top = tk.Toplevel(tk_root)
    try:
        kit = Kit(ui_theme.palette(top), ui_theme.fonts(top))
        e = field(top, kit)
        e.pack()
        hint = placeholder(e, kit, "word-word-word-word")
        top.update()
        assert e.get() == "" and hint.winfo_manager() == "place"
        e.insert(0, "tiger")
        assert e.get() == "tiger" and hint.winfo_manager() == ""
        e.delete(0, "end")
        assert hint.winfo_manager() == "place"
    finally:
        top.destroy()


# ------------------------------------------- resizing and copies, end to end
def test_a_copy_placed_on_the_page_is_in_force_on_the_link(app):
    app._render(app.api.status())
    app.show_page("arrange")
    app.arranger.select("aio")
    assert app.arr_btns["copy"].enabled
    app._arr_do("copy")
    app._apply_arrangement()
    lay = app.api.node.core.layout
    assert [p.key for p in lay.copies()] == [("aio", 1)]
    assert "copy_of" in str(app.api.status()["placement"])


def test_a_resized_box_is_in_force_on_the_link(app):
    app._render(app.api.status())
    app.arranger.desk.set_size("aio", 960, 540)
    app._apply_arrangement(app.arranger.boxes)
    m = app.api.node.core.layout.get("aio")
    assert (m.ww, m.wh) == (960, 540) and (m.w, m.h) == (1920, 1080)


def test_the_right_click_menu_offers_what_applies(app):
    app._render(app.api.status())
    app.show_page("arrange")
    app.arranger.select("aio")

    class E:
        x_root = y_root = 20
    app._arr_menu(E())
    try:
        m = app._menu.items
        assert m.state("Add a copy of aio") == "normal"
        assert m.state("Remove this copy") == "disabled"
        assert m.state("Actual size") == "disabled"
    finally:
        app._menu.close()


# ------------------------------------------------- finding the pointer
def test_find_the_pointer_is_always_in_reach(app, monkeypatch):
    called = []
    monkeypatch.setattr(app.api.node.core, "find",
                        lambda a=None: called.append(1) or __import__(
                            "link.node", fromlist=["Actions"]).Actions())
    for page in ("overview", "settings"):
        app.show_page(page)
        app.btn_find.invoke()
    assert called == [1, 1]


def test_shake_to_find_is_a_switch_that_applies_at_once(app):
    app._render(app.api.status())
    assert app.find_shake.get() is True
    app.find_shake.set(False)
    app._set_find_shake()
    assert app.api.node.core.find_on_shake is False
    assert app.api.cfg["find_on_shake"] is False
    assert app.api.status()["find_on_shake"] is False
    app.find_shake.set(True)
    app._set_find_shake()
    assert app.api.node.core.find_on_shake is True
