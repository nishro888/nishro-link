# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

**Compatibility.** From 1.0, **every 1.x works with every other 1.x**: a 1.x
release may add things, but never anything an older 1.x cannot speak. A change
that breaks that would be 2.0. Before 1.0, in the betas, a minor version could
change the protocol, and a group had to run the same minor version.

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

[1.0.0]: https://github.com/nishro888/nishro-link/releases/tag/v1.0.0
[0.15.2]: https://github.com/nishro888/nishro-link/releases/tag/v0.15.2-beta
[0.15.1]: https://github.com/nishro888/nishro-link/releases/tag/v0.15.1-beta
[0.15.0]: https://github.com/nishro888/nishro-link/releases/tag/v0.15.0-beta
