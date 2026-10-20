"""Typing Bangla from another computer's keyboard (bangla.py).

The composer is fed keys exactly as they arrive from the other computer -
evdev codes, 1 for a press, 2 for a repeat, 0 for a release - and what it
does to the screen is written down: which keys it played as they were,
which characters it typed, and how many it rubbed out. So the screen is
simulated faithfully enough to read the result, with no real window.
"""
import pytest

from link import bangla as tp, keymap

E = keymap.E
BS = E["BACKSPACE"]


class Screen:
    """What a text box would show, given what the composer did."""

    def __init__(self, avro_running=True, wanted=True):
        self.text = ""
        self.played = []          # (code, value) passed through as keys
        self.typed = []           # strings typed as characters
        self.modes = []
        self.avro = avro_running
        self.wanted = wanted
        self.c = tp.Composer(press=self._press, type_text=self._type,
                             active=lambda: self.wanted and self.avro,
                             on_mode=self.modes.append)

    def _press(self, code, value):
        self.played.append((code, value))
        if value and code == BS:
            self.text = self.text[:-1]
        elif value:
            ch = {E["SPACE"]: " ", E["ENTER"]: chr(10), E["TAB"]: chr(9)}.get(
                code, tp.char_of(code, shifted=False))
            if ch is not None and code not in tp.MODIFIERS:
                self.text += ch

    def _type(self, s):
        self.typed.append(s)
        self.text += s

    # ---- the person, at the other computer's keyboard
    def tap(self, name, value_down=1):
        code = E[name]
        self.c.key(code, value_down)
        self.c.key(code, 0)

    def word(self, letters):
        for ch in letters:
            if ch == " ":
                self.tap("SPACE")
            elif ch.isupper():
                self.c.key(E["LEFTSHIFT"], 1)
                self.tap(ch)
                self.c.key(E["LEFTSHIFT"], 0)
            elif ch.isdigit():
                self.tap(ch)
            elif ch in tp.PUNCTUATION_KEY:
                name, shifted = tp.PUNCTUATION_KEY[ch]
                if shifted:
                    self.c.key(E["LEFTSHIFT"], 1)
                self.tap(name)
                if shifted:
                    self.c.key(E["LEFTSHIFT"], 0)
            else:
                self.tap(ch.upper())

    def bangla_on(self):
        self.tap("F12")
        assert self.c.bangla


def bangla(s):
    from link import avro_phonetic
    return avro_phonetic.parse(s)


# ------------------------------------------------------------- the toggle
def test_f12_switches_bangla_on_and_off():
    s = Screen()
    assert not s.c.bangla
    s.tap("F12")
    assert s.c.bangla and s.modes == [True]
    s.tap("F12")
    assert not s.c.bangla and s.modes == [True, False]


def test_f12_itself_never_reaches_the_program():
    """Avro eats it too: in a browser it would otherwise open the developer
    tools every time the language was switched."""
    s = Screen()
    s.tap("F12")
    s.tap("F12")
    assert (E["F12"], 1) not in s.played and (E["F12"], 0) not in s.played


def test_holding_f12_does_not_flicker_the_language():
    """A held key repeats; only the first press switches."""
    s = Screen()
    s.c.key(E["F12"], 1)
    for _ in range(5):
        s.c.key(E["F12"], 2)
    s.c.key(E["F12"], 0)
    assert s.c.bangla and s.modes == [True]


# ----------------------------------------------------------- the typing
def test_english_mode_passes_every_key_through_untouched():
    s = Screen()
    s.word("amar")
    assert s.typed == []
    assert s.text == "amar"


def test_a_word_comes_out_as_avro_would_type_it():
    s = Screen()
    s.bangla_on()
    s.word("amar")
    assert s.text == bangla("amar")


def test_the_letters_themselves_never_reach_the_program_in_bangla_mode():
    """Or the English letter would appear next to the Bangla one."""
    s = Screen()
    s.bangla_on()
    s.word("amar")
    letters = {E[c.upper()] for c in "amar"}
    assert not [p for p in s.played if p[0] in letters], s.played


def test_each_keystroke_re_renders_the_word_as_avro_does():
    """k is ka, then kh is kha - the ka is replaced, not kept."""
    s = Screen()
    s.bangla_on()
    s.word("k")
    assert s.text == bangla("k")
    s.word("h")
    assert s.text == bangla("kh")
    s.word("a")
    assert s.text == bangla("kha")


def test_a_sentence_comes_out_word_for_word():
    s = Screen()
    s.bangla_on()
    s.word("ami banglay gan gai")
    assert s.text == bangla("ami banglay gan gai")


def test_case_matters_where_avro_says_it_does():
    """t is ta, T is Ta - the Shift held on the other keyboard decides."""
    s = Screen()
    s.bangla_on()
    s.word("Tk")
    assert s.text == bangla("Tk")
    assert s.text != bangla("tk")


def test_digits_and_the_full_stop_are_bangla_too():
    s = Screen()
    s.bangla_on()
    s.word("2026.")
    assert s.text == bangla("2026.")


def test_only_what_changed_is_rubbed_out():
    """Typing a long word must not delete and retype the whole of it at
    every letter - that is slow, and it flickers."""
    s = Screen()
    s.bangla_on()
    s.word("bangla")
    rubbed = sum(1 for p in s.played if p == (BS, 1))
    assert rubbed <= len("bangla"), f"{rubbed} backspaces for six letters"


# ------------------------------------------------------- correcting
def test_backspace_corrects_the_word_being_typed():
    """The Roman letter goes, and the word is shown again without it."""
    s = Screen()
    s.bangla_on()
    s.word("kh")
    s.tap("BACKSPACE")
    assert s.text == bangla("k")


def test_backspace_past_the_start_of_the_word_deletes_real_text():
    s = Screen()
    s.bangla_on()
    s.word("ami ")
    before = s.text
    s.tap("BACKSPACE")
    assert s.text == before[:-1], "a real backspace was not passed on"


def test_a_held_backspace_keeps_deleting():
    s = Screen()
    s.bangla_on()
    s.word("amar")
    s.c.key(BS, 1)
    s.c.key(BS, 2)
    s.c.key(BS, 0)
    assert s.text == bangla("am")


# ------------------------------------------------------- what ends a word
def test_space_and_enter_end_the_word_and_reach_the_program():
    s = Screen()
    s.bangla_on()
    s.word("ami")
    s.tap("SPACE")
    assert (E["SPACE"], 1) in s.played
    assert s.c.word == ""


def test_a_shortcut_passes_through_and_ends_the_word():
    """Ctrl+C must copy, not type ch."""
    s = Screen()
    s.bangla_on()
    s.word("am")
    s.c.key(E["LEFTCTRL"], 1)
    s.tap("C")
    s.c.key(E["LEFTCTRL"], 0)
    assert (E["C"], 1) in s.played and (E["C"], 0) in s.played
    assert s.c.word == ""


def test_a_click_ends_the_word_so_the_next_letter_cannot_edit_old_text():
    """The caret moved. Re-rendering the old word now would rub out text at
    the NEW place - characters that have nothing to do with it."""
    s = Screen()
    s.bangla_on()
    s.word("kh")
    s.c.interrupted()
    s.text += "|"                      # the caret is somewhere else now
    s.word("a")
    assert s.text.endswith("|" + bangla("a")), s.text


def test_arrows_end_the_word():
    s = Screen()
    s.bangla_on()
    s.word("am")
    s.tap("LEFT")
    assert s.c.word == ""
    assert (E["LEFT"], 1) in s.played


def test_punctuation_avro_does_not_use_passes_through():
    s = Screen()
    s.bangla_on()
    s.word("ami")
    s.word("-")
    assert s.text == bangla("ami") + "-"


# ---------------------------------------------- no key is ever left down
def test_every_key_kept_back_has_its_release_kept_back_too():
    """A press that was not passed on, followed by a release that was, would
    leave the program seeing a key come up that never went down - and the
    other way round, a key held for ever."""
    s = Screen()
    s.bangla_on()
    s.word("amar bhalo")
    downs = {c for c, v in s.played if v}
    ups = {c for c, v in s.played if not v}
    assert downs == ups, f"downs {downs - ups} / ups {ups - downs}"


def test_switching_the_language_mid_keystroke_leaves_nothing_stuck():
    """A letter held while F12 goes off: its release must still be kept,
    because its press was."""
    s = Screen()
    s.bangla_on()
    s.c.key(E["K"], 1)              # kept, as a Bangla letter
    s.tap("F12")                     # English again
    s.c.key(E["K"], 0)              # the release of the kept press
    assert (E["K"], 0) not in s.played


def test_modifiers_always_reach_the_program():
    s = Screen()
    s.bangla_on()
    for name in ("LEFTSHIFT", "RIGHTSHIFT", "LEFTCTRL", "LEFTALT", "LEFTMETA"):
        s.c.key(E[name], 1)
        s.c.key(E[name], 0)
        assert (E[name], 1) in s.played and (E[name], 0) in s.played, name


# ---------------------------------------------------------- when it is off
def test_without_avro_running_nothing_changes_at_all():
    """Nobody who does not use Avro should lose F12 or get Bangla."""
    s = Screen(avro_running=False)
    s.tap("F12")
    s.word("amar")
    assert not s.c.bangla
    assert (E["F12"], 1) in s.played
    assert s.text.endswith("amar")


def test_switched_off_in_the_settings_nothing_changes_either():
    s = Screen(wanted=False)
    s.tap("F12")
    s.word("amar")
    assert not s.c.bangla
    assert s.text.endswith("amar")


def test_avro_closing_mid_word_drops_the_word_cleanly():
    s = Screen()
    s.bangla_on()
    s.word("am")
    s.avro = False
    s.word("r")
    assert s.c.word == ""
    assert s.text.endswith("r")


def test_a_failure_in_typing_never_eats_a_key():
    """P4, fail open: if the character cannot be typed, the key still goes."""
    s = Screen()
    s.bangla_on()

    def broken(_s):
        raise OSError("SendInput said no")
    s.c.type_text = broken
    s.c.key(E["A"], 1)
    s.c.key(E["A"], 0)
    assert (E["A"], 1) in s.played and (E["A"], 0) in s.played


@pytest.mark.parametrize("name,ch,shifted", [
    ("A", "a", False), ("A", "A", True), ("1", "1", False), ("4", "$", True),
    ("DOT", ".", False), ("SEMICOLON", ":", True), ("6", "^", True),
    ("GRAVE", "`", False), ("COMMA", ",", False),
])
def test_keys_become_the_characters_the_us_layout_gives(name, ch, shifted):
    """Avro Phonetic is defined on the US layout, so that is what a key
    means here, whatever layout either computer has loaded."""
    assert tp.char_of(E[name], shifted) == ch


def test_interrupted_never_waits_even_while_a_word_is_being_typed():
    """P1: interrupted() is called from the capture's hook thread, which must
    never wait for anything. So it must not take the lock that is held while
    characters are being sent - here, sending is made to hang, and
    interrupted() has to return regardless."""
    import threading
    import time as _time
    s = Screen()
    s.bangla_on()
    release = threading.Event()

    def slow(text):
        release.wait(5)              # the agent is slow to take the characters
        s.text += text
    s.c.type_text = slow
    t = threading.Thread(target=lambda: s.c.key(E["A"], 1), daemon=True)
    t.start()
    _time.sleep(0.1)                 # it is now inside the lock, sending
    start = _time.monotonic()
    s.c.interrupted()
    waited = _time.monotonic() - start
    release.set()
    t.join(5)
    assert waited < 0.05, f"interrupted() waited {waited * 1000:.0f} ms"
