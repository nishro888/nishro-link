"""Files over the clipboard, between two real nodes on this machine: the
second connection that carries a copy, proved and sealed like the link,
alongside it and never instead of it."""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from link import files                                     # noqa: E402
from link.files import FilesError, Offer                   # noqa: E402
from test_files import tree                                # noqa: E402
from test_node_live import Pair                            # noqa: E402

PASSWORD = "tiger-lemon-coral-radio"


@pytest.fixture
def pair(tmp_path):
    p = Pair(pin=PASSWORD)
    p.hub.files_dest = lambda: str(tmp_path / "laptop" / files.FOLDER)
    p.aio.files_dest = lambda: str(tmp_path / "aio" / files.FOLDER)
    p.start().connected()
    try:
        yield p
    finally:
        p.stop()


@pytest.fixture
def src(tmp_path):
    s = tmp_path / "src"
    (s / "Album" / "inner").mkdir(parents=True)
    (s / "Album" / "inner" / "photo.jpg").write_bytes(os.urandom(200000))
    (s / "Album" / "list.txt").write_text("one\ntwo")
    (s / "letter.docx").write_bytes(os.urandom(5000))
    return s


def test_a_member_takes_the_hubs_copy(pair, src, tmp_path):
    pair.hub.file_offers[1] = Offer([src / "Album", src / "letter.docx"])
    link = pair.hub.links["aio"].ch
    tops = pair.aio.files_pull(1)
    dest = tmp_path / "aio" / files.FOLDER
    assert tree(dest) == tree(src)
    assert tops == [dest / "Album", dest / "letter.docx"]
    import time
    time.sleep(0.3)                   # long enough for a wrongly kept connection to register
    assert list(pair.hub.links) == ["aio"]
    assert pair.hub.links["aio"].ch is link, "the files connection is never a link"


def test_a_member_sends_its_copy_to_the_hub(pair, src, tmp_path):
    got = []
    pair.hub.on_files = lambda tops, who: got.append((tops, who))
    pair.aio.file_offers[2] = Offer([src / "letter.docx"])
    pair.aio.files_push(2)
    pair.wait(lambda: got, what="the hub to have the copy")
    dest = tmp_path / "laptop" / files.FOLDER
    assert got == [([dest / "letter.docx"], "aio")]
    assert (dest / "letter.docx").read_bytes() == (src / "letter.docx").read_bytes()


def test_the_hub_asks_a_member_for_its_copy(pair, src, tmp_path):
    """The hub cannot dial a member: it asks over the link, and the member
    opens the files connection to send it."""
    got = []
    pair.hub.on_files = lambda tops, who: got.append(who)
    pair.aio.file_offers[3] = Offer([src / "Album"])
    pair.hub.files_request("aio", 3)
    pair.wait(lambda: got, timeout=10, what="the copy to arrive at the hub")
    assert tree(tmp_path / "laptop" / files.FOLDER / "Album") == tree(src / "Album")


def test_a_big_copy_arrives_whole_while_the_link_carries_on(pair, tmp_path, monkeypatch):
    """Small pieces, so there are thousands, and a receiver slower than the
    sender: the connection's queue, which drops its OLDEST frames when full,
    would overflow - the sender must wait instead (files.BACKLOG)."""
    import time
    monkeypatch.setattr(files, "CHUNK", 2048)
    real_data = files.Receive.data
    pieces = []

    # A pause every 16th piece, not a tiny one at every piece: before Python
    # 3.11 a sleep on Windows lasts at least a timer tick (up to 15.6 ms), and
    # 3,072 of those outlasted the test (CI, Python 3.8).
    def slow_data(self, index, text):
        pieces.append(index)
        if len(pieces) % 16 == 0:
            time.sleep(0.004)
        real_data(self, index, text)
    monkeypatch.setattr(files.Receive, "data", slow_data)
    big = tmp_path / "video.mp4"
    big.write_bytes(os.urandom(6 * 1024 * 1024))
    pair.hub.file_offers[4] = Offer([big])
    link = pair.aio.ch
    done = []
    threading.Thread(target=lambda: done.append(pair.aio.files_pull(4)), daemon=True).start()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)        # the pointer crosses meanwhile
    pair.wait(lambda: pair.aio_cap.suppress[0] is True, what="the crossing, mid-copy")
    pair.wait(lambda: done, timeout=30, what="the big copy")
    assert (tmp_path / "aio" / files.FOLDER / "video.mp4").read_bytes() == big.read_bytes()
    assert pair.aio.ch is link, "the link was not touched"


def test_a_copy_no_longer_offered_is_said_so(pair):
    with pytest.raises(FilesError, match="no longer offered"):
        pair.aio.files_pull(99)


def test_the_hub_hangs_up_once_the_copy_is_dealt_with(pair):
    """A files connection is proved and sealed like a link, so it must never
    be read as one: once the copy is dealt with (here, refused), the hub hangs
    up - nothing sent on it after that is read, let alone taken as input."""
    ch, _ = pair.aio._files_channel("pull", 99)          # a copy not offered
    try:
        ch.sock.settimeout(5)
        assert ch.recv()["t"] == "ferr"
        assert ch.recv() is None, "the hub hung up"
    finally:
        ch.close()


def test_a_wrong_password_opens_no_files_connection(pair, src, tmp_path):
    pair.hub.file_offers[5] = Offer([src / "letter.docx"])
    pair.aio.pin = "wrong-words-for-this-group"
    with pytest.raises(FilesError, match="wrong password"):
        pair.aio.files_pull(5)
    assert not (tmp_path / "aio" / files.FOLDER / "letter.docx").exists()
    assert list(pair.hub.links) == ["aio"], "the link itself is untouched"


def test_a_hub_that_cannot_prove_the_password_gets_nothing(pair, src, monkeypatch):
    """Something at the hub's address that answers like a hub but cannot
    prove it knows the password must get neither a copy nor a file."""
    import socket as socket_mod
    from link import protocol, secure
    srv = socket_mod.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    seen = []

    def impostor():
        conn, _ = srv.accept()
        ch = protocol.LineChannel(conn)
        _, dh_pub = secure.keypair()
        ch.send(protocol.auth("laptop", protocol.nonce(), None, dh=dh_pub))
        seen.append(ch.recv())
        ch.send({"t": "files_ok", "v": protocol.VERSION, "node": "laptop",
                 "proof": "0" * 64})
        seen.append(ch.recv())        # anything the member sends after that
        ch.close()
    t = threading.Thread(target=impostor, daemon=True)
    t.start()
    link = pair.aio.links["laptop"]
    monkeypatch.setattr(link, "addr", ("127.0.0.1", srv.getsockname()[1]))
    pair.aio.file_offers[6] = Offer([src / "letter.docx"])
    with pytest.raises(FilesError, match="could not prove"):
        pair.aio.files_push(6)
    t.join(5)                         # it has seen the member hang up
    srv.close()
    assert seen and seen[0].get("t") == "hello"
    assert seen[1:] == [None], "nothing was sent to it after its bad proof"
