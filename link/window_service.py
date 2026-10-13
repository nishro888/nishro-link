"""The service's side of a moved window: start the helper, pass frames along.

docs/moving-windows.md. The service holds the window channel - it did the
proving and the sealing - but it has no desktop: on Windows it is SYSTEM in
session 0, on Linux it is root outside every session. The window and the
frame are in the signed-in session, so one `window_host` is started there per
moved window and the service passes frames between the two
(winmove.relay).

    for one window:  listen on loopback  ->  start the helper in the session
                     ->  it proves a one-use token  ->  pass frames along

The token is made here, used once, and never written down: it is what stops
anything else on the computer from pretending to be the helper and being
handed a window.
"""
from __future__ import annotations

import secrets
import socket
import subprocess
import sys
import threading

from . import winmove

_NO_WINDOW = 0x08000000      # CREATE_NO_WINDOW: no console flash from a GUI app

ACCEPT_TIMEOUT = 25.0          # how long to wait for the helper to turn up


def attach(net, kind: str, which: str, whose: str, log=None,
           launch=None) -> str:
    """See one moved window through, from the service's side.

    `net` is the window channel to the other computer. `kind` is "lend" (one
    of our windows, shown over there) or "show" (one of theirs, shown here).
    Returns "" once the window has come home, or why it could not go.
    """
    log = log or (lambda *_: None)
    token = secrets.token_hex(16)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(ACCEPT_TIMEOUT)
    port = srv.getsockname()[1]
    try:
        proc = (launch or launch_in_session)(port, token, kind, which, whose, log)
    except Exception as e:
        srv.close()
        return f"could not start the part that shows windows: {e}"
    if proc is None:
        srv.close()
        return "nobody is signed in on this computer, so there is no screen"
    try:
        try:
            conn, _ = srv.accept()
        except (socket.timeout, OSError):
            return ("the part that shows windows did not start - is anyone "
                    "signed in?")
        finally:
            srv.close()
        host = winmove.plain_channel(conn)
        try:
            hello, _ = winmove.unpack(host.recv())
            if not secrets.compare_digest(str(hello.get("token") or ""), token):
                host.send(winmove.pack({"t": "err", "msg": "wrong token"}))
                log("something tried to take a moved window and could not "
                    "prove it was ours")
                return "the part that shows windows could not prove itself"
            host.send(winmove.pack({"t": "host_ok"}))
            # It looks at the window - which only it can do - and says
            # whether this is possible at all before anything else happens.
            ready, _ = winmove.unpack(host.recv())
            if not ready.get("ok"):
                why = str(ready.get("why") or "that window cannot be moved")
                net.send(winmove.pack({"t": "gone", "why": winmove.FAILED}))
                return why
            winmove.relay(net, host, log=log)
        finally:
            host.close()
        return ""
    finally:
        _end(proc, log)


def self_command():
    """How to run this program again, as the rest of Nishro Link does it: the
    frozen exe takes its own flags, from source it is python -m."""
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return [exe]
    return [exe, "-m", "link.nishro_link"]


def launch_in_session(port: int, token: str, kind: str, which: str,
                      whose: str, log):
    """Start the helper where the screen is, however this system does that."""
    args = self_command() + ["--window-host", str(port), token, kind,
                             str(which), str(whose)]
    if sys.platform == "win32":
        return _launch_windows(args, log)
    return _launch_linux(args, log)


def _launch_windows(args, log):
    """In the console session, on whichever desktop is showing - the same way
    the desk agent gets there."""
    try:
        from . import winsvc
        if _is_service():
            return winsvc.launch_in_console(subprocess.list2cmdline(args))
    except Exception as e:
        log(f"could not start the window helper in the session ({e}); "
            f"starting it here instead")
    # Not the service - run from source, or in the window: this process is
    # already in the session, so just start it.
    return subprocess.Popen(args, creationflags=_NO_WINDOW)


def _launch_linux(args, log):
    """As the person whose session is in front of the screen."""
    from . import session
    if session.running_as_root():
        who = session.active()
        if who is None:
            return None
        args = session.command(args, who)
    return subprocess.Popen(args)


# How long the helper is given to put the window back by itself before it is
# made to stop. It has a hidden window of someone else's, and killing it
# while it holds one leaves that window invisible until Nishro Link next
# starts - which is exactly what happened before this wait existed.
GOODBYE_S = 8.0


def _end(proc, log=None) -> None:
    """Let the helper finish, then insist.

    Its channel has been closed by now, so its session has ended and it is
    on its way out, putting the window back as it goes.
    """
    log = log or (lambda *_: None)
    try:
        if proc.wait(GOODBYE_S) is not None:
            return
    except Exception:
        pass
    log("the part that shows windows has not finished - stopping it")
    for step in ("terminate", "kill"):
        try:
            getattr(proc, step)()
            proc.wait(2)
            return
        except Exception:
            continue


def windows_in_session(log=None, run=None):
    """[(which, title)] - the windows that could be moved from this computer.

    Asked of the session, because the service has no desktop to look at.
    Returns nothing rather than raising: this is for a tray menu.
    """
    log = log or (lambda *_: None)
    if not _is_service():
        from . import window_host
        return window_host.movable()
    import json
    cmd = self_command() + ["--list-windows"]
    try:
        if sys.platform != "win32":
            from . import session
            if session.running_as_root():
                who = session.active()
                if who is None:
                    return []
                cmd = session.command(cmd, who)
        out = (run or subprocess.run)(cmd, capture_output=True, text=True,
                                      timeout=10)
        last = [ln for ln in (out.stdout or "").splitlines() if ln.strip()]
        return [tuple(x) for x in json.loads(last[-1])] if last else []
    except Exception as e:
        log(f"could not list the windows that could move: {e}")
        return []


# Whether this process is the service - outside every session, with no
# desktop - and so must start the helper in the session rather than here.
# Set by nishro_link when it starts; guessed on Linux, where running as root
# means it, so that a stray caller still does the right thing.
IN_SERVICE = None


def in_service(yes: bool) -> None:
    global IN_SERVICE
    IN_SERVICE = bool(yes)


def _is_service() -> bool:
    if IN_SERVICE is not None:
        return IN_SERVICE
    if sys.platform != "win32":
        try:
            from . import session
            return bool(session.running_as_root())
        except Exception:
            return False
    return False


def put_windows_back(log=None) -> int:
    """Put back whatever was left hidden when Nishro Link last stopped (R6).

    Runs in the session, because that is where the windows are.
    """
    log = log or (lambda *_: None)
    if not _is_service():
        from . import window_host
        return window_host.put_windows_back(log)
    try:
        threading.Thread(target=_put_back_in_session, args=(log,), daemon=True,
                         name="windows-back").start()
    except Exception as e:
        log(f"could not put hidden windows back: {e}")
    return 0


def _put_back_in_session(log) -> None:
    cmd = self_command() + ["--windows-back"]
    try:
        if sys.platform != "win32":
            from . import session
            if session.running_as_root():
                who = session.active()
                if who is None:
                    return
                cmd = session.command(cmd, who)
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if (out.stdout or "").strip() not in ("", "0"):
            log(f"put back {out.stdout.strip()} window(s) left hidden")
    except Exception as e:
        log(f"could not put hidden windows back: {e}")
