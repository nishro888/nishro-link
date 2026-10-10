"""Files over the clipboard: what was copied, sent in pieces, saved safely.

Asked for: copy files, folders, or several at once on one computer (Ctrl+C) and
paste them on the other (Ctrl+V). This module is the part that needs no second
computer: it lists what was copied, cuts it into pieces, and on the other side
writes them under one folder - "Nishro Link Files" on the Desktop - checking
each file before it is given its name. The connection that carries the pieces,
and the clipboards that hold the lists, are elsewhere.

SAFE BY CONSTRUCTION on the receiving side: every name that arrives is cleaned
before it touches the disk - no absolute paths, no "..", no names Windows
cannot hold - so nothing can be written outside the folder, whatever the other
side sends. A file is written under a temporary name and only renamed once its
SHA-256 matches; a half-sent file never looks like a whole one. Nothing that
arrives is opened or run. Symbolic links and special files are not sent.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import sys
import time
from pathlib import Path

FOLDER = "Nishro Link Files"        # under the Desktop, on every computer
MAX_TOTAL = 2 * 1024 ** 3           # bytes in one copy
MAX_ENTRIES = 20000                 # files and folders in one copy
CHUNK = 48 * 1024                   # one piece; sealed and base64'd twice it stays
                                    # well under protocol.MAX_LINE
PART = ".nishro-part"               # a file being received
SPARE = 64 * 1024 * 1024            # disk space kept free beyond the copy itself

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} \
    | {f"LPT{i}" for i in range(1, 10)}


class FilesError(Exception):
    """Something the person is told about: too big, no room, a bad piece."""


# ------------------------------------------------------------------ names
def safe_name(name: str) -> str:
    """One path component, made safe on both systems.

    Characters Windows cannot hold become "_", trailing dots and spaces go
    (Windows drops them silently, so two names would collide), reserved device
    names (CON, NUL, COM1...) get a "_", and it stays within 255 characters."""
    name = _BAD.sub("_", name or "").rstrip(". ")
    if not name or name in (".", ".."):
        name = "_"
    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED:
        name = name.replace(name.split(".", 1)[0], name.split(".", 1)[0] + "_", 1)
    if len(name) > 255:
        root, ext = os.path.splitext(name)
        name = root[:255 - len(ext)] + ext
    return name


def safe_rel(rel: str) -> str:
    """A relative path from the other side, as "a/b/c", made safe: each part
    cleaned, and nothing that could climb out - "..", absolute, or a drive."""
    parts = [p for p in re.split(r"[\\/]+", rel or "") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise FilesError(f"refused a path that leaves the folder: {rel!r}")
    if re.match(r"^[A-Za-z]:", parts[0]):
        raise FilesError(f"refused an absolute path: {rel!r}")
    return "/".join(safe_name(p) for p in parts)


def unique(path: Path) -> Path:
    """`path`, or "name (2).ext", "name (3).ext" ... if it is taken."""
    if not path.exists():
        return path
    root, ext = (path.name, "") if path.is_dir() else (path.stem, path.suffix)
    n = 2
    while True:
        candidate = path.with_name(f"{root} ({n}){ext}")
        if not candidate.exists():
            return candidate
        n += 1


# --------------------------------------------------------------- sending
class Offer:
    """What was copied: the files and folders named, with everything inside.

    `items` is the manifest the other side receives - relative paths with "/",
    sizes, and which are folders - in an order where a folder comes before
    what is in it. Symbolic links and special files are left out."""

    def __init__(self, paths, max_total: int = MAX_TOTAL, max_entries: int = MAX_ENTRIES):
        self.items = []               # {"p": rel, "s": size, "d": is_dir}
        self._src = []                # the real path of each item
        self.skipped = 0
        tops = set()
        for raw in paths:
            src = Path(raw)
            if src.is_symlink() or not src.exists():
                self.skipped += 1
                continue
            top = src.name or src.anchor.strip("\\/:") or "_"
            name, n = top, 2
            while name.lower() in tops:   # two "notes.txt" from different folders
                root, ext = os.path.splitext(top)
                name, n = f"{root} ({n}){ext}", n + 1
            tops.add(name.lower())
            self._add(src, name, max_entries)
        self.total = sum(i["s"] for i in self.items)
        self.files = sum(1 for i in self.items if not i["d"])
        if self.total > max_total:
            raise FilesError(f"{_size(self.total)} is more than one copy can carry "
                             f"({_size(max_total)})")

    def _add(self, src: Path, rel: str, max_entries: int) -> None:
        if len(self.items) >= max_entries:
            raise FilesError(f"more than {max_entries} files and folders in one copy")
        if src.is_symlink():
            self.skipped += 1
        elif src.is_dir():
            self.items.append({"p": rel, "s": 0, "d": True})
            self._src.append(src)
            try:
                children = sorted(src.iterdir(), key=lambda c: c.name.lower())
            except OSError:
                children = []
                self.skipped += 1
            for child in children:
                self._add(child, rel + "/" + child.name, max_entries)
        elif src.is_file():
            self.items.append({"p": rel, "s": src.stat().st_size, "d": False})
            self._src.append(src)
        else:
            self.skipped += 1         # a device, a pipe, a socket

    def manifest(self) -> dict:
        return {"items": self.items, "total": self.total, "files": self.files}

    def pieces(self, chunk: int = None):
        """Everything to send after the manifest, in order:
        ("data", index, base64 text) for each piece of each file, then
        ("end", index, sha256 hex) once a file is complete."""
        for index, (item, src) in enumerate(zip(self.items, self._src)):
            if item["d"]:
                continue
            h = hashlib.sha256()
            chunk = chunk or CHUNK
            with open(_long(src), "rb") as f:
                while True:
                    data = f.read(chunk)
                    if not data:
                        break
                    h.update(data)
                    yield ("data", index, base64.b64encode(data).decode("ascii"))
            yield ("end", index, h.hexdigest())


# ------------------------------------------------------------- receiving
class Receive:
    """One incoming copy, written under `dest` (the Nishro Link Files folder).

    Its top-level items get names free in `dest` ("report (2).pdf" if
    "report.pdf" is there already); everything inside keeps its own. When it
    is done, `top_paths` are what goes on the clipboard."""

    def __init__(self, dest, manifest: dict, max_total: int = MAX_TOTAL,
                 max_entries: int = MAX_ENTRIES, spare: int = SPARE):
        self.dest = Path(dest)
        items = manifest.get("items") or []
        total = int(manifest.get("total") or 0)
        if total > max_total or sum(int(i.get("s") or 0) for i in items) > max_total:
            raise FilesError(f"{_size(total)} is more than one copy can carry")
        if len(items) > max_entries:
            raise FilesError(f"more than {max_entries} files and folders in one copy")
        self.dest.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(self.dest)).free
        if free < total + spare:
            raise FilesError(f"not enough room on this computer: {_size(total)} to "
                             f"receive, {_size(free)} free")
        self.items = []
        self.top_paths = []
        renamed = {}                  # a top-level name -> the free name it got
        seen = set()                  # every target, lower-cased: Windows ignores case
        for item in items:
            rel = safe_rel(str(item.get("p") or ""))
            top, _, rest = rel.partition("/")
            if top not in renamed:
                renamed[top] = unique(self.dest / top).name
                self.top_paths.append(self.dest / renamed[top])
            path = self.dest / renamed[top] / rest if rest else self.dest / renamed[top]
            path = _unseen(path, seen, bool(item.get("d")))
            self.items.append({"path": path, "size": int(item.get("s") or 0),
                               "dir": bool(item.get("d"))})
        for item in self.items:       # folders first, so empty ones arrive too
            if item["dir"]:
                Path(_long(item["path"])).mkdir(parents=True, exist_ok=True)
        self._open = {}               # index -> (file, sha256, bytes so far)
        self.received = 0
        self.done = set()

    def data(self, index: int, text: str) -> None:
        item = self._item(index)
        if index not in self._open:
            item["path"].parent.mkdir(parents=True, exist_ok=True)
            part = _long(str(item["path"]) + PART)
            self._open[index] = [open(part, "wb"), hashlib.sha256(), 0]
        f, h, n = self._open[index]
        data = base64.b64decode(text)
        if n + len(data) > item["size"]:
            self.abort()
            raise FilesError(f"{item['path'].name}: more data than its size")
        f.write(data)
        h.update(data)
        self._open[index][2] = n + len(data)
        self.received += len(data)

    def end(self, index: int, sha: str) -> None:
        item = self._item(index)
        if index not in self._open:               # an empty file
            self.data(index, "")
        f, h, n = self._open.pop(index)
        f.close()
        part = Path(_long(str(item["path"]) + PART))
        if n != item["size"] or h.hexdigest() != sha:
            part.unlink()
            self.abort()
            raise FilesError(f"{item['path'].name} arrived damaged - not kept")
        os.replace(str(part), _long(item["path"]))
        self.done.add(index)

    def complete(self) -> bool:
        return all(i["dir"] or n in self.done for n, i in enumerate(self.items))

    def abort(self) -> None:
        """Remove whatever is half-written; finished files stay."""
        for index, (f, _, _) in list(self._open.items()):
            f.close()
            try:
                os.unlink(_long(str(self.items[index]["path"]) + PART))
            except OSError:
                pass
        self._open.clear()

    def _item(self, index: int) -> dict:
        if not 0 <= index < len(self.items) or self.items[index]["dir"]:
            raise FilesError(f"a piece for an unknown file ({index})")
        return self.items[index]


# ------------------------------------------------------ over a connection
# The connection's own queue drops its OLDEST frames when full - right for
# pointer positions, fatal for a file. So the sender waits while more than
# BACKLOG pieces are queued, and nothing is ever dropped.
BACKLOG = 32


def send_over(ch, offer: Offer, seq: int, progress=None, stopped=lambda: False) -> None:
    """Send `offer` down `ch`: the manifest, the pieces, then wait for the other
    side to say it has all of it. `progress(sent_bytes, total)` is told as it goes."""
    ch.send(dict({"t": "fman", "seq": seq}, **offer.manifest()))
    sent = 0
    for kind, index, value in offer.pieces():
        while ch.backlog() > BACKLOG:
            if stopped():
                ch.send({"t": "ferr", "msg": "the copy was stopped"})
                raise FilesError("the copy was stopped")
            time.sleep(0.002)
        if kind == "data":
            ch.send({"t": "fdata", "f": index, "d": value})
            sent += len(value) * 3 // 4
            if progress:
                progress(sent, offer.total)
        else:
            ch.send({"t": "fend", "f": index, "h": value})
    ch.send({"t": "fdone", "seq": seq})
    reply = ch.recv()
    if not reply or reply.get("t") != "fok":
        raise FilesError((reply or {}).get("msg") or "the other side did not confirm the copy")


def receive_over(ch, dest, progress=None) -> list:
    """Receive one copy from `ch` into `dest`; the paths of its top-level items.
    Anything wrong - a bad name, a damaged file, a dropped connection - leaves
    nothing half-written and is said back to the sender."""
    first = ch.recv()
    if not first or first.get("t") != "fman":
        raise FilesError((first or {}).get("msg") or "the other side sent no list of files")
    receive = None
    try:
        receive = Receive(dest, first)
        while True:
            msg = ch.recv()
            if msg is None:
                raise FilesError("the connection dropped in the middle of the copy")
            t = msg.get("t")
            if t == "fdata":
                receive.data(int(msg["f"]), msg.get("d") or "")
                if progress:
                    progress(receive.received, int(first.get("total") or 0))
            elif t == "fend":
                receive.end(int(msg["f"]), str(msg.get("h") or ""))
            elif t == "fdone":
                if not receive.complete():
                    raise FilesError("the copy ended with files missing")
                ch.send({"t": "fok"})
                return receive.top_paths
            elif t == "ferr":
                raise FilesError(msg.get("msg") or "the other side stopped the copy")
    except FilesError as e:
        if receive is not None:
            receive.abort()
        ch.send({"t": "ferr", "msg": str(e)})
        raise


# ----------------------------------------------------------------- helpers
def _unseen(path: Path, seen: set, is_dir: bool) -> Path:
    """Two names the other side kept apart may clean to one ("a:b" and "a_b"),
    or differ only in case, which Windows does not tell apart: the second
    gets " (2)" rather than overwriting the first. A folder already listed is
    the same folder, not a clash."""
    key = str(path).lower()
    if is_dir and key in seen:
        return path
    root, ext = (path.name, "") if is_dir else (path.stem, path.suffix)
    n = 2
    while key in seen:
        path = path.with_name(f"{root} ({n}){ext}")
        key, n = str(path).lower(), n + 1
    seen.add(key)
    return path


def _long(path) -> str:
    """Windows refuses paths over 260 characters unless they say "\\\\?\\";
    a deep folder copied whole easily goes past that."""
    path = str(path)
    if sys.platform == "win32" and len(path) > 240 and not path.startswith("\\\\?\\"):
        path = "\\\\?\\" + os.path.abspath(path)
    return path


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "bytes" else f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
