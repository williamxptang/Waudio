#!/usr/bin/env python3
"""The layout step (engine/ocr/blocks.py, layout.py): the layout model's boxes cleaned up — low scores,
duplicates and inline formulas inside text dropped, reading order kept — and text lines put in their
blocks' order. With --real, PP-DocLayoutV3 itself on test pages gives the blocks the OCR research
measured (~/Documents/Speech/mlx-staging/ocr-test/results/layout2/).

  python3 backend/tests/test_ocr_layout.py [--real]
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from waudio.engine.ocr import blocks

LABELS = ["text", "doc_title", "image", "inline_formula", "table"]


def row(label, score, box, order):
    return [LABELS.index(label), score, *box, order]


def boxes_are_cleaned_and_put_in_reading_order():
    rows = np.array([
        row("text", 0.9, [10, 300, 500, 400], 2),
        row("doc_title", 0.95, [10, 10, 500, 60], 0),
        row("text", 0.6, [12, 302, 498, 398], 5),      # the same block again, lower score: duplicate
        row("text", 0.5, [20, 310, 300, 380], 6),      # mostly inside a kept block: duplicate
        row("text", 0.2, [10, 700, 500, 800], 7),      # under SCORE_MIN
        row("inline_formula", 0.8, [100, 320, 160, 340], 3),  # inside a text block: read with it
        row("inline_formula", 0.8, [520, 20, 580, 40], 4),  # on its own: kept, read where the model says
        row("image", 0.9, [10, 100, 500, 280], 1),
        row("table", 0.9, [400, 850, 900, 990], 8),    # runs off the page: clipped
    ], np.float32)
    got = blocks.clean(rows, LABELS, w=600, h=900)
    assert [(b["label"], b["order"]) for b in got] == [("doc_title", 0), ("image", 1), ("text", 2),
                                                       ("inline_formula", 4), ("table", 8)], got
    assert got[-1]["box"] == [400.0, 850.0, 600, 900]
    assert blocks.clean(np.zeros((0, 7), np.float32), LABELS, 600, 900) == []


def a_crop_is_padded_inside_the_page():
    assert blocks.padded([10, 10, 100, 50], 1000, 1000) == (2, 2, 108, 58)
    assert blocks.padded([3, 4, 995, 998.6], 1000, 1000) == (0, 0, 1000, 1000)


def block_kinds_pick_the_prompt():
    assert [blocks.kind(l) for l in ("text", "doc_title", "table", "display_formula", "inline_formula")] == \
        ["text", "text", "table", "formula", "formula"]


def quad(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)


def lines_follow_their_blocks_order():
    page = [{"label": "text", "box": [500, 0, 1000, 1000]},   # right column read first
            {"label": "text", "box": [0, 0, 500, 1000]},
            {"label": "image", "box": [0, 600, 500, 900]},     # inside the left column, smaller: wins
            {"label": "text", "box": [520, 100, 980, 160]}]   # a heading inside the right column
    lines = {"left top": quad(10, 10, 400, 30), "left bottom": quad(10, 500, 400, 520),
             "in the picture": quad(20, 700, 300, 720), "right": quad(510, 400, 990, 420),
             "heading": quad(530, 110, 970, 140), "margin": quad(10, 1100, 300, 1120),
             "right first row, second": quad(800, 300, 990, 320), "right first row, first": quad(510, 303, 700, 323)}
    got = blocks.in_layout_order(list(lines.values()), page)
    name = {id(q): n for n, q in lines.items()}
    assert [name[id(q)] for q in got] == ["right first row, first", "right first row, second", "right",
                                          "left top", "left bottom", "heading", "margin"], [name[id(q)] for q in got]


def the_layout_model_finds_the_measured_blocks():
    import cv2
    from waudio.engine.ocr.layout import Layout
    test = Path("~/Documents/Speech/mlx-staging/ocr-test").expanduser()
    lay = Layout(Path("~/Documents/Speech/OCR/onnx/layout-PP-DocLayoutV3").expanduser())
    assert len(lay.labels) == 25, lay.labels
    names = sorted((test / "omnidocbench" / "original").iterdir())[:6]
    assert names, "no test pages"
    for variant in ("original", "warping"):
        for p in names:
            got = lay.find(cv2.imread(str(test / "omnidocbench" / variant / p.name)))
            want = json.loads((test / "results" / "layout2" / variant / (p.stem + ".json")).read_text())
            assert [b["label"] for b in got] == [b["label"] for b in want], (variant, p.name)
            assert np.allclose([b["box"] for b in got], [b["box"] for b in want], atol=1.0), (variant, p.name)


boxes_are_cleaned_and_put_in_reading_order()
a_crop_is_padded_inside_the_page()
block_kinds_pick_the_prompt()
lines_follow_their_blocks_order()
if "--real" in sys.argv:
    the_layout_model_finds_the_measured_blocks()
print("OCR layout checks: ok" + (" (with the real model)" if "--real" in sys.argv else ""))
