# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/). Nishro Link is in
beta: until 1.0, a minor version may change the protocol, and **all computers in
a group must run the same version** (a different one is refused, with a message).

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

[0.15.0]: https://github.com/nishro888/nishro-link/releases/tag/v0.15.0-beta
