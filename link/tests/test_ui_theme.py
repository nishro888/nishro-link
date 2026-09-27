"""Light and dark, and controls that are real controls.

Asked for as "dark/night mode, proper buttons, proper software UI for Windows,
not just looking like a webpage". The buttons were coloured labels: no focus,
no keyboard, no pressed state. They are ttk widgets in Windows 11's look now.
"""
import json
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" and not (os.environ.get("DISPLAY")
                                     or os.environ.get("WAYLAND_DISPLAY")),
    reason="no display for Tk")

import tkinter as tk                                       # noqa: E402
from tkinter import ttk                                    # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))

from link import ui_theme                                  # noqa: E402
from link.ui_kit import Button, Segmented, Toggle, field   # noqa: E402
from test_ui_help import entries                           # noqa: E402
from test_ui_tk import all_text, app, tk_root              # noqa: E402,F401


def saved():
    return json.loads(open(ui_theme.PREF_PATH, encoding="utf-8").read())["mode"]


# ------------------------------------------------------------ the modes
def test_it_starts_in_the_mode_chosen_last(app):
    assert app.theme_pref == "dark" and ui_theme.current() == "dark"
    assert app.root.cget("bg") == ui_theme.DARK["bg"]
    assert ttk.Style(app.root).theme_use() == "sun-valley-dark"


def test_switching_redraws_everything_in_the_other_theme(app):
    app.show_page("devices")
    app.set_theme("light")
    app.root.update()
    try:
        assert ui_theme.current() == "light" and saved() == "light"
        assert ttk.Style(app.root).theme_use() == "sun-valley-light"
        assert app.C["bg"] == ui_theme.LIGHT["bg"]
        assert app.root.cget("bg") == ui_theme.LIGHT["bg"]
        assert app.sidebar.cget("bg") == ui_theme.LIGHT["sidebar"]
        assert app.page == "devices", "the same page, redrawn"
        assert app.arranger.C["surface"] == ui_theme.LIGHT["surface"]
    finally:
        app.set_theme("dark")
    assert app.root.cget("bg") == ui_theme.DARK["bg"]


def test_the_window_works_after_a_switch(app):
    app.set_theme("light")
    try:
        app._render(app.api.status())
        for name in app.nav:
            app.show_page(name)
            app.root.update()
        assert "laptop" in " ".join(all_text(app.pages["devices"]))
        app.sharing_switch._flip()
        assert app.api.status()["enabled"] is False
        app.sharing_switch._flip()
    finally:
        app.set_theme("dark")


def test_a_switch_closes_open_dialogs_first(app):
    about = app._about()
    details = app._details("aio")
    app.set_theme("light")
    try:
        assert not about.alive()
        assert not details._alive
        assert app._dialogs == [] and app._sheets == {}
    finally:
        app.set_theme("dark")


def test_system_follows_the_computer(app, monkeypatch):
    monkeypatch.setattr(ui_theme, "system_mode", lambda: "light")
    app.set_theme("system")
    try:
        assert saved() == "system" and ui_theme.current() == "light"
    finally:
        app.set_theme("dark")


def test_choosing_what_is_already_shown_redraws_nothing(app, monkeypatch):
    monkeypatch.setattr(ui_theme, "system_mode", lambda: "dark")
    before = app.sidebar
    app.set_theme("system")
    assert app.sidebar is before and saved() == "system"


def test_an_unknown_or_unreadable_choice_is_system(tmp_path, monkeypatch):
    p = tmp_path / "ui.json"
    monkeypatch.setattr(ui_theme, "PREF_PATH", str(p))
    assert ui_theme.load_pref() == "system"            # none yet
    p.write_text("{not json", encoding="utf-8")
    assert ui_theme.load_pref() == "system"
    p.write_text('{"mode": "purple"}', encoding="utf-8")
    assert ui_theme.load_pref() == "system"
    ui_theme.save_pref("light")
    assert ui_theme.load_pref() == "light"


# ------------------------------------------------------- where to choose
def test_the_view_menu_offers_the_themes(app):
    got = [e[1] for e in entries(app.menubar.build("View"))]
    assert got[-3:] == ["System theme", "Light theme", "Dark theme"]
    app.menubar.build("View").invoke("Light theme")
    try:
        assert ui_theme.current() == "light" and saved() == "light"
    finally:
        app.set_theme("dark")


def test_settings_has_the_theme_under_appearance(app):
    text = all_text(app.pages["settings"])
    assert "Appearance" in text
    for word in ("System", "Light", "Dark"):
        assert word in text


# ------------------------------------------------------ real controls
def test_the_controls_are_real_widgets(app):
    frame = tk.Frame(app.main, bg=app.C["bg"])
    b = Button(frame, app.kit, "Go", kind="primary")
    t = Toggle(frame, app.kit, tk.BooleanVar(master=frame))
    e = field(frame, app.kit)
    s = Segmented(frame, app.kit, (("a", "A"), ("b", "B")),
                  tk.StringVar(master=frame, value="a"))
    assert isinstance(b, ttk.Button) and b.cget("style") == "Accent.TButton"
    assert isinstance(t, ttk.Checkbutton) and "Switch" in t.cget("style")
    assert isinstance(e, ttk.Entry)
    assert all(isinstance(r, ttk.Radiobutton) for r in s._items.values())
    b.set_enabled(False)
    assert "disabled" in b.state()
    frame.destroy()


def test_a_control_on_a_card_is_drawn_for_the_card(app):
    card = tk.Frame(app.main, bg=app.C["card"])
    b = Button(card, app.kit, "Go")
    style = str(b.cget("style"))
    assert style.endswith(".TButton") and style != "TButton"
    assert ttk.Style(app.root).lookup(style, "background") == app.C["card"]
    card.destroy()


def test_the_title_bar_follows_the_theme(app, monkeypatch):
    calls = []
    monkeypatch.setattr(ui_theme, "title_bar", lambda w: calls.append(
        ui_theme.current()))
    app.set_theme("light")
    app.set_theme("dark")
    assert calls == ["light", "dark"]


def test_the_theme_files_ship_with_their_licence():
    for name in ("sv.tcl", "light.tcl", "dark.tcl", "sprites_light.tcl",
                 "sprites_dark.tcl", "spritesheet_light.png",
                 "spritesheet_dark.png", "LICENSE"):
        assert (ui_theme.THEME_DIR / name).is_file(), name
    assert "MIT License" in (ui_theme.THEME_DIR / "LICENSE").read_text()


@pytest.mark.parametrize("scheme, gtk, env, want", [
    ("'prefer-dark'", "'Yaru'", "", "dark"),
    ("'prefer-light'", "'Yaru-dark'", "", "light"),
    ("'default'", "'Yaru'", "", "light"),
    # The AIO: no colour-scheme key at all, dark only by the theme's name.
    (None, "'Yaru-sage-dark'", "", "dark"),
    (None, "'Adwaita'", "", "light"),
    (None, None, "Adwaita:dark", "dark"),
    (None, None, "", "light"),
])
def test_linux_desktops_are_read_as_they_say(monkeypatch, scheme, gtk, env, want):
    import subprocess

    class R:
        def __init__(self, out):
            self.stdout, self.returncode = (out or "") + "\n", 0 if out else 1

    def fake_run(args, **kw):
        if args[0] != "gsettings":
            raise OSError
        return R({"color-scheme": scheme, "gtk-theme": gtk}[args[-1]])
    monkeypatch.setattr(ui_theme.sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("GTK_THEME", env)
    assert ui_theme.system_mode() == want
