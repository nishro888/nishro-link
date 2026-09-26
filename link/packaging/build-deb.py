"""Build the Debian/Ubuntu package.

    python link/packaging/build-deb.py [output.deb]

Run from the repository root. Writes dist/nishro-link_<version>_all.deb by
default. Pure Python, no dpkg-deb: the release is built on Windows.

Installed with `sudo apt install ./nishro-link_..._all.deb`, or by opening the
file in the App Center. It pulls in its own dependencies (python3-tk,
python3-evdev), adds an app-menu entry, and sets up keyboard and mouse access -
at install time when it can tell who is installing, otherwise from a button in
the program's window behind the desktop's own password prompt.

A .deb is an `ar` archive of three members, in this order:
    debian-binary    "2.0\\n"
    control.tar.gz   control, md5sums, and the install and removal scripts
    data.tar.gz      the files, as they land under /

make-tarball.py's two traps apply here too, and are handled the same way.
Modes come from each file's role, because Windows has no executable bit. Every
text file is written with LF line endings, because a shebang ending in CR does
not run. check() then opens the finished package and verifies both, with the
rest of its structure - a package that installs wrong is only found out on the
other machine.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import os
import pathlib
import sys
import tarfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository
LINK = ROOT / "link"
DEB = pathlib.Path(__file__).resolve().parent / "deb"

PACKAGE = "nishro-link"
STAGE = "~beta"          # sorts before the final 0.9.0; drop it for a release
MAINTAINER = "nishro888 <nishro888@users.noreply.github.com>"
HOMEPAGE = "https://github.com/nishro888/nishro-link"
LIB = "usr/lib/nishro-link"

CRLF = bytes((13, 10))
LF = bytes((10,))

# (source in packaging/deb, where it is installed, mode)
FILES = (
    ("nishro-link", "usr/bin/nishro-link", 0o755),
    ("nishro-link-setup", f"{LIB}/nishro-link-setup", 0o755),
    ("nishro-link.desktop", "usr/share/applications/nishro-link.desktop", 0o644),
    ("nishro-link.svg", "usr/share/icons/hicolor/scalable/apps/nishro-link.svg",
     0o644),
    ("io.github.nishro888.nishro-link.policy",
     "usr/share/polkit-1/actions/io.github.nishro888.nishro-link.policy", 0o644),
    ("60-nishro-link.rules", "usr/lib/udev/rules.d/60-nishro-link.rules", 0o644),
)
SCRIPTS = ("postinst", "prerm", "postrm")

DESCRIPTION = """\
one mouse and keyboard across several computers
 Nishro Link shares one mouse and keyboard between computers on the same
 network: push the pointer off the edge of one screen and it carries on onto
 the next machine. Works between Windows and Linux (X11 and Wayland), with a
 drag-and-drop screen arrangement and pairing by device name.
 .
 The link is authenticated but not encrypted: use it on a network you trust."""


def version() -> str:
    text = (LINK / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'") + STAGE
    raise SystemExit("link/__init__.py has no __version__")


def epoch() -> int:
    """The timestamp every entry gets. SOURCE_DATE_EPOCH, when set, makes two
    builds of the same tree byte-identical."""
    return int(os.environ.get("SOURCE_DATE_EPOCH") or time.time())


def lf(data: bytes) -> bytes:
    return data.replace(CRLF, LF)


# ------------------------------------------------------------ what goes in
def payload(ver: str) -> dict:
    """{installed path: (bytes, mode)} - everything data.tar.gz holds."""
    files = {}
    for src, dst, mode in FILES:
        files[dst] = (lf((DEB / src).read_bytes()), mode)
    for p in sorted(LINK.glob("*.py")):          # top level only: no tests
        files[f"{LIB}/link/{p.name}"] = (lf(p.read_bytes()), 0o644)
    files["usr/lib/modules-load.d/nishro-link.conf"] = (b"uinput\n", 0o644)
    doc = f"usr/share/doc/{PACKAGE}"
    files[f"{doc}/copyright"] = (copyright_file(), 0o644)
    files[f"{doc}/changelog.Debian.gz"] = (changelog(ver), 0o644)
    return files


def copyright_file() -> bytes:
    text = (ROOT / "LICENSE").read_text(encoding="utf-8").replace("\r\n", "\n")
    body = text[text.index("Permission is hereby granted"):].strip()
    lic = "\n".join(" " + line if line.strip() else " ." for line in
                    body.splitlines())
    return (f"Format: https://www.debian.org/doc/packaging-manuals/"
            f"copyright-format/1.0/\n"
            f"Upstream-Name: {PACKAGE}\n"
            f"Source: {HOMEPAGE}\n\n"
            f"Files: *\n"
            f"Copyright: 2026 nishro888\n"
            f"License: Expat\n{lic}\n").encode()


def changelog(ver: str) -> bytes:
    when = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(epoch()))
    text = (f"{PACKAGE} ({ver}) unstable; urgency=medium\n\n"
            f"  * Packaged release. See {HOMEPAGE}/releases\n\n"
            f" -- {MAINTAINER}  {when}\n")
    return _gzip(text.encode())


def control(ver: str, files: dict) -> bytes:
    size = sum(len(data) for data, _mode in files.values())
    return (f"Package: {PACKAGE}\n"
            f"Version: {ver}\n"
            f"Architecture: all\n"
            f"Maintainer: {MAINTAINER}\n"
            f"Installed-Size: {(size + 1023) // 1024}\n"
            f"Depends: python3 (>= 3.10), python3-tk, python3-evdev\n"
            f"Recommends: pkexec | policykit-1, x11-xserver-utils, "
            f"wl-clipboard | xclip\n"
            f"Section: utils\n"
            f"Priority: optional\n"
            f"Homepage: {HOMEPAGE}\n"
            f"Description: {DESCRIPTION}\n").encode()


def md5sums(files: dict) -> bytes:
    return "".join(f"{hashlib.md5(data).hexdigest()}  {path}\n"
                   for path, (data, _mode) in sorted(files.items())).encode()


# ------------------------------------------------------------- the formats
def _gzip(data: bytes) -> bytes:
    out = io.BytesIO()
    # No file name and a fixed time in the header, for the same reproducibility.
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=epoch()) as gz:
        gz.write(data)
    return out.getvalue()


def _tar(files: dict) -> bytes:
    """A gzipped tar of {path: (bytes, mode)}, with every parent directory
    listed before its contents, as dpkg-deb itself writes them."""
    dirs = {"."}
    for path in files:
        parts = path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("./" + "/".join(parts[:i]))
    raw = io.BytesIO()
    stamp = epoch()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for d in sorted(dirs):
            info = tarfile.TarInfo("./" if d == "." else d + "/")
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, stamp
            _owned_by_root(info)
            tar.addfile(info)
        for path, (data, mode) in sorted(files.items()):
            info = tarfile.TarInfo("./" + path)
            info.size, info.mode, info.mtime = len(data), mode, stamp
            _owned_by_root(info)
            tar.addfile(info, io.BytesIO(data))
    return _gzip(raw.getvalue())


def _owned_by_root(info: tarfile.TarInfo) -> None:
    info.uid = info.gid = 0
    info.uname = info.gname = "root"


def _ar(members) -> bytes:
    """The `ar` archive: a magic line, then per member a 60-byte header (name,
    time, owner, group, octal mode, size, "`\\n") and the data, padded to even."""
    out = [b"!<arch>\n"]
    for name, data in members:
        head = (f"{name:<16}{epoch():<12}{0:<6}{0:<6}{'100644':<8}"
                f"{len(data):<10}`\n").encode()
        assert len(head) == 60, head
        out += [head, data, b"\n" if len(data) % 2 else b""]
    return b"".join(out)


def build(out: pathlib.Path) -> pathlib.Path:
    ver = version()
    files = payload(ver)
    ctl = {"control": (control(ver, files), 0o644),
           "md5sums": (md5sums(files), 0o644)}
    for name in SCRIPTS:
        ctl[name] = (lf((DEB / name).read_bytes()), 0o755)
    deb = _ar([("debian-binary", b"2.0\n"),
               ("control.tar.gz", _tar(ctl)),
               ("data.tar.gz", _tar(files))])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(deb)
    return out


# ----------------------------------------------------------- read it back
def read_ar(data: bytes) -> list:
    """[(name, bytes)] from an ar archive."""
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("not an ar archive")
    pos, members = 8, []
    while pos < len(data):
        head = data[pos:pos + 60]
        if len(head) < 60 or head[58:60] != b"`\n":
            raise ValueError(f"bad ar header at {pos}")
        name = head[:16].decode().strip().rstrip("/")
        size = int(head[48:58].decode().strip())
        members.append((name, data[pos + 60:pos + 60 + size]))
        pos += 60 + size + (size % 2)
    return members


def read_tar(data: bytes) -> dict:
    """{name: (TarInfo, bytes or None)} from a gzipped tar."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            body = tar.extractfile(m).read() if m.isfile() else None
            out[m.name] = (m, body)
    return out


def check(path: pathlib.Path) -> list:
    """What is wrong with the package at `path`, in words. [] when nothing."""
    problems = []
    members = read_ar(path.read_bytes())
    names = [n for n, _ in members]
    if names != ["debian-binary", "control.tar.gz", "data.tar.gz"]:
        return [f"members are {names}, not debian-binary, control, data"]
    if members[0][1] != b"2.0\n":
        problems.append(f"debian-binary is {members[0][1]!r}")
    ctl, data = read_tar(members[1][1]), read_tar(members[2][1])

    fields = dict(line.split(": ", 1) for line in
                  ctl["./control"][1].decode().splitlines()
                  if line and not line.startswith(" "))
    for key in ("Package", "Version", "Architecture", "Maintainer",
                "Depends", "Description"):
        if not fields.get(key):
            problems.append(f"control has no {key}")

    executables = {"./" + dst for _src, dst, mode in FILES if mode & 0o111}
    executables |= {"./" + name for name in SCRIPTS}
    for name, (info, body) in {**ctl, **data}.items():
        if info.uid or info.gid:
            problems.append(f"{name}: not owned by root")
        if body is None:
            continue
        want_exec = name in executables
        if bool(info.mode & 0o111) != want_exec:
            problems.append(f"{name}: mode {oct(info.mode)}")
        if want_exec and not body.startswith(b"#!"):
            problems.append(f"{name}: no shebang")
        if not name.endswith(".gz") and CRLF in body:
            problems.append(f"{name}: has CRLF line endings")

    listed = {}
    for line in ctl["./md5sums"][1].decode().splitlines():
        digest, file = line.split("  ", 1)
        listed["./" + file] = digest
    for name, (info, body) in data.items():
        if body is not None and listed.get(name) != hashlib.md5(body).hexdigest():
            problems.append(f"{name}: md5sums does not match")
    for need in ("./usr/bin/nishro-link", f"./{LIB}/link/nishro_link.py",
                 f"./{LIB}/link/__init__.py",
                 "./usr/share/applications/nishro-link.desktop"):
        if need not in data:
            problems.append(f"{need} is missing")
    if any("/tests/" in n or "/packaging/" in n for n in data):
        problems.append("tests or packaging files are in the package")
    return problems


def main() -> int:
    ver = version()
    default = ROOT / "dist" / f"{PACKAGE}_{ver.replace('~', '-')}_all.deb"
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else default
    build(out)
    print(f"{out}  ({out.stat().st_size // 1024} KB, version {ver})")
    problems = check(out)
    if problems:
        for p in problems:
            print("  BROKEN: " + p, file=sys.stderr)
        return 1
    print("checked: structure, control fields, modes, LF endings, md5sums")
    return 0


if __name__ == "__main__":
    sys.exit(main())
