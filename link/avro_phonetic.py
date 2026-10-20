# =============================================================================
# *****************************************************************************
# The contents of this file are subject to the Mozilla Public License
# Version 1.1 (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/  (also in licenses/MPL-1.1.txt in this repository)
#
# Software distributed under the License is distributed on an "AS IS"
# basis, WITHOUT WARRANTY OF ANY KIND, either express or implied. See the
# License for the specific language governing rights and limitations
# under the License.
#
# The Original Code is jsAvroPhonetic
#
# The Initial Developer of the Original Code is
# Rifat Nabi <to.rifat@gmail.com>
#
# Copyright (C) OmicronLab (http://www.omicronlab.com). All Rights Reserved.
#
#
# Contributor(s): Nishro Link (https://github.com/nishro888/nishro-link) -
#   a Python port, 2026-10-18. See the change notes below.
#
# *****************************************************************************
# =============================================================================
"""Avro Phonetic: Roman letters to Bangla, the way Avro Keyboard does it.

A faithful Python port of jsAvroPhonetic's avro-lib.js - OmicronLab's own
JavaScript version of the Avro Phonetic scheme that Avro Keyboard uses - so
that what Nishro Link types matches what Avro would have typed.

WHY A PORT, AND WHY THIS ONE. Nishro Link plays keys from another computer
as injected input, and Avro Keyboard ignores injected input (measured,
devnotes/TESTLOG.md 2026-10-14): it injects its own Bangla characters, so it
has to, or it would read its own output back. So Nishro Link does the
transliteration itself and types the result as characters. The rules had to
be Avro's own, not an approximation, because the person types Bangla with
Avro every day. avro.py (MIT) was considered and not used: its rules derive
from pyAvroPhonetic, which is GPL-3.0, so they cannot honestly be called MIT.
This is the original, by Avro's own developers, under MPL 1.1, kept in this
one file with its notice so the rest of Nishro Link stays MIT (MPL 1.1
section 3.7, Larger Works).

CHANGES FROM THE ORIGINAL, as MPL 1.1 section 3.3 asks:

* Translated from JavaScript to Python. The algorithm in `parse` follows
  avro-lib.js step for step, including two behaviours that look like bugs and
  are kept because they ARE Avro's behaviour: an "exact" match on the right
  needs a character after it (`end < len`, strict), and an empty character
  counts as a vowel and a consonant (JavaScript's indexOf("") is 0). The first
  of those is load-bearing - found by searching rather than assumed: without
  it, "rrr" types "ররর" instead of Avro's "র্র", and a test pins it.
* The grammar is unchanged, but each match's "!" prefix is split into a
  `negative` flag here, where the original strips it the first time the
  match is used (mutating its own data).
* `_char_at` returns "" outside the string, as JavaScript's charAt does,
  where Python's s[-1] would quietly read the LAST character. Every caller
  already keeps the index in range, so this changes no output today
  (searched: no input of up to four letters differs); it is there so that a
  future caller cannot read s[-1] by accident.
* Generated once from avro-lib.js's grammar by a script, so that not one of
  the 289 patterns was typed by hand; this file is the source from now on.

The tests that came with it are in tests/avro_phonetic_cases.py, under this
same licence, and test_avro_phonetic.py checks every one of them.
"""
from __future__ import annotations

VOWEL = "aeiou"
CONSONANT = "bcdfghjklmnpqrstvwxyz"
CASE_SENSITIVE = "oiudgjnrstyz"

# In the original's order, which matters: the first pattern that matches is
# the one used, and they are already longest first.
PATTERNS = [
    {"find": "bhl", "replace": "ভ্ল"},
    {"find": "psh", "replace": "পশ"},
    {"find": "bdh", "replace": "ব্ধ"},
    {"find": "bj", "replace": "ব্জ"},
    {"find": "bd", "replace": "ব্দ"},
    {"find": "bb", "replace": "ব্ব"},
    {"find": "bl", "replace": "ব্ল"},
    {"find": "bh", "replace": "ভ"},
    {"find": "vl", "replace": "ভ্ল"},
    {"find": "b", "replace": "ব"},
    {"find": "v", "replace": "ভ"},
    {"find": "cNG", "replace": "চ্ঞ"},
    {"find": "cch", "replace": "চ্ছ"},
    {"find": "cc", "replace": "চ্চ"},
    {"find": "ch", "replace": "ছ"},
    {"find": "c", "replace": "চ"},
    {"find": "dhn", "replace": "ধ্ন"},
    {"find": "dhm", "replace": "ধ্ম"},
    {"find": "dgh", "replace": "দ্ঘ"},
    {"find": "ddh", "replace": "দ্ধ"},
    {"find": "dbh", "replace": "দ্ভ"},
    {"find": "dv", "replace": "দ্ভ"},
    {"find": "dm", "replace": "দ্ম"},
    {"find": "DD", "replace": "ড্ড"},
    {"find": "Dh", "replace": "ঢ"},
    {"find": "dh", "replace": "ধ"},
    {"find": "dg", "replace": "দ্গ"},
    {"find": "dd", "replace": "দ্দ"},
    {"find": "D", "replace": "ড"},
    {"find": "d", "replace": "দ"},
    {"find": "...", "replace": "..."},
    {"find": ".`", "replace": "."},
    {"find": "..", "replace": "।।"},
    {"find": ".", "replace": "।"},
    {"find": "ghn", "replace": "ঘ্ন"},
    {"find": "Ghn", "replace": "ঘ্ন"},
    {"find": "gdh", "replace": "গ্ধ"},
    {"find": "Gdh", "replace": "গ্ধ"},
    {"find": "gN", "replace": "গ্ণ"},
    {"find": "GN", "replace": "গ্ণ"},
    {"find": "gn", "replace": "গ্ন"},
    {"find": "Gn", "replace": "গ্ন"},
    {"find": "gm", "replace": "গ্ম"},
    {"find": "Gm", "replace": "গ্ম"},
    {"find": "gl", "replace": "গ্ল"},
    {"find": "Gl", "replace": "গ্ল"},
    {"find": "gg", "replace": "জ্ঞ"},
    {"find": "GG", "replace": "জ্ঞ"},
    {"find": "Gg", "replace": "জ্ঞ"},
    {"find": "gG", "replace": "জ্ঞ"},
    {"find": "gh", "replace": "ঘ"},
    {"find": "Gh", "replace": "ঘ"},
    {"find": "g", "replace": "গ"},
    {"find": "G", "replace": "গ"},
    {"find": "hN", "replace": "হ্ণ"},
    {"find": "hn", "replace": "হ্ন"},
    {"find": "hm", "replace": "হ্ম"},
    {"find": "hl", "replace": "হ্ল"},
    {"find": "h", "replace": "হ"},
    {"find": "jjh", "replace": "জ্ঝ"},
    {"find": "jNG", "replace": "জ্ঞ"},
    {"find": "jh", "replace": "ঝ"},
    {"find": "jj", "replace": "জ্জ"},
    {"find": "j", "replace": "জ"},
    {"find": "J", "replace": "জ"},
    {"find": "kkhN", "replace": "ক্ষ্ণ"},
    {"find": "kShN", "replace": "ক্ষ্ণ"},
    {"find": "kkhm", "replace": "ক্ষ্ম"},
    {"find": "kShm", "replace": "ক্ষ্ম"},
    {"find": "kxN", "replace": "ক্ষ্ণ"},
    {"find": "kxm", "replace": "ক্ষ্ম"},
    {"find": "kkh", "replace": "ক্ষ"},
    {"find": "kSh", "replace": "ক্ষ"},
    {"find": "ksh", "replace": "কশ"},
    {"find": "kx", "replace": "ক্ষ"},
    {"find": "kk", "replace": "ক্ক"},
    {"find": "kT", "replace": "ক্ট"},
    {"find": "kt", "replace": "ক্ত"},
    {"find": "kl", "replace": "ক্ল"},
    {"find": "ks", "replace": "ক্স"},
    {"find": "kh", "replace": "খ"},
    {"find": "k", "replace": "ক"},
    {"find": "lbh", "replace": "ল্ভ"},
    {"find": "ldh", "replace": "ল্ধ"},
    {"find": "lkh", "replace": "লখ"},
    {"find": "lgh", "replace": "লঘ"},
    {"find": "lph", "replace": "লফ"},
    {"find": "lk", "replace": "ল্ক"},
    {"find": "lg", "replace": "ল্গ"},
    {"find": "lT", "replace": "ল্ট"},
    {"find": "lD", "replace": "ল্ড"},
    {"find": "lp", "replace": "ল্প"},
    {"find": "lv", "replace": "ল্ভ"},
    {"find": "lm", "replace": "ল্ম"},
    {"find": "ll", "replace": "ল্ল"},
    {"find": "lb", "replace": "ল্ব"},
    {"find": "l", "replace": "ল"},
    {"find": "mth", "replace": "ম্থ"},
    {"find": "mph", "replace": "ম্ফ"},
    {"find": "mbh", "replace": "ম্ভ"},
    {"find": "mpl", "replace": "মপ্ল"},
    {"find": "mn", "replace": "ম্ন"},
    {"find": "mp", "replace": "ম্প"},
    {"find": "mv", "replace": "ম্ভ"},
    {"find": "mm", "replace": "ম্ম"},
    {"find": "ml", "replace": "ম্ল"},
    {"find": "mb", "replace": "ম্ব"},
    {"find": "mf", "replace": "ম্ফ"},
    {"find": "m", "replace": "ম"},
    {"find": "0", "replace": "০"},
    {"find": "1", "replace": "১"},
    {"find": "2", "replace": "২"},
    {"find": "3", "replace": "৩"},
    {"find": "4", "replace": "৪"},
    {"find": "5", "replace": "৫"},
    {"find": "6", "replace": "৬"},
    {"find": "7", "replace": "৭"},
    {"find": "8", "replace": "৮"},
    {"find": "9", "replace": "৯"},
    {"find": "NgkSh", "replace": "ঙ্ক্ষ"},
    {"find": "Ngkkh", "replace": "ঙ্ক্ষ"},
    {"find": "NGch", "replace": "ঞ্ছ"},
    {"find": "Nggh", "replace": "ঙ্ঘ"},
    {"find": "Ngkh", "replace": "ঙ্খ"},
    {"find": "NGjh", "replace": "ঞ্ঝ"},
    {"find": "ngOU", "replace": "ঙ্গৌ"},
    {"find": "ngOI", "replace": "ঙ্গৈ"},
    {"find": "Ngkx", "replace": "ঙ্ক্ষ"},
    {"find": "NGc", "replace": "ঞ্চ"},
    {"find": "nch", "replace": "ঞ্ছ"},
    {"find": "njh", "replace": "ঞ্ঝ"},
    {"find": "ngh", "replace": "ঙ্ঘ"},
    {"find": "Ngk", "replace": "ঙ্ক"},
    {"find": "Ngx", "replace": "ঙ্ষ"},
    {"find": "Ngg", "replace": "ঙ্গ"},
    {"find": "Ngm", "replace": "ঙ্ম"},
    {"find": "NGj", "replace": "ঞ্জ"},
    {"find": "ndh", "replace": "ন্ধ"},
    {"find": "nTh", "replace": "ন্ঠ"},
    {"find": "NTh", "replace": "ণ্ঠ"},
    {"find": "nth", "replace": "ন্থ"},
    {"find": "nkh", "replace": "ঙ্খ"},
    {"find": "ngo", "replace": "ঙ্গ"},
    {"find": "nga", "replace": "ঙ্গা"},
    {"find": "ngi", "replace": "ঙ্গি"},
    {"find": "ngI", "replace": "ঙ্গী"},
    {"find": "ngu", "replace": "ঙ্গু"},
    {"find": "ngU", "replace": "ঙ্গূ"},
    {"find": "nge", "replace": "ঙ্গে"},
    {"find": "ngO", "replace": "ঙ্গো"},
    {"find": "NDh", "replace": "ণ্ঢ"},
    {"find": "nsh", "replace": "নশ"},
    {"find": "Ngr", "replace": "ঙর"},
    {"find": "NGr", "replace": "ঞর"},
    {"find": "ngr", "replace": "ংর"},
    {"find": "nj", "replace": "ঞ্জ"},
    {"find": "Ng", "replace": "ঙ"},
    {"find": "NG", "replace": "ঞ"},
    {"find": "nk", "replace": "ঙ্ক"},
    {"find": "ng", "replace": "ং"},
    {"find": "nn", "replace": "ন্ন"},
    {"find": "NN", "replace": "ণ্ণ"},
    {"find": "Nn", "replace": "ণ্ন"},
    {"find": "nm", "replace": "ন্ম"},
    {"find": "Nm", "replace": "ণ্ম"},
    {"find": "nd", "replace": "ন্দ"},
    {"find": "nT", "replace": "ন্ট"},
    {"find": "NT", "replace": "ণ্ট"},
    {"find": "nD", "replace": "ন্ড"},
    {"find": "ND", "replace": "ণ্ড"},
    {"find": "nt", "replace": "ন্ত"},
    {"find": "ns", "replace": "ন্স"},
    {"find": "nc", "replace": "ঞ্চ"},
    {"find": "n", "replace": "ন"},
    {"find": "N", "replace": "ণ"},
    {"find": "OI`", "replace": "ৈ"},
    {"find": "OU`", "replace": "ৌ"},
    {"find": "O`", "replace": "ো"},
    {"find": "OI", "replace": "ৈ", "rules": [
        {"replace": "ঐ", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}]},
        {"replace": "ঐ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}]},
    ]},
    {"find": "OU", "replace": "ৌ", "rules": [
        {"replace": "ঔ", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}]},
        {"replace": "ঔ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}]},
    ]},
    {"find": "O", "replace": "ো", "rules": [
        {"replace": "ও", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}]},
        {"replace": "ও", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}]},
    ]},
    {"find": "phl", "replace": "ফ্ল"},
    {"find": "pT", "replace": "প্ট"},
    {"find": "pt", "replace": "প্ত"},
    {"find": "pn", "replace": "প্ন"},
    {"find": "pp", "replace": "প্প"},
    {"find": "pl", "replace": "প্ল"},
    {"find": "ps", "replace": "প্স"},
    {"find": "ph", "replace": "ফ"},
    {"find": "fl", "replace": "ফ্ল"},
    {"find": "f", "replace": "ফ"},
    {"find": "p", "replace": "প"},
    {"find": "rri`", "replace": "ৃ"},
    {"find": "rri", "replace": "ৃ", "rules": [
        {"replace": "ঋ", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}]},
        {"replace": "ঋ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}]},
    ]},
    {"find": "rrZ", "replace": "রর‍্য"},
    {"find": "rry", "replace": "রর‍্য"},
    {"find": "rZ", "replace": "র‍্য", "rules": [
        {"replace": "্র্য", "matches": [{"type": "prefix", "scope": "consonant", "negative": False}, {"type": "prefix", "scope": "exact", "negative": True, "value": "r"}, {"type": "prefix", "scope": "exact", "negative": True, "value": "y"}, {"type": "prefix", "scope": "exact", "negative": True, "value": "w"}, {"type": "prefix", "scope": "exact", "negative": True, "value": "x"}]},
    ]},
    {"find": "ry", "replace": "র‍্য", "rules": [
        {"replace": "্র্য", "matches": [{"type": "prefix", "scope": "consonant", "negative": False}, {"type": "prefix", "scope": "exact", "negative": True, "value": "r"}, {"type": "prefix", "scope": "exact", "negative": True, "value": "y"}, {"type": "prefix", "scope": "exact", "negative": True, "value": "w"}, {"type": "prefix", "scope": "exact", "negative": True, "value": "x"}]},
    ]},
    {"find": "rr", "replace": "রর", "rules": [
        {"replace": "র্", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}, {"type": "suffix", "scope": "vowel", "negative": True}, {"type": "suffix", "scope": "exact", "negative": True, "value": "r"}, {"type": "suffix", "scope": "punctuation", "negative": True}]},
        {"replace": "্রর", "matches": [{"type": "prefix", "scope": "consonant", "negative": False}, {"type": "prefix", "scope": "exact", "negative": True, "value": "r"}]},
    ]},
    {"find": "Rg", "replace": "ড়্গ"},
    {"find": "Rh", "replace": "ঢ়"},
    {"find": "R", "replace": "ড়"},
    {"find": "r", "replace": "র", "rules": [
        {"replace": "্র", "matches": [{"type": "prefix", "scope": "consonant", "negative": False}, {"type": "prefix", "scope": "exact", "negative": True, "value": "r"}, {"type": "prefix", "scope": "exact", "negative": True, "value": "y"}, {"type": "prefix", "scope": "exact", "negative": True, "value": "w"}, {"type": "prefix", "scope": "exact", "negative": True, "value": "x"}, {"type": "prefix", "scope": "exact", "negative": True, "value": "Z"}]},
    ]},
    {"find": "shch", "replace": "শ্ছ"},
    {"find": "ShTh", "replace": "ষ্ঠ"},
    {"find": "Shph", "replace": "ষ্ফ"},
    {"find": "Sch", "replace": "শ্ছ"},
    {"find": "skl", "replace": "স্ক্ল"},
    {"find": "skh", "replace": "স্খ"},
    {"find": "sth", "replace": "স্থ"},
    {"find": "sph", "replace": "স্ফ"},
    {"find": "shc", "replace": "শ্চ"},
    {"find": "sht", "replace": "শ্ত"},
    {"find": "shn", "replace": "শ্ন"},
    {"find": "shm", "replace": "শ্ম"},
    {"find": "shl", "replace": "শ্ল"},
    {"find": "Shk", "replace": "ষ্ক"},
    {"find": "ShT", "replace": "ষ্ট"},
    {"find": "ShN", "replace": "ষ্ণ"},
    {"find": "Shp", "replace": "ষ্প"},
    {"find": "Shf", "replace": "ষ্ফ"},
    {"find": "Shm", "replace": "ষ্ম"},
    {"find": "spl", "replace": "স্প্ল"},
    {"find": "sk", "replace": "স্ক"},
    {"find": "Sc", "replace": "শ্চ"},
    {"find": "sT", "replace": "স্ট"},
    {"find": "st", "replace": "স্ত"},
    {"find": "sn", "replace": "স্ন"},
    {"find": "sp", "replace": "স্প"},
    {"find": "sf", "replace": "স্ফ"},
    {"find": "sm", "replace": "স্ম"},
    {"find": "sl", "replace": "স্ল"},
    {"find": "sh", "replace": "শ"},
    {"find": "Sc", "replace": "শ্চ"},
    {"find": "St", "replace": "শ্ত"},
    {"find": "Sn", "replace": "শ্ন"},
    {"find": "Sm", "replace": "শ্ম"},
    {"find": "Sl", "replace": "শ্ল"},
    {"find": "Sh", "replace": "ষ"},
    {"find": "s", "replace": "স"},
    {"find": "S", "replace": "শ"},
    {"find": "oo`", "replace": "ু"},
    {"find": "oo", "replace": "ু", "rules": [
        {"replace": "উ", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
        {"replace": "উ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
    ]},
    {"find": "o`", "replace": ""},
    {"find": "oZ", "replace": "অ্য"},
    {"find": "o", "replace": "", "rules": [
        {"replace": "ও", "matches": [{"type": "prefix", "scope": "vowel", "negative": False}, {"type": "prefix", "scope": "exact", "negative": True, "value": "o"}]},
        {"replace": "অ", "matches": [{"type": "prefix", "scope": "vowel", "negative": False}, {"type": "prefix", "scope": "exact", "negative": False, "value": "o"}]},
        {"replace": "অ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}]},
    ]},
    {"find": "tth", "replace": "ত্থ"},
    {"find": "t``", "replace": "ৎ"},
    {"find": "TT", "replace": "ট্ট"},
    {"find": "Tm", "replace": "ট্ম"},
    {"find": "Th", "replace": "ঠ"},
    {"find": "tn", "replace": "ত্ন"},
    {"find": "tm", "replace": "ত্ম"},
    {"find": "th", "replace": "থ"},
    {"find": "tt", "replace": "ত্ত"},
    {"find": "T", "replace": "ট"},
    {"find": "t", "replace": "ত"},
    {"find": "aZ", "replace": "অ্যা"},
    {"find": "AZ", "replace": "অ্যা"},
    {"find": "a`", "replace": "া"},
    {"find": "A`", "replace": "া"},
    {"find": "a", "replace": "া", "rules": [
        {"replace": "আ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
        {"replace": "য়া", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}, {"type": "prefix", "scope": "exact", "negative": True, "value": "a"}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
        {"replace": "আ", "matches": [{"type": "prefix", "scope": "exact", "negative": False, "value": "a"}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
    ]},
    {"find": "i`", "replace": "ি"},
    {"find": "i", "replace": "ি", "rules": [
        {"replace": "ই", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
        {"replace": "ই", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
    ]},
    {"find": "I`", "replace": "ী"},
    {"find": "I", "replace": "ী", "rules": [
        {"replace": "ঈ", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
        {"replace": "ঈ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
    ]},
    {"find": "u`", "replace": "ু"},
    {"find": "u", "replace": "ু", "rules": [
        {"replace": "উ", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
        {"replace": "উ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
    ]},
    {"find": "U`", "replace": "ূ"},
    {"find": "U", "replace": "ূ", "rules": [
        {"replace": "ঊ", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
        {"replace": "ঊ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
    ]},
    {"find": "ee`", "replace": "ী"},
    {"find": "ee", "replace": "ী", "rules": [
        {"replace": "ঈ", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
        {"replace": "ঈ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
    ]},
    {"find": "e`", "replace": "ে"},
    {"find": "e", "replace": "ে", "rules": [
        {"replace": "এ", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
        {"replace": "এ", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}, {"type": "suffix", "scope": "exact", "negative": True, "value": "`"}]},
    ]},
    {"find": "z", "replace": "য"},
    {"find": "Z", "replace": "্য"},
    {"find": "y", "replace": "্য", "rules": [
        {"replace": "য়", "matches": [{"type": "prefix", "scope": "consonant", "negative": True}, {"type": "prefix", "scope": "punctuation", "negative": True}]},
        {"replace": "ইয়", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}]},
    ]},
    {"find": "Y", "replace": "য়"},
    {"find": "q", "replace": "ক"},
    {"find": "w", "replace": "ও", "rules": [
        {"replace": "ওয়", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}, {"type": "suffix", "scope": "vowel", "negative": False}]},
        {"replace": "্ব", "matches": [{"type": "prefix", "scope": "consonant", "negative": False}]},
    ]},
    {"find": "x", "replace": "ক্স", "rules": [
        {"replace": "এক্স", "matches": [{"type": "prefix", "scope": "punctuation", "negative": False}]},
    ]},
    {"find": ":`", "replace": ":"},
    {"find": ":", "replace": "ঃ"},
    {"find": "^`", "replace": "^"},
    {"find": "^", "replace": "ঁ"},
    {"find": ",,", "replace": "্‌"},
    {"find": ",", "replace": ","},
    {"find": "$", "replace": "৳"},
    {"find": "`", "replace": ""},
]


def _char_at(s: str, i: int) -> str:
    """JavaScript's charAt: "" outside the string, never wrapping round."""
    return s[i] if 0 <= i < len(s) else ""


def _is_vowel(c: str) -> bool:
    return c.lower() in VOWEL          # "" in "aeiou" is True, as in the JS


def _is_consonant(c: str) -> bool:
    return c.lower() in CONSONANT


def _is_punctuation(c: str) -> bool:
    return not (_is_vowel(c) or _is_consonant(c))


def _is_exact(needle: str, hay: str, start: int, end: int,
              negative: bool) -> bool:
    return (start >= 0 and end < len(hay) and hay[start:end] == needle) != negative


def _is_case_sensitive(c: str) -> bool:
    return c.lower() in CASE_SENSITIVE


def fix_string(text: str) -> str:
    """Lower-case everything except the letters whose case means something."""
    return "".join(c if _is_case_sensitive(c) else c.lower() for c in text)


def _rule_holds(rule, fixed: str, start: int, end: int, prev: int) -> bool:
    for m in rule["matches"]:
        kind, scope, negative = m["type"], m["scope"], m["negative"]
        chk = end if kind == "suffix" else prev
        if scope == "punctuation":
            seen = ((chk < 0 and kind == "prefix")
                    or (chk >= len(fixed) and kind == "suffix")
                    or _is_punctuation(_char_at(fixed, chk)))
            if seen == negative:
                return False
        elif scope == "vowel":
            seen = (((chk >= 0 and kind == "prefix")
                     or (chk < len(fixed) and kind == "suffix"))
                    and _is_vowel(_char_at(fixed, chk)))
            if seen == negative:
                return False
        elif scope == "consonant":
            seen = (((chk >= 0 and kind == "prefix")
                     or (chk < len(fixed) and kind == "suffix"))
                    and _is_consonant(_char_at(fixed, chk)))
            if seen == negative:
                return False
        elif scope == "exact":
            value = m.get("value", "")
            if kind == "suffix":
                s, e = end, end + len(value)
            else:
                s, e = start - len(value), start
            if not _is_exact(value, fixed, s, e, negative):
                return False
    return True


def parse(text: str) -> str:
    """Roman letters in, Bangla out - Avro Phonetic, as avro-lib.js does it."""
    fixed = fix_string(text)
    out = []
    cur, n = 0, len(fixed)
    while cur < n:
        start = cur
        matched = False
        for pattern in PATTERNS:
            find = pattern["find"]
            end = cur + len(find)
            if end <= n and fixed[start:end] == find:
                prev = start - 1
                for rule in pattern.get("rules", ()):
                    if _rule_holds(rule, fixed, start, end, prev):
                        out.append(rule["replace"])
                        cur = end - 1
                        matched = True
                        break
                if not matched:
                    out.append(pattern["replace"])
                    cur = end - 1
                    matched = True
                break
        if not matched:
            out.append(fixed[cur])
        cur += 1
    return "".join(out)
