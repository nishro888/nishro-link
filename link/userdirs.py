"""Where files from another computer land: the Desktop of the person at the
screen - not of whoever runs Nishro Link.

The service runs as SYSTEM on Windows and as root on Linux, whose own
Desktop nobody looks at. So the person's is asked for: on Windows from the
shell, with the token of the user signed in at the console (the Desktop may
have moved - to OneDrive, say); on Linux from xdg-user-dir inside their
session (it may be called something else - Schreibtisch, Bureau). Run as the
person - from source, as an app - it is simply their own.

On Linux, what root writes is root's: everything that arrives is given to
the person, or they could not move it or delete it. Windows needs nothing:
files written in the person's Desktop take its permissions.
"""
from __future__ import annotations

import os
import posixpath
import subprocess
import sys
from pathlib import Path

from . import files, session

ERROR_PRIVILEGE_NOT_HELD = 1314


def desktop() -> str | None:
    """The Desktop folder of the person at the screen; None when nobody is
    signed in, or it cannot be found."""
    if sys.platform == "win32":
        return _windows_desktop()
    return _linux_desktop()


def files_folder() -> str:
    """Desktop > Nishro Link Files, made if need be, and the person's.
    FilesError when there is no one to give the files to."""
    top = desktop()
    if not top:
        raise files.FilesError("nobody is signed in on this computer to receive files")
    folder = Path(top) / files.FOLDER
    made, p = [], folder
    while not p.exists() and p != p.parent:
        made.append(p)
        p = p.parent
    folder.mkdir(parents=True, exist_ok=True)
    give_to_person(made, whole=False)
    return str(folder)


def give_to_person(paths, whole: bool = True) -> None:
    """Make what root wrote the person's - folders with all they hold, or
    `whole` False, only themselves. Only as the service on Linux; anywhere
    else it is theirs already."""
    if not session.running_as_root():     # never so on Windows
        return
    who = _person()
    if who is None:
        return
    uid, gid = who
    for top in paths:
        top = str(top)
        _chown(top, uid, gid)
        if whole and os.path.isdir(top) and not os.path.islink(top):
            for root, dirs, names in os.walk(top):
                for name in dirs + names:
                    _chown(os.path.join(root, name), uid, gid)


# ------------------------------------------------------------------ Linux
def _person():
    """(uid, gid) of the person at the screen, or None."""
    s = session.active()
    if s is None:
        return None
    try:
        import pwd
        return s["uid"], pwd.getpwnam(s["user"]).pw_gid
    except (KeyError, ImportError):
        return None


def _home() -> str | None:
    if not session.running_as_root():
        return os.path.expanduser("~")
    s = session.active()
    if s is None:
        return None
    try:
        import pwd
        return pwd.getpwnam(s["user"]).pw_dir
    except (KeyError, ImportError):
        return None


def _linux_desktop() -> str | None:
    home = _home()
    if home is None:
        return None                   # the service, and nobody signed in
    try:
        r = session.run(["env", f"HOME={home}", "xdg-user-dir", "DESKTOP"],
                        capture_output=True, text=True, timeout=5)
        found = r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError, LookupError):
        found = ""
    return found if found.startswith("/") else posixpath.join(home, "Desktop")


def _chown(path: str, uid: int, gid: int) -> None:
    try:
        os.chown(path, uid, gid, follow_symlinks=False)
    except OSError:
        pass


# ---------------------------------------------------------------- Windows
def _windows_desktop() -> str | None:
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    # FOLDERID_Desktop, {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
    desktop_id = GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                      (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                             wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    wtsapi32.WTSQueryUserToken.argtypes = [wintypes.ULONG, ctypes.POINTER(wintypes.HANDLE)]
    wtsapi32.WTSQueryUserToken.restype = wintypes.BOOL
    kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    # Only SYSTEM may have another user's token. Anyone else is told so, and
    # then their own Desktop is the right one.
    token = wintypes.HANDLE()
    if not wtsapi32.WTSQueryUserToken(kernel32.WTSGetActiveConsoleSessionId(),
                                      ctypes.byref(token)):
        if ctypes.get_last_error() != ERROR_PRIVILEGE_NOT_HELD:
            return None               # the service, and nobody signed in at the screen
        token = wintypes.HANDLE()
    path = ctypes.c_wchar_p()
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(desktop_id), 0, token,
                                        ctypes.byref(path)) != 0:
            return None
        return path.value
    finally:
        if path:
            ole32.CoTaskMemFree(path)
        if token:
            kernel32.CloseHandle(token)
