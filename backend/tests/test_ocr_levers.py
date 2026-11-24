#!/usr/bin/env python3
"""The OCR levers (engine/ocr/levers.py): the loop guard finds a runaway repeat and keeps one copy,
leaves real repetition (short repeats, blank form rows in a table, ordinary pages) alone; the token cap
grows with the crop within its bounds; PP-OCR's reading is kept only when every line is confident; a
picture block is read as text only when text lines fill enough of it. With --real, the loop guard also
runs over the OCR research's pages and past outputs (~/Documents/Speech/mlx-staging/ocr-test).

  python3 backend/tests/test_ocr_levers.py [--real]
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.engine.ocr.levers import CAP_MAX, CAP_MIN, accept_ocr, holds_text, loop_start, token_cap

# Pages as a reader writes them, with the repetition real pages have (none of it a runaway)
PAGES = [
    "# Chapter 3\n\nThe mean of X is $E[X] = \\sum_i x_i p_i$. Then, then, then the variance follows.\n" * 3,
    "<table><tr><td>Name</td><td>Score</td></tr>" + "<tr><td></td><td></td></tr>" * 12 + "</table>",
    "第一章 概率\n\n" + "例1 求概率。解：设事件为A，则 P(A) = 1/2。\n" * 4,
    "1. 2. 3. 4. 5. 6. 7. 8. 9. 10. 11. 12. 13. 14. 15. 16. 17. 18. 19. 20.",
    "- item\n- item\n- item\n- item\n- item\n",  # a short list repeating: under MIN_SPAN
    "........................................................ 12\n" * 2,  # a contents page's leader dots
]

# How past outputs ran away (the shapes the research saw), as a small built-in set: the guard must
# catch most; the one with a unit longer than levers.MAX_UNIT is what the token cap is there for
RUNAWAYS = [
    "Name\n" + "☐ ☐ ☐ ☐\n" * 80,                                              # checkboxes (PaddleOCR-VL)
    "<table>" + "<tr><td>0</td><td>0</td><td>0</td></tr>" * 60,                 # a numbers table, as text
    "设 f(x) = x^2，则 " + "f(x) = x^2，" * 50,                                  # Chinese maths
    "Results\n" + "18.5\nT\n" * 100,                                           # a column read forever
    "Line one.\n" + ("A long sentence that goes on and on and repeats itself in full, " * 6 + "\n") * 8,  # unit > 300
]
FINISHED = PAGES + ["Abstract\n\nWe study the problem of ... " + "Figure 1 shows the setup. " + "x " * 40]


def most_past_runaway_shapes_are_caught_and_no_finished_page():
    assert RUNAWAYS and FINISHED
    caught = [loop_start(t) is not None for t in RUNAWAYS]
    assert sum(caught) >= 0.8 * len(RUNAWAYS) and caught[:4] == [True] * 4, caught
    assert not caught[-1]  # longer than MAX_UNIT: left to the token cap
    assert [t[:30] for t in FINISHED if loop_start(t) is not None] == []


def a_runaway_repeat_is_found_and_one_copy_kept():
    units = ["18.5\nT\n", "的", "the cat sat on the mat. ", "<td>0</td>", "☐ ", "\\cdot "]
    assert units
    for unit in units:
        text = "Normal opening line of the page.\n" + unit * 200
        start = loop_start(text)
        assert start is not None, unit
        assert text[:start] == "Normal opening line of the page.\n" + unit, unit


def real_repetition_is_not_a_loop():
    assert loop_start("Wait... wait... wait... the end.") is None
    assert loop_start("1 2 3 4 5 6 7 8 9 10 11 12") is None
    assert loop_start("他笑了：哈哈哈哈哈哈哈哈哈哈") is None and loop_start("She said ha ha ha ha ha ha ha ha") is None  # many, but short
    rows = "<tr><td></td><td></td></tr>" * 10  # a blank 10-row form
    assert loop_start(rows, "table") is None
    assert loop_start(rows, "text") is not None  # the same in a text block is a loop
    assert PAGES
    for kind in ("text", "table"):
        assert [p[:30] for p in PAGES if loop_start(p, kind) is not None] == [], kind


def the_token_cap_grows_with_the_crop_within_bounds():
    caps = [token_cap(a, 0.006) for a in (0, 1_000, 50_000, 200_000, 10_000_000)]
    assert caps == sorted(caps) and caps[0] == CAP_MIN == 64 and caps[-1] == CAP_MAX == 1536, caps
    assert token_cap(50_000, 0.006) == 300


def ppocr_reading_is_kept_only_when_every_line_is_confident():
    for scores, tau, keep in (([0.99, 0.97], 0.95, True), ([0.99, 0.94], 0.95, False), ([0.95], 0.95, True),
                              ([], 0.0, False)):  # nothing found: the page reader reads it
        assert accept_ocr(scores, tau) is keep, (scores, tau)


def a_picture_is_read_as_text_only_when_lines_fill_it():
    block = [0, 0, 1000, 600]
    assert holds_text(block, [[20, y, 900, y + 60] for y in range(20, 560, 90)])  # handwriting: 6 lines
    assert not holds_text(block, [[400, 100, 520, 130]])  # one sign in a photo
    assert not holds_text(block, [[10, 10, 60, 25], [10, 40, 60, 55], [10, 70, 60, 85]])  # tiny labels
    assert not holds_text(block, [[50, 50, 950, 170], [50, 200, 950, 320]])  # a poster's two-line title
    assert not holds_text([0, 0, 0, 0], [[0, 0, 1, 1]] * 5)


def the_guard_holds_on_the_research_pages():
    test = Path("~/Documents/Speech/mlx-staging/ocr-test").expanduser()
    truth = [p.read_text() for p in sorted((test / "omnidocbench" / "truth").iterdir())]
    assert truth, "no truth pages"
    for kind in ("text", "table"):
        assert [t[:40] for t in truth if loop_start(t, kind) is not None] == [], kind
    rows = [json.loads(l) for l in (test / "results" / "results.jsonl").read_text().splitlines()]
    latest = {(r["build"], r["variant"], r["page"]): r for r in rows}
    past = {False: [], True: []}
    for (b, v, p), r in latest.items():
        if "+" not in b and b.endswith("int8"):  # whole-page outputs, no levers
            past[r["status"] != "ok"].append((test / "results" / b / v / (p + ".txt")).read_text())
    assert past[False] and past[True], {k: len(v) for k, v in past.items()}
    assert [t[:40] for t in past[False] if loop_start(t) is not None] == []
    caught = sum(loop_start(t) is not None for t in past[True])
    assert caught >= 0.8 * len(past[True]), (caught, len(past[True]))


a_runaway_repeat_is_found_and_one_copy_kept()
most_past_runaway_shapes_are_caught_and_no_finished_page()
real_repetition_is_not_a_loop()
the_token_cap_grows_with_the_crop_within_bounds()
ppocr_reading_is_kept_only_when_every_line_is_confident()
a_picture_is_read_as_text_only_when_lines_fill_it()
if "--real" in sys.argv:
    the_guard_holds_on_the_research_pages()
print("OCR lever checks: ok" + (" (with the research data)" if "--real" in sys.argv else ""))
