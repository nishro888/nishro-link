# User guide

- [The idea](#the-idea)
- [Adding a device](#adding-a-device)
- [Arranging the screens](#arranging-the-screens)
- [Wrap-around: copies of a machine](#wrap-around-copies-of-a-machine)
- [Taking control](#taking-control)
- [Finding the pointer](#finding-the-pointer)
- [Getting your machine back](#getting-your-machine-back)
- [Managing devices](#managing-devices)
- [Settings](#settings)
- [Keyboard shortcuts](#keyboard-shortcuts)

## The idea

Every computer runs Nishro Link. Together they form a **group**, and the group
shares one pointer: push it off the edge of one screen and it appears on the
next computer's. The keyboard follows the pointer. Text copied on one computer
can be pasted on another.

There is no fixed "main" computer. Whichever mouse you move takes control, and
the others follow it.

One device in a group is the **hub**: the others connect to it, and it decides
who is in control at any moment, so two computers never both think they are.
That is all it does - its own mouse is no different from any other.

## Adding a device

A new install is a group of one. The **Devices** page shows its name and its
**password**: four plain words, like `tiger-lemon-coral-radio`, generated for
it.

To put two computers in one group, on either of them:

1. **Devices → Add a device** (or Ctrl+N).
2. Pick the other computer from the list of devices nearby.
3. Type the password **shown on that computer**. Capitals, spaces and dashes do
   not matter.

The dialog follows each step - searching, connecting, checking the password -
until it says **connected**, or says why not and what to do. There are no IP
addresses to type; if the router gives a computer a new address, it is found
again by name.

To add a third computer, do the same from any computer already in the group.
Which side you start from does not matter: a computer on its own joins the
group, and a computer that already has devices of its own stays the group.

The password proves each computer to the others, and is never sent over the
network. After the first connection, everything - including every key press -
is encrypted. See [security](security.md).

## Arranging the screens

![The arrangement editor](screenshots/arrange.png)

**Arrangement** draws every computer as its actual monitors. Drag them to
match your desk. Where two boxes touch, a bright line shows where the pointer
will cross. A change applies on every computer the moment you let go;
**Undo** (Ctrl+Z) takes it back.

- **Snap.** A box snaps to its neighbours' edges and corners as you drag.
- **Resize.** Select a box to show its eight handles. Resizing changes only
  *where the borders meet* - never the pointer's speed or how far it moves on
  each screen. That lets a small laptop screen meet a big monitor along its
  whole edge, for instance.
- **Aspect ratio** puts a resized box back to the machine's own proportions;
  **Actual size** puts it back to one unit per pixel.
- **In a row** and **In a column** lay everything out tidily at once.
- Arrow keys move the selected box; hold Shift for bigger steps.
- Right-click a box for everything that applies to it.

If two boxes overlap, or a crossing would lead to two places at once, the
editor says so in words and the arrangement is not applied until it is fixed.

Monitors plugged in or out are noticed within seconds, and the arrangement
updates everywhere.

## Wrap-around: copies of a machine

A **copy** is a second box for the same computer. The pointer that enters a
copy lands on the computer itself, as if it had come in through that edge.

For example, with *laptop - AIO* side by side: select the laptop, **Add copy**,
and put the copy to the right of the AIO. Now pushing right off the AIO brings
the pointer back onto the laptop's left edge.

- A copy is always the whole machine, all its monitors.
- Copies can be resized and moved like any box, up to eight per machine.
- **Remove this copy** (or Delete) removes the selected copy.

## Taking control

**Settings → Control → What takes control** chooses what makes a computer's
own mouse take over:

- **Moving the mouse** (the default): a small deliberate movement.
- **A click**: only a click. Useful if a computer's mouse is often bumped.

The keyboard always goes to the computer the pointer is on.

Two more settings decide what each computer may do:

- **Can control other devices** - off, and this computer's mouse and keyboard
  stay its own.
- **Can be controlled** - off, and no other computer's input reaches this one.
  It becomes a wall: the pointer stops at its edges.

## Finding the pointer

Lost the pointer among several screens? **Shake the mouse** - a few quick
movements back and forth. Every screen darkens except a circle around the
pointer, on whichever computer it is. On Ubuntu and other GNOME desktops,
GNOME's own *Locate Pointer* ripple is shown instead.

The **Find the pointer** button in the window does the same. Turn the shake
off in **Settings → Control** if you prefer.

## Getting your machine back

Nishro Link hands input back to each computer whenever anything is in doubt:
a link that dies, a device that leaves, the program stopping. You can also do
it yourself, at any time:

- press **both Ctrl keys** together, on any computer; or
- click **Release input** in the window.

**Sharing on / off** at the top right of the window pauses everything without
leaving the group. Turn it on again to carry on.

## Managing devices

Click a device on the **Devices** page, or right-click it:

- **Details** - its displays, address, version, and when it joined.
- **Rename** - applies at once, on every computer, without restarting. A
  device that is off gets its new name when it next connects.
- **Control rights** - whether it may control others, and be controlled.
- **Remove from the group** - it becomes a group of one again, with a new
  password of its own; the group's password stays with the group.

This works from any computer in the group. A computer can also **Leave this
group** from its own Devices page.

The group's password can be changed on the hub (**Settings → Security**). It
reaches every device connected at that moment; one that was off will ask for
the new password when it returns.

## Settings

| | |
|---|---|
| **This device** | its name, and the network port (8770 unless something else uses it) |
| **Control** | what takes control; what this device may do; shake to find the pointer |
| **Startup** | the background service, or start at login when run without one |
| **Appearance** | System, Light or Dark |
| **Security** | the group's password (changed on the hub) |

The theme, the startup switch and shaking to find the pointer apply as soon as
they are changed; everything else on **Save**.

**Help** (F1) has the version, *Copy details* - everything useful in a bug
report, in one click - and *Open log folder*.

## Keyboard shortcuts

| | |
|---|---|
| Both Ctrl keys | Release input, on every computer |
| Shake the mouse | Find the pointer |
| Ctrl+1 … Ctrl+6 | Go to a page |
| Ctrl+N | Add a device |
| Arrow keys | Move the selected screen (Shift for more) |
| Ctrl+Z | Undo an arrangement change |
| F1 | Help |
| Ctrl+Q | Quit the window |
