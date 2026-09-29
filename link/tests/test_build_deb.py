"""The Debian package, opened back up and checked.

Built on Windows and only ever installed on Linux, so anything wrong with it -
a script without its executable bit, CRLF in a shebang, a path the program
does not look in - would otherwise surface only on the other machine.
"""
import hashlib
import importlib.util
import pathlib
import shutil

import pytest

from link import access, autostart

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "build_deb", HERE.parent / "packaging" / "build-deb.py")
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)


@pytest.fixture(scope="module")
def deb(tmp_path_factory):
    out = tmp_path_factory.mktemp("deb") / "nishro-link.deb"
    bd.build(out)
    members = dict(bd.read_ar(out.read_bytes()))
    return {"path": out, "control": bd.read_tar(members["control.tar.gz"]),
            "data": bd.read_tar(members["data.tar.gz"])}


def fields(deb):
    return dict(line.split(": ", 1) for line in
                deb["control"]["./control"][1].decode().splitlines()
                if line and not line.startswith(" "))


def test_the_package_passes_its_own_check(deb):
    assert bd.check(deb["path"]) == []


def test_ar_members_come_in_the_order_dpkg_requires(deb):
    names = [n for n, _ in bd.read_ar(deb["path"].read_bytes())]
    assert names == ["debian-binary", "control.tar.gz", "data.tar.gz"]


def test_control_names_the_dependencies_the_program_imports(deb):
    f = fields(deb)
    assert f["Package"] == "nishro-link" and f["Architecture"] == "all"
    for need in ("python3-tk", "python3-evdev", "python3 (>= 3.8)",
                 "python3-cryptography"):
        assert need in f["Depends"]
    assert f["Version"].startswith(bd.version().split("~")[0])


def test_scripts_are_executable_and_everything_else_is_not(deb):
    for name, (info, body) in {**deb["control"], **deb["data"]}.items():
        if body is None:
            assert info.mode == 0o755, name
            continue
        executable = name in ("./postinst", "./prerm", "./postrm",
                              "./usr/bin/nishro-link",
                              "./usr/lib/nishro-link/nishro-link-setup")
        assert info.mode == (0o755 if executable else 0o644), name
        assert (info.uid, info.gid) == (0, 0), name


def test_the_program_is_all_there_and_the_tests_are_not(deb):
    lib = "./usr/lib/nishro-link/link/"
    shipped = {n[len(lib):] for n in deb["data"]
               if n.startswith(lib) and "/" not in n[len(lib):]}
    wanted = {p.name for p in (HERE.parent).glob("*.py")} | {"theme"}
    assert shipped == wanted
    theme = {n[len(lib) + 6:] for n in deb["data"]
             if n.startswith(lib + "theme/")}
    assert theme == {p.name for p in (HERE.parent / "theme").iterdir()}
    png = deb["data"][lib + "theme/spritesheet_dark.png"][1]
    assert png == (HERE.parent / "theme" / "spritesheet_dark.png").read_bytes(), \
        "an image must reach the machine byte for byte"


def test_md5sums_lists_every_file(deb):
    listed = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in
              deb["control"]["./md5sums"][1].decode().splitlines()}
    files = {n[2:]: b for n, (_i, b) in deb["data"].items() if b is not None}
    assert set(listed) == set(files)
    for name, body in files.items():
        assert listed[name] == hashlib.md5(body).hexdigest()


# ---------------------------------- the paths that must agree with the code
def test_the_policy_names_the_helper_the_program_runs(deb):
    policy = deb["data"]["./usr/share/polkit-1/actions/"
                         "io.github.nishro888.nishro-link.policy"][1].decode()
    assert f'"org.freedesktop.policykit.exec.path">{access.HELPER}<' in policy
    assert "." + access.HELPER in deb["data"]


def test_the_menu_entry_starts_the_launcher(deb):
    entry = deb["data"]["./usr/share/applications/nishro-link.desktop"][1].decode()
    # A full path: an older per-user install in ~/.local/bin is earlier on
    # PATH, and a bare name would start that instead.
    assert f"\nExec={autostart.INSTALLED}\n" in entry
    assert "\nIcon=nishro-link\n" in entry
    assert "." + autostart.INSTALLED in deb["data"]
    assert "./usr/share/icons/hicolor/scalable/apps/nishro-link.svg" in deb["data"]


def test_the_launcher_is_recognised_as_this_copy_when_installed(deb):
    """autostart uses the installed launcher only if it runs the same code -
    which it tells by the library folder named in it."""
    launcher = deb["data"]["./usr/bin/nishro-link"][1].decode()
    assert "/usr/lib/nishro-link" in launcher
    assert "python3 /usr/lib/nishro-link/launch.py" in launcher
    assert " -m " not in launcher.split("exec", 1)[1], \
        "never -m: it imports from the current directory first"
    assert "./usr/lib/nishro-link/launch.py" in deb["data"]
    assert launcher.startswith("#!/bin/sh\n")
    assert "\r" not in launcher


def test_uinput_is_given_to_the_input_group_and_loaded_at_boot(deb):
    rule = deb["data"]["./usr/lib/udev/rules.d/60-nishro-link.rules"][1].decode()
    assert 'KERNEL=="uinput", GROUP="input", MODE="0660"' in rule
    assert deb["data"]["./usr/lib/modules-load.d/nishro-link.conf"][1] == b"uinput\n"


def test_the_setup_helper_only_sets_up_whoever_asked(deb):
    helper = deb["data"]["." + access.HELPER][1].decode()
    assert "PKEXEC_UID" in helper
    assert 'usermod -aG input "$user"' in helper


# --------------------------------------------------------------- failures
def test_a_crlf_script_is_caught(tmp_path, monkeypatch):
    """The failure that has shipped before, in the tarball: a shebang ending in
    CR does not run, and says only 'No such file or directory'."""
    src = tmp_path / "deb"
    shutil.copytree(bd.DEB, src)
    (src / "postinst").write_bytes(
        (src / "postinst").read_bytes().replace(b"\n", b"\r\n"))
    monkeypatch.setattr(bd, "DEB", src)
    monkeypatch.setattr(bd, "lf", lambda data: data)
    out = bd.build(tmp_path / "broken.deb")
    assert "./postinst: has CRLF line endings" in bd.check(out)


def test_the_same_tree_builds_the_same_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    a = bd.build(tmp_path / "a.deb").read_bytes()
    b = bd.build(tmp_path / "b.deb").read_bytes()
    assert a == b



def test_the_dock_can_match_the_window_to_the_menu_entry(deb):
    """Tk capitalises the first letter of the class it is given; the menu
    entry's StartupWMClass must be that, or the dock shows a nameless app."""
    from link import ui_tk
    entry = deb["data"]["./usr/share/applications/nishro-link.desktop"][1].decode()
    wm_class = ui_tk.WM_CLASS[:1].upper() + ui_tk.WM_CLASS[1:]
    assert f"\nStartupWMClass={wm_class}\n" in entry


def test_xclip_is_required_not_wl_clipboard(deb):
    """On GNOME, wl-clipboard flashes a window in the dock at every read - and
    a Recommends is not installed when an existing package is upgraded."""
    f = fields(deb)
    assert "xclip" in f["Depends"] and "wl-clipboard" not in f["Depends"]


def test_the_service_starts_at_boot_and_the_window_attaches(deb):
    """The engine runs from boot as a system service - the login and lock
    screens need it - and the menu entry only opens a window onto it."""
    unit = deb["data"]["./usr/lib/systemd/system/nishro-link.service"][1].decode()
    assert "ExecStart=/usr/bin/nishro-link --service" in unit
    assert "WantedBy=multi-user.target" in unit and "Restart=always" in unit
    post = deb["control"]["./postinst"][1].decode()
    assert "systemctl enable nishro-link.service" in post
    assert "/var/lib/nishro-link/config.json" in post, "the pairing carried across"
    assert "PKEXEC_UID" in post
    pre = deb["control"]["./prerm"][1].decode()
    assert "systemctl disable nishro-link.service" in pre


def test_the_app_center_has_a_page_and_every_icon_size(deb):
    """A normal app: a proper page when the .deb is opened, and a sharp icon
    in every menu and dock."""
    meta = deb["data"]["./usr/share/metainfo/io.github.nishro888.nishro-link.metainfo.xml"]
    text = meta[1].decode()
    assert "<name>Nishro Link</name>" in text
    assert '<launchable type="desktop-id">nishro-link.desktop</launchable>' in text
    for n in bd.ICON_SIZES:
        png = deb["data"][f"./usr/share/icons/hicolor/{n}x{n}/apps/nishro-link.png"][1]
        assert png[:8] == b"\x89PNG\r\n\x1a\n", n


def test_the_version_carries_the_stage_from_one_place():
    import link
    stage = f"~{link.__stage__}" if link.__stage__ else ""
    assert bd.version() == link.__version__ + stage


def test_the_description_says_the_link_is_encrypted():
    assert "not encrypted" not in bd.DESCRIPTION
    assert "encrypted" in bd.DESCRIPTION


def test_a_link_folder_in_the_current_directory_cannot_take_over(tmp_path):
    """Reported: the window did not open from the app menu. The menu starts
    programs in the home folder, the launcher ran `python3 -m`, which imports
    from the current directory first - and ~/link held version 0.9 from an
    unpacked source tree, which ran instead of the installed program.
    launch.py runs as a script: its own folder comes first."""
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent
    lib = tmp_path / "lib"
    shutil.copytree(src, lib / "link", ignore=shutil.ignore_patterns(
        "tests", "packaging", "__pycache__"))
    shutil.copy(src / "packaging" / "launch.py", lib / "launch.py")
    home = tmp_path / "home"
    (home / "link").mkdir(parents=True)
    (home / "link" / "__init__.py").write_text(
        'raise SystemExit("imported the wrong link, from the current directory")')
    env = {k: v for k, v in __import__("os").environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, str(lib / "launch.py"), "--help"],
                       cwd=home, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "usage" in r.stdout.lower() and "wrong link" not in r.stderr
