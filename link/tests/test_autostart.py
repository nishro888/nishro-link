"""The "Start when I log in" switch: what it stores, where, and that it notices
when what it stores no longer starts this copy."""
import sys

import pytest

from link import autostart


@pytest.fixture
def linux(monkeypatch, tmp_path):
    # tmp_path first: pytest makes it with os.getuid() on anything that is not
    # win32, and a Windows os has no getuid.
    monkeypatch.setattr(sys, "platform", "linux")


exec_split = autostart._exec_split

# ------------------------------------------------------------------ Linux
def test_on_writes_an_autostart_entry_and_off_removes_it(linux, tmp_path):
    cmd = ["/usr/bin/nishro-link", "--background"]
    autostart.switch(True, cmd, where=tmp_path)
    text = (tmp_path / "nishro-link.desktop").read_text()
    assert "Exec=/usr/bin/nishro-link --background\n" in text
    assert "Type=Application" in text and "Terminal=false" in text
    assert autostart.state(cmd, where=tmp_path) == {
        "available": True, "on": True, "current": True}

    autostart.switch(False, where=tmp_path)
    assert not (tmp_path / "nishro-link.desktop").exists()
    assert autostart.state(cmd, where=tmp_path)["on"] is False
    autostart.switch(False, where=tmp_path)      # already off: not an error


def test_a_stale_entry_is_on_but_not_current(linux, tmp_path):
    """A new download in a different folder: the login would start the old one."""
    autostart.switch(True, ["/old/place/nishro-link", "--background"], where=tmp_path)
    s = autostart.state(["/new/place/nishro-link", "--background"], where=tmp_path)
    assert s["on"] and not s["current"]


@pytest.mark.parametrize("line", ["Hidden=true", "X-GNOME-Autostart-enabled=false"])
def test_an_entry_switched_off_by_the_desktop_counts_as_off(linux, tmp_path, line):
    """Desktops switch an entry off by editing it, not deleting it."""
    autostart.switch(True, ["x"], where=tmp_path)
    f = tmp_path / "nishro-link.desktop"
    f.write_text(f.read_text().replace("X-GNOME-Autostart-enabled=true", line))
    assert autostart.state(["x"], where=tmp_path)["on"] is False


@pytest.mark.parametrize("arg", [
    "plain", "/path with spaces/python3", "PYTHONPATH=/a b", 'say "hi"',
    "cost $5", "back\\slash", "100%", "tick`"])
def test_exec_arguments_survive_the_desktop_entry_quoting(arg):
    assert exec_split(autostart._exec_arg(arg)) == [arg]
    assert exec_split(" ".join(autostart._exec_arg(a) for a in [arg, "x y"])) == \
        [arg, "x y"]


def test_a_real_command_round_trips(linux, tmp_path):
    cmd = ["env", "PYTHONPATH=/home/a b/nishro-link", "/usr/bin/python3", "-m",
           "link.nishro_link", "--background", "--config", "/home/a b/c.json"]
    autostart.switch(True, cmd, where=tmp_path)
    line = autostart._read(tmp_path)
    assert exec_split(line) == cmd


def test_the_default_location_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert autostart.autostart_dir() == tmp_path / "autostart"


# --------------------------------------------------------------- commands
def test_always_starts_in_the_background():
    assert autostart.BACKGROUND in autostart.command()


def test_the_installed_launcher_is_used_when_it_is_this_copy(linux, monkeypatch,
                                                              tmp_path):
    root = str(tmp_path)
    monkeypatch.setattr(autostart.pathlib.Path, "resolve",
                        lambda self: tmp_path / "link" / "autostart.py")
    launcher = tmp_path / "nishro-link"
    launcher.write_text(f'#!/bin/sh\nPYTHONPATH="{root}"\n')
    monkeypatch.setattr(autostart.shutil, "which", lambda name: str(launcher))
    monkeypatch.setattr(autostart, "INSTALLED", str(tmp_path / "not-there"))
    assert autostart.command() == [str(launcher), "--background"]

    launcher.write_text('#!/bin/sh\nexec python3 /usr/lib/nishro-link/launch.py\n')
    cmd = autostart.command()
    assert cmd[1] == "-c" and repr(root) in cmd[2], \
        "a copy run from elsewhere must not start the installed one at login"
    assert "-m" not in cmd, "never -m: it imports from the current directory"
    assert cmd[-1] == "--background"


def test_extra_arguments_are_kept():
    assert autostart.command(["--config", "/c.json"])[-3:] == [
        "--background", "--config", "/c.json"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_starts_without_a_console_and_imports_from_the_repo():
    cmd = autostart.command()
    assert cmd[0].lower().endswith("pythonw.exe")
    assert cmd[1] == "-c" and "link.nishro_link" in cmd[2]


# ---------------------------------------------------------------- Windows
@pytest.fixture
def run_key():
    """A throwaway key standing in for ...\\CurrentVersion\\Run."""
    import winreg
    key = r"Software\NishroLinkTest\Run"
    yield key
    for sub in (key, r"Software\NishroLinkTest"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_uses_a_run_value_under_the_user(run_key):
    import winreg
    cmd = [r"C:\Program Files\Nishro Link\NishroLink.exe", "--background"]
    autostart.switch(True, cmd, where=run_key)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as k:
        value, _ = winreg.QueryValueEx(k, "NishroLink")
    assert value == r'"C:\Program Files\Nishro Link\NishroLink.exe" --background'
    assert autostart.state(cmd, where=run_key) == {
        "available": True, "on": True, "current": True}
    assert not autostart.state(["other.exe"], where=run_key)["current"]
    autostart.switch(False, where=run_key)
    assert autostart.state(cmd, where=run_key)["on"] is False
    autostart.switch(False, where=run_key)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_the_installers_own_entry_is_recognised(run_key):
    """install-windows.ps1 -Autostart writes the same value, with the path
    quoted even without spaces. That is this copy, not a different one."""
    import winreg
    exe = r"C:\Users\a\AppData\Local\Programs\NishroLink\NishroLink.exe"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key) as k:
        winreg.SetValueEx(k, "NishroLink", 0, winreg.REG_SZ,
                          f'"{exe}" --background')
    s = autostart.state([exe.lower(), "--background"], where=run_key)
    assert s["on"] and s["current"]


def test_unsupported_systems_say_so(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert autostart.state() == {"available": False, "on": False, "current": False}
    with pytest.raises(OSError):
        autostart.switch(True)


def test_the_package_launcher_wins_over_an_older_one_on_path(linux, monkeypatch,
                                                             tmp_path):
    """An earlier per-user install sits in ~/.local/bin, ahead of /usr/bin."""
    monkeypatch.setattr(autostart.pathlib.Path, "resolve",
                        lambda self: tmp_path / "lib" / "link" / "autostart.py")
    package = tmp_path / "usr-bin-nishro-link"
    package.write_text(f'#!/bin/sh\nPYTHONPATH="{tmp_path / "lib"}"\n')
    old = tmp_path / "local-bin-nishro-link"
    old.write_text('#!/bin/sh\nexec python3 ~/.local/share/nishro-link/...\n')
    monkeypatch.setattr(autostart, "INSTALLED", str(package))
    monkeypatch.setattr(autostart.shutil, "which", lambda name: str(old))
    assert autostart.command()[0] == str(package)
