# Contributing

Thanks for wanting to help. Bug reports, testing on systems not yet covered,
documentation and code are all welcome.

## Reporting a bug

[Open an issue](https://github.com/nishro888/nishro-link/issues/new/choose) with
the bug template. The most useful things to include:

- **Help → Copy details** from the app on each computer involved (versions,
  system, how it runs - no addresses or passwords);
- what you did, what you expected, what happened;
- the log: **Help → Open log folder** (Windows: `%ProgramData%\NishroLink\link.log`
  when installed as a service; Linux: `/var/log/nishro-link/link.log`).

Security problems: see [SECURITY.md](SECURITY.md) - please report those
privately.

## Testing on more systems

Tested so far: Windows 10 22H2 and Ubuntu 26.04 (GNOME, Wayland). Reports from
Windows 11, other Ubuntu and Debian releases, Fedora, KDE Plasma, X11 sessions
and three-computer groups are especially valuable - even "it works".

## Working on the code

Nishro Link is plain Python (3.8+) with Tkinter for the window. Code must run
on Python 3.8 and on the libraries Ubuntu 20.04 and Debian 11 ship - CI runs
the tests there - so no `match`, no `X | Y` outside annotations, and
`from __future__ import annotations` wherever annotations use newer syntax.

```bash
git clone https://github.com/nishro888/nishro-link.git
cd nishro-link
python -m pip install -e ".[dev]"          # cryptography, pytest, ruff (+ evdev on Linux)
python -m pytest                           # about 1,260 tests, a few minutes
python -m ruff check .
python -m link.nishro_link                 # run it from source
```

On Linux the window's tests need a display (`xvfb-run -a python -m pytest`
without one), and running the program needs access to `/dev/input` and
`/dev/uinput` - see [docs/install-linux.md](docs/install-linux.md).

Where things are:

| Area | Files |
|---|---|
| The arrangement and the pointer's movement | `link/desk.py`, `link/motion.py` |
| One machine: control, crossings, the network | `link/node.py`, `link/protocol.py` |
| Pairing and encryption | `link/pairing.py`, `link/secure.py` |
| Input capture and injection | `link/capture_win.py`, `link/capture_linux.py`, `link/inject.py` |
| The window | `link/ui_*.py` |
| Installers | `link/packaging/` |

[link/DESIGN.md](link/DESIGN.md) explains how it works and why - worth reading
before a larger change.

### Guidelines

- **Tests with every change.** A bug fix starts with a test that fails.
- **Safety first.** Nothing may block the input hooks, and the user must always
  get their own mouse and keyboard back (the rules are in DESIGN.md, section 4).
- **Explain the why** in comments and commit messages - the code says what.
- Keep dependencies few: the program ships as one file on Windows and as a
  small package on Linux.

Open an issue before a large change, so we can agree on the approach first.

By contributing you agree that your contribution is licensed under the
project's [MIT License](LICENSE).
