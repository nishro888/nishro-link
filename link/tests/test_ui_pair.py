"""The Add-a-device dialog: no sides to choose, one password, and an outcome.

Reported: adding was "not fully user friendly", the dashes in the password left
it unclear whether to type them, and after entering a name and a password "I
don't see peer connected or not". These pin down that the dialog opens on the
devices it found, asks for one password, and follows the attempt to
"connected" or to the reason not - never closing on its own and leaving the
person guessing.
"""
import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" and not (os.environ.get("DISPLAY")
                                     or os.environ.get("WAYLAND_DISPLAY")),
    reason="no display for Tk")

import tkinter as tk                                       # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))

from link import config, control_api, ui_pair, ui_theme  # noqa: E402
from link.desk import Desk                                  # noqa: E402
from link.node import Node, NodeCore                        # noqa: E402
from link.runtime import RunLog                             # noqa: E402
from test_node_live import FakeCapture, FakeInjector        # noqa: E402


@pytest.fixture(scope="module")
def root(tk_session):
    """A window of the one shared interpreter - see conftest.py."""
    r = tk.Toplevel(tk_session)
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def api(tmp_path):
    """A fresh install: a device on its own, with a password of its own."""
    cfg = config.merge(config.DEFAULTS, {"node": "laptop", "port": 8770,
                                         "hub": True, "pin": "k7qm-2xvp-9hdt"})
    d = Desk("laptop")
    d.add("laptop", 1366, 768)
    node = Node(NodeCore("laptop", d, cfg["policy"], is_hub=True),
                FakeCapture(), FakeInjector(), port=8770, pin=cfg["pin"])
    node.capture.start(node)
    a = control_api.ControlAPI(node, cfg, RunLog(tmp_path / "l.log", echo=False),
                               cfg_path=tmp_path / "c.json", port=0)
    a.sent = []
    real = a.command

    def command(path, body):
        # Starting an attempt is recorded, not run: the tests play its progress
        # through node.adding, the way the real one reports it.
        if path in ("/api/invite", "/api/join"):
            a.sent.append((path, dict(body)))
            return {"ok": True, "started": True}
        return real(path, body)
    a.command = command
    yield a
    node.stop()


FOUND = {"devices": [
    {"name": "aio", "id": "a1", "waiting": True, "alone": True, "group": "aio"},
    {"name": "desk-pc", "id": "d9", "waiting": True, "alone": False,
     "group": "desk-pc"},
    {"name": "tablet", "id": "t1", "waiting": False, "group": "desk-pc"},
]}


def dialog(root, api, found=None, **kw):
    """The dialog, with the network search replaced by a canned answer."""
    return ui_pair.AddDevice(root, api, ui_theme.palette(root),
                             search=lambda: FOUND if found is None else found, **kw)


def settle(d, until, timeout=3.0):
    """Let the background search land, pumping Tk the way mainloop would."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d.top.update()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("never happened")


def texts(w, out=None):
    out = [] if out is None else out
    try:
        t = w.cget("text")
        if t:
            out.append(str(t))
    except tk.TclError:
        pass
    for c in w.winfo_children():
        texts(c, out)
    return out


def buttons(w):
    from link.ui_kit import Button
    out = [w] if isinstance(w, Button) else []
    for c in w.winfo_children():
        out += buttons(c)
    return out


def progress(api, phase, reason=None, detail=None, mode="invite", since=None):
    api.node.adding = {"mode": mode, "phase": phase, "reason": reason,
                       "detail": detail, "target": "aio",
                       "since": time.time() if since is None else since}


# --------------------------------------------------------------- the list
def test_it_opens_on_the_devices_found_not_a_choice_of_sides(root, api):
    d = dialog(root, api)
    try:
        settle(d, lambda: "aio" in texts(d.rows))
        t = texts(d.top)
        assert not any("Let another device connect" in x for x in t)
        assert {"aio", "desk-pc", "tablet"} <= set(t)
    finally:
        d.close()


def test_each_device_says_what_adding_it_means(root, api):
    d = dialog(root, api)
    try:
        settle(d, lambda: "aio" in texts(d.rows))
        t = texts(d.rows)
        assert "  On its own" in t and "  Has its own group" in t
        assert "  In desk-pc's group" in t
        actions = [b.cget("text") for b in buttons(d.rows)]
        assert actions == ["Add", "Join its group", "Join that group"]
    finally:
        d.close()


@pytest.mark.parametrize("found,me,want", [
    ({"name": "aio", "waiting": True, "alone": True}, {"role": "alone"},
     ("Add", "invite", "aio")),
    ({"name": "desk", "waiting": True, "alone": False, "group": "desk"},
     {"role": "alone"}, ("Join its group", "join", "desk")),
    ({"name": "tab", "waiting": False, "group": "desk"}, {"role": "alone"},
     ("Join that group", "join", "desk")),
    ({"name": "aio2", "waiting": False, "group": "laptop"},
     {"role": "hub", "group": "laptop"}, (None, None, None)),
    ({"name": "desk", "waiting": True, "alone": False, "group": "desk"},
     {"role": "hub", "group": "laptop"}, (None, None, None)),
    ({"name": "off", "waiting": False}, {"role": "alone"}, (None, None, None)),
])
def test_what_each_kind_of_device_offers(found, me, want):
    """A device with devices of its own may add, never join - they would be
    stranded. One in this group already needs nothing."""
    assert ui_pair.row_action(found, me)[2:] == want


def test_the_other_way_round_is_always_on_screen(root, api):
    """This device's own name and password, for adding it from there."""
    d = dialog(root, api)
    try:
        t = texts(d.top)
        assert "laptop" in t and "k7qm-2xvp-9hdt" in t
    finally:
        d.close()


def test_finding_nothing_explains_why_rather_than_showing_an_empty_box(root, api):
    d = dialog(root, api, found={"devices": []})
    try:
        settle(d, lambda: any("No devices found" in x for x in texts(d.rows)))

        def tips(w):
            out = [w.tip.text] if hasattr(w, "tip") else []
            for c in w.winfo_children():
                out += tips(c)
            return out
        assert any("UDP 8770" in t for t in tips(d.rows)), "one hover away"
    finally:
        d.close()


def test_a_search_that_fails_says_so(root, api):
    def boom():
        raise OSError("network is unreachable")
    d = ui_pair.AddDevice(root, api, ui_theme.palette(root), search=boom)
    try:
        settle(d, lambda: any("search failed" in x for x in texts(d.rows)))
    finally:
        d.close()


def test_a_name_typed_by_hand_goes_straight_to_the_password(root, api):
    d = dialog(root, api, found={"devices": []})
    try:
        d.f_name.insert(0, "kitchen-pc")
        d._by_name()
        assert (d.mode, d.target) == ("invite", "kitchen-pc")
    finally:
        d.close()


def test_its_own_name_is_refused(root, api):
    d = dialog(root, api, found={"devices": []})
    try:
        d.f_name.insert(0, "LAPTOP")
        d._by_name()
        assert d.mode is None and "this device" in d.found_note.cget("text")
    finally:
        d.close()


# ----------------------------------------------------------- the password
def test_one_password_field_and_the_dashes_explained(root, api):
    d = dialog(root, api)
    try:
        d._password("invite", "aio")
        assert "Enter the password shown on aio" in texts(d.top)
        assert d.f_pin._placeholder.cget("text") == "word-word-word-word"
        assert d.f_pin.get() == "", "the example is shown, never read"
        assert [x for x in texts(d.steps)] == ["○", "Find aio", "○",
                                               "Check the password", "○",
                                               "aio joins this group", "○",
                                               "Connected"]
    finally:
        d.close()


def test_a_short_password_is_caught_before_anything_is_sent(root, api):
    d = dialog(root, api)
    try:
        d._password("invite", "aio")
        d.f_pin.insert(0, "abc")
        d._go()
        assert api.sent == [] and "too short" in d.msg.cget("text")
    finally:
        d.close()


def test_the_attempt_is_followed_to_connected(root, api):
    done = []
    d = dialog(root, api, on_done=done.append)
    try:
        d._password("invite", "aio")
        d.f_pin.insert(0, "B3NR 8WZC 4TYH")
        d._go()
        assert api.sent == [("/api/invite", {"name": "aio",
                                             "pin": "B3NR 8WZC 4TYH"})]
        progress(api, "verifying")
        settle(d, lambda: d.steps.winfo_children()
               and texts(d.steps)[2] == "●")
        assert texts(d.steps)[0] == "✓", "found it"
        progress(api, "connected", detail="aio")
        settle(d, lambda: d.outcome == "connected")
        assert "aio is connected" in texts(d.top)
        assert done and done[0]["name"] == "aio"
    finally:
        d.close()


def test_a_wrong_password_says_so_and_lets_you_try_again(root, api):
    d = dialog(root, api)
    try:
        d._password("invite", "aio")
        d.f_pin.insert(0, "b3nr-8wzc-4tyh")
        d._go()
        progress(api, "failed", reason="wrong_password")
        settle(d, lambda: d.outcome == "wrong_password")
        assert d.msg.cget("text") == "Wrong password"
        assert "aio" in d.msg_detail.cget("text")
        assert d.btn_go.cget("text") == "Try again" and d.btn_go.enabled
        assert str(d.f_pin.cget("state")) == "normal"
        assert "✖" in texts(d.steps), "the step that failed is marked"
    finally:
        d.close()


def test_an_earlier_attempts_result_is_not_taken_for_this_one(root, api):
    """A "connected" left over from adding another device a minute ago."""
    progress(api, "connected", since=time.time() - 60)
    d = dialog(root, api)
    try:
        d._password("invite", "aio")
        d.f_pin.insert(0, "b3nr-8wzc-4tyh")
        d._go()
        for _ in range(10):
            d.top.update()
            time.sleep(0.02)
        assert d.outcome is None
    finally:
        d.close()


def test_a_device_with_its_own_group_offers_to_join_that_instead(root, api):
    """The password was right - it is just not on its own. Offered, not done
    behind the person's back."""
    d = dialog(root, api)
    try:
        d._password("invite", "desk-pc")
        d.f_pin.insert(0, "b3nr-8wzc-4tyh")
        d._go()
        progress(api, "failed", reason="busy", detail="tablet")
        settle(d, lambda: d.outcome == "busy")
        offer = [b for b in buttons(d.extra)
                 if b.cget("text") == "Join desk-pc's group instead"]
        assert offer
        offer[0].invoke()
        assert api.sent[-1] == ("/api/join", {"name": "desk-pc",
                                              "pin": "b3nr-8wzc-4tyh"})
    finally:
        d.close()


def test_connected_offers_to_arrange(root, api):
    went = []
    d = dialog(root, api, on_arrange=lambda: went.append(True))
    try:
        d._password("join", "desk-pc")
        d._done({"detail": "desk-pc"})
        assert "Connected to desk-pc's group" in texts(d.top)
        d._arrange()
        assert went == [True]
    finally:
        d.close()


def test_it_can_open_straight_on_the_password(root, api):
    """For a password that stopped working: the Devices page opens it here."""
    d = dialog(root, api, start=("join", "desk"))
    try:
        assert (d.mode, d.target) == ("join", "desk")
        assert "Join desk's group" in texts(d.top)
    finally:
        d.close()


@pytest.mark.parametrize("reason", [
    "wrong_password", "not_found", "unreachable", "busy", "in_group", "paused",
    "version", "timeout", "name_taken", "impostor", "not_connected", "refused"])
def test_every_failure_is_said_in_words(reason):
    head, detail = ui_pair.failure("invite", reason, "aio", "desk")
    assert head != "That didn't work" and 4 < len(head) <= 40, "a short headline"
    assert len(detail) <= 70, "and one short line, not a paragraph"
    assert "_" not in head + detail, "no codes like wrong_password"


def test_closing_during_a_search_is_harmless(root, api):
    d = dialog(root, api)
    d.close()
    time.sleep(0.2)                                  # the search lands on nothing
