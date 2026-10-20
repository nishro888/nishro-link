"""The Avro Phonetic port (avro_phonetic.py) against Avro's own tests.

Every case jsAvroPhonetic's developers wrote down, in avro_phonetic_cases.py.
If one of these fails the port has drifted from what Avro types, and the
person typing Bangla through Nishro Link would see it word by word.
"""
import pytest

from link import avro_phonetic

from avro_phonetic_cases import CASES


def test_there_are_cases_to_check():
    """A test file of nothing would pass for ever."""
    assert len(CASES) >= 400


@pytest.mark.parametrize("typed,expected", CASES,
                         ids=[c[0][:30] for c in CASES])
def test_avro_phonetic_types_what_avro_types(typed, expected):
    assert avro_phonetic.parse(typed) == expected


def test_words_people_actually_type():
    """A few everyday words, as a reader would recognise them."""
    for typed, bangla in (("ami", "\u0986\u09ae\u09bf"),
                          ("amar", "\u0986\u09ae\u09be\u09b0"),
                          ("bangla", "\u09ac\u09be\u0982\u09b2\u09be")):
        assert avro_phonetic.parse(typed) == bangla, typed


def test_nothing_to_transliterate_comes_back_as_it_was():
    assert avro_phonetic.parse("") == ""
    assert avro_phonetic.parse("   ") == "   "


def test_the_strict_exact_match_on_the_right_is_kept():
    """avro-lib.js asks for a character AFTER an exact suffix match
    (`end < len`, strict). It looks like an off-by-one, and Avro's own 412
    cases do not exercise it - but searching found that it decides real
    output: "fixed", "rrr" types three separate letters instead of Avro's
    ref-plus-ra. Pinned, so nobody tidies it away."""
    assert avro_phonetic.parse("rrr") == "র্র"
    assert avro_phonetic.parse("arrr") == "আর্র"
