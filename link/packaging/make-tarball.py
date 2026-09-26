"""Build the tarball that the Linux side downloads.

    python link/packaging/make-tarball.py [output.tar.gz]

Run from the repository root.

This is built on Windows and unpacked on Linux, and two things about that have
already shipped broken. Both are fixed here and then VERIFIED, because trusting
either cost a round trip with a confused user at the other end.

MODES. Windows has no executable bit, so tarfile records 0666 for everything and
install-linux.sh arrives unrunnable with nothing but "permission denied" to
explain it. Modes are set from each file's role instead.

LINE ENDINGS. A shell script with CRLF fails before it runs a single line: the
kernel reads the shebang as "/usr/bin/env bash\\r" and reports
`env: bash\\r: No such file or directory`. `set -euo pipefail` then dies too, with
"invalid option name". Neither message points at line endings.

Only regular files are added. Handing tarfile a directory makes it recurse, so
walking the tree AND adding directories emits every file twice.
"""
from __future__ import annotations

import io
import pathlib
import sys
import tarfile

SKIP_DIRS = {"__pycache__", "tests"}
SKIP_SUFFIX = {".pyc", ".pyo"}
CRLF = bytes((13, 10))     # spelled this way so no quoting layer can mangle it
LF = bytes((10,))


def build(src: pathlib.Path, out: pathlib.Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    n = 0
    with tarfile.open(out, "w:gz") as tar:
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            if SKIP_DIRS & set(p.parts) or p.suffix in SKIP_SUFFIX:
                continue
            arc = p.as_posix()
            if arc in seen:
                continue
            seen.add(arc)

            data = p.read_bytes()
            if p.suffix == ".sh":
                # Normalise here rather than trusting the working copy: git can
                # hand us either, depending on autocrlf.
                data = data.replace(CRLF, LF)

            info = tar.gettarinfo(str(p), arcname=arc)
            info.mode = 0o755 if p.suffix == ".sh" else 0o644
            info.size = len(data)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
            n += 1
    return n


def check(out: pathlib.Path) -> list:
    """Look inside the artefact we just wrote. Both of these have shipped wrong."""
    problems = []
    with tarfile.open(out) as tar:
        for m in tar.getmembers():
            if not m.name.endswith(".sh"):
                continue
            body = tar.extractfile(m).read()
            if not m.mode & 0o111:
                problems.append(f"{m.name}: not executable ({oct(m.mode)})")
            if CRLF in body:
                problems.append(f"{m.name}: has CRLF line endings")
            if not body.startswith(b"#!"):
                problems.append(f"{m.name}: no shebang")
    return problems


def main() -> int:
    src = pathlib.Path("link")
    if not src.is_dir():
        print("run this from the repository root (no ./link here)", file=sys.stderr)
        return 1
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "dist/nishro-link.tar.gz")
    n = build(src, out)
    print(f"{n} files -> {out}  ({out.stat().st_size // 1024} KB)")

    problems = check(out)
    if problems:
        for p in problems:
            print("  BROKEN: " + p, file=sys.stderr)
        return 1
    print("shell scripts: executable, LF-only, shebang intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
