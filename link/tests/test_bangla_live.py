"""Bangla across a REAL link (bangla.py wired into node.py).

Two nodes, a real connection, the pointer driven across: keys pressed on one
computer's keyboard arrive at the other and must come out as Avro would have
typed them there - with F12, pressed on the first computer's keyboard,
switching the language on the second.

In test_node_live's Pair the hub drives the aio, so here the aio plays the
part of the computer with Avro on it. The path is the same one the laptop
uses when the AIO's keyboard drives it.
"""
import pytest

from link import avro_phonetic, keymap
from test_node_live import FakeInjector, Pair

E = keymap.E


class TypingInjector(FakeInjector):
    """A fake injector that can type characters, as WindowsInjector can."""

    def text(self, s):
        self.calls.append(("text", s))


@pytest.fixture
def pair():
    p = Pair()
    p.aio_inj = TypingInjector()
    p.aio.injector = p.aio_inj
    try:
        yield p
    finally:
        p.stop()


def driving(pair):
    pair.start().connected()
    pair.hub_cap.sink.on_pointer(0, 384, -5, 0)
    pair.wait(lambda: pair.hub_core.cursor_is_remote(), what="the crossing")


def tap(pair, name):
    pair.hub_cap.sink.on_key(E[name], True)
    pair.hub_cap.sink.on_key(E[name], False)


def typed_text(pair):
    return "".join(c[1] for c in pair.aio_inj.calls if c[0] == "text")


def test_f12_from_the_other_keyboard_switches_bangla_here(pair):
    pair.aio.bangla_ok = lambda: True
    driving(pair)
    tap(pair, "F12")
    pair.wait(lambda: pair.aio.typing_bangla, what="Bangla to come on")
    tap(pair, "F12")
    pair.wait(lambda: not pair.aio.typing_bangla, what="English again")


def test_a_word_typed_over_there_arrives_as_bangla(pair):
    pair.aio.bangla_ok = lambda: True
    driving(pair)
    tap(pair, "F12")
    pair.wait(lambda: pair.aio.typing_bangla, what="Bangla to come on")
    for ch in "amar":
        tap(pair, ch.upper())
    want = avro_phonetic.parse("amar")
    pair.wait(lambda: want in _screen(pair), what=f"{want!r} on the screen "
              f"(have {_screen(pair)!r})")


def test_the_letters_never_arrive_as_english_keys_too(pair):
    pair.aio.bangla_ok = lambda: True
    driving(pair)
    tap(pair, "F12")
    pair.wait(lambda: pair.aio.typing_bangla, what="Bangla to come on")
    tap(pair, "A")
    pair.wait(lambda: typed_text(pair), what="the Bangla letter")
    assert ("key", E["A"], 1) not in pair.aio_inj.calls
    assert ("key", E["A"], True) not in pair.aio_inj.calls


def test_without_avro_nothing_is_different_across_the_link(pair):
    """The default: keys go across exactly as they always did."""
    driving(pair)
    tap(pair, "F12")
    tap(pair, "A")
    pair.wait(lambda: any(c[:2] == ("key", E["A"]) for c in pair.aio_inj.calls),
              what="the A as a key")
    assert not pair.aio.typing_bangla
    assert typed_text(pair) == ""
    assert any(c[:2] == ("key", E["F12"]) for c in pair.aio_inj.calls), \
        "F12 must reach the program where Avro is not running"


def test_an_injector_that_cannot_type_characters_still_gets_the_keys(pair):
    """uinput on Linux sends keys only. Bangla cannot be typed there, and the
    keys must arrive rather than vanish (P4)."""
    p_inj = FakeInjector()                    # no text()
    pair.aio.injector = p_inj
    pair.aio.bangla_ok = lambda: True
    driving(pair)
    tap(pair, "F12")
    pair.wait(lambda: pair.aio.typing_bangla, what="Bangla to come on")
    tap(pair, "A")
    pair.wait(lambda: any(c[:2] == ("key", E["A"]) for c in p_inj.calls),
              what="the A, as a key, since characters cannot be typed")


def _screen(pair):
    """What a text box would show from the injector's calls."""
    out = ""
    for c in pair.aio_inj.calls:
        if c[0] == "text":
            out += c[1]
        elif c[0] == "key" and c[1] == E["BACKSPACE"] and c[2]:
            out = out[:-1]
    return out
