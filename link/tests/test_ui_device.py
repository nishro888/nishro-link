"""One device's details: what it is, what it may do, rename, remove.

Reported: device adding, removing and editing should be better; asked what
editing should cover: renaming this device, renaming the others, per-device
control rights, and a details view - all four.
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

from link import config, control_api, ui_device, ui_theme  # noqa: E402
from link.desk import simple                                # noqa: E402
from link.node import Node, NodeCore, _Link                 # noqa: E402
from link.runtime import RunLog                             # noqa: E402
from test_node_live import FakeCapture, FakeInjector        # noqa: E402


@pytest.fixture(scope="module")
def root(tk_session):
    r = tk.Toplevel(tk_session)
    r.withdraw()
    yield r
    r.destroy()


class Channel:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    def close(self, flush=0.0):
        pass


@pytest.fixture
def api(tmp_path):
    """The laptop, hub of a group with the AIO in it."""
    cfg = config.merge(config.DEFAULTS, {"node": "laptop", "hub": True,
                                         "pin": "tiger-lemon-coral-radio",
                                         "port": 8770})
    cfg["devices"] = [{"name": "aio", "id": "aio-1", "addr": "10.0.0.9",
                       "first_seen": time.time() - 86400, "version": "0.11.0"}]
    lay = simple("laptop", (1366, 768), "aio", (1920, 1080), "right")
    node = Node(NodeCore("laptop", lay, cfg["policy"], is_hub=True), FakeCapture(),
                FakeInjector(), port=8770, pin=cfg["pin"])
    node.capture.start(node)
    a = control_api.ControlAPI(node, cfg, RunLog(tmp_path / "l.log", echo=False),
                               cfg_path=tmp_path / "c.json", port=0)
    yield a
    node.stop()


def online(api, name="aio"):
    ch = Channel()
    api.node.links[name] = _Link(name, ch, ("10.0.0.9", 1))
    api.node.core.peer_online(name)
    return ch


def details(root, api, name, **kw):
    return ui_device.DeviceDetails(root, api, ui_theme.palette(root), name, **kw)


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


def test_it_shows_what_the_device_is(root, api):
    d = details(root, api, "aio")
    try:
        t = texts(d.top)
        assert "aio" in t and "1  ·  1920×1080" in t
        assert "10.0.0.9" in t and "0.11.0" in t and "aio-1" in t
        assert "since 1 d ago" in t or any("since" in x for x in t)
    finally:
        d.close()


def test_the_hub_renames_another_device_from_here(root, api):
    d = details(root, api, "aio")
    try:
        d._start_rename()
        d.f_name.delete(0, "end")
        d.f_name.insert(0, "kitchen")
        d._save_rename()
        assert "kitchen" in api.node.core.layout.names()
        assert d.name == "kitchen", "it follows the device, not the old name"
        assert any("next switched on" in x for x in texts(d.top))
    finally:
        d.close()


def test_a_taken_name_is_refused_in_words(root, api):
    d = details(root, api, "aio")
    try:
        d._start_rename()
        d.f_name.delete(0, "end")
        d.f_name.insert(0, "LAPTOP")
        d._save_rename()
        assert "already a device called LAPTOP" in d.msg.cget("text")
    finally:
        d.close()


def test_this_devices_rights_are_changed_here(root, api):
    d = details(root, api, "laptop")
    try:
        d.driven.set(False)
        d._rights_changed()
        assert api.node.core.may_be_driven() is False
        assert config.load(api.cfg_path)["policy"]["may_be_driven"] is False
    finally:
        d.close()


def test_another_devices_rights_go_to_it_while_it_is_on(root, api):
    ch = online(api)
    d = details(root, api, "aio")
    try:
        d.drive.set(False)
        d._rights_changed()
        assert ch.sent[-1] == {"t": "set_policy", "to": "aio", "may_drive": False,
                               "may_be_driven": True}
    finally:
        d.close()


def test_rights_that_cannot_be_changed_say_why(root, api):
    d = details(root, api, "aio")                     # switched off
    try:
        assert any("switched off" in x and "applies them" in x
                   for x in texts(d.top))
    finally:
        d.close()
    api.node.core.set_hub(False)
    api.node.peer_name = "desk"
    d = details(root, api, "aio")
    try:
        assert any("Only the hub" in x for x in texts(d.top))
    finally:
        d.close()


def test_removing_asks_first(root, api):
    asked = []
    d = details(root, api, "aio", confirm=lambda t, x: asked.append(x) or True)
    d._remove()
    assert asked and "aio" not in api.node.core.layout.names()


def test_a_member_can_leave_from_its_own_details(root, api):
    api.node.core.set_hub(False)
    api.node.peer_name = "desk"
    d = details(root, api, "laptop")
    try:
        assert "Leave this group" in texts(d.top)
        assert "Remove from the group" not in texts(d.top)
    finally:
        d.close()


def test_a_device_that_leaves_the_group_is_said_to_have_gone(root, api):
    d = details(root, api, "aio")
    try:
        api.node.core.forget_machine("aio")
        api.cfg["devices"] = []
        d._sig = None
        d._render()
        assert any("no longer in this group" in x for x in texts(d.top))
    finally:
        d.close()
