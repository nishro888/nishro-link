# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

**Compatibility.** From 1.0, **every 1.x works with every other 1.x**: a 1.x
release may add things, but never anything an older 1.x cannot speak. A change
that breaks that would be 2.0. Before 1.0, in the betas, a minor version could
change the protocol, and a group had to run the same minor version.

## [1.1.0] - 2026-09-30

Works with every 1.x.

### Added
- **A tray icon**: Nishro Link's everyday controls without opening the window -
  in the notification area on Windows, in the top bar on Linux. Its menu:
  what is happening ("Connected · 2 of 2 online"), **Sharing** on and off,
  **Find the pointer**, **Release input**, **Devices** (each online or
  offline, and Add a device…), Open Nishro Link, Hide this icon and Quit. The icon
  itself shows the state: as it is when connected, grey when sharing is off,
  with an amber dot when not connected. Both installers start it at sign-in;
  opening the window brings it back if it was hidden. The tray, the dock menu
  and the command-line controls below talk to the background service, so
  they come with the installers; run from source, the window has them.
- **Notifications** when another device connects or drops out, from the tray.
  Settings → Control → *Notify when a device connects or drops*.
- **Linux: the dock's right-click menu** offers Find the pointer, Release
  input, Pause sharing and Resume sharing - the same as the command line's
  new `--find-pointer`, `--release-input` and `--sharing on|off|toggle`.
- **Quit Nishro Link**, at the foot of the window's navigation and in the
  tray menu: the background service stops - sharing too, here and at the
  sign-in screen - and the tray and the window close. Opening Nishro Link
  starts it all again, as does restarting the computer. No prompt on Windows
  (the installer lets signed-in users start and stop this one service) or on
  current Linux (a polkit rule lets the person at the desktop do it; older
  polkit, as on Ubuntu 22.04 and Debian 11, asks for the password).
- **Windows: a warning when this computer is on a network Windows calls
  Public.** Its firewall then keeps every other device out, and Nishro Link
  is allowed on private networks only - by design, as a café's Wi-Fi is
  exactly where a computer should not answer strangers. Found when a
  laptop's Wi-Fi fell back from the router's 5 GHz network to its 2.4 GHz
  one, which Windows had never seen and so made Public: five hours of "not
  connected", with nothing on screen to say why. Now a banner names the
  network and offers **Make it private** (through Windows' own permission
  prompt), the tray icon turns amber and says so, and adding a device says
  so instead of "check that sharing is on there". Checked every minute while
  a device is missing.
- **Images on the clipboard**, both ways: copy a screenshot, a photo or an
  image from a web page on one computer, paste it on the other. Up to 16 MB.
  (On Windows they are read as plain bitmap memory: the "PNG" form is hidden
  from the background service's helper, and a bitmap handle made by another
  program cannot be converted - both found only on the real machines.)
  They travel as PNG, in paced chunks, so the pointer and the keys never wait
  behind them; text still wins when both are copied. A computer on an older
  1.x keeps sharing text and simply never asks for images.
- Adding a device writes each step, and how it ended, to the log.
- `--add` opens the window at *Add a device*.

### Changed
- **A new logo**: two screens side by side and the lit seam between them -
  the edge the pointer crosses, drawn the way the Arrangement page draws it.
  The old one had a pointer arrow between two screens, which in a dock or a
  tray looked like the real pointer. Every size is drawn for itself, so the
  seam stays a clear line even at 16 pixels.

### Fixed
- **Windows: a console window flashed up on every Ctrl+C** (and on every paste
  that arrived). The clipboard was read by running PowerShell, from the
  background service's helper, which has no console of its own - so Windows
  gave it one, for a moment, each time. The clipboard is now read and written
  through Windows' own functions: no window, and microseconds instead of a
  fifth of a second.
- **Volume, mute, media and brightness keys** now follow the pointer like
  every other key. On Linux they come on a device of their own ("Consumer
  Control"), which was never read, so a keyboard's volume keys always acted on
  its own computer; and the Windows side knew no media keys at all, so a
  Windows keyboard's were lost while the pointer was elsewhere. A keyboard's
  volume dial follows the pointer too, every click of it. Power, sleep and
  wake keys always act on the computer they were pressed on, and the
  computer's own power-button devices are never taken. Windows has no
  brightness key to send, so there the built-in screen's brightness is set
  directly (external monitors don't take it).
- **The pointer was slow to start moving after a rest** when one computer's
  mouse drove another's screen over Wi-Fi. Only the computer being driven
  kept its Wi-Fi radio awake; the one driving dozed while its mouse was
  still, and its first movements waited for it to wake. Both ends keep it
  awake now, while the pointer is across (about 2.5 KB/s, only then).
- **The round trip shown on Windows** could only read 0 or 16 ms (or 31...),
  and 0 showed as no reading: it was timed with a clock that ticks every
  15.6 ms there. It is timed to the microsecond now.
- **Opening Nishro Link while its background service was stopped** ran a
  second engine in the window's own process, with this person's settings
  from before the service was installed. It now starts the service.
- **Sharing switched off on the hub could leave a device connected.** A
  device dials straight back when its link drops, and one that arrived
  while the hub was already accepting a connection was let in, linked to
  a computer whose sharing was off. The hub now refuses it ("sharing is
  off"), and it tries again later, as after any refusal - older 1.x
  devices included.
- **Linux: started from a terminal inside a snap** (Visual Studio Code
  installed as a snap, for one), Nishro Link could fail at once with a
  `symbol lookup error`: the snap's library and module paths leaked into it,
  and GTK loaded the snap's copies. The launcher now clears them first.

## [1.0.1] - 2026-09-29

### Fixed
- **Linux: the window did not open from the app menu** on a computer that
  had an old unpacked copy of the source in the home folder (`~/link`). The
  launcher ran `python3 -m`, which imports from the current directory first -
  and the app menu starts programs in the home folder - so the old copy ran
  instead, found the background service's lock taken, and exited with nothing
  on screen. It also explains a missing dock icon: that old copy did not name
  its window for the dock. The launcher now runs a script that sits beside the
  installed program, so only the installed program can run. CI checks it on
  every system, from a folder holding a stale `link`.
- The launcher never starts a second engine beside a running background
  service any more: if the service does not answer, it waits a few seconds,
  then says so, with what to do - instead of exiting silently.

## [1.0.0] - 2026-09-29

The first stable release. It speaks the same protocol as 0.15.x, so it also
works with those.

### Changed
- A simpler window: the navigation pane holds only the pages - Home, Devices,
  Arrangement, Activity, and Settings and Help at its foot. The status line,
  the version and the Hide and Quit buttons that were stacked under them said
  again what the page already says, and looked like the pages. **Find the
  pointer** and **Release input** are now on Home, in the Control card.

### Added
- A **user manual** with pictures of every screen and every step
  ([docs/manual.md](docs/manual.md)).
- **Design decisions**: why Nishro Link is built the way it is
  ([docs/design-decisions.md](docs/design-decisions.md)).

### Fixed
- A device's **Details** window flickered: it rebuilt itself about once a
  second, because an online device's round trip and "last seen" change all the
  time. Now it is built once, and only that line changes.

## [0.15.2] - 2026-09-28

Works with 0.15.0 and 0.15.1: the same protocol.

### Fixed
- Linux: a keyboard or mouse that appeared after Nishro Link started - a
  wireless one waking up, a Bluetooth one connecting after login, one plugged
  back in - was never read, so typing on it while the pointer was on another
  computer typed on this one instead. New devices are now picked up within two
  seconds.

## [0.15.1] - 2026-09-28

Works with 0.15.0: the same protocol.

### Changed
- Runs on **Python 3.8** and later (was 3.10), and with **cryptography 2.5**
  and later (was 3.4): the `.deb` now installs on **Ubuntu 20.04** and
  **Debian 11**. CI runs the whole test suite on both, with their own Python
  and libraries, and on Python 3.8 on Windows.

### Fixed
- On Python 3.8, a device renamed while it was switched off could never
  connect again: the hub's reply used a Python 3.9 feature and failed.
- Reconnecting to a hub that had just started could time out again and again
  on a slow or busy computer: it was given two seconds for a handshake in
  which the hub first works out its password key. Now eight, as when adding
  a device.

## [0.15.0] - 2026-09-28

The first public release.

### Security
- The link's encryption is now built on standard primitives from the
  `cryptography` library: an ephemeral **X25519** key exchange, keys from
  **HKDF-SHA256**, and **ChaCha20-Poly1305** for every frame - the ones
  WireGuard and TLS 1.3 use. It replaces a construction built from hash
  functions. Protocol version 8.
- Adding a device runs over the same encrypted exchange as every link, so the
  invitation - the group's hub, address and password - is never readable on
  the network. The password inside it is also sealed under the new device's
  own password, now with ChaCha20-Poly1305 too.

### Added
- Continuous integration: tests on Windows and Linux, lint, and a test that the
  `.deb` installs on Ubuntu 22.04, Ubuntu 24.04 and Debian 12.
- Releases are built by GitHub Actions from the tag, with `SHA256SUMS`.
- Documentation: installation, requirements, user guide, troubleshooting,
  security, FAQ.

### Removed
- *What takes control → A hotkey*. No hotkey was ever bound to it, so a
  computer set to it could not take control back with its own mouse. A saved
  setting reads as *A click*.

### Fixed
- *Open log folder* and the Activity page pointed at the wrong file when the
  window was attached to the background service.
- A connection reset in the middle of connecting (the other computer
  restarting, for instance) was logged as "link loop stopped" and retried at
  once, over and over, instead of waiting between attempts.
- Linux: a second copy could bind the discovery port beside the first, and
  both answered; and releasing the single-instance lock did not free its port
  while it was being watched.

## [0.14.0] - 2026-09-28
### Changed
- One navigation pane, as in Windows' own Settings: Home, Devices, Arrangement,
  Activity at the top; Settings and Help at its foot; Windows' own icons. The
  menu bar is gone - it offered most things twice.
- Help is a page: About (with *Copy details*), Get started, Keyboard shortcuts,
  Support.

## [0.13.1] - 2026-09-28
### Fixed
- Dialogs and menus open cleanly - no white flash, no empty frame, no jump.
- Menus open four times faster, and no longer take the focus from the window.

## [0.13.0] - 2026-09-28
### Added
- **Find the pointer**: shake the mouse and every screen darkens except a circle
  round the pointer, on whichever computer it is (GNOME's own ripple on
  Ubuntu). Also a button; can be turned off.
### Changed
- Much less lag on Wi-Fi: a computer being controlled keeps its radio awake,
  and mouse reports are batched.
### Fixed
- Control could jump back to a computer nobody had touched, right after a
  handover.

## [0.12.0] - 2026-09-27
### Added
- **Resizable screen boxes**: borders can be placed exactly; a box's size
  never changes the pointer's speed.
- **Copies of a machine**, for wrap-around: right off the last screen comes back
  in on the first.
- A new icon, drawn for every size.

## [0.11.1] - 2026-09-27
### Fixed
- The Windows setup no longer reports that the service did not start when it did.
- Ubuntu's dark theme is followed.

## [0.11.0] - 2026-09-27
### Added
- Light and dark themes that follow the computer's setting, and Windows
  11-style controls.
- A menu bar with Help and About (replaced in 0.14.0).

## [0.10.0] - 2026-09-27
### Added
- Encryption for every link (replaced by standard primitives in 0.15.0).
- Groups: add a device from either side, by name and a four-word password;
  rename, remove and set control rights of any device from any device.
- Runs as a system service on Windows and Linux: works at the login and lock
  screens.
- A setup wizard for Windows; a Debian package with an App Center page.

## [0.9.1] and [0.9.0] - 2026-09-26
Private betas.

[1.1.0]: https://github.com/nishro888/nishro-link/releases/tag/v1.1.0
[1.0.1]: https://github.com/nishro888/nishro-link/releases/tag/v1.0.1
[1.0.0]: https://github.com/nishro888/nishro-link/releases/tag/v1.0.0
[0.15.2]: https://github.com/nishro888/nishro-link/releases/tag/v0.15.2-beta
[0.15.1]: https://github.com/nishro888/nishro-link/releases/tag/v0.15.1-beta
[0.15.0]: https://github.com/nishro888/nishro-link/releases/tag/v0.15.0-beta
