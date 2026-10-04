# Releasing

How a version goes out. **Nothing is tagged until the checks below pass on a
real Windows computer and a real Linux desktop**, with the builds a user would
install. Automated tests do not open an app menu, look at a dock or type on a
keyboard, and 1.0.0 shipped a window that did not open from Ubuntu's app menu.

Every 1.x must work with every other 1.x
([CHANGELOG](CHANGELOG.md), top). A release that cannot do that is 2.0.

## 1. Prepare

- [ ] `link/__init__.py`: `__version__`; `__stage__` empty for a release.
- [ ] `CHANGELOG.md`: the new version's entry, dated.
- [ ] `link/packaging/deb/io.github.nishro888.nishro-link.metainfo.xml`: a
      `<release>` entry (the App Center shows it).
- [ ] The file names in `README.md`, `docs/manual.md`,
      `docs/install-windows.md` and `docs/install-linux.md`.
- [ ] If the look changed, the pictures: `python link/packaging/make-icons.py`
      for the icons, and the manual's and README's screenshots.
- [ ] `python -m ruff check .` and `python -m pytest`: clean.
- [ ] Push to `main`; CI green on every job.

## 2. Build what users install

- **Windows:** `link/packaging/build-windows.ps1` → `dist/NishroLink-Setup-<v>.exe`
  (or the *Release* workflow run by hand, which builds and publishes nothing).
- **Linux:** `python link/packaging/build-deb.py` → `dist/nishro-link_<v>_all.deb`.

## 3. Check on both computers

Install over the previous version, as a user would.

**Windows**

- [ ] The setup runs; at the end, **Open Nishro Link** opens the window.
- [ ] Right after the setup, without signing out, the **tray icon** is there
      (a silent install too: `/VERYSILENT`).
- [ ] The icon is right in the Start Menu, the taskbar and the title bar.
- [ ] Every page opens: Home, Devices, Arrangement, Activity, Settings, Help.
- [ ] **Devices → Details** of another device: no flicker; Rename works.
- [ ] **+ Add a device** lists the other computer.
- [ ] The **tray icon** is there; its menu opens; untick **Sharing** → the icon
      goes grey; tick it again; **Find the pointer**; clicking the icon opens
      the window; **Hide this icon**, then open the window → the icon returns.
- [ ] `NishroLink.exe --sharing toggle` twice, from a command prompt.
- [ ] **Quit** from the tray: no prompt; the service stops (Task Manager: no
      NishroLink.exe left in the session), the tray and window close. Open it
      from the Start Menu: the service starts again, the tray comes back.

**Linux** (the `.deb`, on the desktop session, not over SSH)

- [ ] `sudo apt install ./nishro-link_<v>_all.deb` completes; the service is
      running (`systemctl status nishro-link`).
- [ ] A tray that was running before the upgrade is running again after it
      (a new process, as the same person).
- [ ] **Open from the app menu**: the window opens, and its icon shows in the
      dock.
- [ ] Every page opens; **Details** does not flicker.
- [ ] The **tray icon** is in the top bar; its menu works; the icon goes grey
      with sharing off.
- [ ] **Right-click in the dock**: Find the pointer, Release input, Pause and
      Resume sharing all work.
- [ ] Open it again from a terminal inside VS Code (a snap): it still starts.
- [ ] **Quit** from the tray, then open it from the app menu: the service stops
      and starts again (no password on 24.04+), and the tray comes back.

**Together**

- [ ] The pointer crosses both ways, at every edge the arrangement has.
- [ ] Typing goes where the pointer is, **on both computers' keyboards**.
- [ ] Copy text on each, paste on the other.
- [ ] Shake the mouse: the pointer is found, on whichever computer it is.
- [ ] Both Ctrl keys give each computer back its own input.
- [ ] Unplug the network or stop one side: input returns at once; plug back
      in: it reconnects, and the other computer's tray says so.
- [ ] One computer on the previous 1.x release, the other on this one: they
      connect and work.

## 4. Publish

Only when section 3 is all ticked:

```bash
git tag -a v<version> -m "Nishro Link <version>"
git push origin v<version>
```

The *Release* workflow builds everything from the tag, attaches the setup, the
`.deb` and `SHA256SUMS`, and publishes. A tag with a `-` (`v1.3.0-rc1`) is a
pre-release.

Then:

- [ ] The release page has the three files, and the notes match the changelog.
- [ ] Install the published builds on both computers (not the local ones), and
      repeat **Open from the app menu** and the tray.
