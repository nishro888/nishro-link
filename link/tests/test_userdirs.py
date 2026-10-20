"""Where files from another computer land: the Desktop of the person at the
screen (userdirs). Windows is asked for real, as the person running the
tests; the service's side of Linux - root, someone else's session - with
stand-ins, on any system."""
import os
import subprocess
import sys
import types

import pytest

from link import files, session, userdirs


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_on_windows_it_is_the_desktop_the_shell_says():
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                        "[Environment]::GetFolderPath('Desktop')"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    assert userdirs.desktop() == r.stdout.strip()


def test_the_folder_is_made_on_the_desktop(monkeypatch, tmp_path):
    monkeypatch.setattr(userdirs, "desktop", lambda: str(tmp_path / "Desktop"))
    folder = userdirs.files_folder()
    assert folder == str(tmp_path / "Desktop" / files.FOLDER)
    assert os.path.isdir(folder)


def test_with_nobody_signed_in_nothing_is_received(monkeypatch):
    monkeypatch.setattr(userdirs, "desktop", lambda: None)
    with pytest.raises(files.FilesError, match="nobody is signed in"):
        userdirs.files_folder()


# ----------------------------------------------- Linux, as the service
@pytest.fixture
def as_root(monkeypatch):
    """The service: root, with a person's session in front of the screen."""
    monkeypatch.setattr(session, "running_as_root", lambda: True)
    monkeypatch.setattr(session, "active", lambda: {"user": "person", "uid": 1000,
                                                     "type": "wayland", "env": {}})
    fake_pwd = types.SimpleNamespace(getpwnam=lambda name: types.SimpleNamespace(
        pw_dir="/home/" + name, pw_gid=1001))
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
    ran = []

    def run(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "/home/person/Schreibtisch\n", "")
    monkeypatch.setattr(session, "run", run)
    return ran


def test_as_the_service_it_is_the_persons_desktop_by_its_own_name(as_root):
    assert userdirs._linux_desktop() == "/home/person/Schreibtisch"
    assert as_root == [["env", "HOME=/home/person", "xdg-user-dir", "DESKTOP"]], \
        "asked as root, xdg-user-dir names root's folder"


def test_without_xdg_user_dir_it_is_desktop_in_their_home(as_root, monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError("xdg-user-dir")
    monkeypatch.setattr(session, "run", missing)
    assert userdirs._linux_desktop() == "/home/person/Desktop"


def test_as_the_service_with_nobody_signed_in_there_is_no_desktop(as_root, monkeypatch):
    monkeypatch.setattr(session, "active", lambda: None)
    assert userdirs._linux_desktop() is None


def test_what_arrives_is_given_to_the_person(as_root, monkeypatch, tmp_path):
    (tmp_path / "Album" / "inner").mkdir(parents=True)
    (tmp_path / "Album" / "inner" / "photo.jpg").write_bytes(b"x")
    (tmp_path / "letter.docx").write_bytes(b"y")
    given = {}
    monkeypatch.setattr(os, "chown", lambda p, uid, gid, follow_symlinks=True:
                        given.__setitem__(os.path.relpath(p, tmp_path), (uid, gid, follow_symlinks)),
                        raising=False)
    userdirs.give_to_person([tmp_path / "Album", tmp_path / "letter.docx"])
    assert sorted(given) == sorted(["Album", os.path.join("Album", "inner"),
                                    os.path.join("Album", "inner", "photo.jpg"),
                                    "letter.docx"])
    assert set(given.values()) == {(1000, 1001, False)}


def test_the_folders_the_service_makes_are_the_persons(as_root, monkeypatch, tmp_path):
    monkeypatch.setattr(userdirs, "desktop", lambda: str(tmp_path / "Desktop"))
    given = []
    monkeypatch.setattr(os, "chown", lambda p, uid, gid, follow_symlinks=True:
                        given.append(os.path.relpath(p, tmp_path)), raising=False)
    userdirs.files_folder()
    assert sorted(given) == sorted([os.path.join("Desktop", files.FOLDER), "Desktop"])


def test_run_as_the_person_nothing_changes_hands(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "running_as_root", lambda: False)
    given = []
    monkeypatch.setattr(os, "chown", lambda *a, **kw: given.append(a), raising=False)
    (tmp_path / "a.txt").write_bytes(b"z")
    userdirs.give_to_person([tmp_path / "a.txt"])
    assert given == []
