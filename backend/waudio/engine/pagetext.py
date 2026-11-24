"""A read page -> text to speak: the OCR readers' Markdown, HTML tables, LaTeX and DocTags turned into
plain sentences in reading order. Pure text work (no model): the harness uses it, and read aloud can.

    speakable(blocks)   blocks: a page result's [{"label", "kind", "text"}] (engine/ocr/page.py)

How each kind of block is read:
    text      markup out; lines that only wrap join up (a hyphen at a wrapped word's end goes; Chinese
              and Japanese join without a space); a heading or list item stays a sentence of its own
    table     row by row: a row's filled cells joined with commas, a sentence per row; empty rows skipped
    formula   in words: \\frac{a}{b} "a over b", x^2 "x squared", = "equals", Greek letters by name
Headers, footers, page and equation numbers are not read. Every block ends as a sentence, so the voice pauses.
"""
import re

from . import script

FURNITURE = {"header", "footer", "number", "formula_number"}  # page labels (PP-DocLayoutV3) not read aloud

TAG = re.compile(r"<[^>]+>")  # also DocTags' position tokens (<loc_12>)
OTSL_CELL = re.compile(r"<(?:fcel|ecel|lcel|ucel|xcel|ched|rhed|srow)>")  # DocTags' table cells
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
MD_TABLE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*\n?){2,}", re.M)  # a run of | rows inside running text
MD_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
# $…$ only when it hugs its maths ($x$, not "$5 and $6"): Pandoc's rule
INLINE_MATH = re.compile(r"\$\$(.+?)\$\$|\$(?=\S)(.+?)(?<=\S)\$(?!\d)|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)
MARKER = re.compile(r"^\s*(#{1,6}\s+|[-*+•·]\s+|\d{1,3}[.)]\s+|>\s?)")
ENDS = "。！？.!?:：;；…"

WORDS = {  # LaTeX commands read as words; others are dropped, keeping what they hold
    "times": " times ", "cdot": " times ", "div": " divided by ", "pm": " plus or minus ", "mp": " minus or plus ",
    "leq": " less than or equal to ", "le": " less than or equal to ", "geq": " greater than or equal to ",
    "ge": " greater than or equal to ", "neq": " not equal to ", "ne": " not equal to ", "approx": " approximately ",
    "equiv": " is equivalent to ", "infty": " infinity ", "sum": " the sum of ", "prod": " the product of ",
    "int": " the integral of ", "partial": " partial ", "to": " to ", "rightarrow": " to ", "in": " in ",
    "%": " percent ", "ldots": " and so on ", "cdots": " and so on ", "log": " log ", "ln": " ln ", "sin": " sine ",
    "cos": " cosine ", "tan": " tangent ", "lim": " the limit ", "max": " max ", "min": " min ",
    **{g: f" {g} " for g in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa",
                             "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi",
                             "omega", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega")},
}
SYMBOLS = {"=": " equals ", "+": " plus ", "<": " less than ", ">": " greater than ", "×": " times ", "*": " times ",
           "÷": " divided by ", "≤": " less than or equal to ", "≥": " greater than or equal to ", "≠": " not equal to ",
           "±": " plus or minus ", "≈": " approximately ", "%": " percent ", "∞": " infinity "}
POWERS = {"2": " squared", "3": " cubed"}
GROUP = r"\{([^{}]*)\}"  # one brace group with no braces inside: innermost first, so nesting unfolds


def speakable(blocks):
    parts = []
    for b in blocks:
        if b.get("label") in FURNITURE or not b.get("text", "").strip():
            continue
        read = {"table": table, "formula": formula}.get(b.get("kind"), prose)(b["text"])
        if read.strip():
            parts.append(sentence(read.strip()))
    return "\n".join(parts)


def sentence(s):
    """s, ending as a sentence ("。" after Chinese or Japanese, else ".")."""
    s = s.rstrip()
    if not s or s[-1] in ENDS:
        return s
    return s + ("。" if script.has_cjk(s[-1]) else ".")


def table(text):
    if "<nl>" in text or OTSL_CELL.search(text):  # a DocTags (OTSL) table: <fcel>cell … <nl> per row
        rows = [[cell(c) for c in OTSL_CELL.split(r)] for r in text.split("<nl>")]  # cell() drops other tags
    elif "<tr" in text.lower():
        rows = [[cell(c) for c in CELL.findall(r)] for r in ROW.findall(text)]
    elif "|" in text:
        rows = [[cell(c) for c in line.strip().strip("|").split("|")] for line in text.splitlines()
                if line.strip() and not MD_RULE.match(line)]
    else:
        return prose(text)
    return "\n".join(sentence(", ".join(c for c in row if c)) for row in rows if any(row))


def formula(tex):
    s = tex.strip()
    for a, b in (("$$", "$$"), ("$", "$"), ("\\[", "\\]"), ("\\(", "\\)")):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
            s = s[len(a):-len(b)]
            break
    s = re.sub(r"\\(left|right|big|Big|bigg|Bigg)\b", "", s)
    s = re.sub(r"\\(mathrm|mathbf|mathit|mathbb|mathcal|text|textbf|operatorname|boldsymbol)\s*" + GROUP, r" \2 ", s)
    for _ in range(20):  # nested groups unfold from the inside
        before = s
        s = re.sub(r"\\[dt]?frac\s*" + GROUP + r"\s*" + GROUP, r" \1 over \2 ", s)
        s = re.sub(r"\\sqrt\s*" + GROUP, r" the square root of \1 ", s)
        s = re.sub(r"\^\s*" + GROUP, lambda m: POWERS.get(m[1].strip(), f" to the power {m[1]} "), s)
        s = re.sub(r"_\s*" + GROUP, r" sub \1 ", s)
        if s == before:  # nothing above left to unfold: plain groups go, which may free a \frac's
            s = re.sub(GROUP, r" \1 ", s)
            if s == before:
                break
    s = re.sub(r"\^\s*(\w)", lambda m: POWERS.get(m[1], f" to the power {m[1]}"), s)
    s = re.sub(r"_\s*(\w)", r" sub \1", s)
    s = re.sub(r"\\([A-Za-z]+|%)", lambda m: WORDS.get(m[1], " "), s)
    s = "".join(SYMBOLS.get(c, c) for c in s)
    s = re.sub(r"(?<=\d)\s*([.,])\s*(?=\d)", r"\1", s)    # 3 4 . 8 -> 34.8
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    s = re.sub(r"(?<=\w)\s*-\s*(?=[\w(])", " minus ", s)
    s = re.sub(r"(?<![\w)])-\s*(?=\w)", "minus ", s)
    s = re.sub(r"[\\{}&$]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def prose(text, sentences=True):
    """A text block's markup out, its wrapped lines joined; headings and list items kept apart, each
    paragraph a sentence (sentences=False: the paragraphs just joined)."""
    text = INLINE_MATH.sub(lambda m: " " + formula(next(g for g in m.groups() if g is not None)) + " ", text)
    if "<tr" in text.lower():
        text = re.sub(r"<table.*?</table>", lambda m: rows_apart(table(m[0])), text, flags=re.S | re.I)
    text = re.sub(r"<otsl>.*?</otsl>", lambda m: rows_apart(table(m[0])), text, flags=re.S)
    text = MD_TABLE.sub(lambda m: rows_apart(table(m[0])), text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = TAG.sub(" ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)      # links: their text
    text = re.sub(r"(\*\*|__|\*|`)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    paras, cur = [], []
    for line in text.splitlines():
        if not line.strip() or re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):  # blank line or --- rule
            paras.append(cur)
            cur = []
        elif MARKER.match(line):                     # a heading or list item: a sentence of its own
            paras.append(cur)
            cur = [MARKER.sub("", line).strip()]
            paras.append(cur)
            cur = []
        else:
            cur.append(line.strip())
    paras.append(cur)
    paras = [join_lines(p) for p in paras if p and join_lines(p)]
    return "\n".join(sentence(p) for p in paras) if sentences else " ".join(paras)


def rows_apart(rows):
    """A table inside running text: each row a paragraph of its own (not joined like wrapped lines)."""
    return "\n\n" + rows.replace("\n", "\n\n") + "\n\n"


def cell(text):
    """A table cell: markup out, no sentence end (the row gets one)."""
    return prose(text, sentences=False)


def join_lines(lines):
    out = ""
    for line in (l for l in lines if l):
        if not out:
            out = line
        elif out.endswith("-") and out[-2:-1].isalpha() and line[:1].islower():
            out = out[:-1] + line                    # exam-\nple -> example
        elif script.has_cjk(out[-1]) and script.has_cjk(line[0]):
            out += line
        else:
            out += " " + line
    return re.sub(r"[ \t]+", " ", out).strip()
