"""Read aloud: shorthand the voice models read letter by letter, written out the way a person says it,
just before a piece goes to the model (the pill still shows the text as written).

    $1bn -> 1 billion dollars      £2.5m -> 2.5 million pounds      €300k -> 300 thousand euros
    $5tn -> 5 trillion dollars     US$4 million -> 4 million US dollars      a 2bn deal -> a 2 billion deal

Only money with a scale (k, m, mn, bn, b, tn, or the word: thousand … trillion), and "bn" / "tn" after a
number on their own ("m" and "k" alone are metres and kilos too). Plain amounts ("$12.99") are left to the
model, which reads those well.
"""
import re

# the currency symbol -> its name, as said after the amount ("yen" has no plural)
CURRENCIES = {
    "US$": "US dollars", "HK$": "Hong Kong dollars", "NZ$": "New Zealand dollars", "AU$": "Australian dollars", "CA$": "Canadian dollars",
    "A$": "Australian dollars", "R$": "reais",
    "C$": "Canadian dollars", "S$": "Singapore dollars", "$": "dollars", "£": "pounds", "€": "euros",
    "¥": "yen", "₹": "rupees",
}
SCALES = {"k": "thousand", "m": "million", "mn": "million", "b": "billion", "bn": "billion", "tn": "trillion",
          "thousand": "thousand", "million": "million", "billion": "billion", "trillion": "trillion"}

_SYMBOL = "|".join(re.escape(s) for s in sorted(CURRENCIES, key=len, reverse=True))
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
# a symbol, the amount, then the scale: letters right after the number ("$1bn"), a word after a space ("$1 billion")
# the edges are Latin letters and digits only: Chinese and Japanese run straight on ("价值$1bn的")
MONEY = re.compile(rf"(?<![A-Za-z0-9_$£€¥₹])({_SYMBOL})\s?({_NUMBER})(?:(k|m|mn|bn|b|tn)|\s(thousand|million|billion|trillion))(?![A-Za-z0-9_])", re.I)
BARE = re.compile(rf"(?<![A-Za-z0-9_.,$£€¥₹])({_NUMBER})(bn|tn)(?![A-Za-z0-9_])", re.I)


def _money(m):
    symbol = next(s for s in CURRENCIES if s.lower() == m.group(1).lower())
    return f"{m.group(2)} {SCALES[(m.group(3) or m.group(4)).lower()]} {CURRENCIES[symbol]}"


def money(text):
    """The text with money shorthand written out; anything else as it was."""
    if not text:
        return text
    text = MONEY.sub(_money, text)
    return BARE.sub(lambda m: f"{m.group(1)} {SCALES[m.group(2).lower()]}", text)
