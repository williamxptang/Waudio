"""A page's blocks: the layout model's boxes cleaned up, and text lines put in their blocks' order.
Plain geometry (numpy only), shared by every OCR setup; the models that make the boxes are in
layout.py and ppocr.py (model worker only).

A block: {"label": PP-DocLayoutV3 label, "score": 0..1, "box": [x0, y0, x1, y1] page pixels,
"order": the model's reading-order value}. Boxes come from the layout model as rows
[class, score, x0, y0, x1, y1, order].
"""
import numpy as np

SCORE_MIN = 0.3          # PaddleX's PaddleOCR-VL-1.5 pipeline threshold
CONTAINED = 0.8          # a block mostly inside a kept block is a duplicate
IOU_MAX = 0.5            # so is a block overlapping a kept one this much
FORMULA_IN_TEXT = 0.5    # an inline formula this much inside a text block is read with that block
PAD = 8                  # pixels around each crop

PICTURES = {"image", "chart", "header_image", "footer_image", "seal"}  # not read as text
TEXT_IN_PICTURES = {"image"}  # picture labels that may still be read as text (levers.holds_text)
KIND = {"table": "table", "display_formula": "formula", "inline_formula": "formula"}  # the rest: text


def kind(label):
    """text, table or formula: which prompt a reader gets for a block."""
    return KIND.get(label, "text")


def area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


def overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def clean(rows, labels, w, h):
    """The layout model's rows -> blocks in reading order: low scores, duplicates (mostly inside, or
    overlapping, a block with a higher score) and inline formulas inside a text block dropped."""
    kept = []
    for r in sorted((r for r in rows if r[1] >= SCORE_MIN), key=lambda r: -r[1]):
        box = [max(0.0, float(r[2])), max(0.0, float(r[3])), min(w, float(r[4])), min(h, float(r[5]))]
        a = area(box)
        if a <= 0:
            continue
        if any(overlap(box, k["box"]) / a >= CONTAINED or
               overlap(box, k["box"]) / (a + area(k["box"]) - overlap(box, k["box"])) >= IOU_MAX for k in kept):
            continue
        kept.append({"label": labels[int(r[0])], "score": round(float(r[1]), 3), "box": box, "order": float(r[6])})
    texts = [k["box"] for k in kept if k["label"] not in PICTURES and kind(k["label"]) != "formula"]
    kept = [k for k in kept if k["label"] != "inline_formula"
            or not any(overlap(k["box"], t) / area(k["box"]) >= FORMULA_IN_TEXT for t in texts)]
    return sorted(kept, key=lambda k: k["order"])


def padded(box, w, h, pad=PAD):
    """A block's crop rectangle in whole pixels, `pad` around it, inside the page."""
    return (int(max(0, box[0] - pad)), int(max(0, box[1] - pad)), int(min(w, box[2] + pad)), int(min(h, box[3] + pad)))


def sort_lines(quads):
    """PaddleOCR's sorted_boxes: top to bottom, then left to right within ~10 px rows. A quad is 4
    points (top-left, top-right, bottom-right, bottom-left)."""
    quads = sorted(quads, key=lambda q: (q[0][1], q[0][0]))
    for i in range(len(quads) - 1):
        for j in range(i, -1, -1):
            if abs(quads[j + 1][0][1] - quads[j][0][1]) < 10 and quads[j + 1][0][0] < quads[j][0][0]:
                quads[j], quads[j + 1] = quads[j + 1], quads[j]
            else:
                break
    return quads


def group_lines(quads, blocks):
    """Text lines grouped into the smallest block holding their centre -> [(block, lines)] in reading
    order, lines sorted within each; picture blocks and empty groups left out, lines in no block last
    (block None)."""
    groups = [[] for _ in blocks] + [[]]
    for q in quads:
        cx, cy = float(np.mean(q[:, 0])), float(np.mean(q[:, 1]))
        hit = [i for i, k in enumerate(blocks) if k["box"][0] <= cx <= k["box"][2] and k["box"][1] <= cy <= k["box"][3]]
        groups[min(hit, key=lambda i: area(blocks[i]["box"])) if hit else -1].append(q)
    return [(k, sort_lines(g)) for k, g in zip([*blocks, None], groups)
            if g and (k is None or k["label"] not in PICTURES)]


def in_layout_order(quads, blocks):
    """group_lines, flattened: the lines alone, in reading order."""
    return [q for _, g in group_lines(quads, blocks) for q in g]
