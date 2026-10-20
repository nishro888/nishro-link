# State

**Last updated:** 2026-10-15

## Versions

- **Published:** v1.0.1, "Latest" (2026-09-29).
- **1.1.0 - built, verified, not yet released.** On `main`: `f106a6b` (the
  1.1.0 work), then `CLAUDE.md`, `tools/` and `devnotes/`. CI is green on
  `f106a6b` (15 jobs). A build-only Release run (run 37223652812) made the
  setup and the `.deb`; the setup (sha256 `E299F599BA1EF8A1...`) scans clean
  with Windows Defender and installed cleanly on the Windows laptop.
- 1.1.0 holds: the tray (with Quit), notifications, the Linux dock actions,
  Quit Nishro Link, the public-network warning, images on the clipboard, media
  keys that follow the pointer, keep-awake on both ends, the sharing-off race
  fix, the folder (onedir) Windows build, the "Doorway" logo. See CHANGELOG.
- Since then, also for 1.1.0: a keyboard's media device read even when it
  lists power and sleep (the AIO's volume dial follows the pointer, every
  click; verified on both machines), and a link silent for 8 s is dropped
  and dialled again (`6906f09`; tested, **real-machine sleep test pending**).
- And the Windows release build: PyInstaller's bootloader compiled from
  source; signing through SignPath Foundation, skipped until it is set up
  (Next 3).

## The test machines

- **The Windows laptop** (Windows 10, the hub) runs 1.1.0 from the CI
  build-only setup.
- **The Ubuntu AIO** (Ubuntu 26.04, GNOME on Wayland, Intel 7265 Wi-Fi) runs
  a local 1.1.0 build from before the last two Windows-only clipboard fixes
  (those do not touch Linux behaviour). It is on the 2.4 GHz network with
  Ubuntu's default Wi-Fi power saving; the 5 GHz network is strong there and
  the maintainer is to join it once from the Wi-Fi menu.
- Checks on the AIO are made there by the maintainer, or by an assistant
  the maintainer coordinates outside this repository.


## Moving windows (1.2.0 work, on `main`, nothing pushed)

Stages 1-3 are built and the feature works on both real machines. Stage 4
(dragging across the edge) was built both ways, used, and then **taken out**
at the maintainer's request - the tray menu is the only way now, each way.
Section 4.6 of `docs/moving-windows.md` keeps what it taught.

Where it stands:

- **The tray menu is the way.** *Move the current window to* with the
  devices as a submenu - always a list, even for one other computer, since
  there will be more than two. The window it means is named above it
  ("In front: report.txt - Notepad"), because the maintainer could not
  always tell which was chosen. The tray finds that window by looking at
  this computer's own windows; asking the service would start a helper
  process, far too much for every two-second poll.
- **The notification no longer lies.** `/api/window/move` answers as soon as
  the request is on its way, so the tray used to announce success at once
  and the maintainer saw "the window is on the AIO" while it never
  appeared. It now waits up to 12 s for the service to confirm, and says
  plainly when the window did not go.
- **A refusal now carries its reason.** The refusing computer closed the
  channel without a word, so the near end could only report
  "[WinError 10038] not a socket". A `cannot` frame carries the reason: the
  node sends it while there is still a channel, the relay keeps it,
  `window_trouble` holds it, `/api/status` publishes it, the tray shows it,
  and `HomeSession` understands it so the log has it either way.
- **The pointer's arrow** is drawn as soon as the other computer's mouse
  moves it (GetCursorInfo said it was showing while it was not, so it is no
  longer asked).
- **The mouse wheel** scrolls in a moved window.
- **Avro Keyboard cannot be made to work** - settled and written down
  (TESTLOG, manual section 20). Carrying the CHARACTER instead of the key
  (KEYEVENTF_UNICODE, measured working) would sidestep it; proposed, not
  built.
- **The refresh rate is measured** (TESTLOG): an ordinary window manages 44
  frames a second and MAX_FPS = 25 is not the limit; a window of moving
  colour manages 10.5, limited by the encoder and 300 KB a frame. What would
  help: overlap capture with encode, then a lossy codec.

**Still unexplained:** why the AIO refuses a window. Reproduced reliably on
2026-10-15 (04:17) - the dial succeeds, the helper starts, and the channel
dies within a second. The `cannot` plumbing above exists so that the next
attempt says why instead of leaving it to guesswork; it needs the new build
on both machines to speak.

Built and waiting to be installed: `NishroLink.exe` `55F019CB90077621` and
`nishro-link_1.2.0-dev_all.deb` `bd22634c37e3b44d`. The laptop still runs
`7EE7C0D3842FB2C3` (the maintainer declined the install prompt); the AIO
runs `25d0472a0cdc64db`.

## Known flake: the live node-pair fixture and ports

`test_node_live.Pair` picks one port and binds TCP and UDP on it. Across a
whole CI run, with two dozen pairs in test_window_link.py alone, that
sometimes collides - "timed out waiting for both ends to connect (port
NNNNN)". It fails on a DIFFERENT job each run (Linux 3.14, then ubuntu:20.04,
then Linux 3.10), which is the signature of contention rather than a bug in
what is being tested; raising PATIENCE to 60 s did not stop it, which points
the same way.

Not chased yet, and it should be: a fixture that picks a free port properly
(bind to 0 and read it back, for UDP as well as TCP) would end it. Same
family as the hub join race. Until then a red CI job on this test is worth
re-running once before believing it.

**Part of it found (2026-10-19)**: on the jobs that run the UI tests, a
window test could wait minutes for its window - Tk objects the UI tests left
in cycles, freed on a network thread, a second each (LESSONS, Testing).
Fixed in conftest: the window tests no longer time out that way. What still
fails on the headless ubuntu:20.04 job, which runs no UI tests, is the rest
of this entry and the join race (`test_multi_live`).

## Next

1. **Before tagging v1.1.0**, the maintainer's checks by hand
   (RELEASING.md section 3). Still to do:
   - the trays, looked at on both computers;
   - Quit from the tray, by a click, on both, and opening again;
   - volume keys on the AIO's keyboard with the pointer on the laptop;
   - the resting-mouse delay, AIO mouse on the laptop's screen;
   - a real screenshot copied and pasted, each way.
   (Already verified by the tooling: see TESTLOG.md, 2026-09-30 to 10-05.)
2. **Release:** set the date for 1.1.0 in CHANGELOG.md and in
   `link/packaging/deb/io.github.nishro888.nishro-link.metainfo.xml`, push,
   tag `v1.1.0`, then install the PUBLISHED builds on both machines and repeat
   the tray and app-menu checks.
3. **Windows builds antivirus programs trust** (the maintainer chose
   SignPath Foundation, and the bootloader compiled from source). Done
   locally: `release.yml` compiles PyInstaller's bootloader
   (`pyinstaller-from-source.ps1`: prebuilt ones deleted first, Microsoft's
   compiler only, the result checked against the prebuilt ones), then -
   once SignPath is set up - signs `NishroLink.exe`, smoke-tests it, builds
   the setup around it and signs that; each signature checked
   (`check-signature.ps1`); publish takes the finished files by name. The
   README has the code signing policy ("applied for") and the privacy
   statement; the setup's first page names the firewall rule.
   `link/packaging/signpath/README.md` has the setup steps. Pushed
   (`1aa2322`); the Release run by hand is green - the bootloader compiled
   by Microsoft's compiler on GitHub, and its setup scans clean with
   Defender (TESTLOG). Next:
   - the maintainer applies to SignPath; once accepted: the SignPath
     project, the secret `SIGNPATH_API_TOKEN`, the variable
     `SIGNPATH_ORGANIZATION_ID`; then the "not code-signed yet" lines;
   - at the first signing, watch the setup: Inno pads its name and version
     strings with spaces, which SignPath's check may not ignore.
   The uninstaller stays unsigned (signpath/README.md says why).
4. **1.2 - files over the clipboard - in progress.** Copy files, folders or
   several at once (Ctrl+C), paste on the other computer (Ctrl+V). They land
   in Desktop > Nishro Link Files (the maintainer's choice), and that
   computer's clipboard then holds them, so pasting in a folder works; 2 GB a
   copy; progress shown. Steps:
   1. Clipboard formats - Windows: CF_HDROP read and written, checked both
      ways (Explorer's list read exactly; ours read back exactly).
      **GNOME Files: still to check on the AIO** - which target it copies
      (`x-special/gnome-copied-files`, `text/uri-list`) and which it pastes.
   2. **Done** (`86c73b1`): `link/files.py` - Offer (the list, the pieces),
      Receive (names cleaned, nothing outside the folder, SHA-256 per file,
      temporary names, limits, room checked). 32 tests.
   3. **Done**: the files connection - a second connection, proved and sealed
      like the link and never a link (`files` in the hello; the member always
      dials; the hub asks with `fwant`); paced on the queue, so nothing is
      dropped. 8 live tests.
   4. Next: the clipboards - announce a copied file list (`clipmeta` with
      format "files"), pull on arrival of the pointer, put the received
      files on the clipboard (CF_HDROP + Preferred DropEffect on Windows; the
      GNOME target on Linux); the arrivals folder per user (Desktop through
      the user's shell folders / `xdg-user-dir`); files owned by the person
      on Linux.
   5. Progress notifications; then both machines, both ways: one file,
      several, a folder tree, a big file, odd names.
5. **Moving windows between computers - being built now** (the maintainer
   asked for it, 2026-10-13; `docs/moving-windows.md` is the full design and
   requirements). A window's program stays home; its window is shown and
   driven on the other computer, behind a switch, off by default. The
   maintainer's choices: the frame's close button closes the program;
   Win+Shift+Left/Right; from the AIO the window shows on both for now (the
   GNOME Shell extension later); this before the rest of file copy.
   Feasibility proven on both machines (section 3 and 4.2; TESTLOG
   2026-10-13): on Windows a layered alpha-1 click-through window is gone from
   the screen yet keeps drawing and takes input; on the AIO the frame (Tk)
   works, and GNOME-home via the portal (stage 5) will need the helper to be
   a real window. Stages are in the design; done so far:
   - **Stage 2: done** - everything that is the same on every system, in
     `link/winmove.py`, 87 tests (53 + 34 live), full suite 1,529.
     - the transport (`041e2cb`): BinChannel, sealed binary length-first
       frames on a channel of its own; `secure.seal_bytes/open_bytes`;
       `protocol.LineChannel.detach`.
     - the messages, the diff and the pacing (`6b13ee3`): a 2-byte header
       length + JSON header + the payload as it is; `changed()` in 64-pixel
       squares, joined across and down, whole picture on a resize (the size
       the kept picture was taken at is passed - length cannot tell 64x16
       from 32x32); `Pacer`, at most two pictures unshown.
     - the sessions (`6078734`): HomeSession and AwaySession, with the
       window and the frame as objects, so the OS halves can be faked. All
       input from the other computer is checked before it is played. The
       window is hidden only once `wait_ready()` says the frame is up.
       `BadMessage` apart from `ChannelError`, so a peer sending nonsense
       reads as "failed", not "dropped".
   - **Next, stage 3 (laptop -> AIO)**, the first stage with a real window
     on screen: Windows home in the desk agent (choose the window, hide it
     as layered/alpha 1/click-through, PrintWindow capture, GDI+ encode,
     SendInput into it, and its menus and dialogs with it - R11); Linux
     away in a session helper (the frame, a Tk window, drawing the
     regions); the settings switch; the tray entries; Win+Shift+arrows.
     Then stage 4 (drag across the edge), stage 5 (AIO -> laptop), stage 6
     (optional GNOME extension), stage 7 (docs, release).
6. **After moving windows - the rest of 1.2, files over the clipboard**
   (step 4 below), then drag and drop across the edge.

## Open questions and loose ends

- **CI on `main` is red** (`1aa2322`, 13/15), waiting on the maintainer
  ("not now", 2026-10-11): the big-copy test's fix for Windows' coarse sleep
  before Python 3.11 is committed (`6b81041`), not pushed; and the race
  below fails `test_the_laptop_drives_across_both_aios_in_turn` about one
  run in 13.
- **The hub's join race** (TESTLOG 2026-10-11). Two members joining at the
  same moment - as when all redial after the hub restarts - each find
  nobody else registered yet in `_greet` (`others` is read under `_lock`,
  but `_register` comes later), so each takes a fresh epoch
  (`arbiter.resume()`); the member welcomed first keeps the older epoch and
  drops every input message from the hub as stale (`is_stale`: `e !=
  epoch`) until control changes hands. Groups of three or more only.
  Proposed fix: one lock around "who else is here?" through `_register`, so
  the second join sees the first and joins the epoch in force. Re-sending
  the current grant to everyone is no fix: `Baton.apply` takes an equal
  epoch again, which can snap a driver's cursor. Test that forces it: wrap
  the hub's `_register` with a 0.3 s sleep (monkeypatch), start the trio,
  wait for one epoch on all three - fails 5/5 today.

- GNOME's Locate Pointer ripple (find the pointer on Linux) has never been
  seen working on the AIO.
- Seen once (0.15.x): control flapping between machines ~3 times a second;
  maybe both mice moving. Not seen again.
- The `.deb`'s remove path (prerm stops windows and trays) is tested by code
  review only; removing and reinstalling on the AIO needs the maintainer's
  password twice.
- The social preview image must be uploaded by hand (repository Settings).
- Ideas: a Linux Wi-Fi power-save banner with a fix (like the Windows
  public-network one); a per-user handle so the tray works for source
  installs; a PAKE; a demo GIF for the README.
