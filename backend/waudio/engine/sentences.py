"""Text to read aloud -> pieces the voice model speaks one at a time: sentences, with long ones cut
at a comma (or, failing that, at a space) so the first sound comes quickly and a stop is quick.
Pieces with nothing to say (only symbols, a bare list marker) are dropped. split_ends also says how
each piece ends, so the reader can pause after it as a speaker would."""
import re

MAX_CHARS = 220     # longer sentences are cut at a comma, semicolon or colon
FIRST_MAX = 120     # the first piece is kept shorter: it decides how soon you hear anything
LATIN_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s|$)")
CJK_END = re.compile(r"[。！？；]+[”’」』）]*|…+[”’」』）]*(?=[^\s.!?])|[!?]+(?=[\u3400-\u9fff])")
ABBREV = {"mr", "mrs", "ms", "dr", "prof", "st", "vs", "etc", "e.g", "i.e", "a.m", "p.m", "no", "fig",
          "jr", "sr", "inc", "ltd", "co", "approx", "dept", "est", "mt"}
SOFT = re.compile(r"(?<=[,;:])\s+|(?<=[，、；：])")
SAYABLE = re.compile(r"[^\W_]")  # a letter, digit or CJK character


PIECE_END = re.compile(r"[.!?…。！？；]+[\"'”’)\]」』）]*$")  # as LATIN_END / CJK_END: "；" ends a sentence
CLAUSE_END = re.compile(r"[,;:，、：][\"'”’)\]」』）]*$")


def split(text, max_chars=MAX_CHARS, first_max=FIRST_MAX):
    return [piece for piece, _ in split_ends(text, max_chars, first_max)]


def split_ends(text, max_chars=MAX_CHARS, first_max=FIRST_MAX):
    """[(piece, end)]: how each piece ends, for the pause after it — "paragraph" (another paragraph
    or list item follows), "sentence", "clause" (cut at a comma, colon…) or None (cut mid-clause)."""
    pieces = []
    for para in re.split(r"\n\s*\n|\n(?=\s*(?:[-*•·]|\d{1,3}[.)])\s)", text or ""):  # paragraphs, list items
        para = re.sub(r"\s+", " ", para).strip()
        para = re.sub(r"^(?:[-*•·]|\d{1,3}[.)])\s+", "", para)  # "- item" / "1. item": read the item (not "2024. …")
        first = len(pieces)
        for sentence in _sentences(para):
            limit = first_max if not pieces else max_chars
            for piece in _cut(sentence, limit, max_chars):
                if SAYABLE.search(piece):
                    pieces.append([piece, _end(piece)])
        if len(pieces) > first and first:
            pieces[first - 1][1] = "paragraph"  # the one before this paragraph ended one
    return [tuple(p) for p in pieces]


def _end(piece):
    return "sentence" if PIECE_END.search(piece) else "clause" if CLAUSE_END.search(piece) else None


def _sentences(para):
    """Split at sentence ends, keeping closing quotes with their sentence. A Latin full stop does
    not end a sentence after a known abbreviation or a single letter (Mr. / e.g. / J. Smith) or
    when the next word starts in lower case (p.m. on Monday)."""
    ends = []
    for m in CJK_END.finditer(para):
        ends.append(m.end())
    for m in LATIN_END.finditer(para):
        word = re.findall(r"[\w.]+$", para[: m.start()])
        word = word[0].lower().rstrip(".") if word else ""
        nxt = para[m.end():].lstrip()[:1]
        if para[m.start()] == "." and (word in ABBREV or len(word) == 1 or (nxt and nxt.islower())):
            continue
        ends.append(m.end())
    out, start = [], 0
    for e in sorted(set(ends)):
        if para[start:e].strip():
            out.append(para[start:e].strip())
            start = e
    if para[start:].strip():
        out.append(para[start:].strip())
    return out


def _cut(s, first_limit, limit):
    """Cut s into pieces no longer than the limit, at soft breaks where possible."""
    out = []
    while s:
        lim = first_limit if not out else limit
        if len(s) <= lim:
            out.append(s)
            break
        cut = max((m.end() for m in SOFT.finditer(s, 0, lim + 1) if m.end() <= lim), default=0)
        if cut < lim // 3:  # no comma early enough: a space, else a hard cut (CJK without commas)
            space = s.rfind(" ", 0, lim)
            cut = space + 1 if space > lim // 3 else lim
        out.append(s[:cut].strip())
        s = s[cut:].strip()
    return [p for p in out if p]
