# State

**Last updated:** 2026-10-11

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
5. **After 1.2 - drag and drop across the edge**, with the system's own drag
   icon: first a feasibility test on the AIO (GNOME on Wayland lets a program
   start a drag only from a press on its own window).

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
