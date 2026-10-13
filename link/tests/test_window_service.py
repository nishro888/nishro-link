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

import pytest


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

    def broken(log):
        raise OSError("the session said no")
    assert window_service.windows_in_session(ask=broken) == []


def test_listing_the_windows_asks_the_session_when_it_is_the_service(monkeypatch):
    """The service has no desktop, so it must ask - and the answer comes
    back over loopback, not on standard output, which on Windows goes
    nowhere the service could read."""
    monkeypatch.setattr(window_service, "IN_SERVICE", True)
    assert window_service.windows_in_session(
        ask=lambda log: [("12", "Notepad")]) == [("12", "Notepad")]


def test_listing_the_windows_looks_here_when_it_is_not_the_service(monkeypatch):
    """Run from source, or in the window, this process IS the session."""
    monkeypatch.setattr(window_service, "IN_SERVICE", False)
    assert isinstance(window_service.windows_in_session(), list)


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


# ------------------------------- a helper is let finish, whatever shape it is
class Popenish:
    """What subprocess.Popen looks like: poll() and terminate()/kill()."""

    def __init__(self, finishes_after=0.0):
        self.started = time.monotonic()
        self.after = finishes_after
        self.killed = False

    def poll(self):
        return 0 if time.monotonic() - self.started >= self.after else None

    def terminate(self):
        self.killed = True

    def kill(self):
        self.killed = True


class Winsvcish:
    """What the Windows service's own launcher gives back (winsvc.Proc):
    alive() and kill(), and NO wait() or terminate() at all."""

    def __init__(self, finishes_after=0.0):
        self.started = time.monotonic()
        self.after = finishes_after
        self.killed = False

    def alive(self):
        return time.monotonic() - self.started < self.after

    def kill(self):
        self.killed = True


@pytest.mark.parametrize("shape", [Popenish, Winsvcish])
def test_a_helper_that_finishes_by_itself_is_not_killed(shape):
    """It may be holding a hidden window of someone else's: killing it before
    it has put that window back leaves the window invisible."""
    proc = shape(finishes_after=0.2)
    window_service._end(proc, log=lambda *_: None)
    assert proc.killed is False, "it was killed although it was finishing"


@pytest.mark.parametrize("shape", [Popenish, Winsvcish])
def test_a_helper_that_overstays_is_stopped(shape, monkeypatch):
    monkeypatch.setattr(window_service, "GOODBYE_S", 0.3)
    proc = shape(finishes_after=60)
    said = []
    window_service._end(proc, log=said.append)
    assert proc.killed is True
    assert any("has not finished" in m for m in said)


def test_a_process_shape_we_do_not_know_is_not_waited_on_for_ever():
    """Better to go on than to hang: nothing is left holding a window,
    because its channel is already closed by this point."""

    class Odd:
        killed = False
    window_service._end(Odd(), log=lambda *_: None)


def test_no_helper_at_all_is_fine():
    window_service._end(None)


def test_a_helper_that_cannot_even_start_says_what_went_wrong(monkeypatch):
    """Guessing "is anyone signed in?" hid the first real failure, which was
    a module the helper could not import - only the system log knew."""
    monkeypatch.setattr(window_service, "ACCEPT_TIMEOUT", 0.5)
    far, near = net_pair()

    class Broke:
        """A helper that printed an error and died."""
        def __init__(self):
            import io
            self.stderr = io.BytesIO(
                b"Traceback...\nModuleNotFoundError: No module named 'link'\n")

        def poll(self):
            return 1

        def terminate(self):
            pass

        def kill(self):
            pass

    said = []
    why = window_service.attach(near, "lend", "1", "aio", log=said.append,
                                launch=lambda *a, **k: Broke())
    assert "No module named 'link'" in why, why
    assert any("failed to start" in m for m in said)
    far.close()
    near.close()


def test_a_helper_that_says_nothing_still_gets_a_plain_reason(monkeypatch):
    monkeypatch.setattr(window_service, "ACCEPT_TIMEOUT", 0.5)
    far, near = net_pair()
    helper = FakeHelper(speak=False)
    why = window_service.attach(near, "lend", "1", "aio", launch=helper.launch)
    assert "is anyone signed in" in why
    far.close()
    near.close()


def test_whatever_the_helper_printed_reaches_our_log():
    """It runs in the person's session, so its own output goes to the system
    log at best and nowhere at all from a windowed build - which is why the
    first two real failures could only be found with journalctl."""
    import io

    class Chatty(FakeHelper):
        def launch(self, port, token, kind, which, whose, log):
            proc = super().launch(port, token, kind, which, whose, log)
            proc.stdout = io.BytesIO(b"no window could be opened here: "
                                     b"no display name and no $DISPLAY\n")
            return proc

    far, near = net_pair()
    helper = Chatty()
    said = []
    threading.Thread(target=lambda: window_service.attach(
        near, "show", "1", "laptop", log=said.append,
        launch=helper.launch), daemon=True).start()
    assert helper.ready.wait(10)
    far.close()
    near.close()
    end = time.monotonic() + 8
    while time.monotonic() < end and not any("the window's own part" in m
                                             for m in said):
        time.sleep(0.05)
    assert any("no $DISPLAY" in m for m in said), f"said: {said}"


def test_log_lines_before_the_answer_are_not_mistaken_for_it():
    """The helper says what it is starting before it has looked at the
    window. Read as the answer, a log frame has no "ok" in it, and every
    move failed with "that window cannot be moved"."""
    far, near = net_pair()

    class Chatterbox(FakeHelper):
        def _run(self, port, token):
            import socket as s
            sock = s.create_connection(("127.0.0.1", port), timeout=10)
            self.ch = winmove.plain_channel(sock)
            self.ch.send(winmove.pack({"t": "host", "token": token}))
            self.heard.append(winmove.unpack(self.ch.recv())[0])
            # two lines of log BEFORE the answer
            self.ch.send(winmove.host_log("starting: lend window 42"))
            self.ch.send(winmove.host_log("and another thing"))
            self.ch.send(winmove.pack({"t": "host_ready", "ok": True, "why": ""}))
            self.ready.set()
            try:
                while True:
                    self.heard.append(winmove.unpack(self.ch.recv())[0])
            except winmove.ChannelError:
                pass

    helper = Chatterbox()
    said = []
    threading.Thread(target=lambda: window_service.attach(
        near, "lend", "42", "aio", log=said.append, launch=helper.launch),
        daemon=True).start()
    assert helper.ready.wait(10)
    # the relay is running: a frame gets through
    far.send(winmove.pack({"t": "ready"}))
    end = time.monotonic() + 8
    while time.monotonic() < end and not any(
            h.get("t") == "ready" for h in helper.heard):
        time.sleep(0.05)
    assert any(h.get("t") == "ready" for h in helper.heard), \
        f"the relay never started; heard {helper.heard}"
    assert any("starting: lend window 42" in m for m in said)
    far.close()
    near.close()


def test_a_helper_that_never_answers_is_not_waited_on_for_ever():
    far, near = net_pair()

    class OnlyTalks(FakeHelper):
        def _run(self, port, token):
            import socket as s
            sock = s.create_connection(("127.0.0.1", port), timeout=10)
            self.ch = winmove.plain_channel(sock)
            self.ch.send(winmove.pack({"t": "host", "token": token}))
            winmove.unpack(self.ch.recv())
            for i in range(40):
                self.ch.send(winmove.host_log(f"line {i}"))
            self.ready.set()
            try:
                while True:
                    winmove.unpack(self.ch.recv())
            except winmove.ChannelError:
                pass

    why = window_service.attach(near, "lend", "1", "aio",
                                launch=OnlyTalks().launch)
    assert "did not say whether it could" in why
    far.close()
    near.close()


def test_a_helper_line_is_logged_once_not_twice():
    """Its words arrive two ways - over the channel while it is up, and
    through its output pipe afterwards. Both were logged with the same
    wording, so every line appeared twice."""
    import io
    far, near = net_pair()

    class BothWays(FakeHelper):
        def launch(self, port, token, kind, which, whose, log):
            proc = super().launch(port, token, kind, which, whose, log)
            # as the real helper does now: nothing on stdout once the
            # channel is up
            proc.stdout = io.BytesIO(b"")
            return proc

        def _run(self, port, token):
            import socket as s
            sock = s.create_connection(("127.0.0.1", port), timeout=10)
            self.ch = winmove.plain_channel(sock)
            self.ch.send(winmove.pack({"t": "host", "token": token}))
            winmove.unpack(self.ch.recv())
            self.ch.send(winmove.pack({"t": "host_ready", "ok": True, "why": ""}))
            self.ch.send(winmove.host_log("a line worth saying once"))
            self.ready.set()
            try:
                while True:
                    winmove.unpack(self.ch.recv())
            except winmove.ChannelError:
                pass

    helper = BothWays()
    said = []
    threading.Thread(target=lambda: window_service.attach(
        near, "show", "1", "laptop", log=said.append, launch=helper.launch),
        daemon=True).start()
    assert helper.ready.wait(10)
    end = time.monotonic() + 8
    while time.monotonic() < end and not any("worth saying once" in m
                                             for m in said):
        time.sleep(0.05)
    far.close()
    near.close()
    time.sleep(1.0)
    hits = [m for m in said if "worth saying once" in m]
    assert len(hits) == 1, f"logged {len(hits)} times: {hits}"
