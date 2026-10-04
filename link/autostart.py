"""Start Nishro Link when the user logs in: the "Start when I log in" switch.

Per user, and nothing that needs an administrator:
  Windows  a value under HKCU\\...\\CurrentVersion\\Run
  Linux    ~/.config/autostart/nishro-link.desktop  (XDG autostart: GNOME, KDE,
           Xfce, Cinnamon and the rest all read it)

Started this way it comes up with its window hidden (--background): a window
appearing at every login is noise. Opening it from the app menu then shows the
window, through the single-instance signal, as it does at any other time.

XDG autostart rather than the systemd user unit: the window needs the desktop
session's DISPLAY or WAYLAND_DISPLAY, which an autostart entry inherits and a
user unit gets only if the desktop happened to export it.

What is stored is a command line, and a command line goes stale when the
program moves - a new download to a different folder. state() says whether the
stored one still starts THIS copy, so the window can say so rather than let a
login quietly start an old version.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

NAME = "Nishro Link"
FILE = "nishro-link.desktop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# The same value install-windows.ps1 -Autostart writes. Two names would mean two
# launches at login, and the second one opens the window the first kept hidden.
RUN_VALUE = "NishroLink"
BACKGROUND = "--background"
INSTALLED = "/usr/bin/nishro-link"            # the .deb's launcher


def available() -> bool:
    return sys.platform == "win32" or sys.platform.startswith("linux")


def command(extra=()) -> list:
    """The command line that starts this copy of the program, in the background."""
    return launcher([BACKGROUND, *extra])


def launcher(extra=()) -> list:
    """The command line that starts this copy of the program - the tray's way
    to open the window, and the base of the login entry."""
    extra = list(extra)
    if getattr(sys, "frozen", False):                 # the PyInstaller exe
        return [sys.executable, *extra]
    root = str(pathlib.Path(__file__).resolve().parent.parent)
    if sys.platform == "win32":
        # pythonw, or a console window sits open for the whole session. -c
        # rather than -m: the Run key has no working directory to import from.
        exe = pathlib.Path(sys.executable)
        quiet = exe.with_name("pythonw.exe")
        exe = str(quiet if quiet.exists() else exe)
        code = (f"import sys; sys.path.insert(0, {root!r}); "
                f"from link.nishro_link import main; sys.exit(main())")
        return [exe, "-c", code, *extra]
    # The package's launcher by its full path first: an older per-user install
    # in ~/.local/bin comes earlier on PATH, and would be what a bare name ran.
    for installed in (INSTALLED, shutil.which("nishro-link")):
        if installed and _is_ours(installed, root):
            return [installed, *extra]
    # Not -m: that imports from the current directory first, and a login starts
    # programs in the home folder, where an old unpacked ~/link would win.
    code = (f"import sys; sys.path.insert(0, {root!r}); "
            f"from link.nishro_link import main; sys.exit(main())")
    return [sys.executable, "-c", code, *extra]


def _is_ours(launcher: str, root: str) -> bool:
    """Does the `nishro-link` on PATH start the code in `root`? The package's
    launcher names its library folder; a copy run from anywhere else must not
    set up a login that starts the installed one instead."""
    try:
        text = pathlib.Path(launcher).read_text(errors="replace")
    except OSError:
        return False
    return root in text


# --------------------------------------------------------------- the switch
def state(cmd=None, where=None) -> dict:
    """{"available", "on", "current"}. "current": the stored command is `cmd`."""
    if not available():
        return {"available": False, "on": False, "current": False}
    stored = _read(where)
    cmd = command() if cmd is None else cmd
    return {"available": True, "on": stored is not None,
            "current": stored is not None and _same(_argv(stored), cmd)}


def _same(stored: list, cmd) -> bool:
    """Compared as arguments, not as text: the installer quotes the exe path
    whether or not it has spaces, and Windows paths ignore case."""
    cmd = [str(a) for a in cmd]
    if sys.platform == "win32":
        return [a.lower() for a in stored] == [a.lower() for a in cmd]
    return stored == cmd


def _argv(line: str) -> list:
    return _windows_split(line) if sys.platform == "win32" else _exec_split(line)


def switch(on: bool, cmd=None, where=None) -> None:
    """Switch it on (storing `cmd`, default command()) or off. Raises OSError."""
    if not available():
        raise OSError("starting at login is not supported on this system")
    if on:
        _write(_render(command() if cmd is None else cmd), where)
    else:
        _remove(where)


def _render(cmd) -> str:
    return (_windows_line(cmd) if sys.platform == "win32"
            else " ".join(_exec_arg(a) for a in cmd))


# ------------------------------------------------------------------ Windows
def _windows_line(cmd) -> str:
    import subprocess
    return subprocess.list2cmdline([str(a) for a in cmd])


def _windows_split(line: str) -> list:
    """A command line into arguments, the way Windows itself splits them."""
    import ctypes
    from ctypes import wintypes
    split = ctypes.windll.shell32.CommandLineToArgvW
    split.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    n = ctypes.c_int()
    argv = split(line, ctypes.byref(n))
    if not argv:
        return [line]
    try:
        return [argv[i] for i in range(n.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


def _reg(where, write=False):
    import winreg
    access = winreg.KEY_READ | (winreg.KEY_SET_VALUE if write else 0)
    return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, where or RUN_KEY, 0, access)


# -------------------------------------------------------------------- Linux
def autostart_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return pathlib.Path(base) / "autostart"


def _entry(where) -> pathlib.Path:
    return pathlib.Path(where or autostart_dir()) / FILE


_RESERVED = set(' \t\n"\'\\><~|&;$*?#()`')


def _exec_arg(arg) -> str:
    """One argument of a desktop entry's Exec line, quoted by the spec's rules:
    reserved characters mean double quotes, inside which " ` $ and \\ are
    escaped with a backslash. A literal % is written %%."""
    arg = str(arg).replace("%", "%%")
    if arg and not (_RESERVED & set(arg)):
        return arg
    out = arg
    for ch in ("\\", '"', "`", "$"):
        out = out.replace(ch, "\\" + ch)
    return f'"{out}"'



def _exec_split(line: str) -> list:
    """An Exec line back into arguments, by the same spec: spaces separate,
    double quotes group, and inside quotes a backslash makes the next character
    literal. (Not shlex: it keeps the backslash before $ and `.)"""
    args, cur, quoted, started, i = [], "", False, False, 0
    while i < len(line):
        c = line[i]
        if quoted and c == "\\" and i + 1 < len(line):
            cur, i = cur + line[i + 1], i + 2
            continue
        if c == '"':
            quoted, started = not quoted, True
        elif c in " \t" and not quoted:
            if started:
                args.append(cur)
            cur, started = "", False
        else:
            cur, started = cur + c, True
        i += 1
    if started:
        args.append(cur)
    return [a.replace("%%", "%") for a in args]


def _desktop_file(line: str) -> str:
    return "\n".join((
        "[Desktop Entry]",
        "Type=Application",
        f"Name={NAME}",
        "Comment=Starts Nishro Link in the background when you log in",
        f"Exec={line}",
        "Icon=nishro-link",
        "Terminal=false",
        "NoDisplay=true",
        "X-GNOME-Autostart-enabled=true",
        "")) + "\n"


def _parse_entry(text: str):
    """The Exec line, or None when the entry is switched off. Desktops switch an
    entry off by editing it rather than deleting it (Hidden=true, or GNOME's
    own key), so those count as off too."""
    exec_line, off = None, False
    for raw in text.splitlines():
        key, sep, value = raw.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "Exec":
            exec_line = value
        elif key == "Hidden" and value.lower() == "true":
            off = True
        elif key == "X-GNOME-Autostart-enabled" and value.lower() == "false":
            off = True
    return None if off else exec_line


# ------------------------------------------------------------ both, stored
def _read(where):
    if sys.platform == "win32":
        import winreg
        try:
            with _reg(where) as k:
                value, _kind = winreg.QueryValueEx(k, RUN_VALUE)
                return str(value)
        except OSError:
            return None
    try:
        return _parse_entry(_entry(where).read_text(encoding="utf-8"))
    except OSError:
        return None


def _write(line: str, where) -> None:
    if sys.platform == "win32":
        import winreg
        with _reg(where, write=True) as k:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, line)
        return
    path = _entry(where)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_desktop_file(line), encoding="utf-8")
    os.replace(tmp, path)


def _remove(where) -> None:
    if sys.platform == "win32":
        import winreg
        try:
            with _reg(where, write=True) as k:
                winreg.DeleteValue(k, RUN_VALUE)
        except FileNotFoundError:
            pass
        return
    try:
        _entry(where).unlink()
    except FileNotFoundError:
        pass
