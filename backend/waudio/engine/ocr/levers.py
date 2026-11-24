"""OCR levers (from the OCR research, measured on 51 pages): small pure functions, no model code, so
the engine may import them; page.py uses them in the model worker.

    loop_start(text, kind)         where a runaway repeat begins, or None
    token_cap(area, ratio)         max tokens for a block crop of `area` pixels
    accept_ocr(line_scores, tau)   keep the classic-OCR reading of a block instead of the VLM's
    holds_text(block, lines)       a picture block that is really text (handwriting labelled "image")
"""
import math

# A runaway repeat: the same unit back to back at the end of the output, at least MIN_REPEATS
# times and MIN_SPAN characters in all. Tables legitimately repeat rows (empty form rows), so they
# need more repeats before the guard fires.
MIN_REPEATS = {"text": 6, "formula": 6, "table": 16}
MIN_SPAN = 120
MAX_UNIT = 300  # longest repeating unit looked for, in characters

CAP_MIN, CAP_MAX = 64, 1536

HOLDS_TEXT_LINES = 3      # a picture block with at least this many text lines...
HOLDS_TEXT_COVER = 0.25   # ...covering at least this share of its area is read as text


def repeats_at_end(text, unit):
    """How many times the last `unit` characters repeat back to back at the end of `text`."""
    tail = text[-unit:]
    n, end = 1, len(text) - unit
    while end - unit >= 0 and text[end - unit:end] == tail:
        n += 1
        end -= unit
    return n


def loop_start(text, kind="text"):
    """Index where a runaway repeat at the end of `text` begins (keeping one copy), or None."""
    need = MIN_REPEATS.get(kind, MIN_REPEATS["text"])
    for unit in range(1, min(MAX_UNIT, len(text) // need) + 1):
        n = repeats_at_end(text, unit)
        if n >= need and n * unit >= MIN_SPAN:
            return len(text) - (n - 1) * unit
    return None


def token_cap(area, ratio):
    """Tokens allowed for a crop of `area` pixels: `ratio` tokens per pixel, within CAP_MIN..CAP_MAX."""
    return int(min(CAP_MAX, max(CAP_MIN, math.ceil(area * ratio))))


def accept_ocr(line_scores, tau):
    """Keep the classic-OCR reading when it found text and every line is at least `tau` confident."""
    return bool(line_scores) and min(line_scores) >= tau


def holds_text(block, lines):
    """`block` is a layout box [x0, y0, x1, y1]; `lines` are text-line boxes found inside its crop,
    in the same pixels. True when the lines are many and cover enough of the block."""
    area = (block[2] - block[0]) * (block[3] - block[1])
    if len(lines) < HOLDS_TEXT_LINES or area <= 0:
        return False
    covered = sum((b[2] - b[0]) * (b[3] - b[1]) for b in lines)
    return covered / area >= HOLDS_TEXT_COVER
