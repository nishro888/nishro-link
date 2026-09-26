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
    for need in ("python3-tk", "python3-evdev", "python3 (>= 3.10)"):
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
    shipped = {n.rsplit("/", 1)[1] for n in deb["data"]
               if n.startswith("./usr/lib/nishro-link/link/")}
    wanted = {p.name for p in (HERE.parent).glob("*.py")}
    assert shipped == wanted


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
    assert "\nExec=nishro-link\n" in entry and "\nIcon=nishro-link\n" in entry
    assert "./usr/bin/nishro-link" in deb["data"]
    assert "./usr/share/icons/hicolor/scalable/apps/nishro-link.svg" in deb["data"]


def test_the_launcher_is_recognised_as_this_copy_when_installed(deb):
    """autostart uses the installed launcher only if it runs the same code -
    which it tells by the library folder named in it."""
    launcher = deb["data"]["./usr/bin/nishro-link"][1].decode()
    assert "/usr/lib/nishro-link" in launcher
    assert "python3 -m link.nishro_link" in launcher
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

