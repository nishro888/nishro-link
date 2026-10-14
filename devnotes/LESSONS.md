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
- **PyInstaller's source archive carries the prebuilt bootloaders too.**
  `PYINSTALLER_COMPILE_BOOTLOADER` compiles over them, and a compile that
  writes something else leaves them in place unnoticed. **Works:** delete
  them before compiling, and compare the installed ones with them after
  (`pyinstaller-from-source.ps1`).
- **waf takes any C compiler it finds**: with no Microsoft compiler on the
  laptop, it set up a 32-bit build with an old MinGW gcc. GitHub's runners
  have MinGW too. **Works:** `--check-c-compiler=msvc` - it fails instead.
- **Windows' `tar` gave up on PyInstaller's source**: three entries are
  symbolic links (notes, tests), which an ordinary account cannot create.
  **Works:** Python's `tarfile`, which copies what a link points to.
- **Inno Setup pads the setup's version strings with spaces** ("Nishro Link",
  "1.1.0", then spaces, in Properties). The program's are exact. A check that
  compares them exactly - SignPath's? - may refuse the setup.
- **`download-artifact` with `merge-multiple` takes every artifact of the
  run**: once the unsigned copies handed to SignPath were artifacts too, the
  unsigned setup (same file name) could have replaced the signed one in the
  release. **Works:** download each finished set by name.

- **An elevated process starts in system32.** `Start-Process -Verb RunAs`
  with a relative script path runs nothing at all and exits 0xFFFD0000, with
  no log because the script never began. Pass an absolute path, and
  `-WorkingDirectory`.
- **A windowed build has no console**, so `--flag` output looks empty when
  run from one and works perfectly down a pipe. Judge such a thing by
  redirecting it to a file, never by eye in a terminal.
- **The service cannot see a desktop.** Anything that looks at windows has to
  be asked of the session - and on Windows it must be *started* there
  (`winsvc.launch_in_console`), where its output goes nowhere the service can
  read. Hand the answer back over loopback with a one-use token, as a moved
  window is handed over.
- **Never kill a helper that holds something of the person's.** The process
  that hides a window was killed as soon as its connection broke, so it never
  restored the window. Close its channel, let it finish, and only insist
  after that.
- **A menu item with no handler is drawn and does nothing.** Two tray entries
  shipped that way until they were clicked in a test. A new menu entry needs
  a test that calls `act()` with its action.

- **A connect timeout left on a socket is a read timeout too.**
  `socket.create_connection(addr, timeout=5)` keeps those five seconds for
  every later read. A long-lived channel that is quiet when nothing is
  happening dies on it - and from the far end that looks exactly like the
  other computer letting go, which sent the search in the wrong direction
  for an hour. Clear it (`settimeout(None)`) or set one that suits the
  channel, as files.py does.
- **When two ends both report "the other end closed it", neither is the
  cause.** Say which direction stopped FIRST and why, rather than swallowing
  the error; that one log line found it.
- **A helper in the person's session cannot be heard.** Its output goes to
  the system log at best, and nowhere from a windowed build. Have it send
  its log back on the channel AND write a file: when the channel is what
  broke, the lines that matter most cannot be sent on it.
- **Reading one frame for an answer is a bug the moment anything else can
  arrive first.** A log line sent before the answer was read as the answer,
  and every move failed with a message about the window instead.

## Testing

- **A socket round-trip of more than a buffer's worth deadlocks in one
  thread.** A 2 MB frame sent with `sendall` blocks once the send buffer
  (tens of KB) fills and nothing is draining the other end - the test just
  hangs. Send from a thread while the test reads, which is also how the real
  sessions work (one thread each way). (winmove's big-frame test.)
- **A test that reads what a background thread saw must join it first.** The
  impostor-hub test failed 9 times in 15: it checked before the fake hub's
  thread had noted the member hanging up. Run a new test 15-20 times before
  trusting it; join the thread, never sleep.
- **A pacing test on one machine may never fill the queue**: loopback drains
  faster than the sender fills it, so removing the pacing passed. Make the
  receiver slower than the sender, then the break shows. And no tiny sleep at
  every step: before Python 3.11 a sleep on Windows lasts at least a timer
  tick (up to 15.6 ms) - 3,072 of them timed out on CI. Pause less often.
- **To tell which commit made a test flaky, alternate the runs.** Forty runs
  of one commit, then forty of the other, read 0 vs 3 failures - as if the
  later one brought the race; other work on the computer comes and goes.
  Alternating them in one loop showed it in both (1 vs 4): an older race
  that the change made likelier. Then force the window open (a sleep in the
  right place) for a test that fails every time.
- **A CI job stuck or failed at `apt-get` never ran a test.** Ubuntu's package
  servers once stopped answering plain HTTP (HTTPS still worked): the
  container jobs failed, the others hung - for up to GitHub's 6 hours.
  Check `curl -I http://archive.ubuntu.com/ubuntu/dists/focal/InRelease`,
  then cancel the run and `gh run rerun <id> --failed` (with the
  maintainer's OK).
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

- **`close()` on a socket does not wake a thread blocked in `recv()` on
  Linux** (on Windows it does). A link closed because the far side went
  silent stayed blocked; `shutdown(SHUT_RDWR)` first wakes it. The link tests
  only passed before because the far side answered the close.
- **A connection can look alive for hours with nobody behind it.** A sleeping
  Windows laptop kept acknowledging packets while Nishro Link was frozen;
  Linux then needed ~16 minutes of unanswered retries to give up. Only the
  application can tell: no message for LINK_DEAD_S means dead.
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

- **A test that borrows the person's clipboard must give it back whatever
  happens.** One saved it through PowerShell's text output, which failed on a
  character the console codepage could not decode; the restore then crashed,
  leaving a test file list on the clipboard and the copied text lost. Save it
  as raw clipboard data (CF_UNICODETEXT through user32), restore in a
  `finally` that cannot raise - or test against a fake clipboard.
- **Inline Python in a shell heredoc lost backslashes** on the way, and a
  Windows path like `"...\NishroLink"` is a syntax error (`\N`). Write scripts to
  a file; use raw strings for paths. (It happened again with a patch whose
  strings held `\n`: make such edits with the editor's own tools, not a
  heredoc or `sed`.)
- **A PowerShell wildcard that matches nothing is no error**: `Get-Item
  dist\NoSuch-*.exe` returns nothing, so a signature check looping over it
  passed with nothing checked. **Works:** count what was found, and fail on
  none.
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
- **A program that injects input ignores injected input.** Avro Keyboard
  swallows every key and puts Bangla characters of its own in its place, so
  it has to ignore anything marked injected or it would read its own output
  back - and `SendInput` cannot clear that mark. Scan codes made no
  difference. Before trying to fix such a program, ask whether it CAN act on
  us (see TESTLOG 2026-10-14). The way round is to send the character, not
  the key: `KEYEVENTF_UNICODE` asks no layout and no input method, so
  nothing can refuse it.
- **A test that does not know the state it is testing proves nothing.** The
  first Avro probe typed 'amar' and got 'amar', which says nothing when
  Avro's mode is unknown and unreadable. Rebuilt to press the switch between
  three attempts, so every mode was covered and the answer held whichever one
  it started in.
- **A low-level hook in a test must only swallow what the test injected.**
  Ours swallowed every key so nothing would be typed into a window; a run
  left hanging in the background then ate every keystroke on the machine and
  the maintainer could not type until he restarted. Pass real keys through,
  and unhook in the fixture rather than leaving it to the process exiting.
- **Windows says the pointer arrow is showing when it is not.**
  `GetCursorInfo` returned `CURSOR_SHOWING` while no arrow was being drawn,
  so a fix that asked first never fired. Do not ask: send real mouse input
  whenever driving begins, and put the pointer back on the exact pixel.
- **A WinEvent hook and its message pump must be on the SAME thread.**
  `WINEVENT_OUTOFCONTEXT` posts to the queue of the thread that called
  `SetWinEventHook`; installed on one thread and pumped on another, not one
  event arrives and it looks exactly like a hook that does not work. Cost one
  "the drag was not noticed" before it was spotted.
- **Windows' window coordinates are not the ones the rest of Nishro Link
  speaks.** `GetWindowRect` counts from the PRIMARY screen's corner, so with
  a monitor to the left every x is negative; local coordinates (the wire, the
  desk, `inject.py`) count from the corner of the whole desktop. Subtract
  `SM_XVIRTUALSCREEN`/`SM_YVIRTUALSCREEN`. Measured here: the desktop is
  3286x1080 starting at (-1920, -148), so getting this wrong puts a border
  out by the whole of a screen.
- **`WM_CANCELMODE` ends a window-manager drag cleanly** and puts the window
  back where it was picked up. Escape would do it too, but Escape is a key
  the program itself would also see.
