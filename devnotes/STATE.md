# State

**Last updated:** 2026-10-05

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
3. **1.2 - files over the clipboard.** Design agreed: copied files are sent in
   the background when the pointer arrives, on a connection of their own (a
   big file must not hold up the pointer); they land in Downloads > Nishro
   Link, and that computer's clipboard then holds them, so pasting in a
   folder works; 2 GB a copy; progress shown.

## Open questions and loose ends

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
  installs; a PAKE; a signed installer; a demo GIF for the README.
