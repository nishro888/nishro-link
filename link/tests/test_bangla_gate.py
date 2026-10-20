"""When Bangla typing is offered (bangla_gate.py)."""
import threading
import time

from link import bangla_gate


def gate(wanted=True, running=("avro keyboard.exe",), every=0.05):
    box = {"wanted": wanted, "running": set(running), "looks": 0,
           "threads": set()}

    def programs():
        box["looks"] += 1
        box["threads"].add(threading.current_thread().name)
        return set(box["running"])
    g = bangla_gate.Gate(wanted=lambda: box["wanted"], programs=programs,
                         every=every)
    return g, box


def test_on_where_avro_is_running_and_the_setting_is_on():
    g, _ = gate()
    try:
        assert g() is True
    finally:
        g.stop()


def test_off_where_avro_is_not_running():
    """Nobody who does not use Avro should lose F12 to it."""
    g, _ = gate(running=("notepad.exe",))
    try:
        assert g() is False
    finally:
        g.stop()


def test_off_when_the_setting_is_off_even_with_avro_running():
    g, _ = gate(wanted=False)
    try:
        assert g() is False
    finally:
        g.stop()


def test_it_notices_avro_starting_and_stopping():
    g, box = gate(running=())
    try:
        assert g() is False
        box["running"] = {"avro keyboard.exe"}
        end = time.monotonic() + 3
        while time.monotonic() < end and not g():
            time.sleep(0.02)
        assert g() is True, "Avro starting was never noticed"
        box["running"] = set()
        end = time.monotonic() + 3
        while time.monotonic() < end and g():
            time.sleep(0.02)
        assert g() is False, "Avro stopping was never noticed"
    finally:
        g.stop()


def test_the_lookup_never_runs_on_the_callers_thread_after_the_first():
    """The caller is the key path, with the node's lock held. Asking the
    gate must only ever read an answer, not work one out."""
    g, box = gate(every=0.02)
    try:
        g()                                   # the first answer, given at once
        box["threads"].clear()
        for _ in range(200):
            g()
        time.sleep(0.2)
        assert threading.current_thread().name not in box["threads"], box
    finally:
        g.stop()


def test_a_lookup_that_fails_says_off_rather_than_raising():
    def broken():
        raise OSError("toolhelp said no")
    g = bangla_gate.Gate(wanted=lambda: True, programs=broken, every=10)
    try:
        assert g() is False
    finally:
        g.stop()


def test_the_real_lookup_works_here():
    """On Windows it sees programs; elsewhere it answers with nothing."""
    import sys
    names = bangla_gate.running_programs()
    if sys.platform == "win32":
        assert names, "no programs at all - the lookup is broken"
        assert all(n == n.lower() for n in names)
    else:
        assert names == set()


# ---------------------------------- the setting, as a moved window reads it
def test_the_setting_is_read_from_the_config_file(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"bangla_phonetic": false}', encoding="utf-8")
    assert bangla_gate.Setting([cfg])() is False


def test_it_follows_the_file_as_it_changes(tmp_path):
    """Switching Bangla off in Settings must reach a window already moved."""
    cfg = tmp_path / "config.json"
    cfg.write_text('{"bangla_phonetic": true}', encoding="utf-8")
    now = [0.0]
    s = bangla_gate.Setting([cfg], every=3, clock=lambda: now[0])
    assert s() is True
    cfg.write_text('{"bangla_phonetic": false}', encoding="utf-8")
    assert s() is True                     # not re-read yet
    now[0] = 3.1
    assert s() is False


def test_the_first_readable_file_wins(tmp_path):
    """The service's own config first, then the person's - the service's
    is SYSTEM-only, so when run from source it simply is not readable."""
    missing = tmp_path / "nowhere" / "config.json"
    mine = tmp_path / "config.json"
    mine.write_text('{"bangla_phonetic": false}', encoding="utf-8")
    assert bangla_gate.Setting([missing, mine])() is False


def test_no_readable_file_means_the_default(tmp_path):
    """Safe, because the gate still needs Avro running."""
    bad = tmp_path / "config.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert bangla_gate.Setting([bad, tmp_path / "missing.json"])() is True
