# Test log

Newest first. What was run, on what, and what came out - so a result is not
re-earned, and a failure is not repeated. Machines: "the laptop" (Windows 10,
the hub) and "the AIO" (Ubuntu 26.04, GNOME, Wayland).

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
  clean. Linux: CI runs them on the next push.
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
