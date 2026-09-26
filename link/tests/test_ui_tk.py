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
def test_five_pages_in_the_sidebar(app):
    assert list(app.nav) == ["overview", "devices", "arrange", "activity", "settings"]
    assert app.page == "overview"


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
    for lb in (app.explain, app.hero_sub, app.arr_info):
        wl = int(lb.cget("wraplength"))
        assert 0 < wl <= w, f"{wl} does not fit {w}"


# ------------------------------------------------------------------ the look
def test_one_theme_everywhere(app):
    C = ui_theme.palette()
    assert app.root.cget("bg") == C["bg"]
    assert app.arranger.C["surface"] == C["surface"]
    assert app.log.cget("background") == C["surface"]


def test_the_theme_is_dark_and_readable(tk_root):
    C = ui_theme.palette()
    for key in ("bg", "panel", "card", "ink", "dim", "accent", "ok", "warn", "bad",
                "mine", "theirs", "cross", "offline", "grid"):
        assert key in C, key
    assert ui_theme.luminance(tk_root, C["bg"]) < 0.1
    assert ui_theme.luminance(tk_root, C["ink"]) > 0.8, "text must stand out"
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
    assert app.hero_pill.cget("text") == "YOU"
    assert "online" in app.m_online.caption.cget("text")


def test_the_header_says_whether_linking_is_on(app):
    app._render(app.api.status())
    assert app.chip.cget("text") == "WAITING"
    assert app.btn_toggle.cget("text") == "Stop linking"
    app.api.node.set_enabled(False)
    app._render(app.api.status())
    assert app.chip.cget("text") == "LINKING OFF"
    assert app.btn_toggle.cget("text") == "Start linking"


def test_the_preview_is_a_picture_not_an_editor(app):
    assert app.preview.readonly is True
    assert not app.preview.canvas.bind("<ButtonPress-1>")


# ---------------------------------------------------------------- devices
def test_every_machine_gets_a_card_with_its_displays(app):
    app.show_page("devices")
    app._render(app.api.status())
    text = all_text(app.dev_list)
    assert "laptop" in text and "aio" in text
    assert "THIS DEVICE" in text and "HUB" in text
    assert any("1920×1080" in t for t in text)
    assert any("offline" in t for t in text), "the AIO is not connected here"


def test_only_an_offline_device_can_be_forgotten_from_its_card(app):
    app._render(app.api.status())
    buttons = [w for w in _walk(app.dev_list)
               if isinstance(w, Button) and w.cget("text") == "Forget"]
    assert len(buttons) == 1, "the AIO's card only - never this device's"


def test_forgetting_a_device_takes_it_off_the_page(app):
    app._render(app.api.status())
    app._forget_device("aio")
    assert "aio" not in app.api.node.core.layout.names()
    assert "aio" not in all_text(app.dev_list)


def test_cards_are_not_rebuilt_every_poll(app):
    app._render(app.api.status())
    first = app.dev_list.winfo_children()
    app._render(app.api.status())
    assert app.dev_list.winfo_children() == first, "a rebuild every poll flickers"


# ------------------------------------------------------------ arrangement
def test_a_moved_screen_stays_put_until_apply_and_then_applies(app):
    """The whole round trip, through the window's own refresh: drag, a poll
    lands, Apply - and what was dragged is what is in force."""
    app._render(app.api.status())
    a = app.arranger
    aio, lap = a.desk.get("aio"), a.desk.get("laptop")
    a.desk.move("aio", lap.x - aio.w, lap.y)       # to the laptop's LEFT
    a.dirty = True
    wanted = {b["name"]: (b["x"], b["y"]) for b in a.boxes}
    assert wanted["aio"] != (aio.x, aio.y), "the move must really have happened"

    app._render(app.api.status())                  # the 700ms poll
    assert {b["name"]: (b["x"], b["y"]) for b in a.boxes} == wanted

    app._apply_arrangement()
    assert a.dirty is False
    sides = {(c.a, c.side) for c in app.api.node.core.layout.crossings()}
    assert sides == {("laptop", "left")} or sides == {("aio", "right")}, sides


def test_revert_goes_back_to_what_is_in_use(app):
    app._render(app.api.status())
    a = app.arranger
    before = {b["name"]: (b["x"], b["y"]) for b in a.boxes}
    m = a.desk.get("aio")
    a.desk.move("aio", m.x + 5000, m.y)
    a.dirty = True
    assert {b["name"]: (b["x"], b["y"]) for b in a.boxes} != before
    app._revert_arrangement()
    app._render(app.api.status())
    assert {b["name"]: (b["x"], b["y"]) for b in a.boxes} == before


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
