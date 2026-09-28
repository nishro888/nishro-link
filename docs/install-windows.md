# Install on Windows

Windows 10 or 11, 64-bit. See [requirements](requirements.md) for the details.

## Install

1. Download **`NishroLink-Setup-<version>.exe`** from the
   [latest release](https://github.com/nishro888/nishro-link/releases/latest).
2. Run it. Windows asks once for permission to make changes (the installer sets
   up a background service): choose **Yes**.
3. Click through the wizard. At the end, leave **Open Nishro Link** ticked.

### "Windows protected your PC"

The installer is not code-signed yet, so Microsoft Defender SmartScreen may
warn the first time it is run: choose **More info**, then **Run anyway**.

To check that the file is the one published, compare its SHA-256 hash with
the release's `SHA256SUMS` file. In PowerShell:

```powershell
Get-FileHash .\NishroLink-Setup-0.15.2.exe -Algorithm SHA256
```

## What it changes

| | |
|---|---|
| **Program** | `C:\Program Files\Nishro Link\NishroLink.exe` - one file, Python and every library inside |
| **Background service** | *Nishro Link* (`NishroLink`), started automatically, running as SYSTEM. It is what lets another computer's mouse and keyboard work on the lock and sign-in screens. It restarts itself if it stops. |
| **Firewall** | one inbound rule, *Nishro Link*, allowing the program on **private and domain** networks. On a network Windows calls *public*, other devices cannot reach it: see [troubleshooting](troubleshooting.md#devices-do-not-find-each-other). |
| **Settings** | `C:\ProgramData\NishroLink\private\config.json`, readable by SYSTEM and Administrators only (it holds the group's password) |
| **Log** | `C:\ProgramData\NishroLink\link.log`, and the installer's own `install.log` beside it |
| **Start Menu** | *Nishro Link*, and a desktop shortcut if you ticked it |
| **Apps** | *Nishro Link* in **Settings → Apps**, for uninstalling |

Nothing else: no drivers, no browser extensions, no scheduled tasks, no update
checker, no telemetry.

The window is only a window: closing it does not stop sharing. It attaches to
the background service each time it opens.

## Update

Run the new version's setup. It stops the service, replaces the program and
starts it again, keeping your devices and arrangement.

**Every computer in a group must run the same minor version** - 0.15.0 and
0.15.1 work together; 0.14 and 0.15 do not. A device on another is refused with
a message saying so; update them all together.

## Silent install

For deploying to several machines:

```powershell
NishroLink-Setup-0.15.2.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

It still needs administrator rights, so run it from an elevated prompt or a
deployment tool. The program does not open afterwards; it is running in the
background.

## Uninstall

**Settings → Apps → Nishro Link → Uninstall**. It asks whether to also remove
your settings and pairing: **No** keeps them for a later reinstall, **Yes**
removes `C:\ProgramData\NishroLink`. The service and the firewall rule are
removed either way.

A silent uninstall (`"C:\Program Files\Nishro Link\unins000.exe" /VERYSILENT`)
keeps the settings.

## Build it yourself

With Python 3.11 and [Inno Setup 6](https://jrsoftware.org/isdl.php) installed:

```powershell
git clone https://github.com/nishro888/nishro-link
cd nishro-link
powershell -ExecutionPolicy Bypass -File link\packaging\build-windows.ps1
```

`dist\` then holds `NishroLink.exe` and `NishroLink-Setup-<version>.exe`. The
release builds are made the same way, by GitHub Actions, from the tagged
source ([release.yml](../.github/workflows/release.yml)).

## Run from source

With Python 3.8 or later:

```powershell
pip install cryptography
python -m link.nishro_link
```

Without the service it runs as your user only: it stops when you sign out, and
cannot work on the lock or sign-in screens.
