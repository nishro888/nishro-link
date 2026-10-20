# Licences of the parts not written for Nishro Link

Nishro Link is MIT (`LICENSE`, at the top of the repository). These files are
not, and keep the licence they came with:

| File | Licence | Where it came from |
|---|---|---|
| `link/avro_phonetic.py` | MPL 1.1 (`MPL-1.1.txt`) | a Python port of [jsAvroPhonetic](https://github.com/torifat/jsAvroPhonetic)'s `avro-lib.js`, Copyright (C) OmicronLab, Initial Developer Rifat Nabi |
| `link/tests/avro_phonetic_cases.py` | MPL 1.1 (`MPL-1.1.txt`) | the test cases from jsAvroPhonetic's `avro-lib.test.js` |

MPL 1.1 is a **file-level** licence: those two files stay under it, with their
notice, and the rest of Nishro Link is unaffected (MPL 1.1 section 3.7, Larger
Works). Their changes from the original are described at the top of each.

**Why not avro.py**, which is MIT and does the same thing: its rules derive
from pyAvroPhonetic, which is GPL-3.0, so they cannot honestly be distributed
as MIT. The original above, by Avro's own developers, can be included as it
is.
