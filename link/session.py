"""The desktop session in front of the screen, for a service that runs outside it.

As a system service Nishro Link starts at boot, as root, before anyone logs in:
that is what makes the login screen work. Input needs no session - the keyboard
and mouse are read, and the pointer moved, at the device level (evdev, uinput),
which reaches the login screen, the lock screen and any session alike. Two
things do need the session: the monitor layout (xrandr) and the clipboard.
Those are run inside it, as its user, with its display.

Which session: the one active on seat0, as logind says. A login screen is a
"greeter" session, not a person's; then there is none, and callers fall back
(the kernel's mode list for the screen size; no clipboard until someone logs
in).
"""
from __future__ import annotations

import glob
import os
import subprocess
import time

_CACHE = {"at": 0.0, "value": None}
TTL = 3.0                       # sessions change on login and logout, not often


def _loginctl(*args) -> str:
    r = subprocess.run(["loginctl", *args], capture_output=True, text=True,
                       timeout=3)
    return r.stdout if r.returncode == 0 else ""


def parse_props(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def describe(props: dict, uid_home=None, runtime="/run/user", xsockets="/tmp/.X11-unix"):
    """A session's props (from loginctl) as {"user","uid","type","env"}, or None
    when it is not a person's graphical session."""
    if props.get("Class") != "user" or props.get("Type") not in ("x11", "wayland"):
        return None
    try:
        uid = int(props.get("User") or -1)
    except ValueError:
        return None
    user = props.get("Name")
    if uid < 1000 or not user:
        return None
    rt = f"{runtime}/{uid}"
    env = {"XDG_RUNTIME_DIR": rt}
    if os.path.exists(f"{rt}/bus"):
        # The session's D-Bus: GNOME's settings (gsettings) are reached over it.
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={rt}/bus"
    if props["Type"] == "wayland" and os.path.exists(f"{rt}/wayland-0"):
        env["WAYLAND_DISPLAY"] = "wayland-0"
    display = props.get("Display") or ""
    if not display and os.path.exists(f"{xsockets}/X0"):
        display = ":0"                        # GNOME's XWayland
    if display:
        env["DISPLAY"] = display
        home = uid_home or os.path.expanduser(f"~{user}")
        for pattern in (f"{rt}/.mutter-Xwaylandauth.*", f"{rt}/gdm/Xauthority",
                        f"{home}/.Xauthority"):
            found = sorted(glob.glob(pattern))
            if found:
                env["XAUTHORITY"] = found[0]
                break
    return {"user": user, "uid": uid, "type": props["Type"], "env": env}


def active():
    """The person's graphical session on seat0, or None. Cached for a moment."""
    now = time.monotonic()
    if now - _CACHE["at"] < TTL:
        return _CACHE["value"]
    value = None
    try:
        sid = _loginctl("show-seat", "seat0", "--property=ActiveSession",
                        "--value").strip()
        if sid:
            props = parse_props(_loginctl(
                "show-session", sid, "--property=Name", "--property=User",
                "--property=Type", "--property=Class", "--property=Display"))
            value = describe(props)
    except (OSError, subprocess.SubprocessError):
        value = None
    _CACHE.update(at=now, value=value)
    return value


def running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def command(cmd, s=None) -> list:
    """`cmd` wrapped to run inside the session, as its user."""
    s = s or active()
    if s is None:
        raise LookupError("nobody is logged in")
    return ["runuser", "-u", s["user"], "--", "env",
            *[f"{k}={v}" for k, v in s["env"].items()], *cmd]


def run(cmd, **kw):
    """subprocess.run, inside the session when running as the service; as is
    otherwise."""
    if running_as_root():
        cmd = command(cmd)
    return subprocess.run(cmd, **kw)
