"""The Add-a-device dialog: two choices, and what each one sends."""
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" and not (os.environ.get("DISPLAY")
                                     or os.environ.get("WAYLAND_DISPLAY")),
    reason="no display for Tk")

import tkinter as tk                                       # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))

from link import config, control_api, pairing, ui_pair, ui_theme  # noqa: E402
from link.desk import simple                              # noqa: E402
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
    cfg = config.merge(config.DEFAULTS, {"node": "laptop", "port": 8770})
    core = NodeCore("laptop", simple("laptop", (1366, 768), "peer", (1366, 768),
                                     "right"), cfg["policy"], is_hub=False,
                    side="right")
    node = Node(core, FakeCapture(), FakeInjector(), port=8770)
    node.capture.start(node)
    a = control_api.ControlAPI(node, cfg, RunLog(tmp_path / "l.log", echo=False),
                               cfg_path=tmp_path / "c.json", port=0)
    yield a
    node.stop()


FOUND = {"devices": [
    {"name": "aio", "id": "a1", "addr": "192.168.1.20", "waiting": True},
    {"name": "desk-pc", "id": "d9", "addr": "192.168.1.40", "waiting": False},
]}


def dialog(root, api, found=None):
    """The dialog, with the network search replaced by a canned answer."""
    return ui_pair.AddDevice(root, api, ui_theme.palette(root),
                             search=lambda: FOUND if found is None else found)


def settle(d, until, timeout=3.0):
    """Let the background search land, pumping Tk the way mainloop would."""
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d.top.update()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("the search result never reached the dialog")


def listed(d):
    return [d.devices.get(i) for i in range(d.devices.size())]


# ------------------------------------------------------------- the choice
def test_it_opens_on_the_choice_between_two_sides(root, api):
    d = dialog(root, api)
    try:
        texts = _all_text(d.top)
        assert any("Let another device connect" in t for t in texts)
        assert any("Connect to another device" in t for t in texts)
        assert any("does not matter which" in t for t in texts)
    finally:
        d.close()


def test_you_can_go_back_to_the_choice(root, api):
    d = dialog(root, api)
    try:
        d._enter()
        d._choice()
        assert any("Let another device connect" in t for t in _all_text(d.top))
    finally:
        d.close()


# --------------------------------------------- letting the other one connect
def test_it_shows_a_name_and_a_generated_password_not_an_address(root, api):
    d = dialog(root, api)
    try:
        d._show()
        assert d.f_name.get() == "laptop"
        assert pairing.problem(d.f_pin.get()) is None, "a sound password"
        assert not any("192.168." in t for t in _all_text(d.top))
    finally:
        d.close()


def test_the_password_shown_is_the_one_that_waits(root, api):
    """Otherwise the other device types in something this one never uses."""
    d = dialog(root, api)
    try:
        d._show()
        shown = d.f_pin.get()
        d._do_wait()
        assert api.node.pin == shown
        assert config.load(api.cfg_path)["pin"] == shown
    finally:
        d.close()


def test_a_new_password_replaces_the_one_shown(root, api):
    d = dialog(root, api)
    try:
        d._show()
        old = d.f_pin.get()
        d._new_password()
        assert d.f_pin.get() != old
        assert api.node.pin == d.f_pin.get()
        assert "paired again" in d.msg.cget("text")
    finally:
        d.close()


def test_waiting_pairs_this_device_as_the_listener(root, api):
    d = dialog(root, api)
    try:
        d._show()
        d._do_wait()
        assert d.status and d.status.get("waiting") is True
        assert api.node.core.is_hub is True
        assert config.load(api.cfg_path)["hub"] is True
    finally:
        d.close()


def test_waiting_stays_open_because_the_details_are_still_needed(root, api):
    """Closing it would take the name and password off screen at exactly the
    moment someone is typing them on the other machine."""
    d = dialog(root, api)
    try:
        d._show()
        d._do_wait()
        assert d.top.winfo_exists()
        assert "Leave this open" in d.msg.cget("text")
    finally:
        d.close()


def test_ports_and_addresses_are_tucked_away(root, api):
    d = dialog(root, api)
    try:
        d._enter()
        assert d.advanced.get() is False
        assert not d.f_addr.winfo_ismapped()
    finally:
        d.close()


# -------------------------------------------------- connecting to another
def test_devices_on_the_network_are_listed(root, api):
    d = dialog(root, api)
    try:
        d._enter()
        settle(d, lambda: d.devices.size() == 2)
        rows = listed(d)
        assert "aio" in rows[0] and "waiting" in rows[0]
        assert "desk-pc" in rows[1] and "not accepting" in rows[1]
    finally:
        d.close()


def test_the_only_waiting_device_is_picked_for_you(root, api):
    d = dialog(root, api)
    try:
        d._enter()
        settle(d, lambda: d.f_name.get() == "aio")
    finally:
        d.close()


def test_picking_a_device_that_is_not_waiting_says_what_to_do(root, api):
    d = dialog(root, api)
    try:
        d._enter()
        settle(d, lambda: d.devices.size() == 2)
        d.devices.selection_clear(0, "end")
        d.devices.selection_set(1)
        d._picked()
        assert d.f_name.get() == "desk-pc"
        assert "not waiting" in d.msg.cget("text")
    finally:
        d.close()


def test_finding_nothing_explains_why_rather_than_showing_an_empty_box(root, api):
    d = dialog(root, api, found={"devices": []})
    try:
        d._enter()
        settle(d, lambda: "Nothing found" in d.found_note.cget("text"))
        assert "UDP 8770" in d.found_note.cget("text")
    finally:
        d.close()


def test_a_search_that_fails_says_so(root, api):
    def broken():
        raise OSError("network is unreachable")
    d = ui_pair.AddDevice(root, api, ui_theme.palette(root), search=broken)
    try:
        d._enter()
        settle(d, lambda: "failed" in d.found_note.cget("text"))
    finally:
        d.close()


def test_dialling_pairs_this_device_by_name(root, api):
    d = dialog(root, api)
    try:
        d._enter()
        settle(d, lambda: d.f_name.get() == "aio")
        d.f_pin.insert(0, "k7qm-2xvp-9hdt")
        d._do_dial()
        assert d.status and d.status.get("waiting") is False
        assert api.node.peer_name == "aio"
        assert api.node.peer_addr is None, "no address was typed, none is kept"
    finally:
        d.close()


def test_a_refusal_is_shown_in_the_dialog_not_swallowed(root, api):
    d = dialog(root, api)
    try:
        d._enter()
        settle(d, lambda: d.f_name.get() == "aio")
        d.f_pin.insert(0, "abc")            # too short
        d._do_dial()
        assert d.status is None
        assert "8 characters" in d.msg.cget("text")
        assert d.top.winfo_exists(), "and the dialog stays up to be corrected"
    finally:
        d.close()


def test_dialling_without_a_name_says_so(root, api):
    d = dialog(root, api, found={"devices": []})
    try:
        d._enter()
        d.f_pin.insert(0, "k7qm-2xvp-9hdt")
        d._do_dial()
        assert "name" in d.msg.cget("text")
    finally:
        d.close()


def test_closing_during_a_search_is_harmless(root, api):
    import threading
    gate = threading.Event()
    d = ui_pair.AddDevice(root, api, ui_theme.palette(root),
                          search=lambda: gate.wait(2) and FOUND)
    d._enter()
    d.close()
    gate.set()                              # the result lands after the close
    root.update()


def _all_text(widget, out=None):
    out = [] if out is None else out
    try:
        t = widget.cget("text")
        if t:
            out.append(str(t))
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        _all_text(child, out)
    return out
