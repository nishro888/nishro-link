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


def find(path: Path = None, timeout: float = 1.5):
    """The running service as (port, token), None if there is none, or
    PermissionError if there is one this account may not reach."""
    path = Path(path or HANDLE)
    try:
        h = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except PermissionError:
        raise
    except (OSError, ValueError):
        return None
    api = RemoteAPI(int(h["port"]), str(h["token"]), timeout=timeout)
    try:
        api.status()
    except (OSError, ValueError):
        return None                       # a handle left behind by a crash
    return int(h["port"]), str(h["token"])


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
