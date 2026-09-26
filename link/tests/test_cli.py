"""The CLI's settings resolution: saved config with this run's flags on top."""
import pytest

from link import config
from link.nishro_link import parse_size, settings


@pytest.fixture
def saved(tmp_path):
    """A machine already set up the way the test laptop would be."""
    p = tmp_path / "config.json"
    config.save(config.merge(config.DEFAULTS, {
        "node": "laptop", "peer": "aio", "hub": True, "side": "right",
        "peer_screen": "1920x1080", "pin": "5813",
    }), p)
    return p


def resolve(saved, *argv):
    return settings(["--config", str(saved), *argv])[1]


# ------------------------------------------------------------ the point of it
def test_a_bare_run_uses_what_was_saved(saved):
    cfg = resolve(saved)
    assert cfg["node"] == "laptop"
    assert cfg["peer"] == "aio"
    assert cfg["side"] == "right"
    assert cfg["pin"] == "5813"
    assert cfg["hub"] is True


def test_a_flag_wins_for_this_run(saved):
    assert resolve(saved, "--side", "left")["side"] == "left"


def test_a_flag_does_not_disturb_anything_else(saved):
    """Trying a different --side must not cost you the rest of the setup."""
    cfg = resolve(saved, "--side", "left")
    assert cfg["node"] == "laptop" and cfg["pin"] == "5813" and cfg["hub"] is True


def test_trying_a_flag_does_not_write_it_back(saved):
    resolve(saved, "--side", "left")
    assert config.load(saved)["side"] == "right"


def test_save_persists_the_merged_result(saved, tmp_path):
    args, cfg = settings(["--config", str(saved), "--side", "left", "--save"])
    assert args.save is True
    config.save(cfg, saved)
    assert config.load(saved)["side"] == "left"
    assert config.load(saved)["pin"] == "5813"       # everything else survives


# ------------------------------------------------------- tri-state booleans
def test_absent_hub_flag_keeps_the_saved_value(saved):
    """`--hub` uses store_true, so it arrives False when absent unless we make
    it tri-state - which would silently demote a hub on every bare run."""
    assert resolve(saved)["hub"] is True


def test_no_hub_really_turns_it_off(saved):
    assert resolve(saved, "--no-hub")["hub"] is False


def test_policy_flags_are_tri_state_too(saved):
    assert resolve(saved)["policy"]["may_drive"] is True
    assert resolve(saved, "--no-drive")["policy"]["may_drive"] is False
    assert resolve(saved, "--no-driven")["policy"]["may_be_driven"] is False


def test_a_kiosk_keeps_only_the_flag_it_was_given(saved):
    p = resolve(saved, "--no-drive")["policy"]
    assert p["may_drive"] is False
    assert p["may_be_driven"] is True
    assert p["claim"] == "motion"


# --------------------------------------------------------- no config at all
def test_it_works_with_nothing_saved(tmp_path):
    cfg = settings(["--config", str(tmp_path / "none.json"),
                    "--node", "a", "--peer", "b", "--hub"])[1]
    assert (cfg["node"], cfg["peer"], cfg["hub"]) == ("a", "b", True)
    assert cfg["port"] == 8770                       # default filled in
    assert cfg["policy"]["claim"] == "motion"


# -------------------------------------------------------------- screen sizes
@pytest.mark.parametrize("text,expected", [
    ("1920x1080", (1920, 1080)),
    ("1366X768", (1366, 768)),
    (None, (800, 600)),
    ("", (800, 600)),
    ("nonsense", (800, 600)),
])
def test_screen_sizes_parse_or_fall_back(text, expected):
    assert parse_size(text, (800, 600)) == expected


# ----------------------------------------------------------- no window
def test_a_missing_tkinter_runs_headless_instead_of_crashing(monkeypatch):
    """python3-tk is a separate package on Debian and Ubuntu. The import used to
    be unguarded, so on the Ubuntu box a missing package ended the whole program
    with a traceback - the link went down over a missing WINDOW."""
    import sys

    import link
    from link.nishro_link import load_window

    # Make `import tkinter` fail the way it does without the package. The
    # package attribute has to go too: `from . import ui_tk` returns it without
    # importing anything if it is already there.
    monkeypatch.setitem(sys.modules, "tkinter", None)
    monkeypatch.delitem(sys.modules, "link.ui_tk", raising=False)
    monkeypatch.delattr(link, "ui_tk", raising=False)

    lines = []
    assert load_window(lines.append) is None
    assert len(lines) == 1
    assert "tkinter" in lines[0] and "headless" in lines[0]
    if sys.platform.startswith("linux"):
        assert "python3-tk" in lines[0], "should say which package to install"


# ------------------------------------------------------- upgrading a pairing
@pytest.mark.parametrize("saved,want", [
    ("aio", "aio"),
    ("  aio ", "aio"),
    ("peer", None),        # the placeholder older builds saved
    ("laptop", None),      # our own name is never the other device
    ("", None),
    (None, None),
])
def test_the_name_to_look_for(saved, want):
    """Older builds saved "peer" as the name when pairing by address. Read as a
    name, the Ubuntu box searched forever for a device called "peer", refusing
    the real one at the remembered address as the wrong machine."""
    from link.nishro_link import peer_name
    assert peer_name({"peer": saved, "node": "laptop"}) == want


# ------------------------------------------------ the arrangement at startup
from link import desktop as _desktop                       # noqa: E402

ROWS = _desktop.from_monitors([(0, 0, 1366, 768), (-1920, -148, 1920, 1080)])


def test_startup_keeps_a_saved_arrangement_and_remeasures_this_machine():
    """A saved desk: the AIO right of the laptop, from before the laptop was
    measured as its whole desktop. It must come back with the laptop as both
    monitors and the AIO still beside the panel."""
    from link.nishro_link import build_desk
    cfg = {"node": "laptop", "placement": [
        {"name": "aio", "w": 1920, "h": 1080, "x": 1366, "y": 53, "owner": "aio"},
        {"name": "laptop", "w": 1366, "h": 768, "x": 0, "y": 0, "owner": "laptop"}]}
    lay = build_desk(cfg, ROWS)
    lap = lay.get("laptop")
    assert (lap.w, lap.h, len(lap.displays())) == (3286, 1080, 2)
    assert lay.overlaps() == [] and lay.unreachable() == []
    assert lay.touching("aio") == {"laptop": 768}


def test_startup_with_nothing_saved_puts_the_peer_on_its_side():
    from link.nishro_link import build_desk
    lay = build_desk({"node": "laptop", "side": "right"}, ROWS, "aio")
    assert lay.touching("aio"), "and the pointer can reach it"
    assert lay.get("aio").x >= lay.get("laptop").x + lay.get("laptop").w


def test_startup_with_an_arrangement_missing_this_machine_adds_it():
    from link.nishro_link import build_desk
    cfg = {"node": "laptop", "placement": [
        {"name": "aio", "w": 1920, "h": 1080, "x": 0, "y": 0, "owner": "aio"}]}
    lay = build_desk(cfg, ROWS)
    assert "laptop" in lay.names() and lay.overlaps() == []
