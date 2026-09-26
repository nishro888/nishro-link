"""The desk agent and the service's stand-ins for capture and injection.

On Windows the service cannot reach any screen; the agent does, on whichever
desktop is showing - including the lock screen and the login screen. These
drive the whole relay in-process, with fake devices.
"""
import threading
import time

import pytest

from link import agent
from link.desktop import Desktop


class FakeCapture:
    def __init__(self):
        self.sink = None
        self.suppress = []
        self.noted = []
        self.stopped = False

    def start(self, sink):
        self.sink = sink

    def stop(self):
        self.stopped = True

    def set_suppress(self, m, k, here=False):
        self.suppress.append((m, k, here))

    def note_injected(self, x, y):
        self.noted.append((x, y))


class FakeInjector:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a: self.calls.append((name, *a))


class Sink:
    def __init__(self):
        self.got = []

    def __getattr__(self, name):
        return lambda *a: self.got.append((name, *a))


def wait(cond, what, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    pytest.fail(f"timed out waiting for {what}")


@pytest.fixture
def relay():
    hub = agent.AgentHub()
    sink = Sink()
    cap_proxy = agent.AgentCapture(hub)
    inj_proxy = agent.AgentInjector(hub)
    cap_proxy.start(sink)
    cap, inj = FakeCapture(), FakeInjector()
    board = {"text": "from the laptop", "seq": 7}
    showing = {"name": "Default"}
    stop = threading.Event()
    result = {}

    def go():
        result["code"] = agent.run(
            hub.port, hub.token, capture=cap, injector=inj,
            detect=lambda: Desktop(0, 0, 3286, 1080, ((0, 0, 1920, 1080),
                                                     (1920, 148, 1366, 768))),
            desk_name=lambda: "Default", showing=lambda: showing["name"],
            clip_get=lambda: board["text"],
            clip_set=lambda v: board.update(text=v) or True,
            clip_seq=lambda: board["seq"], watch_every=0.05, stop=stop)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    wait(lambda: hub.connected.is_set(), "the agent to connect")
    yield {"hub": hub, "sink": sink, "cap": cap, "inj": inj, "cap_proxy": cap_proxy,
           "inj_proxy": inj_proxy, "board": board, "showing": showing,
           "result": result, "thread": t}
    stop.set()
    hub.stop()


def test_input_read_by_the_agent_reaches_the_node(relay):
    relay["cap"].sink.on_key(30, True)
    relay["cap"].sink.on_motion(5, -3)
    wait(lambda: len(relay["sink"].got) == 2, "both events")
    assert relay["sink"].got == [("on_key", 30, True), ("on_motion", 5, -3)]


def test_the_nodes_input_is_played_by_the_agent(relay):
    relay["inj_proxy"].move_abs(100, 200)
    relay["inj_proxy"].key(30, True)
    relay["inj_proxy"].button("left", False)
    wait(lambda: len(relay["inj"].calls) == 3, "three calls")
    assert relay["inj"].calls == [("move_abs", 100, 200), ("key", 30, 1),
                                  ("button", "left", False)]
    assert relay["cap"].noted == [(100, 200)], "the capture hears where it went"


def test_suppression_follows_the_node(relay):
    relay["cap_proxy"].set_suppress(True, True, False)
    wait(lambda: (True, True, False) in relay["cap"].suppress, "suppression")


def test_the_agent_reports_the_screens_it_sees(relay):
    wait(lambda: relay["hub"].desktop() is not None, "the desktop")
    d = relay["hub"].desktop()
    assert (d.w, d.h, len(d.parts)) == (3286, 1080, 2)


def test_the_clipboard_is_read_and_written_in_the_console_session(relay):
    hub = relay["hub"]
    assert hub.clip_seq() == 7
    assert hub.clip_read() == "from the laptop"
    assert hub.clip_write("from the aio") is True
    assert relay["board"]["text"] == "from the aio"


def test_the_lock_screen_showing_moves_the_agent(relay):
    """The agent says where to, and leaves; the service starts one there."""
    relay["showing"]["name"] = "Winlogon"
    relay["thread"].join(3)
    assert relay["result"]["code"] == 2
    assert relay["hub"]._desk_name == "Winlogon"
    assert relay["cap"].suppress[-1] == (False, False, False), "input left alive"


def test_a_stranger_on_the_port_gets_nothing():
    hub = agent.AgentHub()
    hub.sink = Sink()
    hub.start()
    import socket
    from link import protocol
    s = socket.create_connection(("127.0.0.1", hub.port))
    ch = protocol.LineChannel(s)
    ch.send({"t": "hello", "token": "guess"})
    ch.send({"t": "ev", "k": "key", "a": [30, True]})
    time.sleep(0.3)
    assert hub.sink.got == [] and not hub.connected.is_set()
    ch.close()
    hub.stop()


def test_between_agents_nothing_is_played_and_nothing_fails():
    hub = agent.AgentHub()
    inj = agent.AgentInjector(hub)
    inj.move_abs(1, 2)                           # no agent: dropped quietly
    assert hub.clip_seq() == 0 and hub.clip_read() == ""
    hub.stop()


def test_the_service_keeps_an_agent_running():
    launched = []

    class Proc:
        def __init__(self):
            self.dead = False

        def alive(self):
            return not self.dead

        def kill(self):
            self.dead = True

    def launch(port, token, desk):
        p = Proc()
        launched.append((desk, p))
        return p
    hub = agent.AgentHub(launch=launch)
    agent.HELLO_TIMEOUT, old = 0.2, agent.HELLO_TIMEOUT
    try:
        hub.start()
        wait(lambda: launched, "a first agent")
        launched[0][1].dead = True               # it died
        hub._desk_name = "Winlogon"
        wait(lambda: len(launched) >= 2, "a second agent")
        assert launched[1][0] == "Winlogon", "started where the screen is"
    finally:
        agent.HELLO_TIMEOUT = old
        hub.stop()
