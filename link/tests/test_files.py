"""Files over the clipboard: the lists, the pieces, and the safe saving.

Asked for: copy files, folders, or several at once on one computer and paste
them on the other. These tests cover the part that needs no second computer;
every one works in temporary folders.
"""
import base64
import os
import sys
from pathlib import Path

import pytest

from link import files
from link.files import FilesError, Offer, Receive, safe_name, safe_rel


def tree(root: Path) -> dict:
    """{relative path: bytes, or None for a folder}, with "/" separators."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = None if p.is_dir() else p.read_bytes()
    return out


def send(offer: Offer, receive: Receive) -> None:
    for kind, index, value in offer.pieces(chunk=7):    # tiny pieces: many of them
        (receive.data if kind == "data" else receive.end)(index, value)


@pytest.fixture
def src(tmp_path):
    s = tmp_path / "src"
    (s / "Photos" / "2026" / "empty").mkdir(parents=True)
    (s / "Photos" / "2026" / "beach.jpg").write_bytes(os.urandom(5000))
    (s / "Photos" / "notes.txt").write_text("trip notes")
    (s / "report.pdf").write_bytes(b"%PDF-1.7 " + os.urandom(300))
    (s / "empty.txt").write_bytes(b"")
    return s


@pytest.fixture
def dest(tmp_path):
    return tmp_path / "Desktop" / files.FOLDER


# --------------------------------------------------------------- the copy
def test_a_folder_and_files_arrive_whole_with_their_structure(src, dest):
    offer = Offer([src / "Photos", src / "report.pdf", src / "empty.txt"])
    receive = Receive(dest, offer.manifest())
    send(offer, receive)
    assert receive.complete()
    assert tree(dest) == {
        "Photos": None, "Photos/2026": None, "Photos/2026/beach.jpg":
            (src / "Photos/2026/beach.jpg").read_bytes(),
        "Photos/2026/empty": None, "Photos/notes.txt": b"trip notes",
        "empty.txt": b"", "report.pdf": (src / "report.pdf").read_bytes()}
    assert receive.top_paths == [dest / "Photos", dest / "report.pdf", dest / "empty.txt"]


def test_the_manifest_says_what_is_coming(src):
    m = Offer([src / "Photos", src / "report.pdf"]).manifest()
    assert m["files"] == 3
    assert m["total"] == 5000 + len(b"trip notes") + (src / "report.pdf").stat().st_size
    assert [i["p"] for i in m["items"]][:2] == ["Photos", "Photos/2026"], "folders first"


def test_two_items_with_one_name_both_arrive(tmp_path, dest):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "notes.txt").write_text("from a")
    (b / "notes.txt").write_text("from b")
    offer = Offer([a / "notes.txt", b / "notes.txt"])
    receive = Receive(dest, offer.manifest())
    send(offer, receive)
    assert tree(dest) == {"notes (2).txt": b"from b", "notes.txt": b"from a"}


def test_what_is_already_there_is_kept(src, dest):
    dest.mkdir(parents=True)
    (dest / "report.pdf").write_bytes(b"older")
    (dest / "Photos").mkdir()
    offer = Offer([src / "report.pdf", src / "Photos"])
    receive = Receive(dest, offer.manifest())
    send(offer, receive)
    assert (dest / "report.pdf").read_bytes() == b"older"
    assert (dest / "report (2).pdf").read_bytes() == (src / "report.pdf").read_bytes()
    assert (dest / "Photos (2)" / "notes.txt").read_text() == "trip notes"
    assert receive.top_paths == [dest / "report (2).pdf", dest / "Photos (2)"]


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlink rights on Windows")
def test_symbolic_links_are_not_followed(tmp_path, dest):
    (tmp_path / "secret").write_text("outside")
    d = tmp_path / "d"
    d.mkdir()
    (d / "real.txt").write_text("in")
    os.symlink(tmp_path / "secret", d / "link")
    offer = Offer([d])
    assert [i["p"] for i in offer.items] == ["d", "d/real.txt"]
    assert offer.skipped == 1


# ------------------------------------------------------------- the limits
def test_a_copy_over_the_limit_is_refused_before_anything_moves(src, dest):
    with pytest.raises(FilesError, match="more than one copy can carry"):
        Offer([src], max_total=1000)
    with pytest.raises(FilesError):
        Receive(dest, {"items": [{"p": "big.iso", "s": 3 * 1024 ** 3}], "total": 3 * 1024 ** 3})
    assert not dest.exists() or not any(dest.iterdir())


def test_too_many_entries_is_refused(src):
    with pytest.raises(FilesError, match="more than 3"):
        Offer([src], max_entries=3)


def test_no_room_is_said_before_anything_is_written(src, dest, monkeypatch):
    import shutil
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(10 ** 9, 10 ** 9 - 1000, 1000))
    offer = Offer([src / "Photos"])
    with pytest.raises(FilesError, match="not enough room"):
        Receive(dest, offer.manifest())


# ------------------------------------------------- nothing outside the folder
@pytest.mark.parametrize("bad", ["../escape.txt", "a/../../escape.txt", "..",
                                 "C:/Windows/evil.dll", "c:\\evil.dll", "", "/"])
def test_names_that_would_leave_the_folder_are_refused(bad, dest):
    with pytest.raises(FilesError):
        Receive(dest, {"items": [{"p": bad, "s": 1}], "total": 1})


def test_an_absolute_unix_path_stays_inside(dest):
    r = Receive(dest, {"items": [{"p": "/etc/passwd", "s": 2}], "total": 2})
    assert r.items[0]["path"] == dest / "etc" / "passwd"


@pytest.mark.parametrize("raw, clean", [
    ("a:b?.txt", "a_b_.txt"), ('quote"d|pipe*.txt', "quote_d_pipe_.txt"),
    ("CON.txt", "CON_.txt"), ("nul", "nul_"), ("com1.log", "com1_.log"),
    ("trailing. . ", "trailing"), ("tab\there", "tab_here"), ("ok name é.txt", "ok name é.txt"),
    ("...", "_"),
])
def test_names_windows_cannot_hold_are_made_safe(raw, clean):
    assert safe_name(raw) == clean


def test_a_very_long_name_keeps_its_extension():
    long = "x" * 300 + ".jpeg"
    assert safe_name(long) == "x" * 250 + ".jpeg"


def test_names_that_clean_to_one_both_survive(dest):
    """From Linux: "a:b" and "a_b" are two files; so are "A.txt" and "a.txt"."""
    items = [{"p": "d", "s": 0, "d": True}, {"p": "d/a:b", "s": 1}, {"p": "d/a_b", "s": 1},
             {"p": "d/A.txt", "s": 1}, {"p": "d/a.txt", "s": 1}]
    r = Receive(dest, {"items": items, "total": 4})
    names = [i["path"].name for i in r.items[1:]]
    assert len({n.lower() for n in names}) == 4, names


def test_safe_rel_joins_with_slashes():
    assert safe_rel("a\\b/c") == "a/b/c"
    assert safe_rel("./a//b") == "a/b"


# ----------------------------------------------------- damage and leftovers
def test_a_damaged_file_is_not_kept(src, dest):
    offer = Offer([src / "report.pdf"])
    receive = Receive(dest, offer.manifest())
    for kind, index, value in offer.pieces():
        if kind == "data":
            data = bytearray(base64.b64decode(value))
            data[0] ^= 0xFF
            receive.data(index, base64.b64encode(bytes(data)).decode())
        else:
            with pytest.raises(FilesError, match="damaged"):
                receive.end(index, value)
    assert tree(dest) == {}


def test_a_half_received_file_never_has_its_real_name(src, dest):
    offer = Offer([src / "report.pdf"])
    receive = Receive(dest, offer.manifest())
    kind, index, value = next(offer.pieces(chunk=10))
    receive.data(index, value)
    assert not (dest / "report.pdf").exists()
    receive.abort()
    assert tree(dest) == {}, "an abandoned copy leaves nothing half-written"


def test_more_data_than_announced_is_refused(dest):
    r = Receive(dest, {"items": [{"p": "a.txt", "s": 2}], "total": 2})
    with pytest.raises(FilesError, match="more data than its size"):
        r.data(0, base64.b64encode(b"too long").decode())
    assert tree(dest) == {}


def test_a_piece_for_no_file_is_refused(dest):
    r = Receive(dest, {"items": [{"p": "d", "s": 0, "d": True}], "total": 0})
    for index in (0, 5, -1):
        with pytest.raises(FilesError, match="unknown file"):
            r.data(index, "")


def test_the_pieces_fit_on_the_link():
    """Each piece is sealed and base64'd again on the way: it must stay under
    protocol.MAX_LINE, or the other side drops the connection."""
    from link import protocol
    piece = len(base64.b64encode(b"x" * files.CHUNK))
    sealed = (piece + 200) * 4 // 3 + 100
    assert sealed < protocol.MAX_LINE
