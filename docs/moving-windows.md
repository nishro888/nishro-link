# Moving windows between computers - design and requirements

**Status: designed, being built.** Asked for by the maintainer: "move a window
of a pc to another", as a switch in the settings. This document is the
specification: what it must do, how it works, what was measured before
deciding, and the order it is built in. `link/DESIGN.md` gets a summary when
it ships.

## 1. What it is

A window is drawn by a program running on its own computer, from that
computer's memory, and a Windows program cannot run on Linux at all. So
nothing can carry the program across. What moves is **the window**: the
program keeps running at **home**; its window leaves the home screen and
appears on the **away** computer's screen, live, and the mouse and keyboard
used over it there drive the program at home. To the person, the window has
moved.

This is what remote-application tools do (Windows Remote Desktop's
RemoteApp, Xpra on Linux), for a single window, between computers that
Nishro Link already joins with one mouse and keyboard.

Words used below:

| | |
|---|---|
| **home** | the computer where the program runs |
| **away** | the computer showing the window |
| **a moved window** | a window shown away from home |
| **the frame** | Nishro Link's window on the away computer that shows a moved window |

## 2. Requirements

### What it must do

- **R1 - A switch, off by default.** Settings -> *Move windows between
  computers*, on each computer. A window moves from A to B only when both
  have it on. Turning it off on either brings every window between them
  home.
- **R2 - Move a window away from Windows:**
  - drag it by its title bar across the edge where the other computer is,
    and it carries on there, dropped where the pointer lets go (stage 4);
  - or a shortcut, the same gesture that moves a window to the next monitor
    (Win+Shift+Left/Right), carried on to the computer beyond the last
    monitor;
  - or the tray: *Move the active window to aio*.
- **R3 - Move a window away from GNOME (Linux):** the tray, *Show a window on
  laptop...*, then GNOME's own dialog to pick it. On Wayland no program may
  hide, move or even see another program's window, so there the window also
  stays where it is at home (section 4.2), unless the optional GNOME Shell
  extension is installed (stage 6).
- **R4 - Away, it is a normal window:** shown live in a frame with the away
  system's own title bar ("Notepad - from laptop"), which can be moved,
  resized (the program at home is resized to match) and maximized there.
  Whatever mouse and keyboard drive the away computer - its own, or another
  computer's through Nishro Link - drive the program. **Its close button
  closes the program's window**, as the same button at home would - the
  program may ask to save first (the maintainer's choice, 2026-10-13).
- **R5 - Bringing it home:** the shortcut the other way (Win+Shift+Left/Right
  towards home), dragging the frame back across the edge, the tray's *Bring
  windows home*, or turning the switch off.
- **R11 - What the window opens goes with it:** its menus, dialogs ("Save
  changes?"), drop-down lists and tooltips appear with it, away - each a
  window of its own at home, owned by the moved one, shown in a frame of its
  own at the same place relative to it. Without this even Notepad's File
  menu would open on the home screen. A new, separate window of the same
  program (a second browser window) opens at home, as it would.
- **R6 - Never lose a window.** A moved window comes home when: the window
  channel or the link drops; either computer's Nishro Link stops, restarts
  or crashes; the away computer locks or signs out; the switch goes off.
  Before a window is hidden, home writes down how to bring it back; at its
  next start, Nishro Link restores anything that record still lists.
- **R7 - The program at home ends:** the frame closes, with a word in the
  tray.
- **R8 - Several at once:** up to four moved windows, either way.
- **R9 - What can move:** ordinary top-level windows of the person's
  programs. Not Nishro Link's own windows, the desktop or taskbar, a
  minimized window, a window on the lock screen, or a window that cannot be
  hidden safely (one that draws its own transparency, section 4.1) - each is
  refused with a reason that says which.
- **R10 - The title follows.** Renames at home (a browser's tab title) show
  on the frame.

### How well

- **N1 - Responsive:** typing shows within about 100 ms on home Wi-Fi; a
  normal window updates at 15-25 pictures a second; a window never runs more
  than two pictures behind - the newest wins.
- **N2 - Bandwidth:** a few hundred KB/s for a document or a web page being
  read; capped (2 MB/s at first) for a window playing video, by lowering the
  picture rate and quality.
- **N3 - CPU:** capture and compression at most about a third of one core at
  home, for a 1280x720 window at 15 pictures a second.
- **N4 - The pointer never waits:** pictures travel on a connection of their
  own, never the link's (as files do).
- **N5 - Private:** only between paired devices, proved with the group's
  password both ways and encrypted like the link, with keys of its own;
  off unless both sides switch it on; the frame always says where the window
  lives; nothing of the window is written to disk.
- **N6 - Every 1.x works with every 1.x:** an older peer neither offers nor
  accepts windows; the tray says it needs a newer Nishro Link there.
- **N7 - No new Python packages, and no new library for pictures at all.**
  Encoding is `zlib` and nothing else (`picture.py`), the same code on both
  systems; Tk, already there for the window, both shows the pictures and is
  the frame. Windows uses its own APIs through `ctypes` (PrintWindow for the
  capture, posted messages for the input). Only **stage 5** - showing a
  window that lives on GNOME - needs anything more: GStreamer and PipeWire
  through the desktop portal (`gir1.2-gstreamer-1.0`,
  `gir1.2-gst-plugins-base-1.0`, `gstreamer1.0-pipewire`, which a GNOME
  desktop normally has; the setting says what is missing).
- **N8 - The safety rules hold** (DESIGN.md, section 4): no input hook waits
  on window work (P1); both Ctrl keys still give the person their input back;
  input played into a moved window is never read back as the person's (P6).

### Not in the first version

Sound; video smoother than the picture rate allows; games; drag and drop into
or out of a moved window; scaling between screens of different density (shown
1:1); moving between two members of a group of three or more (through the hub,
later); the lock and sign-in screens.

## 3. What was measured first

Before designing around it, the laptop (Windows 10) was asked: can a window
leave the screen and still draw, be captured and take typing? Test windows of
the script's own (an animated Tk window, Notepad, Edge with a throwaway
profile), each captured six times with `PrintWindow(PW_RENDERFULLCONTENT)`;
"drawing" = more than one distinct picture:

| | shown | covered | hidden (layered, alpha 1, click-through) | off-screen | minimized |
|---|---|---|---|---|---|
| Tk, animated | drawing | drawing | **drawing** | frozen | frozen |
| Edge, animated page | drawing | **frozen** (see below) | **drawing** | frozen | frozen |

- **Covered, Edge freezes - hidden or not.** The first check covered each
  window with a borderless always-on-top Tk window, and read "drawing"; but
  Chromium does not count a window like that (a tool window) as covering
  anything. Covered by an ordinary window - a maximized Notepad in front -
  Edge stops drawing within a second, shown or hidden, and starts again
  once uncovered (2026-10-18). That is Chromium's own native window
  occlusion tracking, so it is the same for Chrome and for programs built
  on it (VS Code, Teams, Discord). A moved window is therefore also made
  topmost (4.1), and drew on, covered, in every check.

- Hidden that way, a window really is gone from the screen (Edge: 98% of the
  screen matched its picture when shown, 15% - chance - when hidden) and keeps
  drawing.
- A hidden Notepad, made clickable again and brought to the front, took typed
  text through `SendInput` - every character.
- Moving a window off the screen or minimizing it stops it drawing: neither
  can be how a window leaves home.
- Capture: 15-35 ms a picture.
- **Typing and clicking into a hidden window, without taking the focus:**
  posted window messages arrived in a Tk window's entry box, in Notepad's
  editor, and in a web page in Edge (both the text and a click, at the right
  coordinates). The foreground window never changed in any of them. This is
  what section 4.1 now does.
- **Pictures** (see `picture.py` for the full table): PNG written here with
  `zlib`, no library needed - a whole 916x639 window of text in 12.5 ms and
  63 KB, a 64x64 changed square in 0.18 ms and 895 bytes. Working out what
  changed in such a window takes 2.74 ms, and nothing at all when the window
  is still. Typing in a big window costs about 10 KB/s at fifteen pictures a
  second.

The AIO (Ubuntu, GNOME on Wayland, GNOME Shell 50.1) was asked the same:

- **The frame (away) works.** A JPEG picture decodes in about 7 ms (GdkPixbuf),
  and Tk shows an 800x480 picture in about 20 ms, a 1600x960 one in about
  75 ms - so Tk is the limit for a large window on the AIO (a full screen
  would be ~13 pictures a second), and the picture rate (N1) is set per window
  by what the frame can show, not a fixed number.
- **The portal (GNOME home, section 4.2, stage 5) needs a real window to
  parent its dialog.** A terminal-run script's ScreenCast/RemoteDesktop
  `Start` returned nothing and no dialog appeared ("Failed to associate portal
  window with parent window"). So the session helper there must be a real GTK
  toplevel and pass its handle (xdg-foreign) as `parent_window`. This does not
  affect stage 3 (the AIO only shows the frame then); it is a stage-5 note.
- **A Tk window lands about 37 px below where it asks**, under XWayland
  (window-manager decoration) - the frame's placement must allow for it.

## 4. How it works

### 4.1 Home on Windows

The **desk agent** (`agent.py`) already runs in the signed-in session, on the
desktop that is showing, and already plays input there. It gains the window
work:

- **Listing and choosing:** the active window, or the one being dragged;
  checks for R9.
- **Hiding:** the window becomes a layered window at alpha 1, click-through
  (`WS_EX_LAYERED | WS_EX_TRANSPARENT`). Invisible, out of the way of the
  laptop's own mouse, still drawing (section 3). It is also made
  **topmost**, once invisible: a Chromium window stops drawing when it works
  out it is covered, and nothing covers a topmost window - invisible and
  click-through, it is in nobody's way up there. Coming home it leaves the
  topmost band before it shows (unless it was topmost before), and so comes
  back above the other windows. Its old extended style and
  alpha are written down first (`winmove.Rescue`, a JSON file written whole
  and renamed over the old one), so a crash between hiding and showing
  cannot leave someone else's window invisible for good: the next start puts
  back everything the file still lists (R6). A window that is already
  layered with its own per-pixel transparency cannot be hidden this way and
  is refused.
- **Capture:** `PrintWindow(PW_RENDERFULLCONTENT)` - works for covered and
  hidden windows, DirectComposition ones (Chrome, Edge) included; at most as
  often as the away side acknowledges (4.4).
- **Input: posted straight to the window**, in its own coordinates -
  `WM_MOUSEMOVE`, `WM_LBUTTONDOWN`, `WM_MOUSEWHEEL`, `WM_CHAR`,
  `WM_KEYDOWN`. Mouse messages go to the deepest child window at that point
  (`ChildWindowFromPointEx`, then that window's own coordinates), keys to
  whichever control holds the focus inside that program
  (`GetGUIThreadInfo`).

  This replaced the first plan - bring the window to the front and use
  `SendInput` - because home is a computer the person is still using, and
  taking their focus every time they typed in the window on the other screen
  would be unusable. Measured instead (section 3): posted messages reach a
  hidden window and **never move the focus**, in a Tk window, in Notepad,
  and in **Edge** - the case most likely to refuse, since Chromium draws its
  own interface with no window per control, and both the typing and a click
  landed in the web page at the right place.

  What this does not reach is a program that reads the hardware directly
  (`GetRawInputData`, DirectInput) - games, mostly. Those are refused with a
  reason rather than half working (R9), once there is a way to tell; until
  then they simply will not respond, which the person sees at once.
- **Coming home:** the old style and alpha back, a redraw, the record
  cleared.

Pictures travel from the agent to the service on a second loopback
connection of their own, already compressed; the service only relays them.

### 4.2 Home on GNOME (Linux) - to be confirmed on the AIO

Wayland lets no program see, move or hide another program's window. GNOME's
answer is the desktop portal: **ScreenCast** for one window's picture (from
PipeWire), **RemoteDesktop** for pointer and keys aimed at that window, both
granted by the person in GNOME's own dialog. A **session helper** - Nishro
Link run as the person, in their session - does this:

- the dialog, then the window's pictures through GStreamer (`pipewiresrc` to
  an `appsink`), compressed with GdkPixbuf;
- input through `NotifyPointerMotionAbsolute` in the window's own
  coordinates, `NotifyPointerButton`, `NotifyKeyboardKeycode` (evdev codes,
  which is what Nishro Link speaks already).

What GNOME cannot do without help: hide the window at home, or see it being
dragged. So from GNOME the window **stays visible at home** and is shown on
the other computer as well, and it must stay uncovered for clicks to land on
it - the maintainer's choice for now (2026-10-13). An optional **GNOME Shell
extension** (stage 6) can do both later - it runs inside GNOME itself - at the
cost of an extra part that GNOME releases can break and that needs one
log-out to start.

The service is root and outside every session, so the helper is started in
the session (like the tray, at log-in) and attaches to the service the way
the tray does (`api.json`), then holds a loopback connection.

### 4.3 Away: the frame

An ordinary window of Nishro Link's, on both systems: a Tk window (as the
rest of the program), titled "Notepad - from laptop", showing the pictures
1:1. Its own events become input for home:

- pointer moves, buttons and the wheel, in the window's coordinates;
- keys, as evdev codes, while the frame has the keyboard focus;
- resizing the frame asks home to resize the window; maximizing fills this
  screen's work area;
- closing it closes the program's window at home (R4) - `close`, which home
  turns into the window's own close request (`WM_CLOSE` on Windows).

The frame takes its events from its own system, like any window, so it works
whichever mouse drives the away computer - its own, or another computer's
through Nishro Link.

On Windows the frame lives in the desk agent (it is on the desktop that is
showing); on Linux in the session helper.

### 4.4 The window channel

Like the files channel (DESIGN.md, section 6), and for the same reason: a
window's pictures must never queue in front of the pointer.

- **One connection per moved window**, dialled by the member (only the hub
  listens), proved and sealed exactly like the link - the same password proof
  both ways, its own keys. `window` in the hello says which window; the hub
  never treats it as a link. When the hub is home, it asks the member over the
  link to dial (`wopen`), as it does for files.
- **Binary frames after the handshake:** a 4-byte length, then the sealed
  bytes - no base64 and no 128 KB line limit, which pictures would otherwise
  pay twice (the link base64s every sealed line, and JSON would base64 the
  picture first). `secure.py` gains `seal_bytes` / `open_bytes`; the nonce is
  still the frame's number in its direction.
- **Inside a frame:** a 2-byte header length, a small JSON header, then the
  payload.

| home -> away | |
|---|---|
| `meta` | title, size, the program's name |
| `pic` | picture number; the changed rectangles: x, y, w, h, codec (`jpeg`, `png`), length; the payload is the encoded rectangles, one after another |
| `gone` | the program ended, or the window can no longer be shown; why |

| away -> home | |
|---|---|
| `ack` | the last picture shown - home sends at most two ahead |
| `in` | input: `move` x y, `btn` name down, `wheel` dx dy, `key` code down |
| `size` | w h - the frame was resized |
| `close` | the frame's close button: close the program's window |
| `home` | bring it home (the tray, or the frame dragged back across the edge) |

- **Pictures:** the whole window the first time, after a resize, and every
  ten seconds; otherwise only what changed, found by comparing 64x64 tiles
  with the last picture sent and joining neighbours into rectangles.
  Everything is PNG, written by hand with `zlib` (`picture.py`): no GDI+, no
  GdkPixbuf, no Pillow - the same code on both systems, text stays sharp,
  and Tk reads the bytes as they are, so the computer showing the window
  decodes nothing and copies a square into place in about 0.01 ms.
- **Pace:** home captures again only when fewer than two pictures are
  unacknowledged, so the rate follows what the network and the away computer
  can take, and a slow link shows a slower but current window - never a
  queue of old ones. Over the cap (N2), home lowers the picture rate first,
  then the JPEG quality.

### 4.5 On the link

New, optional messages - an older peer ignores them (N6):

- `wins` in the hello and the roster: this device has the switch on, and
  which ways it can move windows (`home`, `away`).
- `wopen` - the hub asks a member to dial a window channel (as `fwant`).
- `whome` - bring every window between us home (the switch went off).

### 4.6 Moving by dragging - built, then taken out

Both halves were built and worked: a window thrown at another computer by
dragging it off the edge of the screen, and a frame dragged back the same
way. The maintainer then decided against the gesture and asked for the
tray menu to be the only way, both directions
(`git log -- link/windrag.py` has it, if it is ever wanted again).

Worth keeping from it, because they were measured rather than reasoned:

- **Windows CUTS a drag once a window hangs about a third of its width off
  the edge of the desktop**, and snaps it back to where it was picked up.
  Measured on the laptop: a 500-wide window reached 180 px of overhang, a
  300-wide one 115. Any gesture judged at the edge of a screen needs a pixel
  limit, not just a proportion of the window.
- **A WinEvent hook and its message pump must be on the same thread.**
  `WINEVENT_OUTOFCONTEXT` posts to the queue of the thread that installed the
  hook; split them and not one event arrives.
- **`GetWindowRect` counts from the PRIMARY screen's corner**, not the
  desktop's, so with a monitor to the left every x is negative. Local
  coordinates - the wire, the desk, `inject.py` - count from the desktop's
  corner.

### 4.7 When things go wrong

| | |
|---|---|
| the window channel or the link drops | away closes the frame at once; home brings the window home at once |
| the program at home ends | home sends `gone`; the frame closes; the tray says so |
| the window is minimized at home | home restores it - minimized, it would stop drawing |
| home's Nishro Link stops or crashes | the record (4.1) brings the window back at its next start; on a clean stop, at once |
| away's Nishro Link stops | the channel drops: home brings the window back |
| the away computer locks | its desk agent moves to the lock screen and the frame goes; the channel drops; home brings the window back |
| the switch goes off on either | `whome`; everything between them comes home |

## 5. Settings and the tray

- **Settings -> Windows:** *Move windows between computers* - "A moved
  window's pictures, and what you type into it, travel to the other computer,
  encrypted like everything else on the link." Off by default. Below it, what
  this computer can do (for example, on GNOME without the extension: "Windows
  from here are shown on the other computer and stay here too").
- **The tray:** *Move the active window to <device>* (Windows); *Show a
  window on <device>...* (GNOME); *Bring windows home*, while any are away;
  the moved windows, each with *Bring home*.
- **The shortcut (the maintainer's choice):** Win+Shift+Left/Right - Windows'
  own "move to the next monitor", carried on across the edge when there is a
  computer that way and no monitor; on the away computer, the same keys
  towards home bring a frame back. GNOME has the same keys for monitors.

## 6. Testing

- **Pure logic, every platform:** tiles and rectangles; the pacing (never
  more than two ahead); the moved window's states; frame and window
  coordinates; the channel's frames (sizes, unknown fields ignored); the
  record that brings windows home after a crash.
- **Live, two real nodes over local sockets** (as the files tests), with a
  fake window at home and a fake frame away: a window moves and comes home;
  input goes round; resizing; home on a dropped channel; refused when the
  switch is off at either end, with the wrong password, or by an older peer;
  never a link; a slow frame does not queue pictures.
- **On the laptop, scripted:** hide and restore, capture, the GDI+ codec,
  typing into a hidden window - the checks of section 3, kept in `tools/`.
- **On both machines:** laptop to AIO and back, with the AIO's test agent:
  pictures a second, delay and bandwidth on the real Wi-Fi.

## 7. Stages

1. **This design**, and the feasibility checks (section 3; the AIO's next).
2. **The window channel:** binary sealed frames, the messages, the pacing,
   the states - with fakes at both ends and live tests. No window yet.
3. **Laptop to AIO:** Windows home (the agent: choose, hide, capture,
   compress, play input; the window's menus and dialogs with it - R11), Linux
   away (the session helper: the frame), the switch, the tray, the shortcut.
4. ~~**Dragging across the edge.**~~ Built both ways and then taken out at
   the maintainer's request: the tray menu is the only way to move a window
   (section 4.6).
5. **AIO to laptop:** GNOME home through the portals; Windows away (the frame
   in the agent).
6. **Optional - the GNOME Shell extension:** dragging from the AIO, and
   hiding the window there.
7. **Documents** (manual, DESIGN.md, CHANGELOG), the real-machine checks, a
   release.

## 8. Risks and open questions

- Windows programs that will not hide this way (their own per-pixel
  transparency; some Store apps) - refused cleanly; to be tried on the
  common ones: Chrome, Edge, Explorer, Office, VS Code.
- Windows' focus rules (`SetForegroundWindow` from the agent); the parked
  pointer while input goes into a hidden window.
- **A Chromium window covered since it was last in front will not become
  topmost** - SetWindowPos says yes and nothing changes, until the window is
  in front again. Moved like that, it freezes over there whenever a window
  here covers it. Rare, since the window moved is the one in front; the log
  says so when it happens. Bringing it to the front for a moment would fix
  it and take the person's focus - not done.
- A topmost window, even an invisible one, over a full-screen game at home
  may cost the game Windows' fastest way of drawing - untested.
- GNOME: does a covered or minimized window keep drawing for the portal; is
  the dialog needed every time; does a helper started at log-in reach the
  portal - the AIO's test answers these.
- The AIO's 2.4 GHz Wi-Fi, with power saving: the picture rate it carries.
- Tk's speed at showing pictures on the AIO (under XWayland).
- Screens of different density: 1:1 at first.
- GNOME home: whether a window's own pop-ups (menus) are in its portal
  stream.
