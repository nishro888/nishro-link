"""Things a long-running program needs that are not its job: a log it owns, and
a guarantee there is only one of it.

Both exist because of one incident. Ubuntu was told to run

    nohup nishro-link > /tmp/link.log 2>&1 &

which TRUNCATES on every launch, so a second run destroyed the first one's
output - and the second run happened to be a process that could never connect,
so the only surviving evidence described a completely different failure than the
one being investigated. Two instances were running at once and nothing said so.

A program that can lock you out of your own keyboard has to be better than that
at saying what it did.
"""
from __future__ import annotations

import base64
import collections
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024      # roll at 2 MB; one previous file is kept


def exclusive(sock: socket.socket) -> socket.socket:
    """Make `sock` the only listener its port will ever have. Call before bind().

    The same intent needs OPPOSITE socket options on the two platforms, and both
    wrong answers have happened here:

      Windows  SO_REUSEADDR means "bind even though someone is listening". Two
               copies that both set it share one port, and connections go to
               whichever the kernel picks - the window polled one node while
               showing another's token. SO_EXCLUSIVEADDRUSE refuses every
               second bind, whatever the second socket asks for.

      POSIX    SO_REUSEADDR only means "bind over TIME_WAIT leftovers"; a live
               listener still refuses. Without it, restarting within a minute
               of the last run fails, because every Connection: close the old
               server sent left its port in TIME_WAIT. Found on the Ubuntu box:
               the first restart after the Windows fix moved the UI to 8772
               with only one copy running.

    Measured on this Windows box before choosing: a restart over TIME_WAIT
    succeeds with no option, SO_REUSEADDR or SO_EXCLUSIVEADDRUSE alike, so the
    strict option costs nothing there.
    """
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return sock


def state_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return base / "NishroLink"
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return base / "nishro-link"


def log_path() -> Path:
    return state_dir() / "link.log"


class RunLog:
    """Appends to a file the program chooses, and echoes to the console.

    Owning the path matters more than it sounds: shell redirection puts the log
    wherever the person happened to be standing, truncates it without saying so,
    and silently interleaves two processes writing to the same file.
    """

    def __init__(self, path: Path = None, echo=True, keep: int = 200):
        self.path = Path(path) if path else log_path()
        self.echo = echo
        # A short in-memory tail, so a UI can show what just happened without
        # re-reading (and re-parsing) a file that two processes may be appending to.
        self.tail = collections.deque(maxlen=keep)
        self._lock = threading.Lock()
        self._fh = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._roll()
            self._fh = open(self.path, "a", encoding="utf-8", errors="replace")
        except OSError:
            self._fh = None          # a log we cannot write must not stop the app

    def _roll(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                prev = self.path.with_suffix(".log.1")
                prev.unlink(missing_ok=True)
                self.path.rename(prev)
        except OSError:
            pass

    def __call__(self, *parts) -> None:
        msg = " ".join(str(p) for p in parts)
        # Timestamp and pid on every line: with two processes appending, the pid
        # is the only way to tell whose story you are reading.
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {msg}"
        self.tail.append(line)
        if self.echo:
            print("[link]", msg, flush=True)
        with self._lock:
            if self._fh:
                try:
                    self._fh.write(line + "\n")
                    self._fh.flush()
                except OSError:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._fh:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None


class SingleInstance:
    """One process per port, enforced by the OS.

    A bound loopback socket is used rather than a lock file because the kernel
    releases it however the process dies - crash, kill -9, power cut. A stale
    PID file after a hard kill would refuse to start with no way to tell whether
    the owner was alive, which is worse than not checking at all.
    """

    def __init__(self, port: int):
        self.port = int(port)
        self._sock = None

    def acquire(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # exclusive(), not a bare bind: on Linux a bare bind also refuses for
            # a minute after a second launch signalled us (the connection it
            # left in TIME_WAIT), and this would then claim another copy was
            # running when none was.
            exclusive(s)
            s.bind(("127.0.0.1", self.port))
            s.listen(1)
        except OSError:
            s.close()
            return False
        self._sock = s
        return True

    def watch(self, on_signal) -> None:
        """Answer a second launch by calling `on_signal`.

        The lock is already a listening socket, so it can carry a message for
        free: a second launch cannot start, and instead connects here and hangs
        up. That is how the running copy learns to show its window again - which
        is what someone who has hidden it and then clicked the shortcut means.
        """
        threading.Thread(target=self._accept, args=(on_signal,), daemon=True).start()

    def _accept(self, on_signal) -> None:
        while self._sock is not None:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return                       # released; we are shutting down
            try:
                conn.close()
            except OSError:
                pass
            try:
                on_signal()
            except Exception:
                pass                         # a UI that will not raise is not fatal

    @staticmethod
    def signal(port: int) -> bool:
        """Ask the copy already running to show itself. True if one answered."""
        try:
            socket.create_connection(("127.0.0.1", int(port)), timeout=2).close()
            return True
        except OSError:
            return False

    def release(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def firewall_hint(port: int) -> str:
    """What to say when a peer cannot reach us. Blocked inbound traffic looks
    exactly like a machine that is switched off, so the guess has to be spelled
    out or it is never made."""
    if sys.platform == "win32":
        exe = sys.executable
        if getattr(sys, "frozen", False):
            exe = sys.executable
        return ("nothing has connected yet. If the other machine cannot find "
                "this one by name, or says 'connect failed: timed out', Windows "
                "Firewall is blocking this program. In an ADMIN PowerShell:\n"
                f'    New-NetFirewallRule -DisplayName "Nishro Link (program)" '
                f'-Direction Inbound -Program "{exe}" -Action Allow '
                f'-Profile Private,Domain')
    return (f"nothing has connected yet. If the other machine cannot find or "
            f"reach this one, check that port {port} is open for both the link "
            f"and the search: sudo ufw allow {port}")


# Windows Firewall, asked about by rule OBJECTS rather than netsh's text, which
# is translated on a non-English Windows and would quietly match nothing there.
_NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW: no console flash from a GUI app


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def firewall_blocks(program: str = None):
    """Enabled inbound BLOCK rules Windows Firewall holds against this program.

    Windows makes these itself when its "allow access" prompt is dismissed, and
    a block outranks every allow - so one click on Cancel turns a working
    machine into one nobody can find or reach, with nothing on screen to say so.
    Seen on the laptop: the first build that listened on UDP brought the prompt
    up, it was dismissed, and the link was down until the rules were found.

    [] means none. None means it could not be checked - not Windows, or
    PowerShell failed - which must never be reported as a problem.
    """
    if sys.platform != "win32":
        return None
    program = program or sys.executable
    # The firewall's COM interface, not the NetSecurity cmdlets: those refuse to
    # READ rules without administrator rights, and netsh's text is translated.
    # Only blocks that apply to a network profile in use now are counted - a
    # program blocked on public networks is not blocked on a home one.
    ps = (f"$p = {_ps_quote(program)}; "
          "$fw = New-Object -ComObject HNetCfg.FwPolicy2; "
          "$now = $fw.CurrentProfileTypes; "
          "$fw.Rules | Where-Object { $_.ApplicationName -and "
          "$_.ApplicationName -ieq $p -and $_.Direction -eq 1 -and "
          "$_.Action -eq 0 -and $_.Enabled -and ($_.Profiles -band $now) } | "
          "ForEach-Object { $_.Name + '|' + $_.Protocol + '|' + $_.Profiles }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", ps], capture_output=True, text=True,
                           timeout=60, creationflags=_NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return parse_blocks(r.stdout)


_PROTOCOLS = {"6": "TCP", "17": "UDP", "256": "any protocol"}


def parse_blocks(text: str) -> list:
    """'name|protocol|profiles' lines from the query above, made readable."""
    out = []
    for line in (text or "").splitlines():
        parts = [x.strip() for x in line.split("|")]
        if len(parts) != 3 or not parts[0]:
            continue
        name, proto, prof = parts
        try:
            bits = int(prof)
        except ValueError:
            bits = 0
        where = [n for b, n in ((1, "domain"), (2, "private"), (4, "public"))
                 if bits & b] or ["all"]
        out.append(f"{name} ({_PROTOCOLS.get(proto, 'protocol ' + proto)}, "
                   f"{'/'.join(where)} networks)")
    return out


def firewall_fix_script(program: str) -> str:
    """What runs elevated: drop the block rules for this program, then allow it
    on private and domain networks - any protocol, so the link (TCP) and being
    found by name (UDP) both work. Not public networks: a café Wi-Fi is exactly
    where a machine should not be answering strangers."""
    p = _ps_quote(program)
    return (f"$p = {p}; "
            "$mine = Get-NetFirewallApplicationFilter | Where-Object { $_.Program -ieq $p } | "
            "Get-NetFirewallRule | Where-Object { $_.Direction -eq 'Inbound' }; "
            "$mine | Where-Object { $_.Action -eq 'Block' } | Remove-NetFirewallRule; "
            "if (-not ($mine | Where-Object { $_.Action -eq 'Allow' -and $_.Enabled -eq 'True' })) { "
            "New-NetFirewallRule -DisplayName 'Nishro Link (program)' -Direction Inbound "
            "-Program $p -Action Allow -Profile Private,Domain | Out-Null }")


def allow_through_firewall(program: str = None) -> bool:
    """Ask Windows - through its own elevation prompt - to apply the fix above.

    Blocks until the prompt is answered. False if it was declined or could not
    be shown; the person saw Windows ask, so nothing changes behind their back.
    """
    if sys.platform != "win32":
        return False
    program = program or sys.executable
    encoded = base64.b64encode(firewall_fix_script(program).encode("utf-16-le")).decode()
    outer = ("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
             f"-ArgumentList '-NoProfile','-EncodedCommand','{encoded}'")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", outer], capture_output=True, text=True,
                           timeout=300, creationflags=_NO_WINDOW)
    except Exception:
        return False
    return r.returncode == 0
