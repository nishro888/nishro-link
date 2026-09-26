"""One JSON document that the CLI, the control API and the tray UI all share.

Run it once with the flags spelled out and `--save`, then just
`python -m link.nishro_link` from then on.

Precedence is file first, command line on top: a flag you pass this run wins,
but does not change what is stored unless you ask for that. So you can try a
different `--side` without losing the setup you are happy with.

Writes are atomic - a new file, then os.replace, which is a rename at the
filesystem level. A config half-written by a crash or a power cut is worse than
no config at all, because the next start would fail in a way that looks like a
bug in the program.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    "node": None,            # this machine's name (default: hostname)
    "device_id": None,       # permanent, generated once; survives a rename
    "peer": None,            # the other machine's name - what is looked for
    "peer_id": None,         # its device ID, learned on first connection
    "peer_addr": None,       # the address it was last found at: a cache, since
                             # DHCP moves it. Typed only as a last resort.
    "hub": False,            # do we listen and arbitrate?
    "side": "left",          # where the PEER sits relative to us
    "screen": None,          # "1366x768"; auto-detected when absent
    "peer_screen": None,     # "1920x1080"
    # Screens placed on a shared desk: [{name, w, h, x, y, owner}]. When present
    # this replaces side/peer_screen entirely - links are derived from the
    # geometry (layout.place), which is what the arrangement editor produces.
    "placement": None,
    "port": 8770,
    "pin": "",
    "policy": {
        "may_drive": True,       # may this machine take control?
        "may_be_driven": True,   # may others inject here?
        "may_admin": True,       # may this machine edit the shared layout?
        "claim": "motion",       # motion | click | hotkey
    },
}


def path() -> Path:
    """Where the config lives, following each platform's own convention."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
        return base / "NishroLink" / "config.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "nishro-link" / "config.json"


def load(p: Path = None) -> dict:
    """Read the config, falling back to defaults for anything missing.

    A corrupt or unreadable file yields defaults rather than an exception: a
    machine that cannot read its settings should still start up usable, not
    refuse to run at all.
    """
    p = Path(p) if p else path()
    cfg = copy.deepcopy(DEFAULTS)
    try:
        stored = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if not isinstance(stored, dict):
        return cfg
    return merge(cfg, stored)


def save(cfg: dict, p: Path = None) -> Path:
    """Write atomically, and keep it to ourselves - it holds the PIN."""
    p = Path(p) if p else path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)     # the PIN is in here in the clear
    os.replace(tmp, p)           # atomic: readers see the old file or the new one
    return p


def merge(base: dict, over: dict) -> dict:
    """Overlay `over` onto `base`, one level deep for `policy`.

    None means "not specified" and never overwrites - otherwise every CLI flag
    the user left alone would wipe the stored value.
    """
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k == "policy" and isinstance(v, dict):
            out["policy"] = {**out.get("policy", {}),
                             **{pk: pv for pk, pv in v.items() if pv is not None}}
        elif v is not None:
            out[k] = v
    return out
