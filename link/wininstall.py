"""Installing and removing the Windows service - what the setup wizard runs,
elevated, once.

    NishroLink.exe --install-service [--from-config PATH]
    NishroLink.exe --uninstall-service [--purge]

Done by the program rather than by the installer's own script: the service's
command line needs quoting that is easy to get wrong in three languages at
once, and this way the same code installs, upgrades and repairs - including a
service left stuck by an earlier attempt, which it stops, deletes and makes
again. Every step goes to ProgramData\\NishroLink\\install.log.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from . import service, winsvc

NAME = winsvc.SERVICE_NAME
RULES = ("Nishro Link", "Nishro Link (service)", "Nishro Link (program)")
CREATE_NO_WINDOW = 0x08000000


class Log:
    def __init__(self, path: Path = None):
        self.path = Path(path or service.BASE / "install.log")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, line: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {line}\n")


def _run(args, log, timeout=60):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                       creationflags=CREATE_NO_WINDOW)
    out = " ".join((r.stdout or "").split())[:300]
    log(f"{args[0]} {' '.join(args[1:3])}: {r.returncode} {out}")
    return r


def _ps(command: str, log):
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                log)


def state() -> str:
    """RUNNING, STOPPED, START_PENDING ... or "" when there is no such service."""
    r = subprocess.run(["sc", "query", NAME], capture_output=True, text=True,
                       creationflags=CREATE_NO_WINDOW)
    for line in (r.stdout or "").splitlines():
        if "STATE" in line:
            return line.split()[-1]
    return ""


def _stop_everything(log) -> None:
    """The service - even one stuck starting - and any copy started by hand.

    Not this process, and not its parent: the exe is one file, which runs as
    a small launcher that unpacks the program and waits for it - both named
    NishroLink.exe. Killing the launcher was killing the process the setup
    wizard waits on, so the wizard said the service had not started while
    this process went on to start it."""
    if state():
        _run(["sc", "stop", NAME], log)
        for _ in range(20):
            if state() in ("STOPPED", ""):
                break
            time.sleep(0.5)
    _run(["taskkill", "/F", "/IM", "NishroLink.exe",
          "/FI", f"PID ne {os.getpid()}", "/FI", f"PID ne {os.getppid()}"], log)
    time.sleep(0.5)


def _firewall(exe: str, log) -> None:
    names = ", ".join(f"'{r}'" for r in RULES)
    _ps(f"Get-NetFirewallRule -DisplayName {names} -ErrorAction SilentlyContinue "
        f"| Remove-NetFirewallRule", log)
    _ps(f"New-NetFirewallRule -DisplayName 'Nishro Link' -Direction Inbound "
        f"-Program '{exe}' -Action Allow -Profile Private,Domain | Out-Null", log)


def _settings(from_config, log) -> None:
    """The service's own settings, locked to SYSTEM and Administrators - they
    hold the group's password. The first time, this account's are carried
    across, so an upgrade keeps its pairing."""
    private = service.CONFIG.parent
    private.mkdir(parents=True, exist_ok=True)
    if from_config and Path(from_config).is_file() and not service.CONFIG.exists():
        shutil.copy2(from_config, service.CONFIG)
        log(f"settings carried across from {from_config}")
    _run(["icacls", str(private), "/inheritance:r", "/grant:r",
          "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"], log)


def _per_user_leftovers(log) -> None:
    """The per-user copy this replaces: its login entry would start a second
    window, and its Start Menu entry would sit beside the real one."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, "NishroLink")
            log("removed the per-user login entry")
    except OSError:
        pass
    appdata = os.environ.get("APPDATA")
    if appdata:
        lnk = Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Nishro Link.lnk"
        if lnk.exists():
            lnk.unlink()
            log("removed the per-user Start Menu entry")
    local = os.environ.get("LOCALAPPDATA")
    if local and (Path(local) / "Programs" / "NishroLink").is_dir():
        shutil.rmtree(Path(local) / "Programs" / "NishroLink", ignore_errors=True)
        log("removed the per-user program folder")


def install(exe: str, from_config: str = None, log=None) -> int:
    log = log or Log()
    exe = str(Path(exe).resolve())
    log(f"installing the service for {exe}")
    _stop_everything(log)
    if state():
        _run(["sc", "delete", NAME], log)
        for _ in range(20):
            if not state():
                break
            time.sleep(0.5)
    _settings(from_config, log)
    _firewall(exe, log)
    r = _run(["sc", "create", NAME, "binPath=", f'"{exe}" --service',
              "start=", "auto", "DisplayName=", "Nishro Link"], log)
    if r.returncode != 0:
        log("could not create the service")
        return 1
    _run(["sc", "description", NAME, "One mouse and keyboard across your "
          "computers - from boot, on the lock and sign-in screens."], log)
    _run(["sc", "failure", NAME, "reset=", "60",
          "actions=", "restart/2000/restart/5000/restart/10000"], log)
    err = service.BASE / "service-error.log"
    try:
        err.unlink()
    except OSError:
        pass
    _run(["sc", "start", NAME], log)
    for _ in range(60):
        if state() == "RUNNING":
            break
        time.sleep(0.5)
    if state() != "RUNNING":
        log(f"the service did not start (it is {state() or 'missing'})")
        if err.exists():
            log(err.read_text(encoding="utf-8", errors="replace"))
        return 2
    log("the service is running")
    _per_user_leftovers(log)
    return 0


def uninstall(purge: bool = False, log=None) -> int:
    log = log or Log()
    log("removing the service")
    _stop_everything(log)
    if state():
        _run(["sc", "delete", NAME], log)
    names = ", ".join(f"'{r}'" for r in RULES)
    _ps(f"Get-NetFirewallRule -DisplayName {names} -ErrorAction SilentlyContinue "
        f"| Remove-NetFirewallRule", log)
    service.withdraw()
    if purge:
        shutil.rmtree(service.BASE, ignore_errors=True)
    return 0
