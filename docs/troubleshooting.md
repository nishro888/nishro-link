# Troubleshooting

First, open **Help** in the window: it shows the version of every device in the
group, and **Copy details** copies everything useful for a bug report. The
**Activity** page, and **Open log folder**, show what happened and why.

- [Devices do not find each other](#devices-do-not-find-each-other)
- [It says the password is wrong](#it-says-the-password-is-wrong)
- [A different version](#a-different-version)
- [Windows: "Windows protected your PC"](#windows-windows-protected-your-pc)
- [Windows: the setup says the service did not start](#windows-the-setup-says-the-service-did-not-start)
- [Linux: the window asks to set up permissions](#linux-the-window-asks-to-set-up-permissions)
- [The pointer lags or stutters](#the-pointer-lags-or-stutters)
- [The pointer does not cross where I expect](#the-pointer-does-not-cross-where-i-expect)
- [A key seems stuck, or input went to the wrong computer](#a-key-seems-stuck-or-input-went-to-the-wrong-computer)
- [Find the pointer shows nothing on Linux](#find-the-pointer-shows-nothing-on-linux)
- [Where the logs are](#where-the-logs-are)

## Devices do not find each other

Devices find each other by name on the local network, with broadcast and
multicast on UDP port 8770, and connect over TCP port 8770. Check, in order:

1. **Both are on the same network**, and the same subnet: a computer on
   Wi-Fi and one on a wired network usually are, if they share a router.
2. **Sharing is on** on both (top right of the window).
3. **Windows: the network is Private.** Windows allows Nishro Link on private
   and domain networks only. *Settings → Network & Internet → (your
   connection) → Properties → Network profile: Private.*
4. **Windows: no block rule.** If the firewall's own prompt was ever dismissed,
   Windows adds a rule *blocking* the program. The window shows a banner,
   *Windows Firewall is blocking Nishro Link*, with an **Allow** button.
5. **Linux: the firewall.** If ufw is on: `sudo ufw allow 8770`. With
   firewalld: `sudo firewall-cmd --permanent --add-port=8770/tcp --add-port=8770/udp && sudo firewall-cmd --reload`.
6. **The network allows devices to see each other.** Guest Wi-Fi, "client
   isolation" or "AP isolation" on a router, and some VPNs block exactly this.
   Try with the VPN off, or on the main network.
7. **Something else uses port 8770.** Change the port in **Settings → This
   device** on every device in the group.

## It says the password is wrong

Type the password shown **on the device you are adding**, not your own.
Capitals, spaces and dashes do not matter. If that device has been removed
from a group or has left one, it has a new password: read it again from its
Devices page.

## A different version

Every device in a group must run the same version of Nishro Link. A device on
another version is refused (*protocol version mismatch*), and **Help** shows
which version each device has. Update all of them to the same release.

## Windows: "Windows protected your PC"

The installer is not code-signed yet, so SmartScreen warns about it until it
is widely downloaded. Choose **More info → Run anyway**. You can check the
file against the release's `SHA256SUMS` first - see
[install-windows.md](install-windows.md#windows-protected-your-pc).

## Windows: the setup says the service did not start

The details are in `C:\ProgramData\NishroLink\install.log`. Run the setup once
more - it repairs an earlier install. If it still fails, open an issue with
that file attached.

## Linux: the window asks to set up permissions

Reading the mouse and keyboard needs your account in the `input` group. The
`.deb` adds whoever installs it with `sudo apt install`; installed from the App
Center, it cannot tell who you are, so the window offers **Set up
permissions** instead. It asks for your password, and then you **log out and
back in once**.

## The pointer lags or stutters

Nishro Link itself takes well under a millisecond per movement; lag comes from
the network, and almost always from **Wi-Fi power saving**: the controlled
computer's Wi-Fi radio sleeps between packets. Nishro Link keeps it awake
while that computer is being controlled, but some adapters still sleep. In
order of effect:

1. Use **5 GHz** Wi-Fi rather than 2.4 GHz, or a cable.
2. Turn off Wi-Fi power saving on the computer being controlled:
   - **Linux (NetworkManager):** `nmcli connection modify "<your Wi-Fi>" 802-11-wireless.powersave 2`,
     then reconnect.
   - **Windows:** *Device Manager → Network adapters → (Wi-Fi) → Power
     Management* - untick *Allow the computer to turn off this device*; and in
     *Power Options*, set *Wireless Adapter Settings* to *Maximum Performance*.
3. Keep the two computers on the same access point.

## The pointer does not cross where I expect

The pointer crosses only where boxes share an edge in **Arrangement** - the
bright lines. Two boxes that only touch at a corner, or have a gap between
them, have no crossing. If the editor lists a problem under the arrangement,
fix that first: the arrangement is not applied until it is right.

On Linux, after a monitor is plugged in or out, restart Nishro Link on that
computer (`sudo systemctl restart nishro-link`) for the pointer to use the new
screen size. The arrangement itself updates on its own.

## A key seems stuck, or input went to the wrong computer

Press **both Ctrl keys** together. That releases all input on every computer
and hands each one back its own mouse and keyboard. Nishro Link also releases
every key it pressed whenever a link drops or control moves.

If it happens again, **Copy details** and the log from around that time make
a good bug report.

## Find the pointer shows nothing on Linux

On Linux, *Find the pointer* uses GNOME's *Locate Pointer* ripple, so it works
on GNOME desktops (Ubuntu's included) only for now. On other desktops it does
nothing yet.

## Where the logs are

| | Installed | Run from source |
|---|---|---|
| **Windows** | `C:\ProgramData\NishroLink\link.log` (and `install.log`) | `%LOCALAPPDATA%\NishroLink\link.log` |
| **Linux** | `/var/log/nishro-link/link.log`, or `journalctl -u nishro-link` | `~/.local/state/nishro-link/link.log` |

**Open log folder** on the Help page opens the right one. Logs hold device
names, addresses and events - never passwords or anything typed.

Still stuck? [Open an issue](https://github.com/nishro888/nishro-link/issues/new/choose)
with the output of **Copy details**.
