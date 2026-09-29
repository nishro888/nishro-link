# Nishro Link — Design

Shared mouse, keyboard, clipboard and files across machines on a LAN, so several
computers feel like one desk — **whichever machine's mouse you happen to reach
for.**

This document is the contract. Code that disagrees with it is a bug in one of them.

---

## 1. Why we are not cloning Input Leap

Input Leap (fork of Barrier, fork of Synergy) is the reference implementation of
this idea and was **archived in 2025** — the third project in that lineage to
stop. Its 25-year-old protocol is worth learning from. Its architecture is not
worth inheriting, and on the central point it is structurally incapable of what
we want: *its client binary contains no input-capture code at all.*

**Taken from it**

- Absolute cursor coordinates, not accumulated deltas (`kMsgDMouseMove`).
- A screen *graph* with fractional edge intervals, not a single `left|right`
  (`server/Config.h`).
- Keep-alive with death detection (3s tick, 3 misses = dead).
- TLS with trust-on-first-use fingerprint pinning, not a shared secret
  (`FingerprintDatabase` — note protocol 1.4 added home-brew crypto and 1.5
  removed it again).
- Release keys by *physical* identity, never by logical keysym.

**Rejected**

- *Roles welded in at launch.* See section 2. This is the big one.
- *Options as an apology for unsafe defaults.* They ship `screenSwitchDelay`,
  `twoTap`, `needsShift` and `corners` because the default escapes too easily.
  We pick one good guard and make it the default.
- *Clipboard that stops at Wayland.* Theirs does not work on Linux/Wayland at
  all. Wayland is our primary Linux target, not a port we failed to finish.
- *Silence as an interface.* Their answer to "why is it laggy" is a log file.
  Ours is a number you can read.

---

## 2. Roles, decomposed

Synergy and its descendants fuse five independent concerns into two words. That
fusion is the reason you cannot use the other machine's mouse.

| Concern | Them | Us |
|---|---|---|
| Accepts TCP connections | server | `hub` — plumbing, set in config |
| Resolves conflicting claims | server | `hub` — same node, still just config |
| **Whose physical input is live right now** | server, permanently | **the baton** — a token that moves |
| Accepts injected input | client, permanently | `may_be_driven` — a permission |
| May edit the shared layout | server | `may_admin` — a permission |

Every node runs **the same program with the same capabilities**. Each can
capture, each can inject, each can hold the baton. What differs between them is
configuration, plus one token that moves at runtime.

```jsonc
{
  "node": "aio",
  "hub":  "laptop",          // who listens and arbitrates — administrative only
  "policy": {
    "may_drive":     true,   // may this machine claim the baton?
    "may_be_driven": true,   // may other machines inject here?
    "may_admin":     false,  // may this machine edit the shared layout?
    "claim": "motion"        // what takes the baton: motion | click | hotkey
  }
}
```

`hub` and `may_drive` are now independent, which is the entire point. A kiosk you
can control but that can never control you is `may_drive: false,
may_be_driven: true`. A locked-down admin laptop is the reverse. Neither has
anything to do with which machine opened the socket.

---

## 3. The baton

> **Exactly one node holds the baton. The baton holder owns the virtual cursor
> and captures its own physical input. The baton moves to whichever machine you
> touch.**

1. The laptop holds the baton. Its mouse slides the shared cursor left onto the
   AIO's screen. The laptop still holds the baton, sending positions; the AIO
   injects them.
2. You let go and grab **the AIO's own mouse.** The AIO claims the baton. It now
   captures its own mouse and drives — and moving around its own screen is
   **purely local, at zero latency.**
3. You push the cursor right, back onto the laptop. The AIO keeps the baton and
   sends positions; the laptop injects.
4. You touch the laptop's mouse. The laptop claims back.

The property that makes this worth the complexity: **motion is always local to
the machine you are touching.** The wire only ever carries the *far* screen's
cursor. The obvious alternative — both mice forwarding deltas to one fixed
integrator — would put a LAN round trip in front of your own pointer on your own
machine. On Wi-Fi that feels broken.

### Claim policy

A claim is triggered by **deliberate local movement**: cumulative physical motion
past `claim_threshold` (default 8px) within `claim_window` (default 300ms), or
any button press. A desk bump does not steal control.

Injected input never counts as local. See P6 — this is not a detail.

### Where the cursor goes on a claim

**It jumps to the claiming machine.** You reached for that mouse because you want
to work on that screen. Each node remembers its `home` — the last cursor position
it held on one of its own screens — and the cursor warps there, falling back to
the centre of its primary screen.

### Arbitration and epochs

All claims go to the `hub`, which grants them. A claim is rare (only when you
swap mice), so one round trip costs nothing, and central arbitration makes
split-brain impossible rather than merely unlikely.

Every grant carries a monotonically increasing **epoch**. Every input message
carries the epoch it was produced under, and **any message bearing a stale epoch
is discarded on arrival.** Two simultaneous claims therefore cannot both take
effect, and in-flight events from the previous holder cannot land after a
handover.

A grant carries the full handover state: the baton holder, the epoch, the cursor
(`screen`, `x`, `y`) and the authoritative held-key set.

### Keyboards follow the cursor, not the baton

Typing goes to whatever screen the cursor is on, from whichever keyboard you
touch, and **never moves the baton**. You can drive with one machine's mouse and
type on the other's keyboard.

This falls out of one rule:

- **Mouse suppression follows the baton.** A node that does not hold it
  suppresses its own mouse, whose only remaining local effect is to claim.
- **Keyboard suppression follows the cursor.** The node hosting the cursor lets
  its own keyboard through untouched. Every other node captures, suppresses, and
  forwards to the node hosting the cursor.

---

## 4. Safety properties

Not features. Promises the design must keep.

- **P1 — The physical mouse never freezes.** Nothing on a capture thread may
  block. Not network I/O, not clipboard reads, not `print()`, not config writes.
  The hook does arithmetic and one `put_nowait`. Everything else is off-thread.
  *(Windows blocks all mouse input system-wide for as long as a `WH_MOUSE_LL`
  callback runs. We have shipped this bug once already.)*
- **P2 — Suppression requires a live baton.** This is the peer model's
  replacement for "control always comes home", and it is stricter because it has
  to be. In a master/slave design only one machine ever suppresses input, so
  there is always an escape. Here **both machines suppress**, so a lost baton —
  crash, dead Wi-Fi, hung process — could kill *both* mice at once and leave no
  way out but the power button. Therefore: every node runs a watchdog, and
  `baton_ttl` (default 1500ms) without fresh traffic un-suppresses
  unconditionally, whatever that node last believed. *Fresh traffic means ANY
  traffic, not just input:* a holder who is simply not moving the mouse sends
  none, so an idle peer would otherwise be indistinguishable from a crashed
  one. The keep-alive is what tells them apart, so its interval must stay well
  under the TTL. **Local input wins whenever
  anything is in doubt.** Every node also carries its own failsafe hotkey — not
  just the hub, as in Input Leap.
- **P2b — The user always gets their machine back.** A claim refused by the
  hub's anti-thrash guard is *silent*: the claimer is told nothing and nothing
  retries it. A real mouse papers over that by streaming events, but a lost
  claim, a wedged hub or a half-dead link leaves a machine whose mouse and
  keyboard both go nowhere and whose only escape is a hotkey nobody remembers.
  So a node chases an unanswered claim, and after `CLAIM_TRIES` stops waiting
  and un-suppresses itself. Handing someone back their own keyboard is always
  right; the worst case is two machines briefly both live, which is
  recoverable — unlike a computer you cannot type on.
- **P3 — No key stays down.** Any transition — crossing, handover, disconnect,
  timeout, crash, failsafe — releases every key and button that machine holds.
  A latched `Ctrl` on a uinput device outlives the process.
- **P4 — Fail open.** An exception in a hook passes the event through rather
  than swallowing it.
- **P5 — Nothing is injected from an unauthenticated peer.**
- **P6 — Injected input is never mistaken for local input.** Every node both
  captures and injects *simultaneously*, so without this there is an instant
  feedback loop: A injects into B, B reads its own injection, B claims the
  baton, forever. Windows is covered by `LLMHF_INJECTED` / `LLKHF_INJECTED`.
  Linux is **not** — `capture_linux.py` globs every `/dev/input/event*` and
  keeps anything exposing `KEY_A`, `REL_X` or `BTN_LEFT`, which matches the
  `"Nishro Link Virtual Input"` uinput node that `inject.py` creates. It must
  exclude its own device by name. Harmless in the old master/slave design, fatal
  in this one.

---

### Latency

Measured on the real pair - a laptop on 5 GHz Wi-Fi, an AIO on 2.4 GHz with
power saving on (the default) - the link's own work is not where the time goes:
two nodes over loopback deliver a thousand-a-second mouse at 0.3 ms median,
0.7 ms at the 99th percentile, 7% of one core. **Wi-Fi power saving** is: a
radio that has been quiet dozes between beacons, and a packet for it waits at
the access point. Pinging the AIO every half second, one packet in ten took
65 ms or more, the worst 113-126 ms - felt exactly when the mouse moves again
after a rest.

- **Keep-awake** (`Node._keep_awake`): while this machine is driven from
  another, it sends a tiny `ka` frame every 40 ms. A radio that transmits stays
  awake. Tried on the real pair before it was built: one packet in ten then
  14-21 ms, the worst 49-53. About 2.5 KB/s, and only while being driven; an
  idle link is left alone. `ka` is hop-local, and feeds the watchdog.
- **Batching** (`LineChannel._send_loop`): whatever is queued goes out in one
  write - one system call, one packet with TCP_NODELAY.
- **Collapse** (`protocol.collapse`): a position replaced by a newer one for
  the same screen, before it was sent, is not sent. Positions are absolute, so
  only the newest matters; nothing else is ever merged or reordered.
- The receiver splits a whole read into frames at once; the second walk of a
  move (motion.py) is skipped when the first lost nothing; a machine's display
  list is worked out once; Python switches threads every 1 ms, not 5.

### Finding the pointer

Shaking the mouse (`shake.py`: far, fast, doubling back sharply at least four
times, within a small patch - circles, drags and zig-zags do not count) or
*Find the pointer* (at the foot of the navigation) shows where the pointer
is, **on the machine it
is on**: the node that notices sends `find` to its owner. Only movement of a
machine that is driving counts: movement that merely asks for control has not
moved the pointer yet. On Windows a click-through layered window darkens every
screen but a circle round the pointer (`spotlight_win.py`; in the service the
desk agent draws it, so it works on the lock screen too). On Linux only the
compositor may draw over everything on Wayland, so GNOME's own Locate Pointer
does it - a lone Ctrl tap - turned on for the moment if it is off, and back.

## 5. The arrangement, and how the pointer moves across it

Two modules, one question each:

```
desk.py     WHERE things are    machines, their displays, where each machine sits;
                                what touches what, what overlaps, what cannot be
                                reached; snapping and resizing. No I/O.
motion.py   HOW the pointer     moves a pointer across the desk the way an OS moves
            MOVES over them     its own across monitors; decides when this
                                machine's own pointer has been pushed off its edge.
```

The arrangement screen (`ui_arrange.py`) draws `desk.py` and turns mouse and
keyboard into `desk.py` operations. Because the screen and the pointer ask the
same module, what the screen shows is exactly what the pointer does.

### The model: rectangles on one plane

A desk is a plane of integer units. On it sit **machines**; a machine is a rigid
group of **displays**, each a rectangle in that machine's own desktop
coordinates, in pixels, exactly as its operating system arranges them. A laptop
with an external monitor to its left is one machine with two displays, and their
relative position is Windows' business - never changed here. What is arranged is
where each *machine* sits, and how big its box is drawn.

```
Machine: name, owner, x, y, w, h, parts, sw, sh
                          w, h   = the whole desktop, in pixels
                          parts  = its displays, (x, y, w, h), in pixels
                          sw, sh = the box's size on the plane (0: its pixels)
Copy:    copy_of, n, x, y, sw, sh      the same machine, placed again
```

**A box can be resized, freely** - it need not keep its aspect ratio. The
size says only *where borders meet*, never how fast the pointer moves: the
pointer always moves in the pixels of the machine it is on. Only at a border
is the size used, to carry the crossing point across in proportion. So a
768-pixel laptop panel drawn as tall as a 1080-pixel AIO meets it along its
whole height: the panel's top pixel reaches the AIO's top, its bottom the
AIO's bottom. "Aspect ratio" and "Actual size" put a box back.

**A machine can be placed again, as a copy** - always the whole machine. A copy
is a doorway, never a place: where another box touches it, the pointer crosses
to the *real* machine, at the same point of the real machine's border, and
back. That is the wrap-round: a copy of the laptop to the right of the AIO
makes right from the AIO arrive at the laptop's left, and left from the
laptop arrive at the AIO's right.

**The pointer crosses wherever a display of one machine shares an edge with a
display of another**, and keeps its physical position across the edge. Where no
display is adjacent, the edge is a wall. That is how an operating system treats
its own monitors, and it is what makes "the AIO above the laptop's second
monitor" mean what it looks like: up from the second monitor reaches the AIO; up
from the panel beside it is a wall, because nothing is above the panel.

The rules the whole thing rests on:

- **Boxes must touch to connect.** A gap is a wall - dragging snaps, and the
  screen names any machine the pointer cannot reach.
- **Boxes never overlap** - no machine, no copy. A point would belong to two.
  A box dropped on another is pushed clear the short way; anything left
  overlapping is refused by `Desk.check()`.
- **No stretch of a border leads to two places.** With copies it could - the
  laptop's left border touching one thing and its copy's left border
  another. Refused, and named. A new copy is therefore placed clear of
  everything, touching nothing, until it is dragged where it belongs.

### Doorways: the arrangement, compiled

The pointer never reads the picture. `Desk.doors()` compiles it: for each
machine, each side and each border line of its desktop, the stretches that
lead somewhere - which machine, which border, which stretch - in pixels on
both sides, with the mapping between them:

```
u = c + ((2(t - a) + 1)(d - c)) div (2(b - a))      t in [a, b) -> u in [c, d)
```

Integer arithmetic only, pixel centre to pixel centre: equal lengths map one to
one, and one pixel across and one back returns to the start (within a pixel
of the longer side, where the two lengths differ). Doors are indexed by
(machine, side, border line) and found by bisection, so a crossing costs a
dictionary lookup and a binary search. Compiling is where every rule is
checked; it happens once per change, not per movement.

Everything that arrives as an arrangement - from the window, over the network
from the hub, from a saved file - goes through `desk.place()`, which accepts
it or raises `ValueError`. Nothing else: sizes, positions, scales and copies
are bounded (`MAX_*` in desk.py), and a fuzz test throws junk at it.

Rectangles are half-open: a display at `x=0, w=1920` covers 0..1919, and its
neighbour starts at 1920. Touching means one's right equals the other's left.

**What this replaced**, and why: screens joined by *declared links* mapping one
edge onto another proportionally. That model knew whole machines and their four
sides, so it could not say "above the second monitor"; and proportional mapping
meant the pointer did not come out where the picture said it would. It also
crossed gaps, which made a sloppy drop behave differently from the picture.

### Movement (`motion.py`)

A move happens in the pixels of the machine the cursor is on, sliding across
that machine's own displays as its OS would. At the edge of its desktop the
door for that point carries the cursor across, and the rest of the delta
carries on over there. Where there is no door, the move slides along the wall
it hit: tried as horizontal-then-vertical and vertical-then-horizontal,
keeping whichever threw less movement away against walls. That is what makes
a diagonal push along a wall slide instead of stick, and it is also why **a
corner is not a doorway** - two displays touching only at a corner share no
border, so no door.

The walk always ends: each pass either uses up the delta or goes through a
door, which costs one pixel of it. With no resizing and no copies it does
exactly what the older one-plane engine did - a test keeps that engine and
runs both over thousands of random arrangements and moves.

The cursor remembers *which machine* it is on and where on that machine's
desktop, not a point on the plane: if the arrangement changes while it is on the
AIO, it stays at the same place on the AIO's screen.

**Leaving this machine by hand** (`exits`): while this machine drives and its
pointer is on its own desktop, the OS moves it - across its own displays too,
which is none of the link's business. Only a push against the *outside* of the
desktop is ours: the pointer on its last pixel in that direction with no display
of this machine beyond. Checked per display, not on the bounding box, so moving
from a laptop panel onto its own external monitor is never mistaken for leaving.

The wire is unchanged: positions travel as fractions of the target machine's
desktop, so a machine whose resolution changed still lands the pointer on its
screen.

### The arrangement is shared

**The hub keeps the one true arrangement** and sends it (`layout`) after the
handshake and on every change. The window applies a change the moment a box is
dropped - there is no Apply button - so every device shows it within a poll;
Undo (Ctrl+Z) walks back through what was applied. A peer that rearranges in its own window sends
`arrange` to the hub, which applies it and sends it back, so both machines change
together or neither does. The machine holding the baton decides crossings with
its own copy, so an arrangement that reached only one machine - as it once did -
changed nothing whenever the other one was driving.

A machine that connects for the first time is put against the edge of everything
on `side`, until someone drags it. One that reconnects with a different desktop
(a monitor plugged in) keeps its place, and `Desk.resize()` moves whatever was
beside it so nothing ends up underneath.

### The arrangement screen

- each machine drawn as its real displays, grouped, with each display's size
- every crossing drawn as a bright line - the answer to "if I push the mouse
  there, where does it go?"
- dragging snaps to other machines' edges and lines up tops, bottoms and
  centres, with dashed guides showing what it snapped to; the view is frozen
  during a drag so the scale does not change under the hand
- arrow keys nudge the selected machine (Shift for bigger steps); a nudge into
  another machine is refused
- the selected machine described in words, and problems listed in words
- In a row / In a column, Apply, Revert; a change is kept until Apply or Revert
  - the window refreshes every 700ms, and a screen that jumped back the moment
  it was let go was a real bug
- a machine that is not connected right now is drawn dimmed

Resizing: the selected box has eight handles - the corners, and the middle of
each side to move one border alone - and a moving border snaps to other boxes'
borders. Copies are placed and removed from the page's buttons, the right-click
menu, or Delete; they are drawn dashed.

Protocol version 3: a version-2 peer would read the new layout as screens with no
links and never cross, so it is refused with a message to update both sides.
Version 7 does the same for boxes with sizes and copies.

---

## 6. Wire protocol

Newline-delimited JSON over TCP. Chosen deliberately: input volume is tiny
(~1 KB/s while moving), and reading the wire with `nc` during a 2 a.m. debugging
session is worth more than the saved bytes.

**Compatibility.** Protocol version 8 is the 1.x protocol, and within 1.x it only
grows: a new message type or an optional field is fine - an older 1.x ignores
what it does not know - but no existing message may change its meaning. A change
that breaks that is 2.0. The version is checked on the first message of every
connection, and a mismatch is refused with a message saying so.

**Framing discipline.** Max line 128 KiB (an encrypted line is base64, a third
longer than the JSON inside it). A peer exceeding it is not slow, it is
broken or hostile — drop the connection. (Input Leap caps at 4 MiB and
disconnects; the principle is theirs, the number is ours, because our messages
are small and bulk data does not use this channel.)

### Handshake — symmetric

```jsonc
-> {"t":"hello","v":2,"node":"aio",
    "screens":[{"name":"aio","w":1920,"h":1080}],
    "policy":{"may_drive":true,"may_be_driven":true}}
<- {"t":"welcome","v":2,"node":"laptop","epoch":7,"holder":"laptop",
    "layout":{...}}
```

Both sides send the same `hello`; neither is "the client". `v` is negotiated,
not assumed, so a mismatch is a clean error rather than a parse failure.

### Baton

```jsonc
{"t":"claim","node":"aio","reason":"motion"}
{"t":"baton","holder":"aio","epoch":8,"screen":"aio","x":960,"y":540,
 "held":[29,42],"toggles":{"caps":0,"num":1}}
{"t":"release","node":"aio"}
```

`baton` is broadcast by the hub and is the single authority on who drives. It
carries the **authoritative held-key set**, so the new holder makes its state
match rather than inferring it from a stream whose beginning it may have missed.
That is the self-healing form of P3.

### Input — all tagged with the epoch

```jsonc
{"t":"p","e":8,"s":"laptop","x":0.4213,"y":0.7105}   // absolute, normalized
{"t":"b","e":8,"k":"left","d":1}
{"t":"w","e":8,"x":0,"y":-1}
{"t":"k","e":8,"c":29,"d":1}                          // evdev code, always
```

Canonical key codes on the wire are **Linux evdev codes**, so no machine's
keyboard layout leaks into the protocol. Messages with a stale `e` are dropped.

### Health

Ping every 400ms carrying a monotonic id; `pong` echoes it, giving true RTT.
Three missed means dead, and P2 fires. The interval is set by the `baton_ttl`
it has to feed, not the other way round. Silence also means release every held key
and reconnect — a network drop must never leave keys latched on a uinput device.

### Clipboard — lazy, symmetric, loop-free

```jsonc
{"t":"clipmeta","origin":"aio","seq":7,"formats":["text"],"bytes":1204}
{"t":"clipget","seq":7,"format":"text"}
{"t":"clipdata","seq":7,"format":"text","i":0,"n":1,"v":"..."}
```

Announce on change, transfer on request, chunked, capped. Copying a 20 MB image
must not stall a screen switch. Any node may originate; `origin` + `seq` stop a
change echoing back to the machine it came from.

### Files — a separate channel

Bulk transfer gets its own connection on its own port. A 2 GB file must never
queue behind mouse positions. Chunked, resumable, any node to any node, with
progress readable from the control API.

Input Leap has file transfer but **not on Linux** (their #855, still open). With
clipboard working on Wayland, this is where we are simply better rather than
merely different.

---

## 7. Connection loss and resume

### Noticing

Detection is by our own ping (2s tick, 3 misses), **not** by TCP. A dead peer can
take minutes to surface as a socket error; the ping calls it in about six
seconds. Death immediately and independently triggers P2 (un-suppress) and P3
(release every held key) on every node — no node waits for permission from
another to become usable again.

**While disconnected, each machine is simply a normal computer.** Both mice work,
both keyboards work, nothing is suppressed. That is the only acceptable failure
mode, and it falls out of P2 rather than needing its own code path.

### Retrying

A fixed interval — today's `time.sleep(1.5)` in `client.py` — is wrong in both
directions at once: too slow for a Wi-Fi blip, and far too fast for a machine
that is simply switched off, which it will hammer all night.

```
attempt 0   immediate                       most drops are transient
otherwise   d = min(cap, base * 2**n)       base 250ms, cap 15s
sleep       d/2 + random(0, d/2)            equal jitter
```

giving roughly `0, 0.19, 0.38, 0.75, 1.5, 3, 6, 11, 11-15, …` seconds.

Jitter is not ceremony. Without it, two nodes dropped by the same event retry in
lockstep forever, and the backoff can phase-lock with whatever periodic thing
broke the link in the first place.

**Flap guard.** The attempt counter resets only once a connection has been
healthy for `stable_after` (5s) — *not* on connect. A link that connects and dies
again in 200ms is failing, and resetting on connect would retry it at full speed
indefinitely.

**Connect timeout 2s**, not the current 6: the backoff should set the pace, not
a blocking syscall.

**Escalate in kind, not only in delay.** After `rediscover_after` (2) consecutive
failures, alternate the remembered address with a search by name. A peer whose
DHCP lease moved is unreachable at its old address no matter how patiently you
wait — more delay cannot fix the wrong destination.

### Finding the peer by name

Nobody types an address. A pairing is recorded as the peer's **name**, its
permanent **device ID** (random, made on first run, survives a rename), and the
address it was **last found at** — a cache, never typed. Dialling tries the
cached address first, because it is usually still right; otherwise, or after two
misses, it searches (`discovery.py`):

```
who   {"app":"nishro-link","t":"who","q":<name|id|*>,"from":<id>}
here  {"app":"nishro-link","t":"here","name","id","port","waiting","group","alone"}
```

Questions go out on UDP to the broadcast address, each local /24's broadcast
address and the site-local group `239.255.87.70`, three times in about a second;
answers come back unicast. UDP port = the link's TCP port, so one firewall rule
covers both. ARP and ping were rejected because they answer the wrong question:
they say a machine exists, not what it is called or whether it runs this.

The hub's first message (`auth`) carries its ID. A dialler that reaches a
different device at a remembered address — the lease went to another machine
running Nishro Link — stops there, before sending any proof, drops the address
and searches.

**An answer is not trusted and need not be:** anything on the network can claim
to be "aio", but the handshake still requires proof of the password, and nothing
is injected into a machine that has not proved it. What a fake *can* do is take
one proof away and guess offline — so every device **generates** its password,
and a chosen one must be at least 8 characters.

The generated password is **four words** (`pairing.py`, from the EFF short word
list in `words.py`: 1,239 words, 41 bits), because it is read off one screen
and typed on another. What is proved is not the password but **a slow key**
made from it - PBKDF2-SHA256, 2^19 iterations, salted with the hub's device ID
(protocol v5) - so every offline guess costs 2^19 hashes: 60 bits of work, more
than the twelve random characters it replaced (59). It takes half a second,
once per password, on the slowest machine it runs on. Dashes, spaces and
capitals do not count.

### Groups: adding, joining, leaving, removing

Every device is always in a group. A fresh install is a **group of one**: it
listens, and shows its name and a generated password on the Devices page. There
is no "which side are you" step - that choice, made twice by two people, was
the least friendly part of pairing.

Adding a device is typing **the password shown on the device you picked**, and
what happens depends on what it is (`here` says: `group` = its hub's name,
`alone` = a group of one):

- **on its own** → *invite*: both sides prove the new device's password, bound
  to both sides' X25519 values as on any link, and the connection is encrypted
  from there on. Only then does this device hand over the group's hub and the
  group's password - which is **also sealed under the new device's own
  password** (`protocol.wrap`: ChaCha20-Poly1305 under a key from HKDF-SHA256
  over that password's key and both handshake challenges). The new device then
  dials the hub like any member. Any member can invite, not only the hub.
- **in another group** → *join*: this device checks the password with that
  group's hub first (`probe`: both sides prove it, nothing is registered), and
  only then switches over. A wrong password changes nothing on either side.

A device with devices of its own never joins another group - they would be
stranded; it can only add. Leaving (a member) or being removed (by the hub)
makes a group of one again, **with a new password of its own**: the old group's
password stays with the old group.

A removed device that was switched off still knows the password, so the hub
keeps its ID and refuses it (`err` code `removed`) until someone pairs it again
on purpose (`hello` with `join`). The dialling side stops retrying on anything a
retry cannot fix - a wrong password, removed, a name already taken - and says
so, instead of retrying forever while the window said "looking for it".

A new password on the hub goes to every member connected at that moment,
over the encrypted link and sealed under the old one as well (`rekey`). One that was switched off is refused on
return and asks for the new one.

### From boot: running as a service

A per-user program starts only after someone signs in, and on Windows cannot
touch the lock or sign-in screen at all. So on both systems the engine runs as
a system service, and the window only attaches to it (`service.py`: the
control API's port and token in a handle file - readable by root and the
`input` group on Linux; the service's own page then needs the token too, its
controls including the password).

- **Linux** (`nishro-link.service`, root, from boot). Input needs no session:
  evdev and uinput work below the desktop, at the login screen, the lock screen
  and every session alike. The monitor layout and the clipboard do need one,
  so they run inside logind's active session on seat0, as its user
  (`session.py`); at the login screen there is none, and the kernel's mode list
  gives the screen size.
- **Windows** (the `NishroLink` service, SYSTEM). Session 0 has no screen, and
  the lock and sign-in screens live on the Winlogon desktop, which ordinary
  programs cannot reach. The service keeps the link; a desk agent
  (`agent.py`) runs in the console session as SYSTEM on whichever desktop is
  showing - started with the service's own token moved into that session
  (`winsvc.py`) - and relays input, the screens and the clipboard over a
  loopback socket. When another desktop comes up the agent says where and
  exits, and the service starts one there; the link never drops.

### Editing a device

- **Rename.** A device's name is baked into the arrangement, the baton, the
  arbiter and the cursor, so a rename resets those and reconnects; the hub
  recognises the machine by its permanent ID and keeps its place. The hub
  renames others: one that is on is told now (`rename`); one that is off is
  renamed at the hub at once and told when it returns (`err` code `rename`).
- **Control rights** are each device's own policy (`may_drive`,
  `may_be_driven`), carried in `hello`, sent to the hub when changed
  (`policy`) and to everyone in the roster. A device that may not be driven is
  a wall for every driver (`reachable()`), not only a machine that ignores
  what arrives. The hub sets another device's rights (`set_policy`) while it
  is on - it is the one that applies them.
- **Details**: each device's version travels in `hello`; the hub keeps when
  each joined and was last seen, and shares both in the roster.
- **From any device.** A member renames, removes or sets the rights of
  another device by asking the hub (`manage` / `manage_result`); the hub,
  which keeps the group, carries it out exactly as if done there and answers.
  Everyone in a group knows its password, so everyone is trusted to manage it.
  While the hub is out of reach, a member can change only itself.

### What resume actually means

Two kinds of state, with very different lifetimes.

**Input state is never replayed.** On reconnect the baton is re-granted under a
**fresh epoch**, so any events still in flight from before the drop are discarded
on arrival by the existing epoch rule. The cursor is restored to its last
position. The held-key set starts **empty** — P3 already released everything, and
restoring it would re-press keys the user has physically let go of.

```jsonc
-> {"t":"hello","v":2,"node":"aio","resume":"<session id>", ...}
<- {"t":"welcome","v":2,"resumed":true,"epoch":9,"holder":"aio",
    "screen":"aio","x":960,"y":540,"layout":{...}}
```

`resumed:false` — unknown or expired session — means a clean handshake. The
layout is re-sent either way, in case it changed while the peer was away.

**Bulk state resumes for much longer.** File transfers are keyed by transfer id
and byte offset, persisted, and continue from where they stopped; those bytes are
on disk and stay valid across a reboot. The clipboard re-announces its latest
`clipmeta`, so a peer that missed a copy catches up without the data being pushed
at it.

### No hub, no baton

If the vanished node *is* the hub, nothing can arbitrate. Every remaining node
stays un-suppressed and behaves as an ordinary computer until it returns.
Degrading to "just a normal PC" is always the correct failure.

---

## 8. Security

**Authentication.** Mutual challenge-response with HMAC-SHA256 over a slow key
made from the password (PBKDF2-SHA256, 2^19 iterations, salted with the hub's
device ID): the password never crosses the wire, each side proves it to the
other, both names and both key-exchange values are in the signed transcript,
and nothing is injected before the peer has proved itself. Each device
generates its own password (see *Finding the peer by name*).

**Encryption** (protocol v8, `secure.py`, the `cryptography` library). Every
connection - a member dialling its hub, a device being added, a password
check before joining - runs an ephemeral **X25519** exchange inside the
password handshake: `auth` and `hello` carry each side's public key, and both
are bound into the password proofs, so a machine in the middle cannot swap its
own in without remaking a proof it cannot make. A key that is not 32 bytes, or
that makes an all-zero secret, is refused. Session keys come from
**HKDF-SHA256** over the X25519 secret *and* the password key, salted with both
challenges: one key per direction.

Every frame after the handshake is sealed with **ChaCha20-Poly1305** (RFC 8439)
and sent as one base64 line. The nonce is the frame's number in that direction
and is never sent: a dropped, replayed, reordered or altered frame fails
authentication and ends the link.

A group's password, when it is handed over (adding a device, `invite`) or
changed (`rekey`), travels inside that encrypted link **and** is sealed again
under the receiver's password key (`protocol.wrap`: ChaCha20-Poly1305 under a
key from HKDF-SHA256 over the password key and two fresh challenges).

Why not TLS: Python's TLS cannot key a connection from a shared password
before 3.13 (the Windows build is 3.11), and certificates would mean a
certificate authority or trust-on-first-use prompts. The primitives are the
ones WireGuard and TLS 1.3 use, from a vetted library; only their assembly is
ours, and `test_secure` checks it - the RFC 7748 test vector, degenerate keys,
tampered frames, and a tap on the wire that sees no key press, baton, layout
or invitation in the clear.

Forward secrecy: the X25519 keys are thrown away with the connection, so
traffic recorded today stays unreadable even if the password is learned later.

What is **not** protected, and other limits, are in
[docs/security.md](../docs/security.md).

---

## 9. Module layout

Pure logic is kept apart from OS calls, so the interesting parts are testable on
any machine including CI.

```
link/
  protocol.py     wire format, LineChannel, version negotiation, size caps
  desktop.py      this machine's monitors, as one desktop
  desk.py         the arrangement: machines, displays, what     [pure]
                  touches what, snapping, problems
  motion.py       the pointer moving across the desk; when      [pure]
                  this machine's own pointer leaves it
  discovery.py    finding a device by name on the network
  pairing.py      generated passwords
  ui_tk.py        the window; ui_arrange.py the arrangement
                  screen; ui_pair.py adding a device
  baton.py        claim policy, epochs, arbitration, watchdog   [pure]
  health.py       keep-alive, RTT, dead-link detection
  reconnect.py    backoff, jitter, flap guard, session resume    [pure]
  config.py       one JSON document, atomic writes
  capture_win.py  WH_MOUSE_LL / WH_KEYBOARD_LL -> events        (obeys P1)
  capture_linux.py evdev read + EVIOCGRAB                       (must obey P6)
  inject.py       Windows SendInput / Linux uinput
  keymap.py       VK <-> evdev
  clip.py         lazy, chunked, multi-format
  files.py        bulk channel
  control_api.py  localhost HTTP for the tray UI
  node.py         one peer: capture + inject + baton  (replaces server/client)
  nishro_link.py  CLI
  tests/          in version control this time
```

`server.py` and `client.py` collapse into `node.py` once the baton lands — under
this model they are the same program with different config. `edges.py`, and
after it `layout.py` + `cursor.py` (declared links, proportional spans), are
superseded by `desk.py` + `motion.py` - see section 5.

### Linux input backend

We inject via **uinput**, not XTEST and not libei/portal. Input Leap went the
portal route, which is the blessed and sandboxable path — and which still cannot
do clipboard on Wayland. uinput needs a device permission but behaves identically
under X11 and Wayland with no compositor cooperation. Portal stays on the list as
an *additional* backend, never the only one.

---

## 10. Known platform limits

- **Windows secure desktops.** A thread cannot switch desktops while it holds
  hooks, and the lock screen, the sign-in screen and UAC prompts are on the
  Winlogon desktop, which ordinary programs cannot touch. The service therefore
  runs as SYSTEM and starts a small desk agent on whichever desktop is showing
  (`agent.py`, `winsvc.py`); when that changes, the agent exits and a new one
  starts there. The link itself never drops.
- **`GetSystemMetrics(0/1)` is the primary monitor**, not the virtual desktop.
  The layout model makes this explicit instead of assuming one screen per node.
- **evdev needs permission.** Capturing on Linux means read access to
  `/dev/input/*` (the `input` group), and injecting means write access to
  `/dev/uinput`. That is the price of working the same on Wayland, X11 and the
  login screen, without a desktop portal - and why there is no Flatpak or Snap.
- **Linux monitor changes** reach the arrangement at once, but the injector
  keeps the screen size it started with until the program restarts.

---

## 11. Where it stands

Done, and verified on real hardware (a Windows 10 laptop and an Ubuntu
desktop on Wayland):

- the arrangement, the pointer moving across it, resizable boxes and copies
  for wrap-around (`desk.py`, `motion.py`);
- the baton, its watchdog and the safety properties (`baton.py`);
- reconnection and session resume (`reconnect.py`);
- both machines capturing and injecting (`node.py`), groups of any size with a
  hub, managed from any device;
- pairing by name and a generated word password; encryption on X25519,
  HKDF-SHA256 and ChaCha20-Poly1305 (section 8);
- the lazy, chunked clipboard, working under Wayland (`clip.py`);
- system services on both, working at the login and lock screens;
- the window, a Windows installer and a Debian package.

Live-run bugs have mostly been of one shape - *state that is correct on the
machine that changed it and never reaches the other one*:

- an idle holder looked like a dead link (watchdog fed only by input, and the
  keep-alive ran slower than the TTL it was meant to feed)
- nobody told a machine the cursor had left it, so it kept typing locally
- a stale anchor made both machines claim the baton off each other forever
- a stale claim, granted just after a handover, took control back to a machine
  nobody had touched

Next, roughly in order: images and files over the clipboard (the file channel,
resumable by transfer id and offset); monitor changes on Linux without a
restart; *Find the pointer* beyond GNOME.
