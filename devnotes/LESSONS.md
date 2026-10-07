# Lessons - what failed, and what works instead

Read the section for the task before starting. Add to it the moment something
fails or misleads. Each entry: **what was tried or seen** - why - **what works**.

## Building and releasing

- **A setup compiled on the developer's laptop is blocked by Windows Defender**
  ("Trojan:Win32/Bearfoos.A!ml", a machine-learning false positive). Not the
  code: a dummy payload with the same name was blocked, the real program under
  another name was not - it is this product's identity, learned from runtime
  detections of local test setups. The CI-built setup and the published 1.0.1
  scan clean. **Do not** rebuild locally hoping for a pass, and **never** rename
  or disguise anything to get past it. **Works:** build the program folder and
  install it with `tools/dev-install-windows.ps1`; for real setups, a
  build-only run of the Release workflow, scanned with
  `MpCmdRun.exe -Scan -ScanType 3 -File <setup> -DisableRemediation` and
  installed before tagging; if a published one is ever flagged, report it to
  Microsoft (microsoft.com/wdsi/filesubmission).
- **One-file PyInstaller build** unpacks itself into %TEMP% at every start, and
  a copy it starts (the tray, from the window) reuses that folder - which the
  first copy deletes on exit (1,002 files down to 19: the tray could no longer
  open a window). **Works:** `--onedir`; `tray.spawn` also sets
  `PYINSTALLER_RESET_ENVIRONMENT=1`.
- **Inno Setup runs `[Run]` entries before `ssPostInstall`.** The tray started
  there was killed when `--install-service` stopped every other NishroLink.exe.
  **Works:** start it at the end of `CurStepChanged(ssPostInstall)` with
  `ExecAsOriginalUser`.
- **A `.deb` upgrade killed the running trays** (postinst's pkill) and nothing
  restarted them. **Works:** postinst notes each tray's owner and session
  variables from `/proc/<pid>/environ`, restarts it with `runuser` + `setsid`
  after waiting for the old one to exit (one tray per person).
- **`sc sdset` with an `S:` (SACL) part** needs a privilege the installer may
  not have. **Works:** the DACL only (Windows' defaults, plus RP/WP for IU).
- **`powershell Start-Process -Wait` on a setup that starts the tray hangs**: it
  waits for the whole process tree. **Works:** start it, poll `HasExited`.
- **PyInstaller does find imports inside functions** (`from . import clip_win`
  was bundled). It was suspected once; it was not the cause.

## Testing

- **`pytest -q` twice hides the summary line** (`-q` is already in the
  project's options), and **`... | tail` reports `tail`'s exit code**. Judge a
  run by pytest's own exit code, with the output saved to a file.
- **A test that only passes because of timing**: Windows' clock ticks every
  15.6 ms, so two `time.time()` values taken together can be equal. Sleep past
  a tick in tests that compare times.
- **Tests that read and write in one process miss the Windows service's
  process boundary.** Every Windows clipboard bug below passed the tests. Check
  on the real service before believing it.
- **A bug-fix test must fail without the fix.** Each new test here was checked
  by reverting the fix and running it.
- **Coverage shows what no test runs** (`pip install coverage`; `python -m
  coverage run --source=link -m pytest`, then compare with the lines a commit
  added). Of 1.1.0's new lines, 62% ran; the gaps were real behaviour - Quit
  and Open's start/stop, the tray's menu when things go wrong, the launcher.
  To check a new test, break the code it guards on purpose (one change at a
  time, restored from git after) and see that test fail.
- **A launcher test gone wrong reached the real service.** With a check
  broken on purpose, `attach()` went on to `service.find()`, which found the
  Nishro Link installed on the laptop and opened a real window onto it until
  the run was stopped. Tests through `attach()` block the real lookup, and
  `test_service.py` fails any test that would open a real window.
- **Before designing around what a device "does", measure it under control.**
  The volume dial's first trace was taken with a person told "3 clicks up"
  and showed bursts of 2-5 presses; they were read as one click's bounce,
  and two versions of grouping followed - each losing real clicks when the
  dial was rolled. One click at a time, slowly, with the presses timed,
  would have shown one press per click from the start. Ask for single,
  paced actions; let a script prompt and label them.
- **A fake that answers at once can hide a state.** "Make it private" turns
  its button off while Windows' prompt is open; a fake prompt that returned
  instantly had turned it back on before the test looked. Make the fake wait
  (an Event) like the real thing.

## Windows at run time

- **The service is SYSTEM in session 0; the desk agent (`agent.py`) is SYSTEM
  in the person's session** and does everything that touches the desktop. A
  console program it starts (PowerShell) flashes a window unless given
  `CREATE_NO_WINDOW` - which is what a Ctrl+C did, every time, until the
  clipboard became native (`clip_win.py`).
- **Reading the clipboard from the agent**: Windows lists the registered "PNG"
  format but calls it unavailable there; and a `CF_BITMAP` handle another
  process made cannot be converted by GDI+ in any other process (status 7) -
  every screenshot. **Works:** read `CF_DIB` (memory, like text) and convert
  it; write PNG + `CF_DIB`. Found only by having the agent log what it saw.
- **Not the cause, already checked:** the agent's clipboard change counter -
  it moves in step with everyone else's (`GetClipboardSequenceNumber`). Don't
  chase it again.
- **Several threads using the clipboard at once crashed the process** (heap
  corruption): opening it keeps other programs out, not other threads. One
  lock around every operation.
- **The agent's channel takes lines of 128 KB at most.** Images cross between
  the service and the agent as a file in the service's private folder,
  deleted once read.
- **`time.monotonic()` ticks every 15.6 ms on Windows**: the round trip read 0
  or 16 ms. Use `time.perf_counter()` for short intervals.
- **"Not connected" for hours with nothing on screen**: the laptop's Wi-Fi had
  moved to the 2.4 GHz network, which Windows had never seen and made Public;
  the firewall rule is Private/Domain only. The firewall check ran once and
  looked only for block rules. Now the network profile is checked every minute
  while a device is missing.
- **Turning sharing off left a member connected**: the accept loop checked the
  switch only between accepts. The handshake checks it too now.
- **Opening Nishro Link with the service stopped ran a second engine** with
  the person's old settings. Opening now starts the service.
- **Brightness keys**: Windows has no brightness key a program can send; WMI
  sets the built-in panel only (external monitors don't take it).

## Linux at run time

- **`python3 -m link...` imports from the current directory**: the app menu
  starts programs in $HOME, where a stale `~/link` ran instead. Launch by path.
- **A terminal in VS Code installed as a snap** leaks the snap's library and
  module paths; GTK crashed (`symbol lookup error`). The launcher clears them.
- **A keyboard's media keys are a separate input device** ("... Consumer
  Control"), never read before. Read it now - but never a device that also has
  KEY_POWER/SLEEP/WAKEUP, and not the display's "Video Bus".
- **A keyboard that appears after start** (wireless waking, plugged in later)
  was never read. The capture rescans every 2 s.
- **polkit JS rules** (`/usr/share/polkit-1/rules.d`) need polkit 0.106 or
  later (Ubuntu 24.04, Debian 12 and later); older ones ask for the password.
- **Ubuntu turns Wi-Fi power saving on by default**
  (`/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf`): latency spikes
  on the AIO's leg. Keep-awake on both ends helps; turning it off, or 5 GHz,
  fixes it.
- **Measured, to tell network from code**: laptop -> router 2-3 ms steady;
  laptop -> AIO 5-143 ms at 1 s intervals; AIO -> router 2-3 ms at 0.2 s.
  Ping the router from each side before touching code.

## The tools used to do the work

- **Inline Python in a shell heredoc lost backslashes** on the way, and a
  Windows path like `"...\NishroLink"` is a syntax error (`\N`). Write scripts to
  a file; use raw strings for paths.
- **`clip.py` defines its own `set`**, shadowing the built-in: inside it, use
  `frozenset`.
- **The assistant's PowerShell tool refused a command** with `Remove-Item` when
  the same line also named `C:\Program Files`. Keep deletions separate.
- **Long-running helpers started as an assistant's background jobs get
  stopped** after a while. Start them as their own process
  (`Start-Process ... -WindowStyle Hidden`).
- **A screenshot by screen rectangle** caught another program's tooltip once.
  Capture windows with PrintWindow (`tools/docs/shot_manual_grab.py`).
- **Defender's history** is the quickest way to see what was blocked and when:
  `Get-MpThreatDetection | Sort-Object InitialDetectionTime -Descending`.
- **`set -e` did not stop a script in the assistant's Bash tool**: the tool
  runs the command inside an `&&` list, where `set -e` is ignored. A failed
  step went on to commit the wrong tree. Put `|| exit 1` after each step that
  must not fail.
- **`git rm` refuses files that `cherry-pick -n` just staged** ("changes
  staged in the index"); `git rm -f` removes them.
