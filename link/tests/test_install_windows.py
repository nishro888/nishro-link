"""The Windows installer's pieces that can be checked anywhere: the service's
command line, the icon, and the setup script's shape."""
import pathlib
import subprocess

from link import icon

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_the_service_command_line_survives_the_quoting():
    """sc.exe reads binPath as one argument with the exe quoted inside it; a
    path with spaces - Program Files - is exactly where that breaks."""
    exe = r"C:\Program Files\Nishro Link\NishroLink.exe"
    line = subprocess.list2cmdline(["sc", "create", "NishroLink", "binPath=",
                                    f'"{exe}" --service', "start=", "auto"])
    assert r'binPath= "\"C:\Program Files\Nishro Link\NishroLink.exe\" --service"' \
        in line


def test_the_window_has_its_own_icon():
    import base64
    png = base64.b64decode(icon.PNG_64)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_exe_and_the_installer_get_the_icon():
    ico = ROOT / "link" / "packaging" / "assets" / "nishro-link.ico"
    assert ico.read_bytes()[:4] == b"\x00\x00\x01\x00", "an .ico"
    build = (ROOT / "link" / "packaging" / "build-windows.ps1").read_text(
        encoding="utf-8")
    assert "--icon" in build and "--version-file" in build and "ISCC" in build
    iss = (ROOT / "link" / "packaging" / "windows" / "NishroLink.iss").read_text(
        encoding="utf-8")
    assert r"SetupIconFile=..\assets\nishro-link.ico" in iss
    assert "PrivilegesRequired=admin" in iss
    assert "--install-service" in iss and "--uninstall-service" in iss


def test_no_line_of_the_setup_script_starts_with_a_hash():
    """Inno's preprocessor takes any such line as a directive and refuses to
    build - it did, over a line-break code."""
    iss = (ROOT / "link" / "packaging" / "windows" / "NishroLink.iss").read_text(
        encoding="utf-8")
    bad = [l for l in iss.splitlines()
           if l.lstrip().startswith("#") and not l.lstrip().startswith(
               ("#ifndef", "#define", "#endif"))]
    assert bad == []


def test_stopping_the_others_spares_this_process_and_its_launcher(monkeypatch):
    """When the exe was one file, it ran as a launcher and the program it
    unpacked, both named NishroLink.exe. The setup wizard waits on the
    launcher. Killing it made the wizard report "the background service did
    not start" every time, while the program went on and started it. (The
    build is a folder now; whatever started this process is still spared.)"""
    import os
    from link import wininstall
    ran = []
    monkeypatch.setattr(wininstall, "state", lambda: "")
    monkeypatch.setattr(wininstall, "_run", lambda args, log, **kw: ran.append(args))
    monkeypatch.setattr(wininstall.time, "sleep", lambda s: None)
    wininstall._stop_everything(lambda line: None)
    kill = next(a for a in ran if a[0] == "taskkill")
    spared = {kill[i + 1] for i, a in enumerate(kill) if a == "/FI"}
    assert spared == {f"PID ne {os.getpid()}", f"PID ne {os.getppid()}"}


def test_the_ico_has_every_size_windows_asks_for():
    """16 for a title bar, 20-40 for the taskbar and shortcuts as the display
    scale goes from 100% to 250%, 48 for Explorer, 256 for large icons. A
    missing size is scaled from another by Windows - which is the blur."""
    import struct
    data = (ROOT / "link" / "packaging" / "assets" / "nishro-link.ico").read_bytes()
    _, kind, count = struct.unpack_from("<HHH", data, 0)
    assert kind == 1
    sizes = {data[6 + 16 * i] or 256 for i in range(count)}
    assert {16, 20, 24, 32, 40, 48, 64, 256} <= sizes


def test_the_window_gets_an_icon_drawn_for_each_size():
    import base64
    import struct
    for data in icon.SIZES:
        png = base64.b64decode(data)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
    widths = [struct.unpack(">I", base64.b64decode(d)[16:20])[0] for d in icon.SIZES]
    assert widths == [16, 24, 32, 48, 64]


def test_the_vector_icon_is_well_formed():
    import xml.etree.ElementTree as ET
    root = ET.parse(ROOT / "link" / "packaging" / "deb" / "nishro-link.svg").getroot()
    assert root.tag.endswith("svg") and root.get("viewBox") == "0 0 256 256"
