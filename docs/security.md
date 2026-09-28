# Security

Nishro Link carries every key you press, including passwords typed on another
computer and at the login screen. This page says what protects that, and just
as plainly what does not. To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

> **Not audited.** The design is in [DESIGN.md](../link/DESIGN.md#8-security) and
> the code is open. Review is welcome.

## What protects the link

**Pairing by password.** Every device generates its own password: four words
from the EFF short word list, about 41 bits. It is read off one screen and
typed on another; it is never sent over the network. Each side proves it knows
the password with HMAC-SHA256 over a slow key made from it (PBKDF2-SHA256,
2^19 iterations, salted with the hub's device ID). Both sides prove it, and
nothing is accepted from a device before it has.

**Encryption**, with standard primitives from the
[`cryptography`](https://cryptography.io/) library - the ones WireGuard and
TLS 1.3 use:

| | |
|---|---|
| Key exchange | ephemeral **X25519**, new on every connection, bound into both password proofs |
| Keys | **HKDF-SHA256** over the exchange *and* the password key; one per direction |
| Every frame | **ChaCha20-Poly1305**, numbered: a dropped, replayed, reordered or altered frame ends the connection |

This covers everything after the handshake: key presses, pointer movement,
the clipboard, the arrangement, device management, and the group's password
when it is handed to a new device or changed (which is also sealed a second
time under the receiving device's own password).

**Forward secrecy.** The X25519 keys are thrown away with each connection.
Traffic recorded today stays unreadable even if the password is learned later.

**Nobody in the middle.** An attacker who relays the connection cannot
substitute its own keys without the password: the proofs cover both sides'
public keys.

**Your machine stays yours.** Each device has *Can be controlled*; off, no
other device's input is accepted at all. Input is injected only from the
device that holds control, only while the link is up, and every held key is
released when anything is in doubt.

## What an attacker on your network can still do

- **See that Nishro Link is running,** and each device's **name**, device ID
  and group, from the discovery messages that let devices find each other by
  name. These are not encrypted.
- **See traffic timing and sizes.** The content is encrypted, but when you type
  on a controlled computer, the rhythm of packets follows the rhythm of your
  keys. This is true of every remote-input tool that sends input as it happens.
- **Try to guess the password offline** from a recorded handshake. With a
  generated password, searching all of it costs about 2^60 hash operations -
  years of work for a high-end GPU, but not beyond a determined, well-equipped
  attacker. If you think a handshake was recorded by someone who would do
  that, **change the group's password** (Settings → Security, on the hub).
  Even a cracked password does not decrypt recorded traffic (forward secrecy);
  it would let the attacker connect as a group member from then on.
- **Stop it working**, by flooding or blocking the network. Nothing on a local
  network prevents that.

A password you choose yourself (at least 8 characters) instead of the
generated one may be much weaker than 41 bits. Prefer the generated one.

## Who is trusted

**Every device in a group is trusted with everything.** Any member can control
the others (unless they turned *Can be controlled* off), rename and remove
devices, change rights, and read the group's password. Add only computers you
control. Removing a device locks it out by its device ID: the hub refuses it
until it is added again on purpose. It still knows the group's password,
though - if you no longer trust whoever has it, **change the group's
password** as well.

**People with an account on the same computer**, depending on the system:

| | |
|---|---|
| **Windows** | The window reaches the background service through `C:\ProgramData\NishroLink\api.json`, which **every local account can read**. Anyone signed in to that computer, with any account, can therefore use Nishro Link's controls on it - including reading the group's password and changing the arrangement. The settings file itself is readable by SYSTEM and Administrators only. On a computer shared with people you do not trust, do not install it, or treat them as members of the group. |
| **Linux (.deb)** | The same handle, `/run/nishro-link/api.json`, is readable by root and the `input` group only - people who can read every keyboard on the computer already. The settings are readable by root only. |

The control API listens on 127.0.0.1 (this computer only) and needs the token
from that file.

## What is stored, and where

| | Windows | Linux (.deb) |
|---|---|---|
| Settings, including the group's password, in plain text | `C:\ProgramData\NishroLink\private\config.json` - SYSTEM and Administrators | `/var/lib/nishro-link/config.json` - root, mode 600 |
| Handle for the window (port and token) | `C:\ProgramData\NishroLink\api.json` - all local accounts | `/run/nishro-link/api.json` - root and `input`, mode 640 |
| Log (names, addresses, events - not passwords or what is typed) | `C:\ProgramData\NishroLink\link.log` | `/var/log/nishro-link/link.log` |

The password is stored in plain text because the service must prove it on
every connection, unattended, from boot. File permissions are what protect it.

## What it does not do

- It makes **no connection to the internet**: no update check, no telemetry,
  no account, no cloud relay. It talks only to devices on the local network.
- It does not run code sent by other devices. Messages are data, parsed with
  size limits.
- It does not work across the internet or between networks, by design.

## Releases

Release files are built by GitHub Actions from the tagged source
([release.yml](../.github/workflows/release.yml)), and their SHA-256 hashes are
published beside them in `SHA256SUMS`. The Windows installer is not
code-signed yet.
