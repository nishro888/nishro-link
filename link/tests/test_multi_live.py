"""Three machines at once, over real sockets: a laptop that waits, and two AIOs
that connect to it. See Node._send / Node._pump for the routing.

Reported: "laptop is connected with aiopc1 and aiopc2, all three should show the
share the arrangement infos". A peer talks only to the hub, so everything one
AIO does to the other goes through the laptop - which is what these check.
"""
import time

import pytest

from link.clip import ClipboardSync
from link.desk import Desk, simple
from link.node import Node, NodeCore

from test_node_live import FakeBoard, FakeCapture, FakeInjector, free_port


class Trio:
    """laptop (hub) + aio1 + aio2, all really connected."""

    def __init__(self, pin="k7qm-2xvp-9hdt"):
        self.port = free_port()
        self.logs = {"laptop": [], "aio1": [], "aio2": []}
        hub_desk = Desk("laptop")
        hub_desk.add("laptop", 1366, 768)
        self.nodes, self.caps, self.injs, self.boards = {}, {}, {}, {}
        for name in ("laptop", "aio1", "aio2"):
            cap, inj, board = FakeCapture(), FakeInjector(), FakeBoard()
            if name == "laptop":
                core = NodeCore(name, hub_desk, {}, is_hub=True, side="right")
                n = Node(core, cap, inj, port=self.port, pin=pin,
                         on_log=self.logs[name].append)
            else:
                core = NodeCore(name, simple(name, (1920, 1080), "laptop",
                                             (1366, 768), "left"), {})
                n = Node(core, cap, inj, port=self.port, pin=pin,
                         peer_addr="127.0.0.1", on_log=self.logs[name].append)
                n._start_responder = lambda: None      # the port's is the hub's
            n.clip = ClipboardSync(name, read=board.read, write=board.write,
                                   seq_fn=board.sequence)
            self.nodes[name], self.caps[name] = n, cap
            self.injs[name], self.boards[name] = inj, board

    def __getitem__(self, name):
        return self.nodes[name]

    def core(self, name):
        return self.nodes[name].core

    def start(self, *names):
        import threading
        for name in names or ("laptop", "aio1", "aio2"):
            threading.Thread(target=self.nodes[name].run, daemon=True).start()
        return self

    def stop(self):
        for n in self.nodes.values():
            n.stop()

    def wait(self, cond, what, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.01)
        tail = "\n".join(f"  {k}: {x}" for k, v in self.logs.items() for x in v[-8:])
        pytest.fail(f"timed out waiting for {what}\n{tail}")

    def all_connected(self):
        self.wait(lambda: len(self["laptop"].links) == 2
                  and self["aio1"].connected() and self["aio2"].connected(),
                  "all three to connect")
        self.wait(lambda: all({"laptop", "aio1", "aio2"} <= set(self.core(n).layout.names())
                              for n in ("laptop", "aio1", "aio2")),
                  "every machine to know every machine")
        return self

    def row(self):
        """laptop | aio1 | aio2, left to right, applied from the laptop."""
        err = self["laptop"].arrange([
            {"name": "laptop", "w": 1366, "h": 768, "x": 0, "y": 0, "owner": "laptop"},
            {"name": "aio1", "w": 1920, "h": 1080, "x": 1366, "y": 0, "owner": "aio1"},
            {"name": "aio2", "w": 1920, "h": 1080, "x": 3286, "y": 0, "owner": "aio2"}])
        assert err is None
        self.wait(lambda: all(self.core(n).layout.get("aio2").x == 3286
                              for n in ("laptop", "aio1", "aio2")),
                  "the row to reach everyone")
        return self

    def moved_to(self, name):
        return any(c[0] == "move_abs" for c in self.injs[name].calls)


@pytest.fixture
def trio():
    t = Trio()
    try:
        yield t
    finally:
        t.stop()


# ------------------------------------------------------------ one group
def test_all_three_connect_and_all_three_know_the_whole_desk(trio):
    trio.start().all_connected()
    for n in ("laptop", "aio1", "aio2"):
        assert set(trio.core(n).layout.names()) == {"laptop", "aio1", "aio2"}
    assert trio.core("aio1").layout.overlaps() == []


def test_every_peer_knows_who_else_is_connected(trio):
    trio.start().all_connected()
    trio.wait(lambda: trio.core("aio1").online == {"laptop", "aio2"}
              and trio.core("aio2").online == {"laptop", "aio1"},
              "the roster to reach both peers")


def test_an_arrangement_made_on_the_hub_reaches_both_peers(trio):
    trio.start().all_connected().row()


def test_an_arrangement_made_on_one_peer_reaches_the_other_and_the_hub(trio):
    trio.start().all_connected()
    boxes = trio.core("aio2").layout.boxes()
    for b in boxes:
        if b["name"] == "aio2":
            b["x"], b["y"] = -1920, 0                  # to the laptop's left
        if b["name"] == "aio1":
            b["x"], b["y"] = 1366, 0
        if b["name"] == "laptop":
            b["x"], b["y"] = 0, 0
    assert trio["aio2"].arrange(boxes) is None
    # what matters is what touches what, on every machine: aio2 now meets the
    # laptop's left edge along the laptop's full height
    trio.wait(lambda: all(trio.core(n).layout.touching("aio2") == {"laptop": 768}
                          for n in ("laptop", "aio1", "aio2")),
              "aio2's arrangement to reach the laptop and aio1")


# ------------------------------------------------------------ driving
def test_the_laptop_drives_across_both_aios_in_turn(trio):
    trio.start().all_connected().row()
    trio.caps["laptop"].sink.on_pointer(1365, 400, 5, 0)       # off the right edge
    trio.wait(lambda: trio.moved_to("aio1"), "aio1's pointer to move")
    trio.caps["laptop"].sink.on_motion(2500, 0)               # on across aio1
    trio.wait(lambda: trio.moved_to("aio2"), "aio2's pointer to move")
    assert trio.core("laptop").cursor.screen == "aio2"


def test_one_aio_drives_the_other_through_the_laptop(trio):
    """aio1's own mouse, onto aio2's screen: aio1 is connected only to the
    laptop, so every movement is relayed."""
    trio.start().all_connected().row()
    trio.caps["aio1"].sink.on_pointer(500, 500, 20, 0)          # aio1 takes control
    trio.wait(lambda: trio.core("aio1").holds(), "aio1 to take control")
    trio.caps["aio1"].sink.on_pointer(1919, 500, 8, 0)          # off its right edge
    trio.wait(lambda: trio.moved_to("aio2"), "aio2's pointer to move")
    assert trio.core("aio1").cursor.screen == "aio2"
    assert not trio.moved_to("laptop"), "the laptop only relayed"


def test_typing_follows_the_cursor_to_the_third_machine(trio):
    trio.start().all_connected().row()
    trio.caps["aio1"].sink.on_pointer(500, 500, 20, 0)
    trio.wait(lambda: trio.core("aio1").holds(), "aio1 to take control")
    trio.caps["aio1"].sink.on_pointer(1919, 500, 8, 0)
    trio.wait(lambda: trio.moved_to("aio2"), "the crossing")
    trio.wait(lambda: trio.core("laptop").cursor.screen == "aio2",
              "the laptop to learn where the cursor went")
    trio.caps["laptop"].sink.on_key(30, True)                   # the laptop's keyboard
    trio.wait(lambda: ("key", 30, True) in trio.injs["aio2"].calls,
              "the keystroke to arrive on aio2")
    assert ("key", 30, True) not in trio.injs["aio1"].calls


# ------------------------------------------------------ someone leaves
def test_when_the_machine_being_driven_leaves_everything_comes_home(trio):
    trio.start().all_connected().row()
    trio.caps["aio1"].sink.on_pointer(500, 500, 20, 0)
    trio.wait(lambda: trio.core("aio1").holds(), "aio1 to take control")
    trio.caps["aio1"].sink.on_pointer(1919, 500, 8, 0)
    trio.wait(lambda: trio.moved_to("aio2"), "the crossing onto aio2")

    trio["aio2"].stop()
    trio["aio2"].ch.close()                                    # aio2 goes away
    trio.wait(lambda: trio.core("laptop").holds(), "the laptop to take control back")
    trio.wait(lambda: trio.core("aio1").cursor.screen != "aio2"
              and not trio.core("aio1").suppress_mouse(),
              "aio1 to stop steering a machine that is gone")
    trio.wait(lambda: trio.core("aio1").online == {"laptop"},
              "aio1 to learn aio2 has left")


def test_the_others_stay_connected_when_one_leaves(trio):
    trio.start().all_connected()
    trio["aio2"].stop()
    trio["aio2"].ch.close()
    trio.wait(lambda: "aio2" not in trio["laptop"].links, "aio2's link to go")
    assert "aio1" in trio["laptop"].links and trio["aio1"].connected()
    # aio1 hears it from the laptop's roster, a moment after the laptop knows
    trio.wait(lambda: "aio2" not in trio.core("aio1").reachable(),
              "aio1 to treat aio2 as a wall")
    # and aio2's machine stays in the arrangement, dimmed, until forgotten
    assert "aio2" in trio.core("aio1").layout.names()


def test_rejoining_does_not_jolt_whoever_is_driving(trio):
    trio.start().all_connected().row()
    trio.caps["laptop"].sink.on_pointer(1365, 400, 5, 0)       # the laptop drives onto aio1
    trio.wait(lambda: trio.moved_to("aio1"), "the crossing")
    epoch = trio.core("laptop").epoch
    where = (trio.core("laptop").cursor.screen, trio.core("laptop").cursor.x)
    trio["aio2"].ch.close()                                    # aio2 drops and comes back
    trio.wait(lambda: "aio2" not in trio["laptop"].links, "aio2 to drop")
    trio.wait(lambda: "aio2" in trio["laptop"].links, "aio2 to come back", timeout=10)
    assert trio.core("laptop").epoch == epoch, "a join must not re-grant under the driver"
    assert (trio.core("laptop").cursor.screen, trio.core("laptop").cursor.x) == where


# -------------------------------------------------------------- clipboard
def test_a_copy_on_one_aio_reaches_everyone(trio):
    trio.start().all_connected()
    trio.boards["aio1"].copy("hello from aio1")
    trio.wait(lambda: trio.boards["aio2"].text == "hello from aio1"
              and trio.boards["laptop"].text == "hello from aio1",
              "the clipboard to reach the laptop and aio2")


# ------------------------------------------------------------- the rules
def test_a_peer_cannot_speak_for_the_hub(trio):
    """Only the hub says who holds control. A peer forging a grant is neither
    believed by the hub nor passed on to the others."""
    from link import protocol
    trio.start().all_connected()
    epoch = trio.core("aio2").epoch
    trio["aio1"]._send(protocol.baton("aio1", epoch + 50, "aio1", 1, 1))
    time.sleep(0.3)
    assert trio.core("laptop").baton.holder == "laptop"
    assert trio.core("aio2").epoch == epoch


def test_a_machine_using_the_hubs_own_name_is_refused(trio):
    trio.start("laptop")
    trio.wait(lambda: trio["laptop"]._srv_sock is not None, "the laptop to listen")
    core = NodeCore("laptop", simple("laptop", (100, 100), "x", (100, 100), "left"), {})
    impostor = Node(core, FakeCapture(), FakeInjector(), port=trio.port,
                    pin="k7qm-2xvp-9hdt", peer_addr="127.0.0.1")
    impostor._start_responder = lambda: None
    import threading
    threading.Thread(target=impostor.run, daemon=True).start()
    try:
        trio.wait(lambda: any("that is our name" in x for x in trio.logs["laptop"]),
                  "the refusal")
        assert not trio["laptop"].links
    finally:
        impostor.stop()


# ------------------------------------------------------ monitors changing
def test_a_monitor_plugged_into_one_aio_reaches_every_machine(trio):
    """aio1 gains a second display. Nobody reconnects; within moments the
    laptop and aio2 both see aio1 as two displays, and nothing overlaps."""
    from link import desktop
    trio.start().all_connected().row()
    two = desktop.from_monitors([(0, 0, 1920, 1080), (1920, 0, 1280, 1024)])
    trio["aio1"]._displays_changed(two)
    trio.wait(lambda: all(len(trio.core(n).layout.get("aio1").displays()) == 2
                          for n in ("laptop", "aio1", "aio2")),
              "every machine to see aio1's second display")
    for n in ("laptop", "aio1", "aio2"):
        assert trio.core(n).layout.overlaps() == [], n
        # aio2 was right of aio1: pushed along, and now beside aio1's NEW
        # display - which is 1024 tall, so that is the stretch they share
        assert trio.core(n).layout.touching("aio2") == {"aio1": 1024}, n


def test_the_hubs_own_monitor_change_reaches_everyone(trio):
    from link import desktop
    trio.start().all_connected().row()
    two = desktop.from_monitors([(0, 0, 1366, 768), (-1920, -148, 1920, 1080)])
    trio["laptop"]._displays_changed(two)
    trio.wait(lambda: all(trio.core(n).layout.get("laptop").w == 3286
                          for n in ("laptop", "aio1", "aio2")),
              "every machine to see the laptop's whole desktop")


def test_a_desktop_that_moved_its_corner_moves_capture_and_injection():
    """Windows counts from the primary monitor; a monitor added to its LEFT
    moves the desktop's corner, and capture and injection must follow."""
    from link import desktop
    from link.node import Node
    seen = []

    class Tracks(FakeCapture):
        def set_origin(self, x, y):
            seen.append(("capture", x, y))

    class Moves(FakeInjector):
        def set_origin(self, x, y):
            seen.append(("inject", x, y))

    core = NodeCore("laptop", simple("laptop", (1366, 768), "aio", (1920, 1080),
                                     "right"), {}, is_hub=True)
    n = Node(core, Tracks(), Moves(), port=1)
    n._displays_changed(desktop.from_monitors([(0, 0, 1366, 768),
                                               (-1920, -148, 1920, 1080)]))
    assert ("capture", -1920, -148) in seen and ("inject", -1920, -148) in seen
    assert core.layout.get("laptop").w == 3286
