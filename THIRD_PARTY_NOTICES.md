# Third-party notices

Nishro Link is released under the [MIT License](LICENSE). It includes or uses:

## Sun Valley ttk theme
`link/theme/` - the look of the window's controls.
Copyright (c) rdbende. MIT License - see [link/theme/LICENSE](link/theme/LICENSE).
<https://github.com/rdbende/Sun-Valley-ttk-theme>

## EFF short word list 1
`link/words.py` - the words of generated passwords (a few left out).
Copyright 2016 Electronic Frontier Foundation (Joseph Bonneau).
[Creative Commons Attribution 3.0 United States](https://creativecommons.org/licenses/by/3.0/us/).
<https://www.eff.org/dice>

## Libraries used at run time
Not included in the source; installed with the program or by the system.

| Library | Used for | License |
|---|---|---|
| [cryptography](https://github.com/pyca/cryptography) | the link's encryption | Apache 2.0 or BSD |
| [python-evdev](https://github.com/gvalkov/python-evdev) (Linux) | reading and making input | BSD 3-Clause |
| Python and Tcl/Tk | the program and its window | PSF / Tcl license |

The Windows program is built with [PyInstaller](https://pyinstaller.org/)
(GPL 2.0 with an exception that permits distributing the built program under
any license) and its installer with [Inno Setup](https://jrsoftware.org/isinfo.php).
