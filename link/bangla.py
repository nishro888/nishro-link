"""Typing Bangla from another computer's keyboard, the way Avro does it.

The maintainer types Bangla with Avro Keyboard, and F12 switches it between
Bangla and English. From the other computer's keyboard neither worked: F12
did nothing and every letter came out English. That is not a bug that can be
fixed by sending keys more carefully. Avro ignores every key that is
injected - it injects its own Bangla characters, so it has to, or it would
read its own output back - and Windows offers no way to inject a key that
is not marked as injected. Measured, devnotes/TESTLOG.md 2026-10-14.

So Nishro Link does what Avro would have done, for keys from another
computer only:

* F12 from the other keyboard switches Bangla on and off here, and never
  reaches the program (Avro eats it too; in a browser it would otherwise
  open the developer tools).
* With Bangla on, the letters of a word are not played as keys. The word so
  far, in Roman letters, is turned into Bangla by Avro's own rules
  (avro_phonetic.py), and the CHARACTERS are typed - which nothing can
  ignore, because nothing is being asked to translate them. Each keystroke
  re-renders the word, as Avro does: k is ka, then kh rubs the ka out and
  types kha.
* Everything else is played exactly as it arrived: shortcuts, arrows,
  Enter, modifiers, and any key Avro's rules do not use.

It only ever does this where Avro Keyboard is running and the setting is on
(`active`), so nobody who does not use Avro loses F12 or meets Bangla.

The local keyboard is never touched: Avro still sees its own computer's
keys, and its own F12 switches its own language. This is a second switch,
for the other keyboard - which is what a person at the other keyboard
reaches for.

Nothing here touches the system. `press(code, value)` plays a key as it is,
`type_text(s)` types characters, and `active()` says whether to do anything
at all - so the whole thing is tested with no screen.
"""
from __future__ import annotations

import string
import threading

from . import avro_phonetic, keymap

E = keymap.E

TOGGLE = E["F12"]                    # Avro's own key for it
BACKSPACE = E["BACKSPACE"]
SHIFTS = frozenset((E["LEFTSHIFT"], E["RIGHTSHIFT"]))
HELD = frozenset((E["LEFTCTRL"], E["RIGHTCTRL"], E["LEFTALT"], E["RIGHTALT"],
                  E["LEFTMETA"], E["RIGHTMETA"]))
CAPSLOCK = E["CAPSLOCK"]
MODIFIERS = SHIFTS | HELD | frozenset((CAPSLOCK,))

# What each key types on the US layout, unshifted and shifted. Avro Phonetic
# is defined on the US layout, so that is what a key means here, whatever
# layout either computer happens to have loaded.
_US = {E[c]: (c.lower(), c) for c in string.ascii_uppercase}
_US.update({E[d]: (d, s) for d, s in zip("1234567890", "!@#$%^&*()")})
_US.update({
    E["MINUS"]: ("-", "_"), E["EQUAL"]: ("=", "+"),
    E["LEFTBRACE"]: ("[", "{"), E["RIGHTBRACE"]: ("]", "}"),
    E["SEMICOLON"]: (";", ":"), E["APOSTROPHE"]: ("'", '"'),
    E["GRAVE"]: ("`", "~"), E["BACKSLASH"]: ("\\", "|"),
    E["COMMA"]: (",", "<"), E["DOT"]: (".", ">"), E["SLASH"]: ("/", "?"),
})

# Which characters belong inside a word: everything Avro's rules ever look
# at. Anything else ends the word and is played as the key it was.
IN_A_WORD = frozenset(string.ascii_letters + string.digits
                      + "".join(sorted({c for p in avro_phonetic.PATTERNS
                                        for c in p["find"]
                                        if not c.isalnum()})))

# For tests and for anyone reading: which key, and whether shifted, gives a
# punctuation character.
PUNCTUATION_KEY = {}
for _code, (_plain, _shifted) in _US.items():
    _name = next(n for n, c in E.items() if c == _code)
    if not _plain.isalnum():
        PUNCTUATION_KEY[_plain] = (_name, False)
    if not _shifted.isalnum():
        PUNCTUATION_KEY[_shifted] = (_name, True)


def char_of(code: int, shifted: bool):
    """The character a key gives on the US layout, or None for a key that
    gives none (an arrow, F5, Enter...)."""
    pair = _US.get(code)
    if pair is None:
        return None
    return pair[1] if shifted else pair[0]


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


class Composer:
    """Turns keys from another computer into Bangla, a word at a time.

    Fed every key exactly as it arrived: an evdev code and 1 (press),
    2 (repeat) or 0 (release). Thread-safe: keys come from the injection
    thread, and `interrupted` from wherever a click or this computer's own
    input is noticed.
    """

    def __init__(self, press, type_text, active, on_mode=None, log=None):
        self.press = press
        self.type_text = type_text
        self.active = active
        self.on_mode = on_mode or (lambda _on: None)
        self.log = log or (lambda *_: None)
        self.bangla = False             # Avro starts in English, and so do we
        self.word = ""                  # the Roman letters of the word so far
        self.shown = ""                 # the Bangla on screen for them
        self._shift = set()
        self._held = set()
        self._caps = False
        # Keys whose PRESS was kept back, so that their release is kept back
        # too. A release passed on for a press that never was - or a press
        # kept with its release passed on - leaves a program thinking a key
        # is down, or up, that is not.
        self._kept = set()
        self._lock = threading.Lock()
        # Set by interrupted(), read by the next key. A flag and not the
        # lock, because interrupted() is called from the capture's hook
        # thread, which must never wait for anything (P1) - and the lock is
        # held while characters are being sent.
        self._stale = False

    # ----------------------------------------------------------- the keys
    def key(self, code: int, value: int) -> None:
        with self._lock:
            self._key(int(code), int(value))

    def _key(self, code: int, value: int) -> None:
        if self._stale:
            self._stale = False
            self._finish_word()
        down = value != 0
        if not down and code in self._kept:
            self._kept.discard(code)    # its press never reached the program
            return

        if not self._wanted():
            self._play(code, value)
            return

        if code in SHIFTS:
            (self._shift.add if down else self._shift.discard)(code)
            self._play(code, value)
            return
        if code in HELD:
            (self._held.add if down else self._held.discard)(code)
            self._finish_word()
            self._play(code, value)
            return
        if code == CAPSLOCK:
            if value == 1:
                self._caps = not self._caps
            self._play(code, value)
            return

        if code == TOGGLE:
            if value == 1:              # a press, not the repeats of one held
                self.bangla = not self.bangla
                self._finish_word()
                try:
                    self.on_mode(self.bangla)
                except Exception as e:
                    self.log(f"could not say the language changed: {e!r}")
            self._kept.add(code)        # and its release, when it comes
            return

        if not self.bangla:
            self._play(code, value)
            return

        if self._held:                  # a shortcut: Ctrl+C must copy
            self._finish_word()
            self._play(code, value)
            return

        # A release that gets this far is one whose press was NOT kept back
        # (those were caught at the top), so it is played whatever it is.
        # Keeping it here as well left a key down for ever whenever its press
        # had fallen back to being played - found by the test that makes
        # typing fail.
        if not down:
            self._play(code, value)
            return

        if code == BACKSPACE and self.word:
            self.word = self.word[:-1]
            if self._render():
                self._kept.add(code)
            else:
                self._play(code, value)
            return

        ch = self._char(code)
        if ch is not None and ch in IN_A_WORD:
            self.word += ch
            if self._render():
                self._kept.add(code)
            else:
                self._play(code, value)
            return

        # Anything else ends the word, and is the key it always was.
        self._finish_word()
        self._play(code, value)

    def interrupted(self) -> None:
        """The caret may have moved: a click, or this computer's own keyboard
        or mouse. The word on screen is left as it is and simply forgotten -
        re-rendering it now would rub out text at the NEW place, characters
        that have nothing to do with it.

        Safe from ANY thread, including the capture's hook thread: it only
        sets a flag, which the next key reads. It never waits (P1)."""
        self._stale = True

    # ---------------------------------------------------------- the inside
    def _wanted(self) -> bool:
        try:
            if self.active():
                return True
        except Exception as e:
            self.log(f"could not tell whether to type Bangla: {e!r}")
        if self.bangla or self.word:
            self.bangla = False         # Avro went away: so does its mode
            self._finish_word()
        return False

    def _char(self, code: int):
        if code in _US and _US[code][0].isalpha():
            return char_of(code, bool(self._shift) != self._caps)
        return char_of(code, bool(self._shift))

    def _render(self) -> bool:
        """Show the word as it is now, changing only what changed. False if
        the characters could not be typed - the caller then plays the key."""
        new = avro_phonetic.parse(self.word)
        keep = _common_prefix(self.shown, new)
        try:
            for _ in range(len(self.shown) - keep):
                self.press(BACKSPACE, 1)
                self.press(BACKSPACE, 0)
            if new[keep:]:
                self.type_text(new[keep:])
        except Exception as e:
            self.log(f"Bangla could not be typed, so the key is sent as it is: {e!r}")
            self._finish_word()
            return False
        self.shown = new
        return True

    def _finish_word(self) -> None:
        self.word = ""
        self.shown = ""

    def _play(self, code: int, value: int) -> None:
        self.press(code, value)
