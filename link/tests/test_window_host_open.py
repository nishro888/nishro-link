"""The helper opens the window BEFORE it tells the service it is ready.

On GNOME, opening the window is where the person answers GNOME's dialog. A
"no" given there must come back as the reason - to the service, which
passes it to both computers' trays, as any refusal is. Once the service is
passing frames along, all anyone could learn is that the window "came home".

Every system: the windows module is a stand-in, and so is the service - a
loopback socket that reads what the helper says.
"""
import socket
import threading

from link import window_host, winmove


class Refusing:
    """A windows module whose window the person declines to share."""
    PICK = "pick"

    @staticmethod
    def ref(which):
        return str(which)

    @staticmethod
    def why_not(which, own_pid=None):
        return ""

    class Window:
        def __init__(self, which, rescue=None, log=None):
            raise Exception("the window was not shared - GNOME's dialog was "
                            "cancelled")


class Numbered:
    """A windows module that takes handles as numbers, as Windows' does."""
    seen = []

    @staticmethod
    def why_not(which, own_pid=None):
        Numbered.seen.append(which)
        return "no, for the test"


def service_stand_in():
    """(port, what the helper said, the thread): accepts the helper once,
    lets it in, and keeps every frame up to its host_ready."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    said = []

    def run():
        conn, _ = srv.accept()
        srv.close()
        ch = winmove.plain_channel(conn)
        try:
            said.append(winmove.unpack(ch.recv())[0])
            ch.send(winmove.pack({"t": "host_ok"}))
            while True:
                header, _ = winmove.unpack(ch.recv())
                said.append(header)
                if header.get("t") == "host_ready":
                    break
        except winmove.ChannelError:
            pass
        finally:
            ch.close()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return srv.getsockname()[1], said, t


def isolated(monkeypatch, tmp_path, module):
    monkeypatch.setenv("NISHRO_LINK_MOVED_RECORD", str(tmp_path / "moved.json"))
    monkeypatch.setenv("NISHRO_LINK_WINDOW_LOG", str(tmp_path / "host.log"))
    monkeypatch.setattr(window_host, "windows_module", lambda: module)


def test_a_no_in_gnomes_dialog_is_the_reason_the_service_is_given(monkeypatch,
                                                                    tmp_path):
    isolated(monkeypatch, tmp_path, Refusing)
    port, said, t = service_stand_in()
    why = window_host.one_window(port, "token", "lend", "pick-1a2b", "laptop",
                                 log=lambda m: None)
    t.join(10)
    ready = [h for h in said if h.get("t") == "host_ready"]
    assert ready and ready[0]["ok"] is False, said
    assert "cancelled" in ready[0]["why"]
    assert "cancelled" in why


def test_a_window_handle_still_reaches_windows_as_a_number(monkeypatch, tmp_path):
    """Windows' module takes a handle as a number: what came as text from
    the service is turned into one, as before."""
    isolated(monkeypatch, tmp_path, Numbered)
    Numbered.seen.clear()
    assert window_host.can_lend_this("4242") == "no, for the test"
    assert Numbered.seen == [4242]


def test_text_that_is_no_handle_is_refused_in_words(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path, Numbered)
    assert window_host.can_lend_this("pick-1a2b") == "that is not a window"
