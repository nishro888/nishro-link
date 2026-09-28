# Requirements

Every computer in a group needs Nishro Link, **the same version**, on the
**same local network**.

## Windows

| | |
|---|---|
| **Supported** | Windows 10 and Windows 11, 64-bit (x64) |
| **Tested** | Windows 10 22H2 (build 19045) |
| **Expected to work, untested** | Windows 11; Windows 10 releases before 22H2 |
| **Not supported** | Windows 7, 8 and 8.1; 32-bit Windows |
| **Untested** | Windows on ARM (runs as x64 under emulation) |
| **To install** | Administrator rights, once - the installer sets up a background service |
| **Needs nothing else** | Python and every library are inside the program |

The dark title bar needs Windows 10 version 2004 or later; before that the
title bar is simply light.

## Linux

| | |
|---|---|
| **Package** | `.deb` for Ubuntu, Debian and derivatives (Linux Mint, Pop!_OS, Zorin, ...) |
| **Tested** | Ubuntu 26.04, GNOME on Wayland |
| **Installs cleanly (checked in CI)** | Ubuntu 22.04, Ubuntu 24.04, Debian 12 |
| **Sessions** | Wayland and X11 |
| **Desktops** | GNOME tested; KDE Plasma and others expected to work, untested |
| **Other distributions** | Fedora, Arch, openSUSE...: run from source, see [install-linux.md](install-linux.md#other-distributions) |
| **Not supported** | macOS, BSDs |

What the program needs - the `.deb` brings all of it:

| Needs | Why | Debian/Ubuntu package |
|---|---|---|
| Python 3.10 or later | the program | `python3` |
| Tk | the window | `python3-tk` |
| python-evdev | reading the mouse and keyboard, and moving the pointer | `python3-evdev` |
| cryptography 3.4 or later | encryption | `python3-cryptography` |
| xclip | the shared clipboard | `xclip` |
| `/dev/uinput` | making input - the kernel's `uinput` module | loaded by the package |
| systemd | running from boot, at the login and lock screens | (present on all of these) |
| polkit | granting access from the window, if asked | `pkexec` (recommended) |
| xrandr | reading the monitor layout | `x11-xserver-utils` (recommended) |

*Find the pointer* uses GNOME's own *Locate Pointer* ripple on Linux; on other
desktops it does nothing yet.

## Network

- Both computers on the same local network (the same subnet).
- **TCP and UDP port 8770**, allowed in on each computer. The Windows installer
  adds the rule, for private networks. On Linux, with ufw: `sudo ufw allow 8770`.
- Devices find each other by broadcast and multicast on that port: guest Wi-Fi
  with "client isolation" and some VPNs prevent it (see
  [troubleshooting](troubleshooting.md#devices-do-not-find-each-other)).
- No internet connection, account or cloud service is used, and nothing is
  sent anywhere but to the other devices.

## Hardware

Anything that runs the systems above. The program uses a few percent of one
core while the pointer is on another computer, and nothing noticeable when idle.
Tested on a low-power Intel Celeron J4125 all-in-one.
