# Test log

Newest first. What was run, on what, and what came out - so a result is not
re-earned, and a failure is not repeated. Machines: "the laptop" (Windows 10,
the hub) and "the AIO" (Ubuntu 26.04, GNOME, Wayland).

## 2026-10-19 - the maintainer's first use: keys, tabs, and windows moved twice

He used a build of last night's branches on both machines. What he found,
and what each turned out to be:

**Ctrl+A in a moved Notepad typed "a"; Shift gave no capitals.** Keys reach
a moved window as posted messages, which change nothing in a program's
keyboard state - and shortcuts (GetKeyState) and capitals (TranslateMessage)
both read that state. Measured on a hidden Notepad of the probe's own:

| | Shift, then a | Ctrl, then a |
|---|---|---|
| posted, as before | "a" | "a" typed |
| the character itself (WM_CHAR) | "A" | 0x01 selects all - Notepad's Edit only |
| state set, then let go (AttachThreadInput) | "a" - the state does not outlive the sharing | - |
| state set, WaitForInputIdle, let go | - | "a" typed: no fence for posted messages |
| **state held for as long as the key is held over there, let go 40 ms after its release** | **"HI", then "j" once let go** | **all selected, nothing typed** |

Built as the last row. Through `window_win.Window` alone, on a real
Notepad: Ctrl+A selected all 11 characters, Shift h i then j gave "HIj",
Caps Lock a b then off c gave "ABc", Ctrl+Shift+Left selected one word.

**Clicking another tab in a moved browser did nothing.** The click on Edge's
tab strip was aimed at its "Intermediate D3D Window", a drawing-only child
over the strip (extended style 0x280024: click-through, layered) that takes
no input; posted to Edge's own window at the same place, the tab changed.
The aim now passes click-through children, as Windows' own does: on a real
Edge with two tabs, each chosen in turn.

**Found while looking, not reported:** the tray's "window in front" could
be a window ALREADY moved - hidden, it is still visible to Windows, at the
top of the stack. Moved again, its hidden look would have been written down
as the one to put back: home, invisible, for good. Refused now in three
places (why_not, hide, the node). And with a frame in front, the tray
offered the window under it; the real window in front is used now, and a
frame - another computer's window - is never moved on from here (its
window property says what it is).

Tests: 2 for the window moved twice, 1 for the node; 4 for frames and the
window in front; 6 for Ctrl, Shift, Caps, Alt, nothing held at home, and a
click past a click-through child (the test window has one on request); 1
for the frame letting go of keys when it loses the keyboard. Each check
broken on purpose was caught.


## 2026-10-18 - windows from the AIO (stage 5): built, checked short of the dialog

`window_gnome.py`: one RemoteDesktop portal session with a ScreenCast WINDOW
source, pictures from PipeWire through GStreamer, input through the same
session. Read first, in the sources:

- GNOME's portal (`xdg-desktop-portal-gnome`, screencast.c) accepts a WINDOW
  source on a remote-desktop session - no monitor-only rule - so one dialog
  covers the window and its use, without the Shell extension.
- mutter (meta-remote-desktop-session.c): a positive discrete scroll step is
  DOWN (or right), so the frame's "up is positive" is turned over; buttons
  are evdev codes; keycodes go straight to its virtual keyboard; absolute
  motion goes through the stream's own transform, which window streams have.

**On the AIO, everything short of GNOME's dialog** (bridge id 306,
read-only, no session opened): the module loads and says it can
(`why_not("pick")` empty: the portal offers WINDOW sources, keyboard and
pointer, and pipewiresrc, videoconvert and appsink are there); every D-Bus
value it sends builds; `Buffer.extract_dup` gives bytes; a real
`Gst.Sample` comes out as the right picture; the PipeWire pipeline parses,
with `fd` and `path`; the appsink's `try-pull-sample` works without the
GstApp bindings, which the AIO lacks; and the real `Stream.newest()`, fed
GStreamer's own test pattern at an odd width (322x241), gave whole pictures
that changed. GStreamer 1.28.2. One thing it showed: the portal's signal
loop was not yet running the instant after it started, so a close in that
moment would have been lost - it now waits for the loop.

Laptop: 24 tests with a fake portal and stream (size and picture agree
across a resize, logical coordinates on a scaled screen, the wheel the
right way up, evdev buttons and keys, nothing left held down, a "no" in the
dialog in words); 3 for the helper (the window is opened before it says it
is ready, so the refusal reaches the service; Windows' handles still become
numbers); 5 for the tray; status and package dependencies. Three deliberate
breaks - the wheel not turned over, a new size slipping in, the window
opened after "ready" - each caught.

Not yet tried: GNOME's dialog itself, with nothing to parent it; pictures
from a real window, covered or not; where a click lands; typing - all with
the maintainer at the AIO.

## 2026-10-14 - a window really moves between the two machines

**It works.** A window of a test script's own was moved from the laptop to
the AIO and stayed there, on screen, for a full minute - the AIO polled
`wmctrl -l` every half second and saw it at every sample, 53+ seconds with
no gap, titled "NL handover check - from laptop" - then came home by itself
the moment the program behind it ended, with the reason "gone". Four real
faults had to be found first, and none of them could have been found by the
tests:

1. **The Linux helper never started at all.** `self_command()` used
   `python3 -m link.nishro_link`, which needs the current directory or
   PYTHONPATH, and `runuser -u <user> -- env <session vars>` gives neither:
   "No module named 'link'", every few seconds, visible only in journalctl.
   It now runs `launch.py` beside the installed program by full path - which
   is exactly why that file exists (1.0.1, the window that would not open
   from Ubuntu's app menu). The AIO's agent found this.
2. **Every moved window came home after exactly five seconds.**
   `_window_channel` copied the files channel's
   `socket.create_connection(addr, timeout=5)` and left out the line that
   clears the timeout afterwards. Those five seconds are also a READ
   timeout, and a window nobody is touching sends nothing for far longer, so
   the dialling side gave up every time - and from the far end that looks
   exactly like the other computer letting go. Found by making the relay say
   which direction stopped first (it was always the side the other computer
   talks on) rather than swallowing the error.
3. **A log line was read as the readiness answer.** The helper says what it
   is starting before it looks at the window; `attach` read one frame and
   took that log frame as the answer, which has no "ok" in it - so every
   move failed with "that window cannot be moved". It now skips log frames.
4. **A helper holding a hidden window was still killed at once** on the
   service's own path: the wait added for this only understood
   `subprocess.Popen`, and what the Windows service starts in a session
   (`winsvc.Proc`) has `alive()` and `kill()` and no `wait()` at all, so the
   wait raised, was swallowed, and the helper was killed - the one thing
   that leaves a window invisible.

Diagnosis needed three things that are now permanent: the helper sends its
log over the channel (its own output goes nowhere the service can read),
writes it to a file as well (the channel's last and most interesting lines
cannot be sent when the channel is what broke), and the relay says which way
stopped and why. Each helper line is logged once, not twice - the AIO's
agent noticed it arriving both ways.

Also proved on the way: the installed exe works as the helper when driven
directly (meta, a 1848-byte picture, and home on request), and the AIO's
showing half works under the exact `runuser` mechanism the service uses,
with XAUTHORITY present and live pointer input reaching the frame.

## 2026-10-14 (earlier) - moving windows: built, and on both machines

- **The whole Windows path, with the real helper, against a real window**
  (test_window_host.py, 6 tests): offered, hidden only once the frame is up,
  still drawing while hidden, driven, and home again - including home after
  the connection breaks. Three real bugs came out of it, each of which would
  have mattered:
  - the helper was **killed** the instant the connection broke, so it never
    put the window back: a dropped network would have left someone's window
    invisible. It is now given 8 s to finish first;
  - the undo record was written to the service's folder, which is locked to
    SYSTEM and Administrators, so the helper (running as the person) failed -
    **silently, inside the thread that hides the window**, leaving the window
    unhidden with nothing in the log. It goes in the person's own folder now;
  - whether a window can move can only be decided where the windows are, so
    the helper says, and the person gets the reason.
- **The frozen build was checked for the new paths** (all imported lazily, so
  PyInstaller could have dropped them): the exe lists movable windows and
  puts hidden ones back. A windowed build writes nothing to a console, so a
  console test of it looks empty and means nothing - down a pipe it works,
  which is how the service asks.
- **Two things were dead on arrival and only found by using them:**
  - the service, in session 0, listed **0 windows** - it has no desktop. It
    now asks the session over loopback with a one-use token, the same way a
    moved window is handed over; printing does not work because on Windows
    the helper must be started on the person's desktop.
  - the tray's window entries were drawn but **their actions were never
    wired**, so clicking them did nothing at all. Wired, and tested (6 tests).
    The entry moves the window in front, as first specified, rather than
    opening a chooser a tray cannot show.
- **Installed on both machines**, 1.2.0~dev:
  - the laptop: `dev-install-windows.ps1` (elevated). An elevated process
    starts in system32, so the first attempt ran nothing at all with a
    relative script path - absolute paths for anything run elevated.
    Service running, the AIO reconnected 5 s later, the switch on.
  - the AIO: the `.deb` (c62a2aba4fb6f53c), service restarted, tray back as
    the same user, reconnected within a second, and the switch on - set
    through the control API, which logged "settings changed from the control
    UI: move_windows".
- Full suite 1,652 before the last two fixes; ruff clean throughout.

## 2026-10-13 (later) - moving windows: pictures and input, both measured

- **Pictures need no library at all.** PNG written here with `zlib`
  (`picture.py`): a real 916x639 window of text is 12.5 ms and 63 KB at zlib
  level 1 (22.7 ms and 36 KB at level 6); a 64x64 changed square, which is
  what nearly every picture is made of, 0.18 ms and 895 bytes. PNG's row
  filters were tried and **rejected**: byte-by-byte against the row above is
  254 ms in Python for one window, 20x the cost for 10% less data. Tk reads
  these bytes directly (checked: blue stays blue) and copies a square into
  the frame in 0.01 ms, so the away computer decodes nothing.
- **Working out what changed, made 4x faster by measuring, not guessing.**
  Typing in a 916x639 window: 11.45 ms -> 2.74 ms, and a still window now
  costs nothing. Three wins: compare the whole picture first; then a band of
  rows at a time; and **plain `bytes` slices instead of `memoryview`** -
  `bytes.__eq__` is a memcmp while two memoryviews go a slower way round
  (a 234 KB band: 0.27 ms against 1.05 ms). Whole path for typing: 2.74 ms
  diff + 0.7 ms encode, 662 bytes on the wire, ~10 KB/s at 15 pictures/s.
- **Input into a hidden window, without taking the focus - the design
  changed because of this.** The plan was `SetForegroundWindow` +
  `SendInput`, which would take the focus from whatever the person is doing
  on the laptop. Posted window messages instead, aimed at the deepest child
  at the point (`ChildWindowFromPointEx`) and at the focused control
  (`GetGUIThreadInfo`):
  - a hidden Tk window: typing arrived in its entry box;
  - hidden Notepad: text arrived in the editor;
  - **hidden Edge**: both the typed text and a click arrived in the web page,
    at the right coordinates - the hardest case, since Chromium draws its own
    interface with no window per control.
  The foreground window never changed in any of them. Not reached this way:
  programs that read the hardware directly (games) - to be refused with a
  reason rather than half working.
- The rescue record (R6, never lose a hidden window): 8 tests - written
  before hiding, forgotten after, survives a crash, a damaged file does not
  stop windows moving for ever, no file left when nothing is hidden.
- 61 tests in test_winmove.py, 23 in test_picture.py, 34 live. ruff clean.

## 2026-10-13 - moving windows: feasibility, and the channel transport

- **Laptop (section 3):** `tools/win-window-spike.py`, test windows of its
  own (animated Tk, Notepad, Edge). A window made layered at alpha 1 and
  click-through is gone from the screen (Edge: 98% of the screen matched the
  window shown, 15% - chance - hidden) yet still draws (6 distinct captures of
  6), and a hidden Notepad took every typed character through SendInput.
  Off-screen and minimized windows freeze (1 of 6). `PrintWindow`
  (PW_RENDERFULLCONTENT) 15-35 ms; GDI+ JPEG of 800x480 ~5 ms, 18 KB.
- **AIO** (GNOME Shell 50.1, Wayland, through the bridge): the frame side
  works - JPEG decode ~7 ms (GdkPixbuf), Tk shows 800x480 in ~20 ms, 1600x960
  in ~75 ms (Tk is the limit for a big window; ~13 pictures a second). The
  ScreenCast/RemoteDesktop portal **could not open its dialog**: `Start`
  returned nothing, journald logged "Failed to associate portal window with
  parent window", no dialog ever rendered. GNOME 50.1 needs a `parent_window`
  the terminal script had no toplevel to give - a stage-5 note (the helper
  there is a real window); stage 3 shows only the frame on the AIO. A Tk
  window landed ~37 px below where it asked (XWayland decoration).
- **The channel transport** (`041e2cb`): `winmove.BinChannel`, 12 tests. Five
  deliberate breaks of its guards each caught - a length over the cap (caught
  as the hang it would be, which is the DoS the cap stops), a too-short frame,
  a tampered or reordered frame (the seal), the payload length ignored, frames
  sent unsealed. A 2 MB frame deadlocked a single-threaded send test (the
  socket buffer is far smaller) - fixed by sending from a thread, which is how
  the sessions will always work; a lesson in LESSONS.
- Full winmove suite 12 pass; secure, protocol, files-live and node-live
  suites still pass with the shared-code changes. ruff clean.

## 2026-10-11 - Windows builds antivirus programs trust (local, not pushed)

- `test_release_build.py`, 10 tests on the release build as written: the
  bootloader compiled, never the prebuilt one; the program signed before the
  setup packs it; signing skipped until SignPath is set up; each signature
  checked; only the finished files published; the code signing policy; the
  setup naming the firewall rule. Written first: the 9 run then all failed.
  8 deliberate breaks, each caught by its own test.
- `check-signature.ps1`: the unsigned `NishroLink.exe` -> "not signed";
  Microsoft-signed `powershell.exe` -> "signed by CN=Microsoft Windows...,
  not SignPath Foundation". A wildcard matching nothing **first passed**
  (exit 0) -> fixed: "no file to check". The signer pattern takes
  `CN=`/`O=SignPath Foundation`, not "SignPath Foundation Fans". No
  SignPath-signed file on this laptop (985 programs looked at): the passing
  case waits for the first signed release.
- `pyinstaller-from-source.ps1`, in a throwaway environment on this laptop
  (no Microsoft compiler): Windows' `tar` failed on three symbolic links ->
  `tarfile`; waf then set up a 32-bit build with an old MinGW gcc (failed in
  zlib) -> `--check-c-compiler=msvc`: now "msvc: not found", it stops,
  nothing installed, no temporary folder left. **The compile itself is
  untested** - it needs a Release run on GitHub.
- `build-windows.ps1 -Step smoke` (the earlier build) and `-Step app` (a fresh
  one, 36.9 MB): pass; its Properties say "Nishro Link", "1.1.0" exactly. An
  unknown step is refused. `-Step setup` not run here (local Inno builds are
  what Defender flags - LESSONS).
- `release.yml` parsed (PyYAML): 16 Windows steps, the 6 for signing
  conditional; publish downloads `windows` and `linux` by name.
- The files connection, re-checked: "a files connection becomes a link" was
  **missed** (on 10-10 only the then-racy impostor test failed on it) -> new
  live test, the hub hangs up once the copy is dealt with: 15/15 runs; all 5
  breaks of the files connection caught.
- Full suite: **1,442 passed**, 2 skipped. ruff clean.
- Pushed (`1aa2322`). **Release run by hand** (38130664282, builds only):
  green. The bootloader compiled by Microsoft's compiler (Visual Studio 18,
  MSVC 14.51), none of the four the prebuilt ones (`runw.exe`
  `F7667A64...`); the program started before and after the signing slot;
  the setup 13.2 MB, sha256 `F16B216C...`: **Defender found no threats**
  (`MpCmdRun -Scan -ScanType 3`), nor on download.
- **CI on `1aa2322`: 13/15** -
  - Windows, Python 3.8: the big-copy test timed out - its 0.4 ms pause at
    each of 3,072 pieces, and before Python 3.11 a Windows sleep lasts at
    least a timer tick. Fixed (`6b81041`): 4 ms every 16th piece; 1.2 s
    here, 3.9 s with full-tick pauses, still caught without pacing.
  - Debian 11: `test_the_laptop_drives_across_both_aios_in_turn` - aio1
    stayed at epoch 2 while the hub and aio2 went to 3, so aio1 dropped all
    the hub's input as stale. **A real race in the hub**: two members joining
    at once each find nobody else registered yet, and each starts a fresh
    epoch; the first keeps the older one. Here, 40 runs each: 0 failures
    before the push (`2fab8cb`), 0 at `6906f09`, 3 at `5c9be8b` and at
    `1aa2322`; alternating 50 each: 1 at `6906f09`, 4 at `5c9be8b` - older
    than the files work, which makes it more likely. A test with the hub
    slow to register makes it fail every time (5/5). The maintainer: not
    now - the fix and the test wait (STATE, loose ends); `6b81041` is not
    pushed either, so CI on `main` stays red until then.
- **The Release run's builds installed on both machines** (the maintainer
  asked), for the final check before publishing:
  - the laptop: the setup (`F16B216C...`) silently, over the 10-04 build -
    installed in 10 s; the service restarted, the tray came back, the AIO
    reconnected 2 s after the service started. Defender, the installed
    folder (1,009 files): no threats; no detection at all since 10-04.
  - the AIO: the `.deb` (`744900b7...`, checked), `apt-get install
    --reinstall` (the same version number); the service restarted, the tray
    came back as the same user, reconnected to the laptop within 1 s.

## 2026-10-10 - dead links, and files over the clipboard (local, not pushed)

- A link gone silent (`6906f09`): 3 live tests - a hub gone silent is dropped
  and dialled again, a member gone silent is dropped by the hub, a quiet but
  healthy link is kept (`LINK_DEAD_S` 1 s in them). **The real-machine sleep
  test is pending.**
- The files core (`86c73b1`): `test_files.py`, 33 tests (the symlink one
  skipped on Windows). Breaks caught: more data than announced, a damaged
  file kept, a copy over the limit.
- The files connection (`5c9be8b`): 7 live tests. The impostor-hub test failed
  9 of 15 runs - it asserted before its thread had finished; it joins it now:
  20/20. Removing the pacing went unnoticed until the 6 MB test got a slow
  receiver and 2 KB pieces.

## 2026-10-07 - the AIO keyboard's volume dial follows the pointer

- CI on `2fab8cb` (the dial work): **15/15 green** - three jobs only after a
  re-run: Ubuntu's package servers did not answer plain HTTP for a while,
  so they failed or hung at `apt-get` before any test ran.
- On the AIO (1.1.0 as installed): the keyboard shows up as five devices. The
  dial fires KEY_VOLUMEUP / KEY_VOLUMEDOWN on its "... Consumer Control"
  device only - which also lists KEY_POWER and KEY_SLEEP, so Nishro Link did
  not read it ("reads it: False"). The keyboard's other devices list the
  volume keys too but never send them. Each click sends 2-5 quick presses
  (the dial bounces); the AIO's own volume follows them.
- 4 of the 7 new tests failed before the fix (`cc99afc`); full suite 1,387
  pass.
- **Verified on the real machines:** the AIO took the build (`ef6ec409...`),
  reconnected at 16:39:01; its pointer was on the laptop from 16:39:02, and
  the laptop's master volume, sampled every 0.2 s, went 24% -> 48% in four
  steps, down to 20% in three, then back to 30% (16:39:05-08) - the dial's
  clicks, a few presses each.
- The maintainer confirmed: with the pointer on the laptop only the laptop's
  volume changed; with the pointer on the AIO the dial works there as before.
  Asked for one click = one step: a run of the same volume key with under
  150 ms between presses now counts as one, for keys sent on (`731b12a`;
  5 tests, 2 failed first; full suite 1,392 pass; the engine on the real
  clock: one press per click).
- That build on the AIO (`2355738341155edd`): a temporary virtual
  "... Consumer Control" device listing power and sleep pressed volume-up
  three times, a second apart, with the pointer on the laptop - the laptop
  went 32% -> 34% -> 36% -> 38%, one step each, in time. Forwarded keys are
  not logged, by design.
- **The maintainer then found a fast spin did not register** (slow clicks
  did): that build merged any run of presses under 150 ms apart, so a whole
  spin was one step. Now a step at most every 230 ms (the dial's bounce lasts
  up to 187 ms; 200 ms left too little room). A test reproduced the report
  first (a 1 s spin: 1 step), and gives 5 now; full suite passes.
- That build on the AIO (`22f1a3b8e610225a`), a temporary device replaying
  the real dial's bounce timings, pointer on the laptop - the laptop's
  volume: 3 slow clicks up 36 -> 42% (one step each), a 1 s fast spin up to
  52% (5 steps), the spin down to 42% (5), 3 slow clicks down to 36%.
- **Wrong, and taken out:** the maintainer: "if i rotate it one click ... no
  problem, but when i am rotating multiple clicks at a time, then only 2-4
  volume is increased". Measured on the AIO, rolled continuously, the dial
  sends ONE press per click, evenly 7-57 ms apart (16 up in 0.29 s, 19 down
  in 0.39 s). The first trace's "bursts of 2-5 presses per click" were
  quick turns of several clicks, misread as bounce - so the grouping (both
  versions) threw real clicks away. Removed: every press is sent, as the
  keyboard sends it (`node.py` is back to `cc99afc`). A test of a real roll
  (16 presses, 7-43 ms apart, all sent) failed before. See LESSONS.
- **Confirmed by the maintainer** with that build on the AIO (`d4fcc628...`,
  installed 23:49, paced test of single clicks and 5-click rolls, pointer on
  the laptop): "now it's okay" - every click counts, slow or rolled.

## 2026-10-06 - tests for what 1.1.0 left untested

- Coverage of the lines 1.1.0 added (Windows): 62% run by tests. 41 tests
  added where it mattered; now **70%**: `service.py` 37% -> 95% (start and
  stop under Quit and Open, with a pretend `sc.exe` and `systemctl`), the
  launcher 54% -> 93%, the window 76% -> 94% (Make it private's outcomes,
  Cancel on Quit, the Notifications switch), the tray 69% -> 89% (its menu when
  things go wrong, starting it), pairing 100%.
- Each new test was checked against a deliberate break of the code it guards:
  24 breaks, all caught.
- Full suite: **1,380 pass**, 1 skipped (6 min, Windows, Python 3.11); ruff
  clean. CI on `f153064`: **15/15 green** - the new tests pass on Linux and
  Python 3.8 too.
- Not covered by tests, checked on the real machines instead: the platform
  shells (`tray_win.py`, `clip_win.py`, and `tray_linux.py`, which cannot run
  on Windows) and the desk agent's error paths.

## 2026-10-06 - notes pushed

- CI on `8087ffa` (`CLAUDE.md`, `tools/`, `devnotes/` on top of `f106a6b`):
  **15/15 green**.

## 2026-10-05 - 1.1.0 release candidate

- The laptop's 2.4 GHz network was still saved as Public (category 0)
  since 09-30; set to Private (1) in its saved profile, with the
  maintainer's OK. Firewall: no block rules for Nishro Link; six allow
  rules for old development builds (harmless).
- Unit tests: **1,340 pass** (Windows, Python 3.11); ruff clean.
- CI on `f106a6b`: **15/15 green** - lint; Windows 3.8, 3.11, 3.12; Linux
  3.10, 3.12, 3.14; Ubuntu 20.04 and Debian 11 with their own Python and
  libraries; the `.deb` installed on Ubuntu 20.04, 22.04, 24.04, Debian 11, 12.
- Release workflow, build only (run 37223652812): Windows and Linux built,
  publish skipped. Setup sha256 `E299F599BA1EF8A1...`, 13.8 MB: Defender scan
  **clean**; installed silently on the laptop: **no detection**, 1,009 files,
  service and tray up at once, the AIO reconnected within a second, the
  clipboard synced.
- Tools moved to `tools/`: `shot_tray.py` and `social_preview.py` re-run from
  there, same output.

## 2026-10-04 - clipboard images, Defender, folder build

- **Images laptop -> AIO: FAILED** on the real service while every test
  passed: the agent read "nothing". Diagnosed with temporary logging in the
  agent (format list, PNG id, which step fails) - see LESSONS.md. **Fixed**
  (CF_DIB) and **verified**: the AIO logged "an image from laptop (2 KB) is on
  this computer's clipboard"; `xclip` gave a valid 480x270 PNG.
- Images AIO -> laptop: **verified** (a 3 KB icon crossed).
- Text both ways through the agent: **verified** (30 chars read and sent).
- Cross-process clipboard on Windows: image written by one process, read by
  another, pixels identical, both through PNG and through CF_DIB only.
- Defender blocked three local setup builds (setup at run time twice, at
  compile once) - see LESSONS.md. Program folder and old setups: clean.
- Folder (onedir) build: installs, service and tray single processes; closing
  the window leaves the tray's files intact (1,002 before and after).
- AIO `xclip` image round trip as the person: byte-for-byte.

## 2026-10-03/04 - lag and media keys

- Lag: laptop -> router 2-3 ms; laptop -> AIO 5-143 ms (avg 18); AIO ->
  router 2-3 ms at 0.2 s; AIO Wi-Fi power save on; AIO on 2.4 GHz.
- Keep-awake on the driving machine: new test fails without the fix ("only 0
  in 0.4 s"), passes with it (3/3 runs).
- Round-trip clock: new test fails with `monotonic`, passes 5/5 with
  `perf_counter`.
- Volume keys injected on the laptop: master volume 42% -> 44% -> 42%.
- WMI brightness on the laptop's panel: read 50%, set +0 succeeded.
- Media-key device selection (AIO's keyboard): Consumer Control read; System
  Control, Power Button and Video Bus left alone.
- **Not yet verified by hand:** volume keys AIO -> laptop; the resting-mouse
  feel after the fix.

## 2026-09-30 - tray, Quit, public network

- Tray on the AIO: registered (StatusNotifierWatcher), icon follows the state
  (ok -> paused -> ok with sharing off/on, checked by D-Bus property).
- **Windows tray vanished after install: FAILED** -> fixed (Inno order),
  verified: tray running right after a silent install.
- **Linux tray vanished after upgrade: FAILED** -> fixed (postinst), verified
  on the AIO: new pid, same user, clean session variables, no temp dir left.
- Quit and Open: laptop - no prompt, service stopped, no NishroLink.exe left,
  reopened with the window and the tray; AIO - polkit 127, no password either
  way, tray re-registered, reconnected to the laptop.
- Sharing off on the laptop (12:47:47-12:48:05): the AIO's redial was refused
  ("sharing is off on laptop") and reconnected the second it came back on.
- Public network: the laptop's Wi-Fi moved to the 2.4 GHz network (Public);
  the AIO failed 728 dials over 5 h. Detection: `[]` on the Private network,
  0.5 s per check.

## 2026-10-14 - Avro Keyboard acts on no injected key (settled)

Reported twice: F12 from the AIO's keyboard does not switch Avro between
Bangla and English, and with Bangla on, the AIO's keyboard still types
English, while the laptop's own keyboard is fine.

First suspicion was the scan code, and it was a real bug - we sent zero.
Fixed and verified live (`test_inject_win.py` watches a real low-level hook:
A carries 30, F12 carries 88, arrows and right Ctrl carry the extended flag).
Confirmed on the installed build the maintainer was using: NishroLink.exe
1.2.0, sha256 E78BBFD7990F374C, service RUNNING, Avro Keyboard.exe running
(pid 14004). Still broken. So the scan code was not the cause here.

The deciding test (`avro_probe2.py`, a Notepad of its own, nothing of the
maintainer's touched). Avro's mode is not readable - its windows are
`TAvroMainForm1`, `TTopBar`, `TfrmPrevW`, seven `TPUtilWindow`, `MSCTFIME UI`,
`IME`, and no title says Bangla or English - so the test was built not to
need it: type by injection, press F12 by injection, type, press F12, type.
Whatever mode Avro starts in, if an injected F12 switched it at all then one
of the three had to come out Bangla.

    as Avro is now               -> 'amar'   Bangla: False
    after one injected F12       -> 'amar'   Bangla: False
    after a second injected F12  -> 'amar'   Bangla: False

**Conclusion: Avro acts on nothing we send.** It must ignore injected input -
it injects its own Bangla characters, so otherwise it would read its own
output back. There is no user-mode fix: `SendInput` cannot clear the injected
mark, and Windows offers no way to. Only a virtual-HID keyboard driver (a
signed kernel driver) would look like real hardware. Said plainly in the
manual (section 20) rather than left as something to retry.

**A way round, measured and working:** `SendInput` with `KEYEVENTF_UNICODE`
carries a CHARACTER, not a key, and asks no layout and no input method for
anything - so nothing can refuse it. Injected U+0986 U+09AE U+09BE U+09B0 and
read back exactly those four from Notepad (`unicode_probe.py`). So Nishro
Link could do the Bangla itself - transliterate on the home side and send
characters - which would work whatever Avro does. That is a feature of real
size (a phonetic engine); proposed, not built.

## 2026-10-14 - where a moved window's refresh rate actually goes

Asked for: "the communication should be optimized more to share window with
a great refresh rate without laggy". Measured before changing anything, on
the laptop, on a 1000x700 window - the size someone really moves.

Per frame, the three things home does:

| | a Notepad being typed in | a browser of moving colour |
|---|---|---|
| capture (PrintWindow) | 19.2 ms | 21.4 ms |
| diff (which squares changed) | 2.5 ms | 2.7 ms |
| encode (PNG) | 1.2 ms | 71.0 ms |
| **all three** | **22.8 ms (44 fps)** | **95.2 ms (10.5 fps)** |
| bytes a frame | 3 KB | 303 KB |

So `MAX_FPS = 25` is not what holds an ordinary window back, and for the
common case - a document, code, a terminal - only a few small squares change
and the whole thing costs well under a millisecond to encode. **The laggy
case is a window whose whole picture changes**, and there the cost is the
encoder and the bytes, not our pacing.

### zlib's level: measured on real frames, not guessed

picture.py compresses a whole window at level 6, "worth the extra 10 ms to
halve what goes over the air" - measured on text, where it holds. On real
captured frames (12 of each):

| | level 1 | level 6 | level 6 costs | and saves |
|---|---|---|---|---|
| browser, colour | 20.4 ms, 484 KB | 51.5 ms, 302 KB | +31.2 ms | 37.6% of the bytes |
| Notepad, text | 5.2 ms, 33 KB | 12.5 ms, 16 KB | +7.3 ms | 50.0% of the bytes |

A 64 KB sample compressed at level 1 tells the two apart cheaply (0.3-0.8 ms;
text 0.020 of its size, colour 0.271 - a clean line at 0.14), so the level
COULD be chosen per picture rather than by size.

**But it should not be changed.** At level 1 a colour window would need
484 KB x 22 fps = 85 Mbit/s, and at level 6 302 KB x 13 fps = 31 Mbit/s -
both beyond this Wi-Fi. Compressing less only moves the queue from the
encoder to the network, where the pacer (AHEAD = 2) throttles it anyway and
the window gets no fresher. Level 6 stands; written down so it is not
re-litigated.

### What would actually make a busy window smoother

1. **Overlap capture with encode.** They run one after the other, 21 ms then
   71 ms; on two threads a frame costs max(21, 71) rather than 21 + 71 -
   about a fifth off, and a third off for a text window. No new dependency,
   no protocol change. The biggest safe win.
2. **A lossy codec for pictures that are not text.** JPEG would turn 302 KB
   into roughly 40 KB and encode faster - about 7x less to send, which is
   the only thing that would make moving colour feel smooth. It needs an
   encoder AND a decoder, and Tk cannot show JPEG, so it means either a
   dependency (Pillow) or each system's own codecs (WIC on Windows,
   GdkPixbuf on Linux) - a real piece of work, and `winmove.CODECS` already
   leaves room for it.
3. Not worth doing: raising `MAX_FPS` (nothing is waiting on it), or
   raising `AHEAD` (it would queue older pictures, which is exactly what the
   design refuses).

## 2026-10-14 - the AIO updated, and a threshold nobody could reach

### The AIO (reported by the agent there, bridge id 295)

Package `nishro-link_1.2.0-dev_all.deb`, sha256 `8022c788e05e5166` -
checked on arrival before installing. Installed with
`pkexec apt-get install -y --allow-downgrades`, Roni typing the password.
Exit 0, clean unpack and setup, all three dpkg triggers normal. Afterwards:
service active (restarted by the install), tray back by itself (new pid
80284), connected to the laptop (`192.168.0.191:33586 <-> ...:8770`),
journalctl clean - a tidy stop, a start, and a reconnection within the same
second. No window had been shown yet, so the frame's `--window-home-side`
and the drag-home log line could not be checked there; that waits on Roni.

**A trap this exposed:** the AIO already had a `1.2.0~dev` installed from
the previous session, and the new package has the SAME version string with
different contents. `dpkg -l` therefore cannot tell which build is running.
A dev version string needs a build id in it.

### Windows cuts a drag at the edge of the desktop

The live drag test kept failing under load and I kept assuming the test was
at fault. It was not. Stepping a real drag 20 px at a time:

    step 17: pointer x 1066 -> the window at local x 2926
    step 18: pointer x 1086 -> the window at local x 2946
    step 19: pointer x 1106 -> the window at local x 2966
    step 20: pointer x 1126 -> the window at local x 2586   <- snapped back
    ...                        and it stayed there

The window tracks the pointer exactly, then Windows **cuts the drag** and
returns the window to where it was picked up. How far it gets first:

| window width | furthest overhang | as a fraction of itself |
|---|---|---|
| 300 px | 115 px | 38% |
| 500 px | 180 px | 36% |

Both short of the 40% the gesture asked for. On a single-screen machine the
border to the other computer IS the desktop's outer edge, so **the threshold
could not be reached at all** - the window would have snapped back and
nothing would have been sent, with no word why. A real defect in the
gesture, found only because a live test failed.

Fixed: `winmove.far_enough` - 40% of the window or 80 px, whichever comes
first. 80 sits well under both measured limits and is still a deliberate
shove, because the drag has to begin inside the border
(`windrag.START_INSIDE`). Both directions use the same rule.

**Both machines were installed BEFORE this fix** (`CE90E293DA5BC8AF` on the
laptop, `8022c788e05e5166` on the AIO), so both need rebuilding before the
gesture can be tested by hand.

## Before 1.1.0 (from the history)

- v1.0.1 (2026-09-29): fixed the window not opening from Ubuntu's app menu
  (`python3 -m` and a stale `~/link`). CI launches from a folder holding a
  stale copy since.
- v1.0.0 (2026-09-29): first stable release.
- v0.15.2 (2026-09-28): a keyboard plugged in after start did not type
  across; now rescanned every 2 s. Verified: 64 keys counted on the laptop.
- v0.15.1 (2026-09-28): Python 3.8 support; CI found a real 3.8 bug
  (`dict | dict`). Debian 11's security archive 404s: CI drops it.
- v0.15.0 (2026-09-28): first public release; the first Linux CI run found
  three real bugs (UDP `SO_REUSEADDR` port sharing, a port not freed on
  release, a handshake error escaping the dial loop).
