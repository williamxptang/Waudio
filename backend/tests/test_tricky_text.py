#!/usr/bin/env python3
"""Text that is hard to read aloud (tests/data/tricky.txt): units, Roman numerals, money, dates, a URL,
an email address, emoji, decorative quotes, Chinese characters with two readings (多音字), numbers in
Chinese, mixed English and Chinese, a long sentence with no punctuation.

Fast: the file holds every kind, one well-formed line each at least.
--real (the slow set): every voice model on this Mac that can run reads every line, and two rules hold:
- each line's audio is 0.3–2.5× as long as that model's own pace says it should be (measured on a plain
  English and a plain Chinese sentence; lengths as spoken: counts()) — silence or a cut-off below; a
  run-on into noise above (English lines measured 0.77–1.84×, so 2.5 leaves ~35 %);
- with two models or more, no model's line is over twice the models' median for that line, each at its own
  pace (the ratio above): one build stalling or running on where the others don't. Measured 2026-11-14:
  Breeze mix48 on the Chinese numbers 2.2×, the next highest 1.6×.
The Chinese lines are read in the same English preset (no Chinese preset ships). The clips go on a page to judge the pronunciation by ear (not a pass/fail):
<main checkout>/tmp/tricky-text/index.html.

  python3 backend/tests/test_tricky_text.py [--real]
"""
import argparse, html, re, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA = Path(__file__).resolve().parent / "data" / "tricky.txt"
KINDS = {"units", "roman", "money", "dates", "url", "email", "emoji", "quotes", "long", "polyphone", "zh-numbers", "mixed"}
MODELS = Path("~/Documents/Speech/TTS").expanduser()
VOICES = Path("~/Documents/Speech/TTS/voices").expanduser()
VOICE = "en-gb-rose"
PLAIN = {"en": "The weather is mild today, so we will walk to the market after lunch.",
         "zh": "今天天气很好，我们吃完午饭以后走路去市场买菜。"}
RATIO = (0.3, 2.5)  # silence or a cut-off below; a run-on into noise above (a spoken-form estimate is rough)
PEERS = 2.0         # at most this many times the models' median ratio on the same line
CJK = re.compile(r"[㐀-鿿豈-﫿]")


def lines():
    out = []
    for raw in DATA.read_text().splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        kind, sep, text = raw.partition("\t")
        assert sep and kind in KINDS and text.strip(), f"not '<kind><TAB><text>' with a known kind: {raw!r}"
        out.append((kind, text.strip()))
    return out


def counts(text):
    """(letters, Chinese syllables) as the line is spoken, not as written: in English a digit says about
    three letters ("2026": twenty twenty-six), so does a symbol in an address (@ / : . -), an emoji about
    six (its name); in a Chinese line a digit is one syllable (二零二六)."""
    rest = CJK.sub("", text)
    letters = len(re.findall(r"[^\W\d_]", rest))
    digits = len(re.findall(r"\d", rest))
    symbols = len(re.findall(r"[@/:$£€%&+=#]|\.(?=\w)|(?<=\w)-(?=\w)", rest))
    emoji = len(re.findall(r"[\U0001F300-\U0001FAFF]", rest))
    cjk = len(CJK.findall(text))
    if cjk:
        return letters + 3 * symbols, cjk + digits + emoji
    return letters + 3 * (digits + symbols) + 6 * emoji, 0


def the_file_holds_every_kind():
    found = lines()
    assert found, "no lines"
    missing = KINDS - {k for k, _ in found}
    assert not missing, f"kinds without a line: {sorted(missing)}"


def page_folder():
    """The main checkout's tmp/tricky-text/ (ignored by git), wherever this runs from."""
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=Path(__file__).parent, capture_output=True, text=True).stdout.strip()
    root = (Path(__file__).parent / common).resolve().parent if common else Path(tempfile.gettempdir())
    return root / "tmp" / "tricky-text"


def real():
    import numpy as np
    import soundfile as sf
    from waudio.engine import tts
    runnable = [m for m in tts.scan_models(str(MODELS)) if m["format"] == "mlx" and not m["why"]]
    assert runnable, f"no voice model this Mac can run under {MODELS}"
    out = page_folder(); out.mkdir(parents=True, exist_ok=True)
    rows, bad = [], []
    with tempfile.TemporaryDirectory() as t:
        for f in (f"{VOICE}.wav", f"{VOICE}.txt"):
            (Path(t) / f).write_bytes((VOICES / f).read_bytes())
        for m in runnable:
            e = tts.TtsEngine({"model": m["path"], "voices_dir": t, "voice": VOICE})
            try:
                e.load()
                rate = e.sample_rate
                say = lambda text: e.speak(text, persona_text="")
                en, zh = say(PLAIN["en"]), say(PLAIN["zh"])
                per_letter = len(en) / rate / counts(PLAIN["en"])[0]
                per_char = len(zh) / rate / counts(PLAIN["zh"])[1]
                t0 = time.time()
                for i, (kind, text) in enumerate(lines()):
                    audio = say(text)
                    letters, chars = counts(text)
                    want = letters * per_letter + chars * per_char
                    ratio = len(audio) / rate / want
                    name = f"{Path(m['path']).name}-{i:02d}-{kind}.wav"
                    sf.write(out / name, audio, rate)
                    rows.append((m["name"], kind, text, name, ratio, len(audio) / rate))
                    if not RATIO[0] <= ratio <= RATIO[1]:
                        bad.append(f"{m['name']} {kind}: {ratio:.2f}× the expected length — {text}")
                print(f"{m['name']}: {len(lines())} lines in {time.time() - t0:.0f}s", flush=True)
            finally:
                e.unload()
    if len(runnable) > 1:
        for text in {x for _, _, x, _, _, _ in rows}:
            took = [(n, r, s) for n, _, x, _, r, s in rows if x == text]
            median = float(np.median([r for _, r, _ in took]))
            bad += [f"{n}: {s:.1f}s, {r / median:.1f}× the models' median at their own pace — {text}" for n, r, s in took if r > PEERS * median]
    rows_html = "\n".join(f"<tr><td>{html.escape(n)}</td><td>{k}</td><td>{html.escape(x)}</td><td>{r:.2f}× · {s:.1f}s</td>"
                          f"<td><audio controls preload=none src='{f}'></audio></td></tr>" for n, k, x, f, r, s in rows)
    (out / "index.html").write_text(f"<!doctype html><meta charset=utf-8><title>Tricky text</title><table border=1 cellpadding=4>"
                                    f"<tr><th>model</th><th>kind</th><th>text</th><th>length</th><th>listen</th></tr>{rows_html}</table>\n")
    print(f"listen: {out / 'index.html'}")
    assert not bad, "lines read too short or too long:\n  " + "\n  ".join(bad)


def main():
    p = argparse.ArgumentParser(); p.add_argument("--real", action="store_true"); args = p.parse_args()
    the_file_holds_every_kind()
    print("tricky text: file ok")
    if args.real:
        real()
        print("tricky text real: ok")


if __name__ == "__main__":
    main()
