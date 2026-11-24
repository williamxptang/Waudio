#!/usr/bin/env python3
"""The harness scores (waudio/harness/score.py): only the characters count (markup, case, punctuation,
spaces and traditional vs simplified Chinese don't); empty output is a full miss; a shuffled page
scores low in order and full with order ignored; English word error.

  python3 backend/tests/test_harness_score.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.harness.score import distance, in_order, normalize, order_ignored, word_error

PAGE = "The mean of X is 2.5, and its variance is 1.25."


def known_distances():
    for a, b, d in (("kitten", "sitting", 3), ("", "abc", 3), ("abc", "", 3), ("flaw", "lawn", 2), ("same", "same", 0)):
        assert distance([ord(c) for c in a], [ord(c) for c in b]) == d, (a, b)


def only_the_characters_count():
    same = ["# The mean of *X* is 2.5,\n\nand its **variance** is 1.25.", "<p>THE MEAN OF X IS 2.5 AND ITS VARIANCE IS 1.25</p>",
            "the mean of x is 2.5 — and its variance is 1.25!"]
    assert same
    for got in same:
        assert in_order(got, PAGE) == 1.0 and order_ignored(got, PAGE) == 1.0, got
    assert in_order("$E[X] = \\frac{a}{b}$", "E[X]=ab") == 1.0  # LaTeX commands and symbols out
    assert in_order("這是一個測試", "这是一个测试") == 1.0         # traditional = simplified


def a_misread_costs_in_proportion():
    one_wrong = "The mean of X is 2.5, and its variance is 1.26."
    assert in_order(one_wrong, PAGE) == 1 - 1 / len(normalize(PAGE))
    assert order_ignored(one_wrong, PAGE) == 1 - 1 / len(normalize(PAGE))
    padded = PAGE + " and some extra words read from a picture" * 3  # too much costs as much as too little
    assert in_order(padded, PAGE) == len(normalize(PAGE)) / len(normalize(padded)) > 0


def empty_output_is_a_full_miss():
    assert in_order("", PAGE) == 0.0 and order_ignored("", PAGE) == 0.0 and word_error("", PAGE) == 1.0
    try:
        in_order("anything", "  ** ")
        raise AssertionError("scored against nothing")
    except ValueError:
        pass


def reading_order_is_told_apart_from_misreading():
    parts = ["First column talks about apples.", "Second column talks about pears.", "A footnote at the end."]
    shuffled = " ".join([parts[2], parts[0], parts[1]])
    want = " ".join(parts)
    assert order_ignored(shuffled, want) == 1.0
    assert in_order(shuffled, want) < 0.6, in_order(shuffled, want)
    misread = want.replace("apples", "apqles").replace("pears", "peers")
    assert order_ignored(misread, want) < 1.0 and in_order(misread, want) > 0.9


def english_word_error():
    assert word_error("The mean of X is 2.5.", "the mean of x is 2.5") == 0.0
    assert word_error("It costs 1,000, said Ann.", "it costs 1,000 said ann") == 0.0
    assert word_error("the mean of y is 2.5", "the mean of x is 2.5") == 1 / 6
    assert word_error("the mean x is", "the mean of x is 2.5") == 2 / 6


known_distances()
only_the_characters_count()
a_misread_costs_in_proportion()
empty_output_is_a_full_miss()
reading_order_is_told_apart_from_misreading()
english_word_error()
print("harness score checks: ok")
