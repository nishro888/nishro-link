"""Generated passwords: words, easy to read and type, proved through a slow key.

Reported: "random password generation should be better". Asked which way, the
answer was words.
"""
import math

from link import pairing
from link.words import EXCLUDED, WORDS


def test_a_new_password_is_four_words_from_the_list():
    pw = pairing.new_password()
    parts = pw.split("-")
    assert len(parts) == 4 and all(w in WORDS for w in parts)


def test_new_passwords_differ():
    assert len({pairing.new_password() for _ in range(50)}) == 50


def test_the_list_is_short_plain_words():
    assert len(WORDS) == len(set(WORDS)) >= 1200
    assert all(w.isalpha() and w.islower() and 3 <= len(w) <= 5 for w in WORDS)
    assert not EXCLUDED & set(WORDS)
    assert "yo-yo" not in WORDS, "its dash is the separator"


def test_the_words_and_the_slow_key_add_up_to_the_old_strength():
    """Twelve random characters from 31 were 59 bits. Four words are fewer, and
    each guess against the key costs 2^19 hashes: more work, not less."""
    words = pairing.WORD_COUNT * math.log2(len(WORDS))
    assert words + math.log2(2 ** 19) >= 12 * math.log2(31)
    assert pairing.ITERATIONS in (2 ** 19, 1024), "production strength, or the tests'"


def test_the_production_strength(monkeypatch):
    import importlib
    fresh = importlib.reload(pairing)
    try:
        assert fresh.ITERATIONS == 2 ** 19
    finally:
        monkeypatch.setattr(fresh, "ITERATIONS", 1024)


def test_dashes_spaces_and_capitals_do_not_count():
    a = pairing.key("tiger-lemon-coral-radio", "hub-1")
    for typed in ("Tiger Lemon Coral Radio", "tigerlemoncoralradio",
                  "TIGER-LEMON-CORAL-RADIO", " tiger lemon-coral radio "):
        assert pairing.key(typed, "hub-1") == a


def test_the_key_depends_on_the_hub():
    """A table built against one group is useless against another."""
    assert pairing.key("tiger-lemon-coral-radio", "hub-1") != \
        pairing.key("tiger-lemon-coral-radio", "hub-2")


def test_no_password_is_no_key():
    assert pairing.key("", "hub-1") == ""


def test_a_password_that_lost_its_dashes_is_shown_as_words_again():
    assert pairing.display("tigerlemoncoralradio") == "tiger-lemon-coral-radio"
    assert pairing.display("Tiger Lemon Coral Radio") == "tiger-lemon-coral-radio"


def test_older_passwords_still_show_in_their_groups():
    assert pairing.display("k7qm2xvp9hdt") == "k7qm-2xvp-9hdt"
    assert pairing.display("my own secret!") == "my own secret!"


def test_short_passwords_are_refused():
    assert pairing.problem("abc") and pairing.problem("abcd-efg")
    assert pairing.problem(pairing.new_password()) is None
