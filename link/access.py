"""Can this user use the keyboard and mouse? On Linux the answer needs setup.

Two separate permissions, which fail in different ways:
  reading  /dev/input/event*   - the keyboard and mouse themselves. Granted by
                                 the `input` group.
  writing  /dev/uinput         - the virtual device the pointer and keys are
                                 played through. Granted by a udev rule giving
                                 it to the `input` group, and the uinput module.

Both need root to set up, and group membership only applies to sessions that
start after it - so "I added myself to the group" still fails until the next
login. This module tells those cases apart, in words a person can act on, so
the program can say "log out and back in once" instead of just failing.

It used to be worse: without permission the program logged "cannot start" and
exited - with no window, so someone who had just double-clicked an installed
package saw nothing happen at all. Now it starts with linking paused and offers
to fix it.

The fix itself runs a small root helper through pkexec - the desktop's own
password prompt - which the Debian package installs. See packaging/deb/.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys

HELPER = "/usr/lib/nishro-link/nishro-link-setup"


def check(user: str = None, dev_input: str = "/dev/input",
          uinput: str = "/dev/uinput", helper: str = HELPER) -> dict:
    """What stands between this user and the keyboard and mouse.

    {"ok", "read", "write", "relogin", "fixable", "problems": [..words..]}
    "relogin" means setup is done and only a new login is missing.
    Off Linux there is nothing to set up: always ok.
    """
    if not sys.platform.startswith("linux"):
        return {"ok": True, "read": True, "write": True, "relogin": False,
                "fixable": False, "problems": []}
    events = sorted(glob.glob(os.path.join(dev_input, "event*")))
    read = any(os.access(p, os.R_OK) for p in events)
    write = os.path.exists(uinput) and os.access(uinput, os.W_OK)
    listed, live = _group_state(user)
    relogin = (not read or not write) and listed and not live
    problems = []
    if relogin:
        problems.append("Setup is done - log out and back in once to finish it.")
    else:
        if not read:
            problems.append("It cannot read this computer's keyboard and mouse.")
        if not write:
            problems.append("It cannot move the pointer or type here."
                            + ("" if os.path.exists(uinput) else
                               " (the uinput device is missing)"))
    fixable = (not relogin and bool(problems) and os.path.exists(helper)
               and shutil.which("pkexec") is not None)
    return {"ok": not problems, "read": read, "write": write, "relogin": relogin,
            "fixable": fixable, "problems": problems}


def fix(user: str = None, helper: str = HELPER) -> dict:
    """Ask the desktop's password prompt to set up access for `user`.

    Blocks until the prompt is answered. {"ok": True} when done - after which
    the person must still log out and back in, which check() will report.
    """
    user = user or _me()
    try:
        r = subprocess.run(["pkexec", helper, user], capture_output=True,
                           text=True, timeout=300)
    except FileNotFoundError:
        return {"ok": False, "error": "pkexec is not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "the password prompt was not answered"}
    if r.returncode in (126, 127):            # pkexec: dismissed, or not allowed
        return {"ok": False, "declined": True}
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout).strip()[:300]}
    return {"ok": True}


def _me() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return os.environ.get("USER", "")


def _group_state(user: str = None):
    """(listed, live): is the user in the input group in /etc/group, and is
    that group actually in force in THIS session?"""
    try:
        import grp
        g = grp.getgrnam("input")
    except (ImportError, KeyError):
        return False, False
    user = user or _me()
    listed = user in g.gr_mem
    try:
        import pwd
        listed = listed or pwd.getpwnam(user).pw_gid == g.gr_gid
    except Exception:
        pass
    live = g.gr_gid in os.getgroups()
    return listed, live


# ------------------------------------------------ for a machine not set up yet
class NoCapture:
    """Stands in for the real capture until access is set up: the program runs,
    the window shows what is missing, and nothing touches the devices."""

    def start(self, sink):
        self.sink = sink

    def stop(self):
        pass

    def set_suppress(self, mouse, keyboard, cursor_here=False):
        pass


class NoInjector:
    def move_abs(self, x, y):
        pass

    def move(self, dx, dy):
        pass

    def button(self, name, down):
        pass

    def key(self, code, down):
        pass

    def wheel(self, dx, dy):
        pass

    def close(self):
        pass
