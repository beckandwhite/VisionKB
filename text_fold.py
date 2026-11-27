"""text_fold.py -- the one diacritic-aware text-fold primitive for the pipeline.

The NER pass (``ner``), the n-gram miner (``work6``), the canonical-tag registry
(``work7``) and the human tag review (``tag_review``) all need the same contract
for "what counts as the same entity / tag": a case- and diacritic-insensitive
*fold*, a diacritic detector, a diacritic-preferred *spelling* picker, and a
diacritic-aware *word* regex.

These four used to each carry their own copy -- the copies even advertised the
copy ("copied from ner._NAME_TOKENS", "mirroring ner._NAME_TOKENS", "Folding /
canonical keys (copied from ner.py)"). The stated reason the copies existed --
"ner.py imports spacy at module top, so the stdlib-only works cannot import it"
-- is stale: ``ner`` already guards spacy behind ``try/except ImportError``
(issue #29), so it imports cleanly without spacy, and these helpers are pure
stdlib (``unicodedata`` + ``re``) with no dependency on that guarded import.

The four copies had also *drifted*: the word-regex character classes differed
slightly, and ``ner.norm_key`` filters ``NON_NAME``/digit tokens while
``work7.norm_key`` does not. This module consolidates the *shared* primitive
(``fold``, ``has_diacritic``, ``pick_display``, ``NAME_TOKENS``); the
deliberate caller-specific pieces -- each ``norm_key``'s token filtering -- stay
local to ``ner``/``work7`` and are NOT unified here.

``NAME_TOKENS`` is the one canonical diacritic-aware word shape, taken as the
superset of the four prior variants so no caller loses a token it matched
before: the accented Latin range is ``À``..``ǿ`` (U+00C0-U+00D6,
U+00D8-U+00F6, U+00F8-U+017F -- this covers Hungarian ``ő``/``ű`` etc.) and the
internal joiners are the straight/curly apostrophe plus the hyphen, en-dash and
em-dash, so a compound like ``Wi-Fi`` stays one token.

Stdlib-only and Python 3.11-safe.
"""

import re
import unicodedata
from collections import Counter


def fold(text):
    """NFD-fold case + diacritics so ``macos``/``MacOS`` / ``Tamas``/``Tamás``
    collapse to the same key."""
    decomposed = unicodedata.normalize("NFD", text)
    base = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return base.lower()


def has_diacritic(s):
    """True if ``s`` contains a character in the accented-Latin range
    (U+00C0 .. U+017F)."""
    return any("À" <= ch <= "ſ" for ch in s)


def pick_display(variants):
    """Most-frequent, diacritic-preferring, non-ALLCAPS spelling wins."""
    counts = Counter(variants)

    def score(v):
        return (1 if has_diacritic(v) else 0, 0 if v.isupper() else 1, counts[v])

    return max(variants, key=score)


# Diacritic-aware word shape, shared by the whole pipeline. The accented Latin
# range keeps Hungarian names like "Gyömrő" whole; the joiners keep hyphenated
# compounds like "Wi-Fi" as one token.
NAME_TOKENS = re.compile(
    r"[A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u017F]+"
    r"(?:['\u2019\u002D\u2013\u2014-][A-Za-z\u00C0-\u017F]+)*"
)
