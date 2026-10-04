"""The Linux tray icon: an AppIndicator (StatusNotifierItem) in the top bar.

Ubuntu shows these out of the box (its "AppIndicator" extension), as do KDE
Plasma and most other desktops; plain GNOME needs that extension. Through the
GTK bindings GNOME already ships - python3-gi, and the Ayatana AppIndicator
library (the .deb recommends both). Where there is no tray to show it in, the
tray says so in the log and exits; the dock's right-click actions (the .desktop
file's) still offer the same controls.

The icon files are SVGs beside the program (tray/nishro-link-<state>.svg),
passed by path: a tray draws them crisp at whatever size its panel uses.
Notifications go to the desktop's own notification service, over D-Bus.
"""
from __future__ import annotations

import pathlib

HOST = "org.kde.StatusNotifierWatcher"


def _icon_dir():
    here = pathlib.Path(__file__).resolve().parent
    for d in (here.parent / "tray",                          # the .deb, per-user
              here / "packaging" / "assets" / "tray"):       # from source
        if (d / "nishro-link-ok.svg").exists():
            return d
    return None


def _host_present(Gio, GLib) -> bool:
    """Is anything showing StatusNotifierItems on this desktop?"""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        r = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus", "NameHasOwner",
                          GLib.Variant("(s)", (HOST,)), GLib.VariantType("(b)"),
                          Gio.DBusCallFlags.NONE, 2000, None)
        return bool(r.unpack()[0])
    except Exception:
        return True       # cannot tell: try, rather than refuse


class LinuxTray:
    def __init__(self, AI, Gtk, GLib, Gio, icons, log=print):
        self.AI, self.Gtk, self.GLib, self.Gio = AI, Gtk, GLib, Gio
        self.icons, self.log = icons, log
        self.on_action = lambda action: None
        self.ind = AI.Indicator.new("nishro-link", self._icon("ok"),
                                    AI.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_status(AI.IndicatorStatus.ACTIVE)
        try:
            self.ind.set_title("Nishro Link")
        except Exception:
            pass
        self._set_menu([{"kind": "header", "label": "Nishro Link", "enabled": False}])

    def _icon(self, state) -> str:
        return str(self.icons / f"nishro-link-{state}.svg")

    def _build(self, items):
        Gtk = self.Gtk
        m = Gtk.Menu()
        for it in items:
            kind = it["kind"]
            if kind == "sep":
                m.append(Gtk.SeparatorMenuItem())
                continue
            if kind == "check":
                w = Gtk.CheckMenuItem(label=it["label"])
                w.set_active(bool(it.get("checked")))     # before connecting: no echo
            else:
                w = Gtk.MenuItem(label=it["label"])
            w.set_sensitive(bool(it.get("enabled", True)))
            if kind == "sub":
                w.set_submenu(self._build(it["items"]))
            elif it.get("action"):
                signal = "toggled" if kind == "check" else "activate"
                w.connect(signal, lambda _w, a=it["action"]: self.on_action(a))
            if it.get("default"):
                self._default = w
            m.append(w)
        return m

    def _set_menu(self, items):
        # Let go of the old menu's default item first: the indicator checks
        # it against the new menu, and complained (Gtk-CRITICAL) when it was
        # still the old one's.
        try:
            self.ind.set_secondary_activate_target(None)
        except Exception:
            pass
        self._default = None
        m = self._build(items)
        m.show_all()
        self.ind.set_menu(m)
        self._menu = m                                   # kept alive
        if self._default is not None:
            try:                                          # middle-click: open
                self.ind.set_secondary_activate_target(self._default)
            except Exception:
                pass

    def _apply(self, st, items):
        self.ind.set_icon_full(self._icon(st["icon"]), st["tooltip"])
        self._set_menu(items)
        return False                                     # run once

    # ---- what tray.Tray calls: safe from any thread
    def show(self, st, items) -> None:
        self.GLib.idle_add(self._apply, st, items)

    def notify(self, title, text) -> None:
        def send():
            try:
                bus = self.Gio.bus_get_sync(self.Gio.BusType.SESSION, None)
                bus.call_sync(
                    "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                    "org.freedesktop.Notifications", "Notify",
                    self.GLib.Variant("(susssasa{sv}i)", (
                        "Nishro Link", 0, self._icon("ok"), title, text, [], {}, 5000)),
                    None, self.Gio.DBusCallFlags.NONE, 2000, None)
            except Exception as e:
                self.log(f"tray: could not show a notification ({e})")
            return False
        self.GLib.idle_add(send)

    def quit(self) -> None:
        self.GLib.idle_add(self.Gtk.main_quit)

    def run(self) -> None:
        self.Gtk.main()


def make(log=print):
    """The tray, or None - having said why - where there can be none."""
    icons = _icon_dir()
    if icons is None:
        log("tray: its icon files are missing")
        return None
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AI
        except (ValueError, ImportError):
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AI
        from gi.repository import Gio, GLib, Gtk
    except Exception as e:
        log(f"tray: no tray support here ({e}). On Ubuntu or Debian: "
            f"sudo apt install gir1.2-ayatanaappindicator3-0.1")
        return None
    if not _host_present(Gio, GLib):
        log("tray: this desktop shows no tray icons (on GNOME, the AppIndicator "
            "extension adds them); the dock's right-click menu has the same controls")
        return None
    try:
        return LinuxTray(AI, Gtk, GLib, Gio, icons, log)
    except Exception as e:
        log(f"tray: could not start ({e!r})")
        return None
