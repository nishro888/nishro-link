"""The part of moving windows that lives where the screen is.

docs/moving-windows.md. Nishro Link's service runs outside any session - as
SYSTEM on Windows, as root on Linux - which is what lets another computer's
mouse work on the lock and sign-in screens. But a window, and a frame to show
one in, only exist inside the signed-in session. So the service keeps the
network connection, the password and the group, and passes the frames of a
moved window to this, over a loopback connection (winmove.relay):

    service (no desktop)                     this, in the person's session
      the window channel, sealed      <-->     the window itself, hidden and
      the group, the password                  captured (window_win), or the
      passes frames along, no more             frame showing one (window_frame)

One of these runs per moved window and exits when the window comes home, so
a window that goes wrong takes nothing else with it. The service starts it
the way it already starts things in the session - CreateProcessAsUser on
Windows, runuser on Linux - and tells it where to connect:

    python -m link.window_host --port 54321 --token <one use> \\
        --kind lend|show --which <window> --whose <computer>

`lend` means one of this computer's windows is to be shown over there;
`show` means a window from over there is to be shown here.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import socket
import sys
import threading
import time

from . import picture, winmove

CONNECT_TIMEOUT = 15.0


def windows_module():
    """The module that can hide and capture a window on this system, or None
    where that cannot be done yet - Linux, until the GNOME side is built
    (docs/moving-windows.md, stage 5)."""
    if sys.platform == "win32":
        from . import window_win
        return window_win
    return None


def can_lend() -> bool:
    """Can a window on this computer be shown on another?"""
    return windows_module() is not None


def movable():
    """[(which, title)] - the windows that could be moved from here."""
    win = windows_module()
    if win is None:
        return []
    return [(str(h), t) for h, t, _c in win.movable(own_pid=os.getpid())]


def rescue_path():
    """Where the undo for a hidden window is written (R6).

    This person's own folder, not the service's: the service's is locked to
    SYSTEM and Administrators, and this runs as the person whose windows
    these are - writing there failed silently and left a window unhidden
    with nothing in the log at all. Per-user is right anyway, since a
    window belongs to one session.
    """
    told = os.environ.get("NISHRO_LINK_MOVED_RECORD")
    if told:
        # So a test can use a folder of its own rather than the person's
        # real one, and so a diagnosis can watch a file of its own.
        return pathlib.Path(told)
    if sys.platform == "win32":
        base = pathlib.Path(os.environ.get("LOCALAPPDATA")
                            or pathlib.Path.home()) / "NishroLink"
    else:
        base = pathlib.Path(os.environ.get("XDG_STATE_HOME")
                            or pathlib.Path.home() / ".local" / "state") / "nishro-link"
    return base / "moved-windows.json"


def rescue_record(log=None):
    return winmove.Rescue(rescue_path(), log=log)


def log_path():
    """Where this helper writes what it did, as well as saying it over the
    channel.

    The channel is not enough by itself: when a moved window ends because
    the channel broke, the last and most interesting lines cannot be sent on
    it. A file survives that. On Windows it goes beside the service's handle
    in ProgramData, which this helper (SYSTEM, started by the service) can
    write and any local account can read; elsewhere, beside the record of
    hidden windows.
    """
    told = os.environ.get("NISHRO_LINK_WINDOW_LOG")
    if told:
        return pathlib.Path(told)
    if sys.platform == "win32":
        try:
            from . import service
            return service.BASE / "window-host.log"
        except Exception:
            pass
    return rescue_path().parent / "window-host.log"


def _log_paths():
    """Where to try writing, best first - the second is this person's own
    folder, which anything in their session can always write."""
    out = []
    for get in (log_path, lambda: rescue_path().parent / "window-host.log"):
        try:
            path = get()
        except Exception:
            continue
        if path not in out:
            out.append(path)
    return out


def write_log(message: str) -> None:
    """Best effort, always: a helper must never fail over its own log.

    Two places are tried. The window helper runs as SYSTEM and writes beside
    the service's own files; anything in the PERSON's session - the tray, and
    the drag watcher inside it - can only READ there. That silently swallowed
    every line the drag watcher tried to say, so when its hook did not
    install there was nothing anywhere to say why, and the gesture looked
    like it simply did nothing. A line nobody can write is a line nobody can
    read.
    """
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{when} [{os.getpid()}] {message}\n"
    for path in _log_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            return
        except Exception:
            continue


def put_windows_back(log=None) -> int:
    """Put back anything left hidden when Nishro Link last stopped (R6).
    Called at start-up, before any window may be hidden again."""
    win = windows_module()
    if win is None:
        return 0
    return win.restore_everything(rescue_record(log), log=log)


# ----------------------------------------------------------------- one window
def can_lend_this(which: str) -> str:
    """Why this particular window cannot be moved, or "" if it can (R9).

    Asked in the session, because only here can a window be looked at - the
    service has no desktop. Its answer is what the person is shown.
    """
    win = windows_module()
    if win is None:
        return "this computer cannot move its own windows yet"
    try:
        return win.why_not(int(which), own_pid=os.getpid())
    except (TypeError, ValueError):
        return "that is not a window"


def lend(which: str, to_whom: str, ch, log) -> str:
    """Hide one of our windows and send what it looks like over `ch`."""
    win = windows_module()
    window = win.Window(int(which), rescue=rescue_record(log), log=log)
    session = winmove.HomeSession(ch, window, picture.encode, log=log)

    # The window is hidden only once the frame is really up over there, so a
    # frame that never appears cannot leave a window invisible here; and it
    # is put back whatever happens, including a crash (R6, Rescue).
    def hide_once_shown():
        # Nothing in here may raise unseen: this is the thread that hides
        # someone else's window, and a silent death here left a window
        # visible at home with no word in the log (seen when the undo could
        # not be written).
        try:
            if session.wait_ready() and not window.hide():
                log("that window cannot be hidden here, so it stays on this "
                    "screen as well as showing over there")
        except Exception as e:
            log(f"that window could not be hidden ({e!r}), so it stays on "
                f"this screen as well as showing over there")
    threading.Thread(target=hide_once_shown, daemon=True,
                     name="window-hide").start()
    try:
        why = session.run()
        log(f"our window came back from {to_whom} ({why})")
    finally:
        window.show()
    return ""


def show(which: str, whose: str, ch, log, home_side: str = "") -> str:
    """Show a window that lives on `whose` computer, here."""
    from . import window_frame
    frame = window_frame.Frame(where=whose or "the other computer", log=log,
                               home_side=home_side)
    session = winmove.AwaySession(ch, frame, log=log)
    try:
        why = session.run()
        log(f"the window from {whose} has gone back ({why})")
    finally:
        frame.close(session.ended or winmove.HOME)
    return ""


def send_list(port: int, token: str) -> str:
    """Send the windows that could move to the service, over loopback.

    Not printed: the service runs outside the session, so it cannot simply
    start this and read what it wrote - on Windows it has to be started on
    the person's desktop, where its output goes nowhere the service can see.
    The same loopback-and-token way a moved window is handed over works for
    a list too.
    """
    sock = socket.create_connection(("127.0.0.1", int(port)),
                                    timeout=CONNECT_TIMEOUT)
    ch = winmove.plain_channel(sock)
    try:
        ch.send(winmove.pack({"t": "host", "token": token, "kind": "list"}))
        answer, _ = winmove.unpack(ch.recv())
        if answer.get("t") != "host_ok":
            return answer.get("msg") or "the service would not have us"
        ch.send(winmove.pack({"t": "windows", "windows": movable()}))
        return ""
    finally:
        ch.close()


def one_window(port: int, token: str, kind: str, which: str, whose: str,
               log=print, home_side: str = "") -> str:
    """Connect to the service and see one moved window through."""
    sock = socket.create_connection(("127.0.0.1", int(port)),
                                    timeout=CONNECT_TIMEOUT)
    ch = winmove.plain_channel(sock)
    try:
        ch.send(winmove.pack({"t": "host", "token": token, "kind": kind}))
        answer, _ = winmove.unpack(ch.recv())
        if answer.get("t") != "host_ok":
            return answer.get("msg") or "the service would not have us"
        # Whether this can be done at all is decided here, where the windows
        # are, and sent back so the person is told why rather than left with
        # a window that never appears.
        # From here its log goes to the service, which can see it - this
        # process cannot: in the session its output reaches the system log at
        # best, and nothing at all from a windowed build.
        # From here the log goes over the channel, where the service can see
        # it, and to a file, which survives the channel breaking. NOT to
        # stdout as well: the service reads that too, as a last resort for
        # anything printed before this point, and every line was arriving in
        # its log twice.
        def log(message):
            write_log(message)
            try:
                ch.send(winmove.host_log(message))
            except Exception:
                pass
        log(f"starting: {kind} window {which} with {whose}")
        why = can_lend_this(which) if kind == "lend" else ""
        ch.send(winmove.pack({"t": "host_ready", "ok": not why, "why": why}))
        if why:
            return why
        if kind == "lend":
            return lend(which, whose, ch, log)
        return show(which, whose, ch, log, home_side)
    finally:
        ch.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="window_host",
        description="show one moved window, where the screen is")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--kind", choices=("lend", "show"), required=True)
    p.add_argument("--which", default="")
    p.add_argument("--whose", default="")
    p.add_argument("--home-side", default="")
    args = p.parse_args(argv)

    def log(message):
        print(message, flush=True)

    try:
        why = one_window(args.port, args.token, args.kind, args.which,
                         args.whose, log=log, home_side=args.home_side)
    except Exception as e:
        log(f"the moved window went wrong: {e!r}")
        return 1
    if why:
        log(why)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
