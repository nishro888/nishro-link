"""The menu bar and the Help menu's windows.

Asked for as "like normal Windows software: home, menu, help, about, version".
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

import link                                                # noqa: E402
from link import ui_help, ui_menu, ui_tk                   # noqa: E402
from test_ui_tk import all_text, app, tk_root              # noqa: E402,F401


def entries(m):
    return [(e["kind"], e.get("label"), e.get("state"), e.get("accelerator"))
            for e in m.entries if e["kind"] != "separator"]


def invoke(app, menu, label):
    app.menubar.build(menu).invoke(label)
    app.root.update()


@pytest.fixture
def opened(app):
    """Open a menu for real; closed again afterwards."""
    def open_(title, keyboard=False):
        app.menubar.post(title, keyboard=keyboard)
        app.root.update()
        return app.menubar.open
    yield open_
    if app.menubar.open is not None:
        app.menubar.open.close()


# --------------------------------------------------------------- the bar
def test_the_window_has_a_menu_bar_like_any_program(app):
    assert list(app.menubar.titles) == ["File", "View", "Sharing", "Help"]
    for title, lb in app.menubar.titles.items():
        assert lb.winfo_manager() and int(lb.cget("underline")) == 0


def test_alt_and_a_letter_opens_each_menu(app):
    for key in ("f", "v", "s", "h", "F", "H"):
        assert app.root.bind(f"<Alt-Key-{key}>")
    assert app.root.bind("<F10>")


def test_the_usual_keys_are_bound(app):
    for seq in ("<Control-n>", "<Control-q>", "<F1>"):
        assert app.root.bind(seq), seq


def test_the_first_page_is_home(app):
    assert ui_tk.PAGES[0][:2] == ("overview", "Home")
    assert app.title.cget("text") == "Home"


def test_every_entry_says_its_shortcut(app):
    file = {e[1]: e[3] for e in entries(app.menubar.build("File"))}
    assert file["Add a device…"] == "Ctrl+N" and file["Quit"] == "Ctrl+Q"
    view = entries(app.menubar.build("View"))[:len(ui_tk.PAGES)]
    assert [e[1] for e in view] == [t for _, t, _ in ui_tk.PAGES]
    assert [e[3] for e in view] == [f"Ctrl+{i}" for i in range(1, 6)]


# -------------------------------------------------------------- the menus
def test_view_ticks_the_page_on_show_and_goes_to_another(app):
    app.show_page("activity")
    assert app._page_var.get() == "activity"
    invoke(app, "View", "Devices")
    assert app.page == "devices"


def test_sharing_can_be_switched_off_from_the_menu(app):
    assert app.sharing.get() is True
    invoke(app, "Sharing", "Sharing")
    assert app.api.status()["enabled"] is False
    invoke(app, "Sharing", "Sharing")
    assert app.api.status()["enabled"] is True


def test_the_sharing_menu_offers_what_this_device_can_do(app):
    app._render(app.api.status())
    got = {e[1]: e[2] for e in entries(app.menubar.build("Sharing")) if e[1]}
    assert got["New password…"] == "normal"          # the hub
    assert "Leave the group…" not in got
    assert got["Undo arrangement"] == "disabled"      # nothing to undo yet
    app._last = dict(app._last, role="member")
    got = {e[1]: e[2] for e in entries(app.menubar.build("Sharing")) if e[1]}
    assert got["New password…"] == "disabled" and "Leave the group…" in got


def test_arranging_from_the_menu_shows_the_arrangement(app):
    app.show_page("overview")
    invoke(app, "Sharing", "Arrange in a column")
    assert app.page == "arrange"


def test_help_links_go_to_the_project(app, monkeypatch):
    opened = []
    monkeypatch.setattr(app, "_browse", opened.append)
    for label in ("Documentation", "Report a problem", "Downloads"):
        invoke(app, "Help", label)
    assert opened == [ui_help.DOCS, ui_help.ISSUES, ui_help.RELEASES]
    assert all(u.startswith("https://github.com/nishro888/nishro-link")
               for u in opened)


def test_file_add_a_device_opens_it(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_add_device", lambda *a: calls.append(1))
    invoke(app, "File", "Add a device…")
    assert calls == [1]


# ------------------------------------------------------------------ About
def test_about_says_which_version_and_which_device(app):
    about = app._about()
    try:
        app.root.update()
        text = " ".join(all_text(about.top))
        assert f"Version {link.__version__}" in text
        assert link.__stage__.capitalize() in text
        s = app.api.status()
        assert s["node"] in text and s["device_id"] in text
        assert "MIT" in text and "EFF" in text
    finally:
        about.close()


def test_about_opens_once(app):
    a = app._about()
    try:
        assert app._about() is a
    finally:
        a.close()
    b = app._about()
    assert b is not a
    b.close()


def test_the_copied_details_are_safe_to_post(app):
    s = app.api.status()
    text = ui_help.copy_text(s)
    assert text.startswith(f"Nishro Link {link.__version__}")
    assert s["device_id"] in text and "Python" in text
    assert "k7qm" not in text                        # the password
    for addr in s.get("addresses") or []:
        assert addr not in text


def test_copy_details_puts_them_on_the_clipboard(app):
    got = []
    about = ui_help.About(app.root, app.kit, app.api.status(), copy=got.append)
    try:
        about.btn_copy.invoke()
        assert got and got[0] == ui_help.copy_text(app.api.status())
        assert about.btn_copy.cget("text") == "Copied"
    finally:
        about.close()


def test_the_group_line_names_devices_on_another_version():
    devs = [{"name": "laptop", "me": True, "version": link.__version__},
            {"name": "aio", "version": "0.9.1"}]
    assert ui_help.group_line({"role": "hub", "devices": devs}) == \
        "2 devices · aio 0.9.1"
    devs[1]["version"] = link.__version__
    assert ui_help.group_line({"role": "hub", "devices": devs}) == \
        f"2 devices · all on {link.__version__}"
    assert ui_help.group_line({"role": "alone", "devices": devs[:1]}) == \
        "On its own"


def test_the_system_is_named():
    name = ui_help.system_name()
    assert name and (not sys.platform == "win32" or name.startswith("Windows"))


def test_the_sidebar_shows_the_version_and_opens_about(app):
    assert app.side_version.cget("text") == f"Version {ui_help.version()}"
    about = app._about()
    about.close()


# ------------------------------------------------------- the other windows
@pytest.mark.parametrize("open_", ["_quick_start", "_shortcuts", "_about"])
def test_help_windows_are_short_and_close_on_escape(app, open_):
    sheet = getattr(app, open_)()
    app.root.update()
    long = [t for t in all_text(sheet.top) if len(t) > 70]
    assert long == [], long
    # A key goes to the window with the focus.
    sheet.top.focus_force()
    app.root.update()
    sheet.top.event_generate("<Escape>", when="now")
    app.root.update()
    assert not sheet.alive()


def test_quick_start_can_go_straight_to_adding(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_add_device", lambda *a: calls.append(1))
    qs = app._quick_start()
    buttons = [w for w in qs.box.winfo_children()[-1].winfo_children()
               if str(w.cget("text")) == "Add a device"]
    buttons[0].invoke()
    assert calls == [1] and not qs.alive()


def test_the_failsafe_is_in_the_shortcuts():
    assert ui_help.SHORTCUTS[0][0] == "Both Ctrl keys"


# ------------------------------------------------------ the open menus
# Tk's own menus showed a white border and white-embossed grey items on a dark
# window on Windows; these are drawn on the theme and must still act as menus.
def test_a_menu_opens_under_its_title(app, opened):
    dd = opened("Help")
    lb = app.menubar.titles["Help"]
    assert dd.alive() and app.menubar.open_title == "Help"
    assert abs(dd.top.winfo_rootx() - lb.winfo_rootx()) <= 2
    assert dd.top.winfo_rooty() >= lb.winfo_rooty() + lb.winfo_height() - 2
    assert [e["label"] for e, _ in dd.rows] == app.menubar.build("Help").labels()


def test_the_keys_move_through_a_menu_and_choose(app, opened):
    app.show_page("overview")
    dd = opened("View", keyboard=True)
    assert dd.active == 0                     # opened by key: the first is lit
    dd.move(1)
    dd.move(1)
    dd.choose()
    app.root.update()
    assert app.page == "arrange" and not dd.alive()


def test_up_from_the_top_wraps_to_the_bottom(app, opened):
    dd = opened("View", keyboard=True)
    dd.move(-1)
    assert dd.active == len(dd.rows) - 1


def test_left_and_right_go_to_the_next_menu(app, opened):
    opened("View", keyboard=True)
    app.menubar.open._step(1)
    app.root.update()
    assert app.menubar.open_title == "Sharing"
    app.menubar.open._step(-1)
    app.menubar.open._step(-1)
    app.menubar.open._step(-1)
    app.root.update()
    assert app.menubar.open_title == "Help"


def test_the_pointer_slides_to_the_next_menu(app, opened):
    first = opened("File")
    lb = app.menubar.titles["Help"]
    app.menubar._over(lb.winfo_rootx() + 3, lb.winfo_rooty() + 3, click=False)
    app.root.update()
    assert app.menubar.open_title == "Help" and not first.alive()


def test_a_click_elsewhere_only_closes_the_menu(app, opened):
    dd = opened("File")

    class E:
        x_root = app.root.winfo_rootx() + 400
        y_root = app.root.winfo_rooty() + 400
    assert dd._press(E()) == "break"
    assert not dd.alive() and app.menubar.open is None


def test_a_click_on_the_open_title_closes_it(app, opened):
    dd = opened("File")
    lb = app.menubar.titles["File"]

    class E:
        x_root = lb.winfo_rootx() + 3
        y_root = lb.winfo_rooty() + 3
    dd._press(E())
    app.root.update()
    assert app.menubar.open is None


def test_escape_closes_a_menu(app, opened):
    dd = opened("File")
    dd.top.focus_force()
    app.root.update()
    dd.top.event_generate("<Escape>", when="now")
    app.root.update()
    assert not dd.alive()


def test_a_greyed_item_cannot_be_chosen(app, opened):
    app._undo = []
    dd = opened("Sharing")
    assert "Undo arrangement" not in [e["label"] for e, _ in dd.rows]
    items = app.menubar.build("Sharing")
    assert items.state("Undo arrangement") == "disabled"
    called = []
    items.find("Undo arrangement")["command"] = lambda: called.append(1)
    items.invoke("Undo arrangement")
    assert called == []


def test_a_ticked_item_shows_its_tick(app, opened):
    app.show_page("devices")
    dd = opened("View")
    ticks = [dd.tick(i) for i in range(len(dd.rows))]
    assert ticks == ["", "•", "", "", "",       # the pages
                     "", "", "•"]               # System, Light, Dark


def test_the_right_click_menu_is_the_same_kind(app):
    app._render(app.api.status())

    class E:
        x_root = y_root = 10
    app._card_menu(E(), "aio")
    try:
        assert isinstance(app._menu, ui_menu.Dropdown) and app._menu.alive()
    finally:
        app._menu.close()
