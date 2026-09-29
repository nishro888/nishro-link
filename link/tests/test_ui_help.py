"""The Help page, and the menus drawn on the theme.

Asked for first as "like normal Windows software: home, menu, help, about,
version" - a menu bar - and then: "fix the messy look of File, View, Sharing,
Help and five tabs at the left". One navigation pane now, with Help at its
foot; the right-click menus keep the themed menu.
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


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def _text(w):
    try:
        return str(w.cget("text"))
    except tk.TclError:
        return ""


def _button(page, label):
    return next(w for w in _walk(page) if _text(w) == label
                and hasattr(w, "invoke"))


# ================================================================ Help
def test_help_is_a_page_with_about_and_the_version(app):
    app.show_page("help")
    app._render(app.api.status())
    app.root.update()
    text = " ".join(all_text(app.pages["help"]))
    assert f"Version {link.__version__}" in text
    assert link.__stage__.capitalize() in text
    s = app.api.status()
    assert s["node"] in text and s["device_id"] in text
    assert "MIT" in text and "EFF" in text and "Sun Valley" in text
    for card in ("About", "Get started", "Keyboard shortcuts", "Support"):
        assert card in text


def test_f1_opens_help(app):
    assert app.root.bind("<F1>")
    app.show_page("overview")
    app.root.focus_force()
    app.root.event_generate("<F1>", when="tail")
    app.root.update()
    assert app.page == "help"


def test_every_page_has_a_ctrl_number(app):
    for i, (name, _, _) in enumerate(ui_tk.PAGES, start=1):
        assert app.root.bind(f"<Control-Key-{i}>"), name
    assert len(ui_tk.PAGES) == 6


def test_copy_details_is_safe_to_post(app):
    s = app.api.status()
    text = ui_help.copy_text(s)
    assert text.startswith(f"Nishro Link {link.__version__}")
    assert s["device_id"] in text and "Python" in text
    assert "k7qm" not in text                        # the password
    for addr in s.get("addresses") or []:
        assert addr not in text


def test_copy_details_puts_them_on_the_clipboard(app):
    app._render(app.api.status())
    app.help_page.btn_copy.invoke()
    assert app.root.clipboard_get() == ui_help.copy_text(app.api.status())
    assert app.help_page.btn_copy.cget("text") == "Copied"


def test_support_links_go_to_the_project(app):
    opened = []
    app.help_page.on["browse"] = opened.append
    for label in ("Documentation", "Report a problem", "Downloads"):
        _button(app.pages["help"], label).invoke()
    assert opened == [ui_help.DOCS, ui_help.ISSUES, ui_help.RELEASES]


def test_get_started_can_go_straight_to_adding(app):
    calls = []
    app.help_page.on["add"] = lambda: calls.append(1)
    _button(app.pages["help"], "Add a device").invoke()
    assert calls == [1]


def test_the_help_page_reads_in_short_lines(app):
    long = [t for t in all_text(app.pages["help"]) if len(t) > 70]
    assert long == [], long


def test_the_failsafe_and_the_shake_are_in_the_shortcuts():
    keys = [k for k, _ in ui_help.SHORTCUTS]
    assert keys[0] == "Both Ctrl keys" and "Shake the mouse" in keys


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


# ================================================ menus on the theme
def fill_demo(var):
    def fill(m):
        m.add_command(label="First", accelerator="Ctrl+1")
        m.add_command(label="Second")
        m.add_separator()
        m.add_command(label="Greyed", state="disabled")
        m.add_radiobutton(label="Pick A", value="a", variable=var)
        m.add_radiobutton(label="Pick B", value="b", variable=var)
    return fill


@pytest.fixture
def menu(app):
    made = []

    def open_():
        var = tk.StringVar(master=app.root, value="b")
        dd = ui_menu.popup(app.root, app.kit, fill_demo(var), 200, 200)
        app.root.update()
        made.append(dd)
        return dd, var
    yield open_
    for dd in made:
        dd.close()


def test_a_menu_draws_every_choosable_item(menu):
    dd, _ = menu()
    assert [e["label"] for e, _ in dd.rows] == ["First", "Second", "Pick A", "Pick B"]
    assert dd.alive()


def test_the_keys_move_through_a_menu_and_choose(menu):
    dd, var = menu()
    dd.move(1)                                  # First
    dd.move(1)                                  # Second
    dd.move(1)                                  # Pick A: Greyed is skipped
    dd.choose()
    assert var.get() == "a" and not dd.alive()


def test_up_from_the_top_wraps_to_the_bottom(menu):
    dd, _ = menu()
    dd.move(-1)
    assert dd.active == len(dd.rows) - 1


def test_a_ticked_item_shows_its_tick(menu):
    dd, _ = menu()
    assert [dd.tick(i) for i in range(len(dd.rows))] == ["", "", "", "•"]


def test_a_greyed_item_cannot_be_chosen():
    items = ui_menu.Items()
    called = []
    items.add_command(label="Greyed", state="disabled",
                      command=lambda: called.append(1))
    items.invoke("Greyed")
    assert called == []


def test_a_click_elsewhere_only_closes_the_menu(app, menu):
    dd, _ = menu()

    class E:
        x_root = app.root.winfo_rootx() + 900
        y_root = app.root.winfo_rooty() + 600
    assert dd._press(E()) == "break"
    assert not dd.alive()


def test_escape_closes_a_menu(menu):
    dd, _ = menu()
    assert dd._key(type("E", (), {"keysym": "Escape"})) == "break"
    assert not dd.alive()


def test_a_menu_does_not_take_the_focus(app, menu):
    """It used to, and the main window's title bar flickered inactive and back
    each time one opened. Its keys are borrowed from what has the focus."""
    app.root.focus_force()
    app.root.update()
    before = app.root.focus_get()
    dd, _ = menu()
    assert app.root.focus_get() is before
    if before is not None:
        assert ui_menu.KEY_TAG in before.bindtags()
    dd.close()
    if before is not None:
        assert ui_menu.KEY_TAG not in before.bindtags()


def test_the_right_click_menu_is_the_same_kind(app):
    app._render(app.api.status())

    class E:
        x_root = y_root = 10
    app._card_menu(E(), "aio")
    try:
        assert isinstance(app._menu, ui_menu.Dropdown) and app._menu.alive()
    finally:
        app._menu.close()
