"""The frame (window_frame.py): a real Tk window showing a moved window.

This opens a window on the screen, so it needs a display - skipped where
there is none. It drives the frame as winmove.AwaySession would, and reads
back what the frame says the person did, by sending it real Tk events.
"""
import queue
import sys
import threading
import time

import pytest

from link import picture, window_frame

tk = pytest.importorskip("tkinter")


def bgra(w, h, b=0, g=0, r=0):
    return bytes([b, g, r, 255]) * (w * h)


def region(x, y, w, h, **colour):
    codec, data = picture.encode(bgra(w, h, **colour), w, h)
    return (x, y, w, h, codec, data)


@pytest.fixture
def frame():
    """A frame, taken off the screen however the test ends.

    No Tk is made on this thread: Tcl may only be used from the thread that
    made it, and a second root in another thread kills the process - which
    is exactly what a display check of `tk.Tk()` here used to do."""
    f = window_frame.Frame(where="laptop")
    try:
        yield f
    finally:
        f.close("stopped")
        f._up.wait(5)


def wait(cond, what, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {what}")


def drained(q, timeout=2.0, until=None):
    """Everything on a queue, waiting for `until` to be satisfied if given.

    Without `until` this returns as soon as the queue goes quiet, which is
    not the same as "everything the test is waiting for has arrived" - a
    frame may well put something of its own on the queue first.
    """
    out = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            out.append(q.get(timeout=0.05))
        except queue.Empty:
            if out and (until is None or until(out)):
                break
        if until is not None and until(out):
            break
    return out


# ------------------------------------------------------------------ opening
def on_tk(fn, timeout=8.0):
    """Run something on the Tk thread and wait for it.

    Tcl may only be used from the thread that made it, so a test cannot
    poke a widget directly: event_generate from this thread does nothing at
    all (silently - which cost three puzzling failures). Everything the real
    code does to a frame goes through the host's queue, and so does this.
    """
    done = threading.Event()
    box = []

    def job():
        try:
            box.append(fn())
        finally:
            done.set()
    window_frame._HOST.later(job)
    assert done.wait(timeout), "the Tk thread never got to it"
    return box[0] if box else None


def opened(frame, *args):
    """Open it, or skip the test where there is no display to open it on.

    Waits until the window is really on the screen, not merely built: an
    event generated on a window that is not mapped yet is quietly dropped,
    which looked for a while like a button that did not work.
    """
    frame.open(*args)
    if not frame.wait_until_open(15):
        pytest.skip("no display to put a window on")
    wait(lambda: on_tk(lambda: bool(frame._canvas is not None
                                    and frame._canvas.winfo_ismapped())),
         "the frame to appear on the screen")


def test_the_frame_opens_at_the_size_it_was_told(frame):
    opened(frame, "Notepad", 320, 200)
    assert frame._size == (320, 200)
    assert frame._top is not None


@pytest.mark.skipif(sys.platform != "win32", reason="the mark is a Windows property")
def test_the_frame_is_marked_so_it_is_never_moved_from_here(frame):
    """Taken for a window of this computer's and moved, another computer's
    window would be shown back to it, or on, in a frame of a frame."""
    from link import window_win
    opened(frame, "Notepad", 200, 120)
    hwnd = on_tk(lambda: int(frame._top.wm_frame(), 16))
    assert window_win.is_frame(hwnd)
    assert "from another computer" in window_win.why_not(hwnd)


def test_the_title_always_says_where_the_window_really_lives(frame):
    """N5: the person must never have to wonder which computer they are
    typing into."""
    opened(frame, "Notepad", 200, 120)
    wait(lambda: "Notepad" in frame._top.title(), "the title")
    assert frame._top.title() == "Notepad - from laptop"
    frame.retitle("Untitled - Notepad")
    wait(lambda: "Untitled" in frame._top.title(), "the new title")
    assert frame._top.title() == "Untitled - Notepad - from laptop"


# ------------------------------------------------------------------ drawing
def test_a_picture_is_drawn_where_it_belongs(frame):
    opened(frame, "x", 64, 64)
    frame.draw([region(0, 0, 64, 64, b=20, g=20, r=20)])
    frame.draw([region(16, 8, 16, 16, b=255)])
    wait(lambda: frame._picture is not None
         and frame._picture.get(20, 12) == (0, 0, 255),
         "the blue patch to land")
    assert frame._picture.get(4, 4) == (20, 20, 20), "and nowhere else"


def test_several_pieces_in_one_picture_all_land(frame):
    opened(frame, "x", 96, 96)
    frame.draw([region(0, 0, 96, 96, b=10, g=10, r=10),
                region(0, 0, 16, 16, r=255),
                region(80, 80, 16, 16, g=255)])
    wait(lambda: frame._picture.get(4, 4) == (255, 0, 0), "the first piece")
    wait(lambda: frame._picture.get(88, 88) == (0, 255, 0), "the last piece")


def test_a_piece_whose_bytes_are_not_the_size_claimed_is_not_drawn(frame):
    """Rather than draw something misplaced. The far end is proved, but it
    could still be a newer or buggier version."""
    opened(frame, "x", 64, 64)
    frame.draw([region(0, 0, 64, 64, g=90)])
    wait(lambda: frame._picture.get(1, 1) == (0, 90, 0), "the background")
    said = []
    frame.log = said.append
    codec, data = picture.encode(bgra(8, 8, b=255), 8, 8)
    frame.draw([(0, 0, 32, 32, codec, data)])          # header says 32x32, bytes 8x8
    wait(lambda: said, "a word about it")
    assert "arrived as" in said[0]
    assert frame._picture.get(1, 1) == (0, 90, 0), "nothing was drawn"


def test_a_codec_the_frame_cannot_show_is_refused_not_guessed_at(frame):
    opened(frame, "x", 32, 32)
    said = []
    frame.log = said.append
    frame.draw([(0, 0, 32, 32, "jpeg", b"not really a jpeg")])
    wait(lambda: said, "a word about it")
    assert "cannot show" in said[0]


# ------------------------------------------------------------- what to send back
def test_the_pointer_the_buttons_and_the_wheel_are_sent_on(frame):
    opened(frame, "x", 200, 150)
    c = frame._canvas
    on_tk(lambda: c.event_generate("<Motion>", x=40, y=30, when="now"))
    on_tk(lambda: c.event_generate("<Button-1>", x=40, y=30, when="now"))
    on_tk(lambda: c.event_generate("<ButtonRelease-1>", x=40, y=30, when="now"))
    on_tk(lambda: c.event_generate("<MouseWheel>", delta=-120, x=40, y=30,
                                   when="now"))
    got = drained(frame.events,
                  until=lambda seen: any(e.get("how") == "wheel" for e in seen))
    kinds = [(e.get("how"), e.get("name") or e.get("dy")) for e in got
             if e.get("t") == "in"]
    assert ("move", None) in kinds
    assert ("btn", "left") in kinds
    assert ("wheel", -1) in kinds


def test_a_button_carries_the_place_it_was_pressed(frame):
    """Home plays a button wherever the pointer last was, and a click can
    arrive with no move before it."""
    opened(frame, "x", 200, 150)
    on_tk(lambda: frame._canvas.event_generate("<Button-3>", x=77, y=55,
                                               when="now"))
    got = [e for e in drained(frame.events,
                              until=lambda seen: any(e.get("how") == "btn"
                                                     for e in seen))
           if e.get("t") == "in"]
    assert got[0] == {"t": "in", "how": "move", "x": 77, "y": 55}
    assert got[1]["how"] == "btn" and got[1]["name"] == "right"


def test_keys_are_sent_as_the_codes_nishro_link_speaks(frame):
    opened(frame, "x", 120, 80)
    from link import keymap
    on_tk(lambda: frame._top.event_generate("<KeyPress-a>", when="now"))
    on_tk(lambda: frame._top.event_generate("<KeyRelease-a>", when="now"))
    got = [e for e in drained(frame.events,
                              until=lambda seen: sum(e.get("how") == "key"
                                                     for e in seen) >= 2)
           if e.get("how") == "key"]
    assert got, "no key was sent on"
    assert got[0]["code"] == keymap.E["A"]
    assert got[0]["down"] is True
    assert any(e["down"] is False for e in got), "the key was never let go"


def test_keys_still_held_when_the_frame_loses_focus_are_let_go(frame):
    """Clicked away with Ctrl down, the release goes to some other window:
    home would otherwise hold Ctrl in the moved window for good."""
    opened(frame, "x", 120, 80)
    from link import keymap
    on_tk(lambda: frame._top.event_generate("<KeyPress-Control_L>", when="now"))
    on_tk(lambda: frame._focus_lost())
    got = [e for e in drained(frame.events,
                              until=lambda seen: sum(e.get("how") == "key"
                                                     for e in seen) >= 2)
           if e.get("how") == "key"]
    ctrl = keymap.E["LEFTCTRL"]
    assert {"t": "in", "how": "key", "code": ctrl, "down": True} in got
    assert {"t": "in", "how": "key", "code": ctrl, "down": False} in got, got


def test_a_key_nishro_link_does_not_carry_is_said_once_and_dropped(frame):
    opened(frame, "x", 120, 80)
    said = []
    frame.log = said.append
    for _ in range(3):
        on_tk(lambda: frame._top.event_generate("<KeyPress-Help>", when="now"))
    time.sleep(0.4)
    assert [e for e in drained(frame.events, 0.3) if e.get("how") == "key"] == []
    assert len(said) <= 1, f"said it more than once: {said}"


def test_the_close_button_asks_for_the_programs_window_to_close(frame):
    """The maintainer's choice: the frame's X does what the same button at
    home would."""
    opened(frame, "x", 120, 80)
    frame._asked_to_close()
    assert {"t": "close"} in drained(
        frame.events, until=lambda seen: {"t": "close"} in seen)


def test_resizing_is_sent_once_it_has_settled(frame):
    """A corner being dragged makes many events; the far window must be
    resized once, not forty times."""
    opened(frame, "x", 200, 150)
    drained(frame.events, 0.3)
    for w in range(240, 300, 10):
        on_tk(lambda w=w: frame._top.geometry(f"{w}x170"))
        time.sleep(0.05)
    time.sleep(window_frame.RESIZE_SETTLE_MS / 1000 + 0.6)
    sizes = [e for e in drained(frame.events, 1.0) if e.get("t") == "size"]
    assert len(sizes) == 1, f"sent {len(sizes)} sizes: {sizes}"
    assert sizes[0]["w"] >= 240


# ------------------------------------------------------------------- closing
def test_closing_the_frame_takes_it_off_the_screen_and_says_why(frame):
    gone = []
    frame.on_gone = gone.append
    opened(frame, "x", 120, 80)
    frame.close("gone")
    wait(lambda: gone, "the frame to report it has gone")
    assert gone == ["gone"]
    assert frame._top is None


def test_a_frame_that_cannot_open_asks_for_the_window_to_come_home(monkeypatch):
    """No display, or Tk refuses: the window must not be left hidden on the
    other computer waiting for a frame that will never appear."""
    f = window_frame.Frame(where="laptop")
    monkeypatch.setattr(window_frame._HOST, "start", lambda log: False)
    f.open("x", 100, 100)
    assert f.wait_until_open(5) is False, "no frame should have opened"
    assert {"t": "home"} in drained(
        f.events, until=lambda seen: {"t": "home"} in seen)
