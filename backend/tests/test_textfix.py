#!/usr/bin/env python3
"""Text clean-up after the model (engine/textfix.py): spoken commands, replacements, numbers, junk.
No model needed.

  python3 backend/tests/test_textfix.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.engine.textfix import apply

ON = {"commands": True, "numbers": True, "replace": [["chat gpt", "ChatGPT"], ["阿里", "Ali"]]}
CASES = [
    # commands that are commands
    ("Hello comma how are you question mark", "Hello, how are you?"),
    ("Hello, comma, how are you? New line. Thanks full stop", "Hello, how are you?\nThanks."),
    ("Dear John, new paragraph", "Dear John\n\n"),
    ("New line", "\n"),
    ("open quote hi close quote", "“hi”"),
    ("我想去旺角逗號然後食飯句號", "我想去旺角，然後食飯。"),
    ("你好，換行，再見。", "你好\n再見。"),
    # ordinary words that stay words
    ("I have a new line of products.", "I have a new line of products."),
    ("The colon was fine", "The colon was fine"),
    ("Put a comma after it", "Put a comma after it"),
    ("This is the full stop of the line", "This is the full stop of the line"),
    ("不要換行", "不要換行"),
    ("请按冒号", "请按冒号"),
    ("A period of time", "A period of time"),
    ("He said, new line of products are here", "He said, new line of products are here"),
    ("Hello, new line, how are you", "Hello\nHow are you"),
    ("What is a full stop? Full stop", "What is a full stop?"),
    ("fifty fifty chance", "fifty fifty chance"),
    # numbers
    ("I have twenty four apples and one pear", "I have 24 apples and one pear"),
    ("call two four six eight", "call 2468"),
    ("between two and three o'clock", "between two and three o'clock"),
    ("nineteen ninety", "1990"), ("twenty twenty four", "2024"), ("two thousand twenty six", "2026"),
    ("one hundred and five", "105"), ("He gave me one two", "He gave me one two"),
    ("a hundred", "a hundred"), ("a thousand years", "a thousand years"),
    ("第二十四名", "第24名"), ("一百二十三", "123"), ("三千五百萬", "35000000"),
    ("七八十年", "七八十年"), ("十分好", "十分好"), ("萬一", "萬一"), ("千萬唔好", "千萬唔好"), ("第一", "第一"),
    # replacements, junk
    ("I like chat GPT", "I like ChatGPT"), ("阿里巴巴", "Ali巴巴"),
    ("謝謝觀看。", ""), ("Thanks for watching!", ""),
]


def main():
    bad = [(t, apply(t, ON), want) for t, want in CASES if apply(t, ON) != want]
    for t, got, want in bad:
        print(f"FAIL {t!r}: got {got!r}, want {want!r}")
    assert apply("twenty four", {"numbers": False}) == "twenty four"  # numbers are off by default
    assert apply("Hello comma there", {"commands": False}) == "Hello comma there"
    assert not bad
    print(f"textfix checks: ok ({len(CASES)} cases)")


if __name__ == "__main__":
    main()
