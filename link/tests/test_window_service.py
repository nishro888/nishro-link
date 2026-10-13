"""The service's side of a moved window: start the helper, pass frames along.

window_service.attach() is what the node calls. It makes a one-use token,
starts the part of Nishro Link that has a screen, waits for it to prove
itself, and then passes frames between the window channel and it.

Here the helper is a fake, so this runs anywhere; the real helper is driven
end to end in test_window_host.py on Windows.
"""
import socket
import threading
import time


from link import winmove, window_service


class FakeHelper:
    """Stands in for the part of Nishro Link in the session. It connects,
    proves the token (or does not), and says what it heard."""

    def __init__(self, token=None, speak=True, prove=True, ready=True,
                 why=""):
        self.token = token
        self.speak = speak
        self.prove = prove
        self.ready_ok = ready
        self.why = why
        self.heard = []
        self.sent = []
        self.ch = None
        self.ready = threading.Event()
        self.done = threading.Event()
        self.args = None
        self._thread = None
        self.returncode = None

    def launch(self, port, token, kind, which, whose, log):
        self.args = {"port": port, "token": token, "kind": kind,
                     "which": which, "whose": whose}
        self._thread = threading.Thread(target=self._run, args=(port, token),
                                        daemon=True)
        self._thread.start()
        return self

    # it must look enough like a process for attach() to end it
    def terminate(self):
        self.done.set()

    def kill(self):
        self.done.set()

    def wait(self, timeout=None):
        return 0

    def _run(self, port, token):
        if not self.speak:
            return
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        except OSError:
            return
        self.ch = winmove.plain_channel(sock)
        self.ch.send(winmove.pack(
            {"t": "host", "token": (token if self.prove else "not the token")}))
        try:
            answer, _ = winmove.unpack(self.ch.recv())
            self.heard.append(answer)
            if answer.get("t") != "host_ok":
                self.ready.set()
                return
            # Only the session can look at the window, so it says whether
            # this is possible at all.
            self.ch.send(winmove.pack({"t": "host_ready", "ok": self.ready_ok,
                                       "why": self.why}))
            self.ready.set()
            if not self.ready_ok:
                return
            while True:
                self.heard.append(winmove.unpack(self.ch.recv())[0])
        except winmove.ChannelError:
            pass
        finally:
            self.ready.set()
            self.done.set()


def net_pair():
    """The two ends of a window channel, as the network would be."""
    a, b = socket.socketpair()
    return winmove.plain_channel(a), winmove.plain_channel(b)


def test_the_helper_is_told_where_to_connect_and_what_to_do():
    far, near = net_pair()
    helper = FakeHelper()
    threading.Thread(target=lambda: window_service.attach(
        near, "lend", "1234", "aio", launch=helper.launch), daemon=True).start()
    assert helper.ready.wait(10)
    assert helper.args["kind"] == "lend"
    assert helper.args["which"] == "1234"
    assert helper.args["whose"] == "aio"
    assert helper.args["port"] > 0
    assert helper.heard[0]["t"] == "host_ok"
    far.close()
    near.close()


def test_frames_are_passed_both_ways():
    """What the service is for here: it proved and sealed the connection, and
    then only carries frames between the network and the screen."""
    far, near = net_pair()
    helper = FakeHelper()
    threading.Thread(target=lambda: window_service.attach(
        near, "show", "7", "laptop", launch=helper.launch), daemon=True).start()
    assert helper.ready.wait(10)
    far.send(winmove.pack({"t": "meta", "title": "Notepad", "w": 80, "h": 60}))
    end = time.monotonic() + 8
    while time.monotonic() < end and len(helper.heard) < 2:
        time.sleep(0.02)
    assert any(h.get("t") == "meta" and h.get("title") == "Notepad"
               for h in helper.heard), f"heard {helper.heard}"
    helper.ch.send(winmove.pack({"t": "ack", "n": 1}))
    got, _ = winmove.unpack(far.recv())
    assert got == {"t": "ack", "n": 1}
    far.close()
    near.close()


def test_a_helper_that_cannot_prove_the_token_is_turned_away():
    """The token is what stops anything else on this computer from being
    handed a window."""
    far, near = net_pair()
    helper = FakeHelper(prove=False)
    said = []
    why = window_service.attach(near, "lend", "1", "aio", log=said.append,
                                launch=helper.launch)
    assert "could not prove itself" in why
    assert any("could not prove it was ours" in m for m in said)
    far.close()
    near.close()


def test_a_helper_that_never_turns_up_is_waited_for_and_then_given_up_on(
        monkeypatch):
    monkeypatch.setattr(window_service, "ACCEPT_TIMEOUT", 1.0)
    far, near = net_pair()
    helper = FakeHelper(speak=False)
    why = window_service.attach(near, "lend", "1", "aio", launch=helper.launch)
    assert "did not start" in why
    far.close()
    near.close()


def test_nobody_signed_in_is_said_plainly():
    """On Linux the helper cannot be started when there is no session: the
    person must be told that, not given a silence."""
    far, near = net_pair()
    why = window_service.attach(near, "lend", "1", "aio",
                                launch=lambda *a, **k: None)
    assert "nobody is signed in" in why
    far.close()
    near.close()


def test_a_helper_that_will_not_start_is_reported():
    far, near = net_pair()

    def no(*a, **k):
        raise OSError("no such program")
    why = window_service.attach(near, "lend", "1", "aio", launch=no)
    assert "could not start" in why and "no such program" in why
    far.close()
    near.close()


def test_each_window_gets_a_token_of_its_own():
    tokens = []
    for _ in range(3):
        far, near = net_pair()
        helper = FakeHelper(speak=False)
        window_service.attach(near, "lend", "1", "aio", launch=helper.launch)
        tokens.append(helper.args["token"])
        far.close()
        near.close()
    assert len(set(tokens)) == 3, "a token was used twice"
    assert all(len(t) >= 32 for t in tokens)


# ------------------------------------------------- how it runs itself again
def test_the_command_to_run_this_program_again_is_the_right_shape():
    cmd = window_service.self_command()
    assert cmd and cmd[0]
    # From source it is python -m; frozen, the exe takes its own flags.
    assert cmd[1:] == ["-m", "link.nishro_link"] or len(cmd) == 1


def test_whether_this_is_the_service_is_told_not_guessed():
    was = window_service.IN_SERVICE
    try:
        window_service.in_service(True)
        assert window_service._is_service() is True
        window_service.in_service(False)
        assert window_service._is_service() is False
    finally:
        window_service.IN_SERVICE = was


def test_listing_the_windows_never_raises(monkeypatch):
    """It is for a tray menu: an empty list is fine, an exception is not."""
    monkeypatch.setattr(window_service, "IN_SERVICE", True)

    def broken(*a, **k):
        raise OSError("the session said no")
    said = []
    assert window_service.windows_in_session(said.append, run=broken) == []
    assert any("could not list" in m for m in said)


def test_listing_the_windows_reads_what_the_session_printed(monkeypatch):
    monkeypatch.setattr(window_service, "IN_SERVICE", True)

    class Out:
        stdout = 'something harmless first\n[["12", "Notepad"]]\n'
    assert window_service.windows_in_session(run=lambda *a, **k: Out()) == \
        [("12", "Notepad")]


def test_a_window_the_session_says_cannot_move_is_reported():
    """Only the session can look at a window - the service has no desktop -
    so its reason is what the person must be shown."""
    far, near = net_pair()
    helper = FakeHelper(ready=False, why="that window is minimized")
    why = window_service.attach(near, "lend", "1", "aio", launch=helper.launch)
    assert why == "that window is minimized"
    # and the other computer is told, so no frame is left waiting
    got, _ = winmove.unpack(far.recv())
    assert got["t"] == "gone"
    far.close()
    near.close()
