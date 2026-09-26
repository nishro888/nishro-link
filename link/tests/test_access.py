"""Keyboard and mouse access on Linux: telling "not set up" from "set up, but
log in again", and the password-prompt fix.

The program used to exit when access was missing - with no window, so someone
who had just installed the package saw nothing happen. These pin down what it
says instead.
"""
import subprocess
import sys

import pytest

from link import access


@pytest.fixture
def linux(monkeypatch, tmp_path):
    # tmp_path first: pytest makes it with os.getuid() on anything that is not
    # win32, and a Windows os has no getuid.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(access, "_group_state", lambda user=None: (False, False))


@pytest.fixture
def devices(tmp_path):
    """A /dev/input with one keyboard, and a /dev/uinput, both usable."""
    inp = tmp_path / "input"
    inp.mkdir()
    (inp / "event0").write_bytes(b"")
    uinput = tmp_path / "uinput"
    uinput.write_bytes(b"")
    return inp, uinput


def test_nothing_to_set_up_off_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    r = access.check()
    assert r["ok"] and not r["problems"] and not r["fixable"]


def test_usable_devices_need_nothing(linux, devices):
    inp, uinput = devices
    r = access.check(dev_input=str(inp), uinput=str(uinput))
    assert r["ok"] and r["read"] and r["write"] and r["problems"] == []


def test_no_readable_keyboard_is_said_in_words(linux, devices, tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    r = access.check(dev_input=str(empty), uinput=str(devices[1]))
    assert not r["ok"] and not r["read"] and r["write"]
    assert r["problems"] == ["It cannot read this computer's keyboard and mouse."]


def test_a_missing_uinput_device_is_named(linux, devices, tmp_path):
    r = access.check(dev_input=str(devices[0]), uinput=str(tmp_path / "gone"))
    assert not r["ok"] and not r["write"]
    assert "uinput device is missing" in r["problems"][0]


def test_set_up_but_not_logged_in_again_is_its_own_case(linux, monkeypatch,
                                                         tmp_path):
    """In the group on paper, not in this session: the fix has run, and
    running it again would change nothing. Say what will."""
    monkeypatch.setattr(access, "_group_state", lambda user=None: (True, False))
    r = access.check(dev_input=str(tmp_path), uinput=str(tmp_path / "gone"))
    assert r["relogin"] and not r["ok"] and not r["fixable"]
    assert r["problems"] == ["Setup is done - log out and back in once to finish it."]


def test_fixable_only_with_the_helper_and_pkexec(linux, monkeypatch, tmp_path):
    helper = tmp_path / "helper"
    missing = dict(dev_input=str(tmp_path), uinput=str(tmp_path / "gone"))
    monkeypatch.setattr(access.shutil, "which", lambda name: "/usr/bin/pkexec")
    assert not access.check(helper=str(helper), **missing)["fixable"]
    helper.write_text("#!/bin/sh\n")
    assert access.check(helper=str(helper), **missing)["fixable"]
    monkeypatch.setattr(access.shutil, "which", lambda name: None)
    assert not access.check(helper=str(helper), **missing)["fixable"]


def _run_returning(monkeypatch, code=0, err="", exc=None):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if exc:
            raise exc
        return subprocess.CompletedProcess(argv, code, stdout="", stderr=err)
    monkeypatch.setattr(access.subprocess, "run", run)
    return calls


def test_fix_asks_pkexec_to_run_the_helper_for_this_user(monkeypatch):
    calls = _run_returning(monkeypatch, 0)
    assert access.fix("sam", helper="/h") == {"ok": True}
    assert calls == [["pkexec", "/h", "sam"]]


@pytest.mark.parametrize("code", [126, 127])
def test_a_dismissed_prompt_is_not_an_error(monkeypatch, code):
    _run_returning(monkeypatch, code)
    assert access.fix("sam") == {"ok": False, "declined": True}


def test_a_failing_helper_says_why(monkeypatch):
    _run_returning(monkeypatch, 2, err="only a can be set up from their own session")
    r = access.fix("sam")
    assert not r["ok"] and "own session" in r["error"]


def test_no_pkexec_is_reported(monkeypatch):
    _run_returning(monkeypatch, exc=FileNotFoundError())
    assert access.fix("sam") == {"ok": False, "error": "pkexec is not installed"}


def test_the_stand_ins_take_every_call_the_node_makes():
    cap, inj = access.NoCapture(), access.NoInjector()
    cap.start(object())
    cap.set_suppress(True, True, cursor_here=True)
    cap.stop()
    inj.move_abs(1, 2)
    inj.move(1, 1)
    inj.button("left", True)
    inj.key(30, True)
    inj.wheel(0, 1)
    inj.close()
