"""Start Nishro Link from the files beside this script - never from the
current directory.

Not `python3 -m link.nishro_link`: -m puts the CURRENT directory first on
Python's path, and the app menu starts programs in the home folder. There an
old unpacked source tree (~/link, version 0.9) was imported instead of the
installed program, which then tried to start a second engine beside the
service and exited: reported as "the window does not open from the menu". A
script's own folder goes first instead, and this script lives with the
program: /usr/lib/nishro-link/launch.py, or ~/.local/share/nishro-link.
"""
import sys

from link.nishro_link import main

sys.exit(main())
