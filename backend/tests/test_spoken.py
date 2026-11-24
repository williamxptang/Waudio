#!/usr/bin/env python3
"""Read aloud's spoken form (engine/spoken.py): money shorthand the voice models read letter by letter is
written out; everything else is left as it was.

  python3 backend/tests/test_spoken.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.engine.spoken import money

WRITTEN_OUT = [  # (as written, as said)
    ("a $1bn firm", "a 1 billion dollars firm"),
    ("worth $1.5bn now", "worth 1.5 billion dollars now"),
    ("£2.5m in fees", "2.5 million pounds in fees"),
    ("€300k a year", "300 thousand euros a year"),
    ("$5tn of debt", "5 trillion dollars of debt"),
    ("¥2bn", "2 billion yen"),
    ("US$4 million", "4 million US dollars"),
    ("HK$10bn", "10 billion Hong Kong dollars"),
    ("$1,200m", "1,200 million dollars"),
    ("$3BN", "3 billion dollars"),
    ("$7 billion and $8m", "7 billion dollars and 8 million dollars"),
    ("a 2bn deal", "a 2 billion deal"),
    ("4.2tn", "4.2 trillion"),
    ("AU$5m", "5 million Australian dollars"),
    ("CA$5m", "5 million Canadian dollars"),
    ("价值$1bn的公司", "价值1 billion dollars的公司"),  # Chinese runs straight on: no space needed
    ("估值5bn美元", "估值5 billion美元"),
    ("日本は¥2bnを", "日本は2 billion yenを"),
]
LEFT_AS_IT_WAS = [
    "The ticket cost $12.99, or about £10.50 in London.",  # plain amounts: the models read these well
    "She ran 5m and lifted 20k of stone.",                  # m and k alone are metres and kilos too
    "Version 2bn3 of the file",                             # not a scale: letters run on
    "Call ext. $1bnx",                                      # not a scale either
    "",
    "Nothing to change here.",
]

assert WRITTEN_OUT and LEFT_AS_IT_WAS
for written, said in WRITTEN_OUT:
    assert money(written) == said, (written, money(written), said)
for text in LEFT_AS_IT_WAS:
    assert money(text) == text, (text, money(text))
assert money(None) is None
print("spoken checks: ok")
