# FAQ

- [How is it different from Synergy, Deskflow, Input Leap or Mouse Without Borders?](#how-is-it-different-from-synergy-deskflow-input-leap-or-mouse-without-borders)
- [Does it work on Wayland?](#does-it-work-on-wayland)
- [Why is there no Flatpak or Snap?](#why-is-there-no-flatpak-or-snap)
- [macOS?](#macos)
- [Does it work over the internet, or a VPN?](#does-it-work-over-the-internet-or-a-vpn)
- [Does it send anything anywhere?](#does-it-send-anything-anywhere)
- [How many computers and monitors?](#how-many-computers-and-monitors)
- [Keyboard layouts?](#keyboard-layouts)
- [Files, images in the clipboard?](#files-images-in-the-clipboard)
- [Games?](#games)
- [Why Python? Is it fast enough?](#why-python-is-it-fast-enough)
- [Is it free?](#is-it-free)

## How is it different from Synergy, Deskflow, Input Leap or Mouse Without Borders?

Those are mature projects, and each may suit you better. The differences, to
our knowledge in September 2026 (corrections welcome):

| | Nishro Link | Deskflow, Synergy, Input Leap, Barrier | Mouse Without Borders |
|---|---|---|---|
| **Systems** | Windows, Linux | Windows, macOS, Linux | Windows |
| **Whose mouse drives** | any computer's, whichever moves | the server's; clients receive it | any computer's |
| **Setting up** | pick the device, type its four-word password | server and client roles, an address or name, a certificate fingerprint | a security key |
| **Linux on Wayland** | at the device level (evdev and uinput): no desktop prompts, works on the login screen | through the desktop's portals and libei, on recent desktops | - |
| **Arrangement** | free-form: monitors placed exactly, borders resized, wrap-around copies | a grid of screens | up to four computers, in a row or a square |
| **macOS** | no | yes | no |
| **File transfer** | no | varies | yes |

Nishro Link was written for a desk with a Windows laptop and an Ubuntu
desktop, where either keyboard might be the one in reach, and it should keep
working at the login and lock screens of both.

## Does it work on Wayland?

Yes. Rather than going through the desktop, which Wayland deliberately
restricts, it reads the mouse and keyboard from the kernel (`/dev/input`, via
evdev) and plays input back through a virtual device (`/dev/uinput`). That
works the same on Wayland and X11, and at the login and lock screens. It is
tested on GNOME; KDE and other desktops should behave the same, but are
untested. The price is the `input` group - see
[security](security.md#who-is-trusted).

## Why is there no Flatpak or Snap?

Their sandboxes exist to keep apps away from exactly what Nishro Link needs:
reading every input device, creating a virtual one, and running as a system
service from boot. Packaged that way, it would need so many holes in the
sandbox that it would give a false impression of confinement. The `.deb` (and
the source install for other distributions) is honest about what it does.

## macOS?

Not supported, and not planned for now. Contributions are welcome, but it is a
large piece of work: capture, injection, permissions and a service all differ.

## Does it work over the internet, or a VPN?

No. It is for computers on the same local network: devices find each other by
broadcast, and the latency of a local network is what makes the pointer feel
native. Some VPNs that bridge two networks into one subnet may work, but that
is not supported.

## Does it send anything anywhere?

Only to the other devices in your group, encrypted. There is no telemetry, no
update check, no account and no cloud service.

## How many computers and monitors?

Every computer's monitors are drawn as they are, however many it has. A group
can hold many devices. It is developed on two physical computers, and groups
of three are tested automatically with real network connections.

## Keyboard layouts?

Keys are sent as keys, not characters: the computer being typed on applies its
own keyboard layout, as if the keyboard were plugged into it. If both use the
same layout, what you type is what appears.

## Files, images in the clipboard?

The shared clipboard carries text. Images and files are not supported yet.

## Games?

Not a goal. Games that capture the mouse for looking around (most 3D games)
read it in ways a shared pointer does not provide, and will likely behave
poorly on a computer being controlled. Everyday use - and games played with
the local mouse - are unaffected.

## Why Python? Is it fast enough?

Python makes the program small, readable, and the same on both systems.
Handling one pointer movement takes about 0.3 ms; the network takes far
longer. Where the pointer lags, the cause is almost always Wi-Fi power saving
([troubleshooting](troubleshooting.md#the-pointer-lags-or-stutters)).

## Is it free?

Yes: [MIT License](../LICENSE). No paid edition, no account.
