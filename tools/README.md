# Tools

For working on Nishro Link, not part of it. Windows, with the repository's
own Python environment (`pip install -e ".[dev]"`) and Pillow.

| | |
|---|---|
| `dev-install-windows.ps1` | Install a freshly built program folder on this computer, as the setup would - for when Defender blocks a locally compiled setup (see RELEASING.md). Run elevated. |
| `docs/shot_manual.py` | Every screenshot in `docs/manual.md`, with numbered callouts. |
| `docs/shot_tray.py` | The tray menu and the icon's three states, for the manual. |
| `docs/shot_setup.py` | The setup wizard's pages, for the manual (needs Inno Setup 6). |
| `docs/shot_theme.py` | The README's screenshots: `python tools/docs/shot_theme.py dark publish`. |
| `docs/social_preview.py` | `docs/social-preview.png`, for the repository's social preview. |

**The screenshots show example data only** - devices called laptop and
desktop-1, documentation addresses, a made-up password - and capture only
Nishro Link's own windows, as they draw themselves (PrintWindow), and its
popup menu by the menu's own rectangle while it is on top: nothing else on the
desktop can end up in a picture. Keep it that way: the repository is public.

They open windows on the desktop for a minute; leave the mouse alone meanwhile.
