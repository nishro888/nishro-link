"""The tray icon: Nishro Link's everyday controls without opening the window.

A small process per person (`nishro-link --tray`), started at login, that
shows Nishro Link's state as an icon - in the notification area on Windows, in
the top bar on Linux - and offers what people reach for most: sharing on and
off, find the pointer, release input, the devices, and the window. Like the
window it is only a client of the background service, over the same local API.

What the tray shows is decided HERE, from /api/status, as plain data - state(),
menu(), changes() - so it is the same on both systems and testable anywhere.
tray_win.py and tray_linux.py only draw it and report clicks.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

POLL_S = 2.0


# ------------------------------------------------------------------ what to show
def state(s) -> dict:
    """What the tray shows for one /api/status - or for none (not running)."""
    if not s:
        return {"icon": "alert", "header": "Nishro Link isn't running",
                "tooltip": "Nishro Link isn't running", "sharing": None,
                "devices": [], "notify": False, "reachable": False}
    devs = s.get("devices") or []
    online = sum(1 for d in devs if d.get("online"))
    role = s.get("role")
    if s.get("setup"):
        icon, header = "alert", "Setup needed - open Nishro Link"
    elif not s.get("enabled"):
        icon, header = "paused", "Sharing is off"
    elif s.get("network_public") and not s.get("connected"):
        icon, header = "alert", "Windows blocks other devices - open Nishro Link"
    elif role == "alone":
        icon, header = "ok", "Ready - add a device to start"
    elif s.get("connected"):
        icon, header = "ok", f"Connected · {online} of {len(devs)} online"
    elif role == "hub":
        icon, header = "alert", "Waiting for the other devices"
    else:
        icon, header = "alert", f"Not connected to {s.get('group') or 'the hub'}"
    devices = [{"name": d.get("name", "?"), "online": bool(d.get("online")),
                "me": bool(d.get("me")), "hub": bool(d.get("hub"))} for d in devs]
    return {"icon": icon, "header": header, "tooltip": f"Nishro Link - {header}",
            "sharing": bool(s.get("enabled")), "devices": devices,
            "notify": bool(s.get("notify", True)), "reachable": True}


def device_label(d: dict) -> str:
    name = d["name"] + ("  (hub)" if d["hub"] else "")
    if d["me"]:
        return f"{name}  ·  this device"
    # No round trip here: it changes every few seconds, and a menu that is
    # rebuilt that often closes under the person reading it (on Linux).
    return f"● {name}  ·  online" if d["online"] else f"○ {name}  ·  offline"


def menu(st: dict) -> list:
    """The menu, as data both systems' trays draw.

    Each item: {"kind": header|check|item|sep|sub, "label", "action",
    "enabled", "checked", "default", "items" (for sub)}."""
    live = st["reachable"]
    devices = [{"kind": "item", "label": device_label(d), "action": None,
                "enabled": False} for d in st["devices"]]
    if devices:
        devices.append({"kind": "sep"})
    devices.append({"kind": "item", "label": "Add a device…", "action": "add",
                    "enabled": live})
    return [
        {"kind": "header", "label": st["header"], "action": None, "enabled": False},
        {"kind": "sep"},
        {"kind": "check", "label": "Sharing", "action": "sharing",
         "checked": bool(st["sharing"]), "enabled": live},
        {"kind": "item", "label": "Find the pointer", "action": "find", "enabled": live},
        {"kind": "item", "label": "Release input", "action": "release", "enabled": live},
        {"kind": "sep"},
        {"kind": "sub", "label": "Devices", "items": devices, "enabled": True},
        {"kind": "sep"},
        {"kind": "item", "label": "Open Nishro Link", "action": "open", "enabled": True,
         "default": True},
        {"kind": "item", "label": "Hide this icon", "action": "hide", "enabled": True},
        {"kind": "item", "label": "Quit Nishro Link", "action": "quit", "enabled": True},
    ]


def changes(before: dict, after: dict) -> list:
    """(title, text) for each device that connected or dropped out since the
    last look. Nothing on the first look, or across the service coming and
    going - only the devices' own comings and goings."""
    if not (before and after and before["reachable"] and after["reachable"]):
        return []
    was = {d["name"]: d["online"] for d in before["devices"] if not d["me"]}
    out = []
    for d in after["devices"]:
        if d["me"] or d["name"] not in was or was[d["name"]] == d["online"]:
            continue
        if d["online"]:
            out.append(("Device connected", f"{d['name']} is connected."))
        else:
            out.append(("Device disconnected", f"{d['name']} is no longer connected."))
    return out


# --------------------------------------------------------------------- the tray
class Tray:
    """Keeps a tray `ui` in step with the service, and does what it is asked.

    `ui` draws: show(state, menu), notify(title, text), quit() - all safe from
    any thread - and calls back on_action(name) for a click."""

    def __init__(self, find_api, ui, open_command):
        self.find_api = find_api          # () -> an API, or None: found afresh
        self.api = None                   # after a restart of the service
        self.ui = ui
        self.open_command = list(open_command)
        self.st = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.born = time.time()          # a Quit after this closes this tray

    def poll_once(self) -> None:
        from . import service
        if service.quit_asked_since(self.born):     # Quit, from a window
            self._stop.set()
            self.ui.quit()
            return
        with self._lock:
            s = None
            for _ in range(2):            # the service may have restarted
                if self.api is None:
                    self.api = self.find_api()
                if self.api is None:
                    break
                try:
                    s = self.api.status()
                    break
                except Exception:
                    self.api = None
            st = state(s)
            if self.st is not None and st["notify"]:
                for title, text in changes(self.st, st):
                    self.ui.notify(title, text)
            if st != self.st:
                self.st = st
                self.ui.show(st, menu(st))

    def loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass                       # a tray must never die of a hiccup
            self._stop.wait(POLL_S)

    def act(self, action) -> None:
        try:
            api = self.api
            if action in ("sharing", "find", "release") and api is None:
                return
            if action == "sharing":
                on = not (self.st or {}).get("sharing")
                api.command("/api/enable" if on else "/api/disable", {})
            elif action == "find":
                api.command("/api/find", {})
            elif action == "release":
                api.command("/api/release", {})
            elif action == "open":
                spawn(self.open_command)
            elif action == "add":
                spawn(self.open_command + ["--add"])
            elif action == "hide":
                self._stop.set()
                self.ui.quit()
                return
            elif action == "quit":
                threading.Thread(target=self._quit, daemon=True).start()
                return
        except Exception as e:
            self.ui.notify("Nishro Link", f"That didn't work: {e}")
        threading.Thread(target=self.poll_once, daemon=True).start()

    def _quit(self) -> None:
        """Quit Nishro Link: the service stops, then this tray goes - and this
        person's windows, which see the same note (service.quit_everything)."""
        from . import service
        r = service.quit_everything()
        if not r.get("ok"):
            self.ui.notify("Nishro Link", f"Could not quit: {r.get('error')}")
            return
        self._stop.set()
        self.ui.quit()

    def stop(self) -> None:
        self._stop.set()


# ------------------------------------------------------------------ helpers
def spawn(cmd) -> None:
    """Start a program and let it go: it outlives the tray - and the tray, the
    window that started it.

    As its own instance. A one-file Windows build (PyInstaller) unpacks itself
    to a temporary folder, and a copy it starts reuses that folder instead of
    unpacking its own - a folder deleted when the first copy exits. Seen when
    the build was one file: closing the window gutted the tray it had started
    (1,002 files down to 19, Tk's among them), so the tray's Open Nishro Link
    could no longer open one. The build is a folder now; this stays, as it
    costs nothing: PYINSTALLER_RESET_ENVIRONMENT makes such a copy unpack for
    itself, and anywhere else it means nothing."""
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL, "close_fds": True,
          "env": dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED, NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(list(cmd), **kw)


def start_in_background(open_command) -> None:
    """Make sure this person's tray is running - the window calls this, so a
    tray hidden or never started (a fresh install) comes back. A second tray
    finds the first and exits at once."""
    try:
        spawn(list(open_command) + ["--tray"])
    except OSError:
        pass


class _Only:
    """One tray per person, per session."""

    def __init__(self):
        self._h = None

    def acquire(self) -> bool:
        if sys.platform == "win32":
            import ctypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateMutexW.restype = ctypes.c_void_p
            self._h = k.CreateMutexW(None, False, "Local\\NishroLinkTray")
            return ctypes.get_last_error() != 183          # ALREADY_EXISTS
        import fcntl
        base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/nishro-link-{os.getuid()}"
        os.makedirs(base, exist_ok=True)
        self._h = open(os.path.join(base, "nishro-link-tray.lock"), "w")
        try:
            fcntl.flock(self._h, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def run(log=print) -> int:
    """`nishro-link --tray`: show the tray icon until logout or 'Hide'."""
    from . import autostart, service
    only = _Only()
    if not only.acquire():
        return 0                            # already showing

    def find_api():
        try:
            found = service.find()
        except PermissionError:
            return None
        return service.RemoteAPI(*found, timeout=5.0) if found else None

    if sys.platform == "win32":
        from . import tray_win
        ui = tray_win.make(log)
    else:
        from . import tray_linux
        ui = tray_linux.make(log)
    if ui is None:
        return 0                            # no tray on this desktop: said why
    tray = Tray(find_api, ui, autostart.launcher())
    ui.on_action = tray.act
    tray.poll_once()
    threading.Thread(target=tray.loop, daemon=True).start()
    try:
        ui.run()
    finally:
        tray.stop()
    time.sleep(0.1)
    return 0
