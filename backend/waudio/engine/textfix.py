"""After the model, before the text is typed: spoken commands, your replacements, numbers, and
text the model makes up from silence. Runs on every pass (live and final), so what is typed live
and what the final check writes agree. Settings: config.toml [text].

Spoken commands (like Mac Dictation's): "new line", "new paragraph", "comma", "full stop",
"question mark", "exclamation mark", "colon", "semicolon", "open/close quote", "open/close bracket";
Chinese and Cantonese 換行 / 新段落 / 逗號 / 句號 / 問號 / 感嘆號 / 冒號 / 分號 / 頓號 (simplified too).
The model often adds its own punctuation around the command word ("Hello, comma, how") — that is
absorbed. "period" and "dot" are left alone: too common as ordinary words.

A command word is only taken as a command when it can't be an ordinary word in the sentence:
  - never right after a/an/the/this/that/any/no/one/each/every or a possessive ("put a comma
    after it", "the colon was fine", "the full stop of the line" stay as they are);
  - "new line" / "new paragraph" only at the start or end of what was heard, or with punctuation
    on both sides ("Hello. New line. Thanks"); "I have a new line of products" and "He said, new
    line of products…" stay;
  - Chinese: never right after 個 个 的 嘅 按 打 加 用 要 寫 写 一 兩 两 不 唔 冇 無 没 沒 ("不要換行",
    "请按冒号" stay).
"""
import re

from .script import has_cjk

# (spoken words, what to write). Latin ones match whole words, any case.
LATIN = [("new paragraph", "\n\n"), ("new line", "\n"), ("full stop", "."), ("comma", ","),
         ("question mark", "?"), ("exclamation mark", "!"), ("exclamation point", "!"),
         ("semicolon", ";"), ("colon", ":"), ("open quote", "“"), ("close quote", "”"),
         ("open bracket", "("), ("close bracket", ")")]
CJK = [("新段落", "\n\n"), ("新一段", "\n\n"), ("換行", "\n"), ("换行", "\n"), ("逗號", "，"), ("逗号", "，"),
       ("句號", "。"), ("句号", "。"), ("問號", "？"), ("问号", "？"), ("感嘆號", "！"), ("感叹号", "！"),
       ("冒號", "："), ("冒号", "："), ("分號", "；"), ("分号", "；"), ("頓號", "、"), ("顿号", "、")]
BEFORE = r"[\s,.，。、]*"           # punctuation the model put before a command word ("?" and "!" stay)
AROUND = r"[\s,.，。、!?！？;；:：]*"  # … and after it
OPENING = set("“(（")
JUNK = {"謝謝觀看", "谢谢观看", "謝謝收看", "谢谢收看", "字幕由amara.org社區提供", "thanks for watching",
        "thank you for watching", "(music)", "[music]", "(音樂)", "[音樂]", "(音乐)", "[音乐]"}


def apply(text, cfg):
    """cfg: the [text] table (commands, numbers, replace). Never raises on odd input."""
    if not text:
        return text
    if text.strip().lower().rstrip(".!。！") in JUNK:
        return ""
    if cfg.get("commands", True):
        text = commands(text)
    for heard, write in cfg.get("replace") or []:
        text = replace(text, heard, write)
    if cfg.get("numbers", False):
        text = numbers(text)
    return text


def replace(text, heard, write):
    """Your own "heard → write" fix. Latin words match whole words in any case; Chinese anywhere."""
    if not heard:
        return text
    if has_cjk(heard):
        return text.replace(heard, write)
    return re.sub(rf"(?<![\w]){re.escape(heard)}(?![\w])", lambda m: write, text, flags=re.I)


DETERMINERS = {"a", "an", "the", "this", "that", "these", "those", "any", "no", "one", "each", "every",
               "my", "your", "his", "her", "its", "our", "their", "another", "of"}
CJK_NOT_AFTER = set("個个的嘅按打加用要寫写一兩两不唔冇無没沒")
PUNCT = set(",.，。、!?！？;；:：")


def commands(text):
    changed = False
    for spoken, mark in CJK:
        if spoken in text:
            new = re.sub(BEFORE + re.escape(spoken) + AROUND,
                         lambda m, mk=mark: mk if _cjk_ok(m, text, spoken) else m.group(0), text)
            changed, text = changed or new != text, new
    for spoken, mark in LATIN:
        pattern = BEFORE + r"(?<![\w])" + re.escape(spoken).replace(r"\ ", r"\s+") + r"(?![\w])" + AROUND
        new = re.sub(pattern, lambda m, mk=mark: _latin_mark(mk) if _latin_ok(m, text, mk) else m.group(0),
                     text, flags=re.I)
        changed, text = changed or new != text, new
    return _tidy(text) if changed else text


def _cjk_ok(m, text, spoken):
    word_at = m.start() + m.group(0).index(spoken)
    return not (word_at > 0 and text[word_at - 1] in CJK_NOT_AFTER)


def _latin_ok(m, text, mark):
    before = text[:m.start()]
    prev = re.findall(r"[A-Za-z']+", before[-20:])
    if prev and not re.search(r"[,.;:!?]\s*$", before) and prev[-1].lower() in DETERMINERS:
        return False
    if mark.startswith("\n"):  # a line command: at an edge, or set off by punctuation
        at_start = not before.strip()
        at_end = not text[m.end():].strip()
        matched = m.group(0)
        letters = [i for i, c in enumerate(matched) if c.isalpha()]
        lead, trail = matched[:letters[0]], matched[letters[-1] + 1:]
        punct_before = any(c in PUNCT for c in lead) or before.rstrip()[-1:] in PUNCT
        punct_after = any(c in PUNCT for c in trail) or text[m.end():].lstrip()[:1] in PUNCT
        return at_start or at_end or (punct_before and punct_after)  # set off on both sides
    return True


def _latin_mark(mark):
    if mark.startswith("\n"):
        return mark
    if mark in OPENING:
        return " " + mark
    return mark + " "


def _tidy(text):
    """Spaces and capitals around the marks the commands put in."""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"([“(（]) +", r"\1", text)
    text = re.sub(r" +([”)）,.;:?!])", r"\1", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"([?!？！])[.。]", r"\1", text)  # "…stop? Full stop" -> "?" not "?."
    # a capital after a sentence end or a line break; Latin only
    text = re.sub(r"([.?!]\s+|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text.strip(" ")


# ---------- numbers ----------
ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                     "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
SCALES = {"hundred": 100, "thousand": 1000, "million": 10**6, "billion": 10**9}
NUM_WORD = re.compile(r"\b(?:" + "|".join(sorted([*ONES, *TENS, *SCALES], key=len, reverse=True))
                      + r")(?:[\s-]+(?:and[\s-]+)?(?:" + "|".join(sorted([*ONES, *TENS, *SCALES], key=len, reverse=True))
                      + r"))*\b", re.I)
CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNIT = {"十": 10, "百": 100, "千": 1000}
CN_BIG = {"萬": 10**4, "万": 10**4, "億": 10**8, "亿": 10**8}
CN_RUN = re.compile("[" + "".join([*CN_DIGIT, *CN_UNIT, *CN_BIG]) + "]+")


def numbers(text):
    """Number words to digits: "twenty four" -> 24, "two four six" -> 246, 二十四 -> 24, 一百 -> 100.
    A lone small word stays ("one of them", 第一); so do 十分, 萬一, 千萬 and similar."""
    text = NUM_WORD.sub(_latin_number, text)
    return CN_RUN.sub(_cn_number, text)


def _latin_number(m):
    """One run of number words. "and" joins only after hundred/thousand… ("one hundred and five");
    elsewhere it separates two numbers ("between two and three")."""
    tokens = [w for w in re.split(r"[\s-]+", m.group(0)) if w]
    groups, cur = [], []
    for i, w in enumerate(tokens):
        if w.lower() == "and" and not (cur and cur[-1].lower() in SCALES):
            groups.append(cur)
            cur = []
            groups.append(None)  # a literal "and"
        elif w.lower() != "and":
            cur.append(w)
    groups.append(cur)
    out = [("and" if g is None else _number_words(g)) for g in groups if g != []]
    return " ".join(out)


def _number_words(original):
    words = [w.lower() for w in original]
    text = " ".join(original)
    if len(words) == 1 and (words[0] in SCALES or (words[0] in ONES and ONES[words[0]] < 10)):
        return text  # "one of them", "a hundred"
    if all(w in ONES and ONES[w] < 10 for w in words):  # a digit sequence: "two four six" (3 or more)
        return "".join(str(ONES[w]) for w in words) if len(words) >= 3 else text
    if not any(w in SCALES for w in words):  # a year said as two pairs: "nineteen ninety", "twenty twenty four"
        pairs, cur = [], []
        for w in words:
            if cur and (w in TENS or (w in ONES and ONES[w] >= 10) or cur[-1] in ONES):
                pairs.append(cur)
                cur = []
            cur.append(w)
        pairs.append(cur)
        values = [sum(TENS.get(w, ONES.get(w, 0)) for w in p) for p in pairs]
        if len(pairs) == 2 and 11 <= values[0] <= 29 and 10 <= values[1] <= 99:  # 1100–2999
            return str(values[0] * 100 + values[1])
        if len(pairs) > 1:
            return text  # not one number
    total = current = 0
    for w in words:
        if w in ONES:
            current += ONES[w]
        elif w in TENS:
            current += TENS[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        else:
            total += max(current, 1) * SCALES[w]
            current = 0
    return f"{total + current:,}" if total + current >= 10000 else str(total + current)


def _cn_number(m):
    run = m.group(0)
    if re.search("[" + "".join(CN_DIGIT) + "]{2}[" + "".join([*CN_UNIT, *CN_BIG]) + "]", run):
        return run  # 七八十, 三四百: "seventy or eighty", not a number
    if not any(c in CN_DIGIT for c in run) or run[0] in CN_UNIT.keys() - {"十"} or run[0] in CN_BIG:
        return run
    if not any(c in CN_UNIT or c in CN_BIG for c in run):  # plain digits: only 3 or more (一二三 -> 123)
        return "".join(str(CN_DIGIT[c]) for c in run) if len(run) >= 3 else run
    if len(run) < 2:
        return run
    total = section = digit = 0
    for c in run:
        if c in CN_DIGIT:
            digit = CN_DIGIT[c]
        elif c in CN_UNIT:
            section += (digit or 1) * CN_UNIT[c]
            digit = 0
        else:
            total += (section + digit) * CN_BIG[c]
            section = digit = 0
    return str(total + section + digit)
