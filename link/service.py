"""Running as a system service, and the window that attaches to one.

As a service the link engine starts with the computer - before anyone logs in -
and keeps running across logins, logouts and locks. The window is then only a
window: it finds the service through a small "handle" file and drives it over
the same local API the in-process window uses (status() and command()), so
closing it no longer stops anything.

The handle holds the API's port and its token. The token is what stands
between any local program and the controls - including the group's password -
so the file is readable by root and the `input` group only: the people who can
read every keyboard on the machine already.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    BASE = Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "NishroLink"
    HANDLE = BASE / "api.json"
    CONFIG = BASE / "private" / "config.json"     # SYSTEM and Administrators only
    STATE = BASE
else:
    HANDLE = Path("/run/nishro-link/api.json")
    CONFIG = Path("/var/lib/nishro-link/config.json")
    STATE = Path("/var/log")          # runtime.state_dir() adds nishro-link/


def publish(port: int, token: str, path: Path = None) -> Path:
    """Write the handle for windows to find."""
    path = Path(path or HANDLE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": int(port), "token": token,
                               "pid": os.getpid()}), encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(tmp, 0o640)
        try:
            import grp
            os.chown(tmp, 0, grp.getgrnam("input").gr_gid)
        except (KeyError, PermissionError, OSError):
            pass
    os.replace(tmp, path)
    return path


def withdraw(path: Path = None) -> None:
    try:
        Path(path or HANDLE).unlink()
    except OSError:
        pass


problem = None     # why the last find() found nothing, for the launcher to say


def find(path: Path = None, timeout: float = 1.5):
    """The running service as (port, token), None if there is none, or
    PermissionError if there is one this account may not reach."""
    global problem
    problem = None
    path = Path(path or HANDLE)
    try:
        h = json.loads(path.read_text(encoding="utf-8"))
        port, token = int(h["port"]), str(h["token"])
    except FileNotFoundError:
        return None
    except PermissionError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as e:
        problem = f"its handle {path} could not be read ({e})"
        return None
    try:
        RemoteAPI(port, token, timeout=timeout).status()
    except (OSError, ValueError) as e:
        # A handle left behind by a crash - or a service busy or restarting.
        problem = f"it did not answer on port {port} ({e})"
        return None
    return port, token


def published(path: Path = None) -> bool:
    """Has a service published its handle here - is one installed and, on
    Linux, running? (Its runtime directory goes when it stops.)"""
    return Path(path or HANDLE).exists()


class RemoteAPI:
    """The service's control API, seen through the same two calls the window
    makes of the in-process one."""

    def __init__(self, port: int, token: str, host: str = "127.0.0.1",
                 timeout: float = 5.0):
        self.base = f"http://{host}:{int(port)}"
        self.token = token
        self.timeout = timeout

    def _call(self, path: str, body=None, timeout=None):
        url = f"{self.base}{path}?t={self.token}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data, method="GET" if body is None else "POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            return json.loads(r.read() or b"{}")

    def status(self) -> dict:
        return self._call("/api/status")

    def command(self, path: str, body: dict):
        # Some commands wait on people or the network (a search, a password
        # prompt, adding a device): give them the time they take.
        try:
            return self._call(path, body or {}, timeout=max(self.timeout, 30))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read() or b"{}")
            except ValueError:
                return {"error": f"the service answered {e.code}"}
        except (OSError, ValueError) as e:
            return {"error": f"the service did not answer: {e}"}


# ------------------------------------------------ Quit, and Open after a Quit
UNIT = "nishro-link.service"
WIN_NAME = "NishroLink"                 # winsvc.SERVICE_NAME, without importing it
_NO_WINDOW = 0x08000000


def installed() -> bool:
    """Is Nishro Link installed here as a system service - by the Windows setup
    or the .deb - rather than run from source, which has none?"""
    if sys.platform == "win32":
        return _sc("query")[0] == 0             # 1060: no such service
    return any(Path(d, UNIT).exists() for d in
               ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system"))


def _sc(*args, timeout=30):
    """(return code, output) of sc.exe on this service."""
    import subprocess
    try:
        r = subprocess.run(["sc", args[0], WIN_NAME, *args[1:]], capture_output=True,
                           text=True, timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)
    return r.returncode, " ".join((r.stdout or "").split())


def _win_state() -> str:
    code, out = _sc("query")
    for word in ("RUNNING", "STOPPED", "START_PENDING", "STOP_PENDING"):
        if f" {word}" in out:
            return word
    return "" if code == 1060 else "UNKNOWN"


def control(action: str, timeout: float = 30.0) -> dict:
    """Stop or start the service as the person signed in: what Quit and then
    Open do. {"ok": True}, or {"ok": False, "error": ...}.

    Windows: the installer lets signed-in users start and stop this one
    service, so no prompt - and if that right is missing, Windows' own
    elevation prompt instead. Linux: polkit decides; the .deb's rule lets an
    active desktop user in the `input` group do it without a password, and
    where polkit is too old for rules (Ubuntu 22.04, Debian 11) the desktop
    asks for one."""
    import subprocess
    assert action in ("start", "stop")
    want = "RUNNING" if action == "start" else "STOPPED"
    if sys.platform != "win32":
        try:
            r = subprocess.run(["systemctl", action, UNIT], capture_output=True,
                               text=True, timeout=180)   # a password prompt waits
        except (OSError, subprocess.SubprocessError) as e:
            return {"ok": False, "error": str(e)}
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout).strip()
                    or f"systemctl {action} failed"}
        return {"ok": True}
    if _win_state() == want:
        return {"ok": True}
    code, out = _sc(action)
    if code == 5:                                # access denied: ask Windows
        ps = (f"Start-Process sc.exe -Verb RunAs -Wait -WindowStyle Hidden "
              f"-ArgumentList '{action}','{WIN_NAME}'")
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=300, creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as e:
            return {"ok": False, "error": str(e)}
    elif code not in (0, 1056, 1062):            # 1056 running, 1062 not started
        return {"ok": False, "error": out or f"sc {action} failed ({code})"}
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if _win_state() == want:
            return {"ok": True}
        time.sleep(0.3)
    return {"ok": False, "error": f"the service is {_win_state().lower() or 'missing'}"}


def _quit_marker() -> Path:
    """This person's note that they quit - watched by their tray and windows."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "NishroLink"
    else:
        base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/nishro-link-{os.getuid()}")
    return base / "nishro-link-quit"


def quit_asked_since(t: float) -> bool:
    """Has this person quit Nishro Link since `t` (a time.time())?"""
    try:
        return float(_quit_marker().read_text()) > t
    except (OSError, ValueError):
        return False


def quit_everything() -> dict:
    """Quit Nishro Link: the service stops - sharing too, here and at the
    sign-in screen - and this person's tray and windows close. Opening it
    starts it all again; so does the next start of the computer."""
    import time
    if installed():
        r = control("stop")
        if not r.get("ok"):
            return r
    try:
        m = _quit_marker()
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(repr(time.time()))
    except OSError as e:
        return {"ok": True, "warning": f"could not tell the tray: {e}"}
    return {"ok": True}
