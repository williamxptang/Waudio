"""Scores for every stage of the harness: what was read, cleaned or heard against what it should be.

One normaliser for all of them (the OCR research's and the ASR bench's merged): Unicode NFKC, markup
out (HTML / DocTags tags, LaTeX commands, Markdown symbols), lower case, traditional -> simplified
Chinese, then every punctuation mark, symbol and space out. So only the characters count: a page read
as Markdown and the same words heard back score the same.

    in_order(got, want)       1 - edit distance / longer length: a misread and a reading-order
                              mistake both cost
    order_ignored(got, want)  the characters both share, by count, / longer length: reading order
                              doesn't cost, so in_order much lower than this = an order mistake
    word_error(got, want)     English: word edit distance / words wanted (0 = perfect)

Every score is 0..1 (word_error can pass 1); empty output scores 0 (word_error 1).
"""
import collections, re, unicodedata

import numpy as np

from ..engine import script

TAG = re.compile(r"<[^>]*>")
LATEX = re.compile(r"\\[A-Za-z]+")
WORD = re.compile(r"[^\w\s'.,]|(?<!\d)[.,]|[.,](?!\d)")  # punctuation out; 2.5 and 1,000 stay one word


def normalize(s):
    s = unicodedata.normalize("NFKC", s)
    s = LATEX.sub("", TAG.sub("", s))
    s = script.convert(s.lower(), "t2s")
    return "".join(c for c in s if unicodedata.category(c)[0] not in "PZS" and not c.isspace())


def distance(a, b):
    """Levenshtein distance between two sequences of ints, one numpy row at a time."""
    if not len(a) or not len(b):
        return max(len(a), len(b))
    b = np.asarray(b)
    idx = np.arange(len(b) + 1)
    prev = idx.copy()
    for i, x in enumerate(a, 1):
        t = np.empty(len(b) + 1, dtype=np.int64)
        t[0] = i
        t[1:] = np.minimum(prev[1:] + 1, prev[:-1] + (b != x))
        prev = np.minimum.accumulate(t - idx) + idx  # insertions within the row
    return int(prev[-1])


def _wanted(want):
    if not want:
        raise ValueError("nothing to score against: the wanted text is empty after normalising")
    return want


def in_order(got, want):
    g, w = normalize(got), _wanted(normalize(want))
    return 1 - distance([ord(c) for c in g], [ord(c) for c in w]) / max(len(g), len(w))


def order_ignored(got, want):
    g, w = normalize(got), _wanted(normalize(want))
    shared = sum((collections.Counter(g) & collections.Counter(w)).values())
    return shared / max(len(g), len(w))


def words(s):
    s = script.convert(unicodedata.normalize("NFKC", LATEX.sub("", TAG.sub("", s))).lower(), "t2s")
    return WORD.sub(" ", s).split()


def word_error(got, want):
    g, w = words(got), _wanted(words(want))
    ids = {}
    return distance([ids.setdefault(x, len(ids)) for x in g], [ids.setdefault(x, len(ids)) for x in w]) / len(w)
