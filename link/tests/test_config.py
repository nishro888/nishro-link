"""Config: one JSON document, atomic writes, CLI on top. DESIGN.md section 9."""
import json
import os

import pytest

from link import config


@pytest.fixture
def cfg_path(tmp_path):
    return tmp_path / "nested" / "config.json"


# ---------------------------------------------------------------- defaults
def test_a_missing_file_gives_defaults(cfg_path):
    cfg = config.load(cfg_path)
    assert cfg["port"] == 8770
    assert cfg["policy"]["may_drive"] is True


def test_defaults_are_not_shared_between_calls():
    """A returned dict must not be the module's own, or one caller mutating it
    would quietly reconfigure every later one."""
    a = config.load("does-not-exist")
    a["policy"]["may_drive"] = False
    assert config.load("does-not-exist")["policy"]["may_drive"] is True
    assert config.DEFAULTS["policy"]["may_drive"] is True


def test_the_path_follows_the_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.path().name == "config.json"
    assert tmp_path in config.path().parents


# ------------------------------------------------------------- round trip
def test_what_is_saved_is_what_is_loaded(cfg_path):
    cfg = config.load(cfg_path)
    cfg.update(node="laptop", peer="aio", side="right", pin="5813", hub=True)
    config.save(cfg, cfg_path)

    back = config.load(cfg_path)
    assert back["node"] == "laptop"
    assert back["side"] == "right"
    assert back["hub"] is True
    assert back["pin"] == "5813"


def test_saving_creates_the_directory(cfg_path):
    assert not cfg_path.parent.exists()
    config.save(config.load(cfg_path), cfg_path)
    assert cfg_path.exists()


def test_the_file_is_readable_by_a_human(cfg_path):
    """It is hand-edited more often than not."""
    config.save(config.load(cfg_path), cfg_path)
    text = cfg_path.read_text(encoding="utf-8")
    assert "\n" in text and text.endswith("\n")
    json.loads(text)


def test_no_leftover_temp_file(cfg_path):
    config.save(config.load(cfg_path), cfg_path)
    assert list(cfg_path.parent.iterdir()) == [cfg_path]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_the_config_is_private_because_it_holds_the_pin(cfg_path):
    config.save(config.load(cfg_path), cfg_path)
    assert oct(cfg_path.stat().st_mode & 0o777) == "0o600"


# ------------------------------------------------------------- robustness
@pytest.mark.parametrize("junk", ["", "{not json", "null", "[1,2,3]", '"a string"'])
def test_a_corrupt_file_falls_back_to_defaults(cfg_path, junk):
    """A machine that cannot read its settings should still start up usable,
    not refuse to run in a way that looks like a bug in the program."""
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(junk, encoding="utf-8")
    assert config.load(cfg_path)["port"] == 8770


def test_an_unreadable_file_falls_back_to_defaults(cfg_path):
    assert config.load(cfg_path / "is" / "a" / "directory")["port"] == 8770


def test_a_partial_file_keeps_the_other_defaults(cfg_path):
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('{"node": "aio"}', encoding="utf-8")
    cfg = config.load(cfg_path)
    assert cfg["node"] == "aio"
    assert cfg["port"] == 8770
    assert cfg["policy"]["claim"] == "motion"


def test_a_partial_policy_keeps_the_other_policy_defaults(cfg_path):
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('{"policy": {"may_drive": false}}', encoding="utf-8")
    p = config.load(cfg_path)["policy"]
    assert p["may_drive"] is False
    assert p["may_be_driven"] is True


def test_saving_replaces_rather_than_appends(cfg_path):
    cfg = config.load(cfg_path)
    config.save({**cfg, "node": "first"}, cfg_path)
    config.save({**cfg, "node": "second"}, cfg_path)
    assert config.load(cfg_path)["node"] == "second"
    assert cfg_path.read_text(encoding="utf-8").count('"node"') == 1


# ----------------------------------------------------------------- merge
def test_the_command_line_wins():
    base = config.merge(config.DEFAULTS, {"side": "left", "port": 8770})
    assert config.merge(base, {"side": "right"})["side"] == "right"


def test_unspecified_flags_do_not_wipe_stored_values():
    """Every flag the user left alone arrives as None. If those overwrote, one
    run with a short command line would erase the whole setup."""
    stored = config.merge(config.DEFAULTS, {"node": "laptop", "pin": "5813"})
    after = config.merge(stored, {"node": None, "pin": None, "side": "right"})
    assert after["node"] == "laptop"
    assert after["pin"] == "5813"
    assert after["side"] == "right"


def test_merging_policy_is_one_level_deep():
    stored = config.merge(config.DEFAULTS, {"policy": {"may_drive": False}})
    after = config.merge(stored, {"policy": {"claim": "click"}})
    assert after["policy"]["may_drive"] is False      # kept
    assert after["policy"]["claim"] == "click"        # updated
    assert after["policy"]["may_be_driven"] is True   # default survives


def test_merge_does_not_mutate_its_input():
    stored = config.merge(config.DEFAULTS, {"node": "laptop"})
    config.merge(stored, {"node": "changed", "policy": {"claim": "click"}})
    assert stored["node"] == "laptop"
    assert stored["policy"]["claim"] == "motion"


def test_false_is_a_real_value_not_an_absent_one():
    """`hub: false` and `may_drive: false` must survive - the None check has to
    be `is None`, not falsiness."""
    stored = config.merge(config.DEFAULTS, {"hub": True})
    assert config.merge(stored, {"hub": False})["hub"] is False
    assert config.merge(config.DEFAULTS, {"port": 0})["port"] == 0


@pytest.mark.parametrize("stored, want", [("hotkey", "click"), ("click", "click"),
                                          ("motion", "motion"), ("junk", "motion"),
                                          (None, "motion"), (5, "motion")])
def test_what_takes_control_is_always_something_that_can(tmp_path, stored, want):
    """'A hotkey' was offered, but no hotkey was ever bound: a machine set to it
    could not take control back with its own mouse. Saved, it reads as a click."""
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"policy": {"claim": stored}}), encoding="utf-8")
    assert config.load(p)["policy"]["claim"] == want
