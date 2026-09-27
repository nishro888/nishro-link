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
