# Install on Linux

A `.deb` package for Ubuntu, Debian and their derivatives; a per-user install
from source for everything else. Wayland and X11 both work. See
[requirements](requirements.md) for the details.

## Ubuntu, Debian, Linux Mint, Pop!_OS, Zorin...

1. Download **`nishro-link_<version>_all.deb`** from the
   [latest release](https://github.com/nishro888/nishro-link/releases/latest).
2. Install it - either open it (Ubuntu's App Center installs it), or in a
   terminal:

   ```bash
   sudo apt install ./nishro-link_1.0.1_all.deb
   ```

   Use `apt`, not `dpkg -i`: `apt` also installs what it depends on.
3. **Log out and back in once**, so that your account's new group membership
   takes effect. (If you installed from the App Center, the window offers a
   *Set up permissions* button instead, and asks for your password.)
4. Open **Nishro Link** from the app menu.

To check that the file is the one published, compare it with the release's
`SHA256SUMS` file:

```bash
sha256sum -c SHA256SUMS --ignore-missing
```

### What it installs

| | |
|---|---|
| **Program** | `/usr/lib/nishro-link`, started by `/usr/bin/nishro-link` |
| **Service** | `nishro-link.service`, a systemd system service, enabled and started. It runs from boot, so another computer's mouse and keyboard also work on the login and lock screens. |
| **Input access** | reads the mouse and keyboard through `/dev/input` and plays input through `/dev/uinput` (the kernel's `uinput` module, loaded now and at every boot). A udev rule gives `/dev/uinput` to the `input` group, and **the person installing is added to the `input` group** - the window reaches the service through a file only that group can read. |
| **Polkit action** | lets the window's *Set up permissions* button add you to the `input` group, after asking for your password |
| **Settings** | `/var/lib/nishro-link/config.json`, readable by root only (it holds the group's password). The first install copies your own settings from `~/.config/nishro-link/` if you had run it before. |
| **Log** | `/var/log/nishro-link/link.log` |
| **App menu** | *Nishro Link*, with its icon and an App Center page |

It needs, and `apt` installs: `python3` (3.8 or later), `python3-tk`,
`python3-evdev`, `python3-cryptography` and `xclip`. It recommends `pkexec`
and `x11-xserver-utils`.

**About the `input` group:** its members can read every keyboard and mouse on
the computer. That is what software like this needs, and it is also why the
package adds only the person who installed it. On a computer several people
use, keep that in mind.

### Firewall

Ubuntu's firewall (ufw) is off by default. If you have turned it on, allow the
port Nishro Link uses:

```bash
sudo ufw allow 8770
```

(That is TCP for the link and UDP for finding devices; `ufw allow 8770`
covers both.)

### The service

```bash
systemctl status nishro-link          # is it running?
journalctl -u nishro-link -f          # what it is doing
sudo systemctl restart nishro-link
```

The window is only a window: closing it does not stop sharing.

### Update

Install the new `.deb` the same way. The service restarts with the new
version, keeping your devices and arrangement.

**Every 1.x version works with every other 1.x**, so the computers need not
all be updated at the same moment. A version that cannot work with 1.x
would be 2.0, and would say so.

### Uninstall

```bash
sudo apt remove nishro-link     # keeps the settings in /var/lib/nishro-link
sudo apt purge nishro-link      # also removes the settings and the log
```

Your account stays in the `input` group - other software may rely on it. To
leave it: `sudo gpasswd -d $USER input`, then log out and back in.

## Other distributions

Fedora, Arch, openSUSE and others: install from source, for your user only.

1. Install the dependencies with your package manager:

   | | Python 3.8+ | Tk | evdev | cryptography 2.5+ | clipboard |
   |---|---|---|---|---|---|
   | **Fedora** | `python3` | `python3-tkinter` | `python3-evdev` | `python3-cryptography` | `xclip` |
   | **Arch** | `python` | `tk` | `python-evdev` | `python-cryptography` | `xclip` |

   Elsewhere, the same five under your distribution's names. The installer
   below can also fetch evdev and cryptography with `pip` if they are missing.

2. Download the source - **Source code (tar.gz)** on the release page - and
   run the installer:

   ```bash
   tar xf nishro-link-1.0.1.tar.gz
   cd nishro-link-1.0.1
   ./link/packaging/install-linux.sh
   ```

   (Or `git clone https://github.com/nishro888/nishro-link` and run it from
   there.)

   It installs to `~/.local/share/nishro-link`, puts `nishro-link` in
   `~/.local/bin`, and asks - explaining each - before the steps that need
   root: adding you to the `input` group, a udev rule for `/dev/uinput`, and
   loading `uinput` at boot. Log out and back in afterwards.

3. Start it with `nishro-link`, or at every login:

   ```bash
   systemctl --user enable --now nishro-link
   ```

This install runs as your user, not as a system service: it works once you
are logged in, but not on the login screen. To uninstall:

```bash
systemctl --user disable --now nishro-link
rm -rf ~/.local/share/nishro-link ~/.local/bin/nishro-link \
       ~/.config/systemd/user/nishro-link.service
```

## Build the .deb yourself

The package is plain Python; it builds on any system with Python 3.8+:

```bash
git clone https://github.com/nishro888/nishro-link
cd nishro-link
python3 link/packaging/build-deb.py      # writes dist/nishro-link_<version>_all.deb
```

The release builds are made the same way, by GitHub Actions, from the tagged
source ([release.yml](../.github/workflows/release.yml)), and every change is
test-installed on Ubuntu 20.04, 22.04 and 24.04 and on Debian 11 and 12
([ci.yml](../.github/workflows/ci.yml)).
