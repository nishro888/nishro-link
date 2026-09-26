# Nishro Link

**One mouse and keyboard across several computers, on Windows and Linux.**
Push the pointer off the edge of one screen and it carries on onto the next
computer's. Type, and it goes to whichever screen the pointer is on. Copy on one
machine, paste on another. Any machine's own mouse can take over at any time -
there is no fixed "server with the keyboard".

![The Overview page](docs/screenshots/overview.png)

> **Status: beta (v0.9).** Developed and tested on a Windows 10 laptop and an
> Ubuntu (Wayland) desktop, with 615 automated tests - but read
> [Limitations](#limitations) first. In particular, **traffic is not encrypted
> yet**: use it on a home or office network you trust.

## Features

- **Any machine drives.** Whichever mouse you touch takes control. The others
  follow. Keyboards follow the pointer.
- **Several devices in one group.** One device lets the others connect; any
  number can join it, and it relays between them - so one desktop's mouse can
  drive another desktop's screen through it.
- **Pair by name, not IP address.** One device shows its name and a generated
  password; the other picks it from a list of devices found on the network.
  If DHCP gives a device a new address, it is found again automatically.
- **A real arrangement editor.** Every machine is drawn as its actual monitors.
  Drag them to match your desk - beside, above, below, against a particular
  monitor - and bright lines show exactly where the pointer will cross. The
  arrangement is shared with every device the moment you press Apply.
- **Monitors plugged in or out** are noticed within seconds and the arrangement
  updates everywhere.
- **Shared clipboard** (text).
- **It gives your machine back.** If a link dies, a device leaves, or anything is
  in doubt, local input is restored at once. Pressing **both Ctrl keys**
  together, or *Release input* in the window, always frees the machine you are
  at.

| Devices | Arrangement |
|---|---|
| ![Devices](docs/screenshots/devices.png) | ![Arrangement](docs/screenshots/arrange.png) |

## Install

### Windows 10 / 11

Download `NishroLink.exe` from the [Releases](../../releases) page and run it.
It is a single file with Python built in; nothing else is needed.

- The exe is not code-signed yet, so Windows SmartScreen may warn the first
  time: choose **More info → Run anyway**.
- When Windows Firewall asks, **allow it on private networks**. If that prompt
  is dismissed, Windows blocks the program and other devices cannot find it -
  the window will say so and offer an *Allow through the firewall* button.

To build it yourself and install it for your user (Start Menu entry, optional
start at sign-in):

```powershell
powershell -ExecutionPolicy Bypass -File link\packaging\build-windows.ps1
powershell -ExecutionPolicy Bypass -File link\packaging\install-windows.ps1 -Autostart
```

### Linux (tested on Ubuntu, Wayland and X11)

```bash
git clone https://github.com/nishro888/nishro-link.git
cd nishro-link
./link/packaging/install-linux.sh
```

The installer puts it in `~/.local/share/nishro-link`, adds a `nishro-link`
command and a systemd user unit, and sets up the two device permissions it
needs - reading `/dev/input` (the `input` group) and writing `/dev/uinput` (a
udev rule). Where that needs `sudo` it prints the exact commands. After being
added to the `input` group, **log out and back in**.

For the window: `sudo apt install python3-tk`. If a firewall (ufw) is running:
`sudo ufw allow 8770`.

Start it with `nishro-link`, or at login with
`systemctl --user enable --now nishro-link`.

## Getting started

1. On the device the others will connect to: **Devices → Add a device → Let
   another device connect.** It shows its name and a password.
2. On each other device: **Add a device → Connect to another device**, pick it
   from the list, type the password.
3. Open **Arrangement**, drag the machines to match your desk, press **Apply**.

Push the pointer across a bright line to cross. Move any machine's own mouse to
take control from there.

## Limitations

- **Not encrypted.** Devices prove to each other that they know the password
  (a challenge-response; the password never crosses the network), but
  keystrokes themselves travel in plain text. Encryption is the next piece of
  work.
- **Three-device groups** are tested with live nodes on one machine, not yet on
  three physical machines.
- **Linux:** after a monitor change, restart Nishro Link on that machine for the
  pointer to use the new size (the arrangement updates straight away).
  Multi-monitor Linux is read through `xrandr`.
- **macOS** is not supported.
- Clipboard is text only. File transfer is not implemented.

## How it works

One device in a group is the **hub**: the others connect to it, and it decides
who holds control (the *baton*) so two machines can never both think they are
driving. That is an administrative role only - any machine's mouse can drive.

The arrangement is a plane of rectangles: each machine is a rigid group of its
displays, and the pointer crosses wherever displays of two machines share an
edge, keeping its physical position - the way an operating system treats its
own monitors. `link/desk.py` knows where everything is; `link/motion.py` moves
the pointer across it; the arrangement screen draws `desk.py`, so what it shows
is what the pointer does.

[`link/DESIGN.md`](link/DESIGN.md) is the full design: the safety properties
(the mouse never freezes, you always get your machine back, no key stays
down), the wire protocol, reconnection, discovery and security.

## Development

Python 3.10+. No dependencies on Windows beyond the standard library; `evdev`
on Linux (`pip install -r link/requirements-linux.txt`, or `python3-evdev`).

```bash
python -m pytest link/tests          # 615 tests, about a minute and a half
python -m link.nishro_link           # run from source
```

The test suite never touches the real network: discovery is confined to
loopback, and the two- and three-machine tests run real nodes over local
sockets with the mouse, keyboard and screen faked out.

```
link/
  node.py            one machine: the core (pure) and the shell (sockets, threads)
  desk.py            the arrangement: machines, displays, what touches what
  motion.py          the pointer moving across the arrangement
  baton.py           who holds control; the safety watchdog
  protocol.py        the wire format
  discovery.py       finding a device by name on the network
  pairing.py         generated passwords
  capture_*.py       reading the local mouse and keyboard (Windows hooks / evdev)
  inject.py          moving the pointer and typing (SendInput / uinput)
  control_api.py     the local control API the window and web page use
  ui_*.py            the window
  packaging/         Windows build and install, Linux install
```

## License

[MIT](LICENSE)
