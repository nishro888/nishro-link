# Working on Nishro Link

A software KVM: one mouse and keyboard across Windows and Linux computers.
Plain Python (3.8+), Tk for the window, a system service on both systems.

## Where the knowledge is

| | |
|---|---|
| `link/DESIGN.md` | How it works, and the safety rules (P1-P6). Read before a larger change. |
| `docs/design-decisions.md` | Why each choice was made, and the bugs that taught something. |
| `CONTRIBUTING.md` | Setup, where things are, guidelines. |
| `RELEASING.md` | The release checklist, including the checks made by hand on real machines. |
| `CHANGELOG.md` | What changed, per version. Update it with every user-visible change. |
| `docs/manual.md` | The user manual. Update it when behaviour or the window changes. |

## Commands

```bash
python -m pip install -e ".[dev]"        # cryptography, pytest, ruff (+ evdev on Linux)
python -m pytest -q                      # ~1,340 tests, a few minutes
python -m ruff check .
python link/packaging/build-deb.py       # dist/nishro-link_<v>_all.deb, self-checking
powershell -File link/packaging/build-windows.ps1   # dist/NishroLink/ and the setup
python link/packaging/make-icons.py      # every icon, from code
python tools/docs/shot_manual.py         # the manual's screenshots (see tools/README.md)
```

`pytest -q` is already set in the project's options: with another `-q` the
summary line disappears. Judge a run by its exit code, not by piping it to
`tail`, which reports `tail`'s.

## Rules that are not negotiable

- **Python 3.8**: no `match`, no `X | Y` outside annotations, no `dict | dict`.
  CI runs Ubuntu 20.04 and Debian 11 with their own Python and libraries.
- **Few dependencies**: the standard library, `cryptography`, and `evdev` on
  Linux. Windows APIs through `ctypes`. Pillow only in tests and tools.
- **Every 1.x works with every 1.x**: the protocol only grows. New messages
  and fields are optional; unknown ones are ignored; a new refusal code is one
  an older peer treats as "try again later".
- **The safety rules in DESIGN.md**: nothing blocks an input hook thread (P1);
  the person always gets their own mouse and keyboard back (both Ctrl keys,
  any lost link); a machine never reads its own injected input (P6).
- **Tests with every change.** A bug fix starts with a test that fails - and
  check that it does fail without the fix.
- **This repository is public.** No personal details anywhere: not in code,
  tests, docs, screenshots or commit messages. Examples use laptop,
  desktop-1, aio, Home-WiFi, 192.168.1.x and made-up passwords.

## Style

Comments and docs explain *why*, in plain words, and say what was seen when
a bug taught something ("Seen on the laptop: ...", "Reported: ..."). Match the
surrounding code. Commit messages say what changed and why.

## What only real machines showed

The tests pass on one machine, in one process. These did not show up there:

- **Windows runs as two processes**: the service (SYSTEM, session 0, no
  desktop) and a desk agent it starts in the signed-in session (`agent.py`),
  which does everything that touches the desktop: input, the clipboard, the
  spotlight. Test across that boundary.
- **The clipboard from the agent**: Windows hides the registered "PNG" format
  from it, and a bitmap handle another program made cannot be converted -
  images are read as CF_DIB memory (`clip_win.py`). A program the agent starts
  must get `CREATE_NO_WINDOW`, or a console window flashes.
- **Installers**: Inno Setup runs `[Run]` entries before the post-install
  step; whatever an installer stops, it must start again after the last step
  that stops things (the tray, on both systems).
- **Windows Defender** flags setups compiled on a developer's machine (a
  machine-learning false positive, `Bearfoos.A!ml`); the CI-built setup scans
  clean. To test locally, install the program folder with
  `tools/dev-install-windows.ps1`. Never rename or disguise anything to get
  past a scanner; a false positive is reported to Microsoft.
- **Linux**: the service is root, outside every session; anything for the
  person's session goes through `session.run`. The launcher clears a snap's
  environment (a terminal in VS Code installed as a snap crashed GTK). The
  `.deb`'s maintainer scripts must stay LF (`.gitattributes`).
- **Wi-Fi**: power saving and a network Windows calls Public both look like
  "laggy" or "not connected"; measure before changing code.

So before a release, `RELEASING.md` section 3 is done on a real Windows and
a real Linux computer, with the builds a user would install.

## Practical notes for scripts on Windows

- Python strings holding Windows paths must be raw (`r"C:\..."`): `\N` in
  `"...\NishroLink"` is a syntax error.
- Inline Python in a shell heredoc can lose backslashes on the way; for
  anything with paths or escapes, write the script to a file first.
- `powershell Start-Process -Wait` waits for the whole process tree, so it
  hangs on a setup that starts the tray; poll the process instead.
- Line endings: `core.autocrlf` is on; the repository stores LF.

## Releasing

Ask the maintainer before pushing, tagging or publishing. A release is a tag
(`v1.2.0`; with a `-`, a pre-release) that the Release workflow builds and
publishes with `SHA256SUMS`. Run the workflow by hand first: it builds without
publishing, so the setup can be scanned and installed before anyone downloads
it.
