"""Files on the Windows clipboard, as Explorer puts them there (clip_win).

Against the real clipboard, so each test keeps whatever text was on it and
puts it back. Checked both ways against Windows' own reader and writer - the
shell's FileDropList, as PowerShell's Get-Clipboard and Set-Clipboard use it
- because a list only Nishro Link can read back would paste nowhere.

Skipped off Windows.
"""
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

if sys.platform == "win32":
    from link import clip_win


@pytest.fixture(autouse=True)
def clipboard_kept():
    was = clip_win.get_text() if sys.platform == "win32" else None
    yield
    if was:
        clip_win.set_text(was)


@pytest.fixture
def two_files(tmp_path):
    a = tmp_path / "report one.txt"
    b = tmp_path / "নথি.txt"                     # not every name is ASCII
    a.write_text("one", encoding="utf-8")
    b.write_text("two", encoding="utf-8")
    return [str(a), str(b)]


def _ps(command):
    return subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", command],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=60)


def test_files_put_on_the_clipboard_read_back_exactly(two_files):
    assert clip_win.set_files(two_files)
    assert clip_win.get_files() == two_files


def test_a_paste_of_them_copies_and_never_moves(two_files):
    """Files that came from another computer are never moved away by a paste
    here: Preferred DropEffect says COPY."""
    clip_win.set_files(two_files)
    assert clip_win.drop_effect() == clip_win.DROPEFFECT_COPY


def test_windows_own_reader_sees_the_same_files(two_files):
    clip_win.set_files(two_files)
    r = _ps("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            "(Get-Clipboard -Format FileDropList) | ForEach-Object { $_.FullName }")
    assert r.returncode == 0, r.stderr
    assert [line.strip() for line in r.stdout.splitlines() if line.strip()] == two_files


def test_files_put_there_the_shells_way_are_read(two_files):
    quoted = ",".join("'" + p.replace("'", "''") + "'" for p in two_files)
    r = _ps(f"Set-Clipboard -Path {quoted}")
    assert r.returncode == 0, r.stderr
    assert sorted(clip_win.get_files()) == sorted(two_files)


def test_text_on_the_clipboard_is_not_taken_for_files():
    clip_win.set_text("just words")
    assert clip_win.get_files() == []
