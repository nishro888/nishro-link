"""The log it owns, and the guarantee there is only one of it.

Both exist because of one incident: `nohup nishro-link > /tmp/link.log` truncates
on every launch, so a second run destroyed the first one's output - and two
instances were running at once with nothing to say so. The surviving log
described a completely different failure from the one being investigated.
"""
import socket
import time

import pytest

from link.runtime import (MAX_BYTES, RunLog, SingleInstance, exclusive,
                          firewall_hint)


# ------------------------------------------------------------------ RunLog
def test_it_appends_rather_than_truncating(tmp_path):
    """The whole reason this module exists. Shell redirection destroyed the
    evidence of a lockout by starting a second process over the same file."""
    p = tmp_path / "link.log"
    a = RunLog(p, echo=False)
    a("first process was here")
    a.close()

    b = RunLog(p, echo=False)
    b("second process arrived")
    b.close()

    text = p.read_text(encoding="utf-8")
    assert "first process was here" in text
    assert "second process arrived" in text


def test_every_line_carries_a_time_and_a_pid(tmp_path):
    """With two processes appending, the pid is the only way to tell whose
    story you are reading."""
    import os
    p = tmp_path / "link.log"
    log = RunLog(p, echo=False)
    log("hello")
    log.close()
    line = p.read_text(encoding="utf-8").strip()
    assert f"[{os.getpid()}]" in line
    assert line[:2].isdigit() and line[4] == "-"      # 2026-...
    assert line.endswith("hello")


def test_it_rolls_instead_of_growing_without_limit(tmp_path):
    p = tmp_path / "link.log"
    p.write_text("x" * (MAX_BYTES + 1), encoding="utf-8")
    log = RunLog(p, echo=False)
    log("after the roll")
    log.close()
    assert p.with_suffix(".log.1").exists()
    assert "after the roll" in p.read_text(encoding="utf-8")
    assert p.stat().st_size < 1000                    # fresh file, not the old one


def test_a_log_it_cannot_write_does_not_stop_the_program(tmp_path):
    """Losing the log is bad. Refusing to run because of it is worse."""
    bad = tmp_path / "a-file"
    bad.write_text("not a directory", encoding="utf-8")
    log = RunLog(bad / "nested" / "link.log", echo=False)
    log("this must not raise")
    log.close()


def test_it_survives_text_that_cannot_be_encoded(tmp_path):
    p = tmp_path / "link.log"
    log = RunLog(p, echo=False)
    log("emoji \U0001F600 and   and a lone surrogate is fine")
    log.close()
    assert "emoji" in p.read_text(encoding="utf-8")


# --------------------------------------------------------- SingleInstance
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_the_first_one_gets_it():
    a = SingleInstance(free_port())
    try:
        assert a.acquire() is True
    finally:
        a.release()


def test_the_second_one_is_refused():
    """Two instances fight over the same devices and interleave into the same
    log - which is how an incident ends up described by the wrong process."""
    port = free_port()
    a, b = SingleInstance(port), SingleInstance(port)
    try:
        assert a.acquire() is True
        assert b.acquire() is False
    finally:
        a.release(), b.release()


def test_releasing_lets_the_next_one_in():
    port = free_port()
    a, b = SingleInstance(port), SingleInstance(port)
    assert a.acquire() is True
    a.release()
    try:
        assert b.acquire() is True
    finally:
        b.release()


def test_different_ports_do_not_collide():
    """Two links on two ports is a legitimate setup, not a mistake."""
    a, b = SingleInstance(free_port()), SingleInstance(free_port())
    try:
        assert a.acquire() is True
        assert b.acquire() is True
    finally:
        a.release(), b.release()


def test_release_is_safe_to_call_twice():
    a = SingleInstance(free_port())
    a.acquire()
    a.release()
    a.release()


# ------------------------------------------------------------------ hints
def test_the_firewall_hint_names_the_port_and_a_command():
    """A blocked port looks exactly like a machine that is switched off, so the
    guess has to be spelled out or nobody makes it. Four hours of 'timed out'
    said neither."""
    hint = firewall_hint(8770)
    assert "8770" in hint or "NewNetFirewallRule" in hint.replace("-", "")
    assert "firewall" in hint.lower() or "Firewall" in hint


# ------------------------- a second launch is "show me", not an error
def test_a_second_launch_can_signal_the_first():
    """The lock is already a listening socket, so it carries a message for free.
    Clicking the shortcut again while the window is in the background means
    "show me" - not "fail with a port error"."""
    port = free_port()
    first = SingleInstance(port)
    seen = []
    try:
        assert first.acquire() is True
        first.watch(lambda: seen.append(1))

        second = SingleInstance(port)
        assert second.acquire() is False, "two instances must never both run"
        assert SingleInstance.signal(port) is True

        end = time.monotonic() + 3
        while time.monotonic() < end and not seen:
            time.sleep(0.01)
        assert seen, "the running copy was never told to show itself"
    finally:
        first.release()


def test_signalling_nobody_says_so():
    """Distinguishes 'already running, raised it' from 'the port is taken by
    something else entirely'."""
    assert SingleInstance.signal(free_port()) is False


def test_several_signals_all_arrive():
    port = free_port()
    first = SingleInstance(port)
    seen = []
    try:
        first.acquire()
        first.watch(lambda: seen.append(1))
        for _ in range(3):
            SingleInstance.signal(port)
        end = time.monotonic() + 3
        while time.monotonic() < end and len(seen) < 3:
            time.sleep(0.01)
        assert len(seen) == 3
    finally:
        first.release()


def test_a_callback_that_raises_does_not_kill_the_watcher():
    """A UI that will not come to the front is not a reason to stop listening."""
    port = free_port()
    first = SingleInstance(port)
    seen = []
    try:
        first.acquire()
        first.watch(lambda: (seen.append(1), 1 / 0)[0])
        SingleInstance.signal(port)
        SingleInstance.signal(port)
        end = time.monotonic() + 3
        while time.monotonic() < end and len(seen) < 2:
            time.sleep(0.01)
        assert len(seen) == 2
    finally:
        first.release()


# --------------------------------------------------------------- exclusive
# One helper, opposite socket options per platform. Each test below is a way it
# has actually gone wrong, on one platform or the other.
def _free():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _listen(port, reuse=False, excl=True):
    s = socket.socket()
    if excl:
        exclusive(s)
    if reuse:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(4)
    return s


def test_a_second_exclusive_listener_is_refused():
    port = _free()
    first = _listen(port)
    try:
        with pytest.raises(OSError):
            _listen(port).close()
    finally:
        first.close()


def test_a_socket_asking_for_reuse_cannot_steal_the_port():
    """The Windows failure. http.server sets SO_REUSEADDR, and two sockets that
    BOTH set it share a port there - so a second copy of the program bound the
    control UI on top of the first, and the window talked to the wrong node."""
    port = _free()
    first = _listen(port)
    try:
        with pytest.raises(OSError):
            _listen(port, reuse=True, excl=False).close()
    finally:
        first.close()


def test_restarting_over_time_wait_works():
    """The Linux failure. Every Connection: close the server sends leaves its
    port in TIME_WAIT for a minute, and on Linux a bind without SO_REUSEADDR
    refuses for all of it - so the first restart after turning the option off
    moved the UI to the next port with only one copy running. Found on the
    Ubuntu box. On Windows this passes either way; it is here for Linux."""
    port = _free()
    srv = _listen(port)
    client = socket.create_connection(("127.0.0.1", port), timeout=2)
    conn, _ = srv.accept()
    conn.close()                     # the SERVER hangs up first: TIME_WAIT is ours
    time.sleep(0.05)
    client.close()
    srv.close()
    time.sleep(0.05)
    _listen(port).close()            # must not raise


def test_the_single_instance_lock_survives_being_signalled_then_restarted():
    """A second launch signals the first by connecting and hanging up, and the
    first closes its end first - leaving the lock port in TIME_WAIT. Without
    exclusive(), a restart within the next minute on Linux saw its own ghost and
    refused to start, saying another copy was running."""
    port = _free()
    a = SingleInstance(port)
    assert a.acquire()
    seen = []
    a.watch(lambda: seen.append(1))
    assert SingleInstance.signal(port)
    end = time.monotonic() + 3
    while not seen and time.monotonic() < end:
        time.sleep(0.01)
    assert seen, "the signal did not arrive"
    a.release()
    time.sleep(0.05)
    b = SingleInstance(port)
    try:
        assert b.acquire(), "refused to start after a normal signal + restart"
    finally:
        b.release()



# ------------------------------------------------ a firewall that blocks us
# Windows makes BLOCK rules itself when its "allow access" prompt is dismissed,
# and a block outranks every allow. That took the link down on the laptop with
# nothing on screen to say why. These run the real parsing and script building
# with the OS calls faked: no test may change a firewall rule.
import base64 as _b64
import types as _types

from link import runtime as _rt


def test_blocks_are_described_in_words():
    got = _rt.parse_blocks("nishrolink.exe|6|2\nnishrolink.exe|17|3\n"
                           "odd|256|7\n\ngarbage line\n|6|2")
    assert got == ["nishrolink.exe (TCP, private networks)",
                   "nishrolink.exe (UDP, domain/private networks)",
                   "odd (any protocol, domain/private/public networks)"]


def test_not_being_able_to_check_is_not_a_problem(monkeypatch):
    monkeypatch.setattr(_rt.sys, "platform", "linux")
    assert _rt.firewall_blocks("x") is None
    monkeypatch.setattr(_rt.sys, "platform", "win32")

    def fails(*a, **k):
        return _types.SimpleNamespace(returncode=1, stdout="", stderr="denied")
    monkeypatch.setattr(_rt.subprocess, "run", fails)
    assert _rt.firewall_blocks("x") is None, "a failed check must not read as 'blocked'"


def test_a_block_is_reported(monkeypatch):
    monkeypatch.setattr(_rt.sys, "platform", "win32")
    monkeypatch.setattr(_rt.subprocess, "run", lambda *a, **k: _types.SimpleNamespace(
        returncode=0, stdout="nishrolink.exe|6|2\n", stderr=""))
    assert _rt.firewall_blocks(r"C:\x\NishroLink.exe") == \
        ["nishrolink.exe (TCP, private networks)"]


def test_the_fix_removes_blocks_and_allows_only_private_networks():
    s = _rt.firewall_fix_script(r"C:\Users\o'brien\NishroLink.exe")
    assert "Remove-NetFirewallRule" in s and "New-NetFirewallRule" in s
    assert "Private,Domain" in s and "Public" not in s
    assert r"'C:\Users\o''brien\NishroLink.exe'" in s, "quotes must be escaped"


def test_the_fix_goes_through_windows_own_elevation_prompt(monkeypatch):
    monkeypatch.setattr(_rt.sys, "platform", "win32")
    seen = []
    monkeypatch.setattr(_rt.subprocess, "run", lambda args, **k: seen.append(args)
                        or _types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert _rt.allow_through_firewall(r"C:\p\NishroLink.exe") is True
    outer = seen[0][-1]
    assert "-Verb RunAs" in outer
    encoded = outer.split("'-EncodedCommand','")[1].split("'")[0]
    assert _b64.b64decode(encoded).decode("utf-16-le") == \
        _rt.firewall_fix_script(r"C:\p\NishroLink.exe")


def test_declining_the_prompt_is_reported_as_declined(monkeypatch):
    monkeypatch.setattr(_rt.sys, "platform", "win32")
    monkeypatch.setattr(_rt.subprocess, "run", lambda *a, **k: _types.SimpleNamespace(
        returncode=1, stdout="", stderr="The operation was canceled by the user."))
    assert _rt.allow_through_firewall(r"C:\p\NishroLink.exe") is False


# ------------------------------------------------------- a public network
def test_a_public_network_is_named(monkeypatch):
    """Seen: the laptop's Wi-Fi moved to a network Windows called Public, and
    its firewall kept the other computer out for five hours, unexplained."""
    import subprocess
    from link import runtime
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    seen = []

    def run(cmd, **kw):
        seen.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, stdout="Home-WiFi\r\n", stderr="")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.public_networks("C:/x.exe") == ["Home-WiFi"]
    assert "Get-NetConnectionProfile" in seen[0] and "'C:/x.exe'" in seen[0]
    monkeypatch.setattr(runtime.subprocess, "run", lambda cmd, **kw:
                        subprocess.CompletedProcess(cmd, 0, stdout="\r\n", stderr=""))
    assert runtime.public_networks("C:/x.exe") == []


def test_a_network_check_that_failed_is_not_a_problem(monkeypatch):
    import subprocess
    from link import runtime
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime.subprocess, "run", lambda cmd, **kw:
                        subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no"))
    assert runtime.public_networks("C:/x.exe") is None
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    assert runtime.public_networks() is None


def test_making_a_network_private_goes_through_the_elevation_prompt(monkeypatch):
    from link import runtime
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    scripts = []
    monkeypatch.setattr(runtime, "_elevated", lambda s: scripts.append(s) or True)
    assert runtime.make_private(["Home-WiFi", "it's mine"]) is True
    assert scripts == ["Set-NetConnectionProfile -Name 'Home-WiFi' -NetworkCategory "
                       "Private; Set-NetConnectionProfile -Name 'it''s mine' "
                       "-NetworkCategory Private"]
    assert runtime.make_private([]) is False
