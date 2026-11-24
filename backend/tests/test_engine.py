#!/usr/bin/env python3
"""Engine tests.

  python3 backend/tests/test_engine.py [--real [--n 25]]

1. Unit checks: repeat-loop cut, token budget, script conversion (no model).
With --real (the slow set, run.py --all), also:
2. Parity: the first N benchmark clips of each set through the engine (auto language) must give
   the same text as the benchmark run of the same build (ASR/test/simple_0.6B-int8.jsonl).
3. Loop cap: ascend-mixed-038 with "Chinese" forced looped for 8192 tokens / 83 s in the
   benchmark; the engine must cut it short.
4. Quiet speech: the same clips (plus any long recordings in ASR/test/long/*.wav) set to a stated
   speech level over a stated room (QUIET_ROOM_DB): spoken quietly (QUIET_KEPT_DB) they still count
   as speech for the gate (engine/segments.py has_speech, VAD on as in the app); below the floor
   (TOO_QUIET_DB) they don't. The level is the clip's voiced speech, not its recording: the
   benchmark clips are recorded at very different levels (FLEURS English at -57 to -68 dBFS, below
   the floor at full level). Reported, not asserted: the text's change against full level, and the
   clips made 12 / 20 dB quieter than recorded (a quieter input may change words without the gate
   dropping anything; a clip recorded quietly falls below the floor).
"""
import argparse, json, re, sys, tempfile, time
from pathlib import Path

import mlx.core as mx
import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import config
from waudio.engine import Engine, keyboard_choice, script, segments, vad
from waudio.engine.models.mlx_audio import Runner
from waudio.engine.safeguards import find_loop, token_budget

ASR_TEST = Path("~/Documents/Speech/ASR/test").expanduser()
LONG = ASR_TEST / "long"  # your own long dictations (2–3 min, 16 kHz wav), if any
QUIETER_DB = (12, 20)       # reported: this much quieter than recorded
QUIET_ROOM_DB = -70         # room noise under the quiet speech (dBFS RMS): a quiet room on a Mac's mic
QUIET_KEPT_DB = (-40, -45)  # speech this quiet (dBFS RMS of its voiced frames) is speech
TOO_QUIET_DB = -60          # and this quiet is not (below segments.SPEECH_FLOOR_DB)
REFERENCE = ASR_TEST / "simple_0.6B-int8.jsonl"
MODEL = ASR_TEST.parent / "Qwen3-ASR-0.6B" / "Qwen3-ASR-0.6B-mlx-int8"  # the build REFERENCE was made with


def unit_checks():
    assert find_loop("就，他比其他球员更快、更强、" + "更" * 40) == len("就，他比其他球员更快、更强、更")
    assert find_loop("hello world " * 6) == len("hello world ")
    assert find_loop("哈哈哈，好好笑") is None
    assert find_loop("no no no, not that one") is None
    assert find_loop("我们回去吧") is None
    assert find_loop("") is None
    assert token_budget(10) == 198
    assert script.convert("我们回去吃饭了", "s2hk") == "我們回去吃飯了"
    assert script.convert("Hello", "s2hk") == "Hello"
    assert script.convert("我们", "as_is") == "我们"
    two = {"follow_keyboard": True, "languages": ["English", "Chinese"]}
    for code, cfg, want in (
            ("en", two, "English"), ("zh-Hans", two, "Chinese"), ("zh_Hant", two, "Chinese"),
            ("ja", two, "English"),                                         # not ranked: the first
            ("nonsense", two, "English"), (None, two, None),                # macOS doesn't say: detect
            ("zh-Hans", {**two, "follow_keyboard": False}, None),           # switch off: detect
            ("zh-Hans", {**two, "mix_languages": True}, None),              # mixing: detect
            ("zh-Hans", {**two, "languages": ["English"]}, None),           # one language: that one anyway
            ("zh-Hans", {**two, "languages": [], "language": "English"}, None)):  # so is a fixed one
        assert keyboard_choice(code, cfg) == want, (code, cfg)
    class Tokens:  # the model's tokenizer: what each token writes
        words = ["hello", "你", "，", "\ufffd", "<asr_text>", "ok"]
        def decode(self, ids, skip_special_tokens=False): return "".join(self.words[i] for i in ids)
    r = Runner.__new__(Runner); r.tokenizer, r.blocks = Tokens(), {}
    logits = r._strict("English")[0](None, mx.zeros((1, 6)))[0].tolist()
    allowed = [w for w, l in zip(Tokens.words, logits) if l > float("-inf")]
    assert allowed == ["hello", "<asr_text>", "ok"], allowed
    assert r._strict("Chinese") is None and r._strict(None) is None
    # a dictation that runs to the token cap says so; one the model ends, or a repeat loop cut, doesn't
    class Model:  # writes `n` tokens of "hello" (or until max_tokens)
        def __init__(self, n): self.n = n
        def stream_generate(self, audio, max_tokens, **kw):
            for _ in range(min(self.n, max_tokens)):
                yield 0, None
    #  tokens written  stop asked once the text is this long (a repeat loop)  capped
    rows = ((50, None, True), (10, None, False), (50, 1, False),
            (50, 40 * len("hello"), False))  # the loop is cut just as the budget runs out: cut, not capped
    assert rows
    for writes, stop_at, capped in rows:
        r = Runner.__new__(Runner); r.tokenizer, r.blocks, r.model = Tokens(), {}, Model(writes)
        out = r.transcribe(None, "English", 40, "", lambda so_far: stop_at is not None and len(so_far) >= stop_at)
        assert len(out) == 4 and out[3] is capped, (writes, stop_at, out[3])
    print("unit checks: ok")


def read16k(path):
    audio, sr = sf.read(path, dtype="float32")
    assert sr == 16000, f"{path}: {sr} Hz"
    return audio if audio.ndim == 1 else audio.mean(axis=1)


def char_errors(a, b):
    """Edit distance between two texts, in characters (the benchmark counts errors the same way)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def voiced_rms(audio):
    """RMS of the frames the voice detector calls voiced (0 if none): the clip's speech level."""
    rms = segments.frame_rms(audio, 16000)
    voice = vad.voiced(audio, 16000, len(rms), segments.FRAME_S)
    return float(np.sqrt(np.mean(rms[voice] ** 2))) if voice is not None and voice.any() else 0.0


def at_level(audio, speech_db, room_db=QUIET_ROOM_DB, seed=5):
    """audio with its voiced speech at speech_db dBFS RMS, after 1 s of room noise at room_db, with that
    noise added throughout (the clip's own recorded background is scaled with its speech: room_db is
    the least noise under it, not the only noise)."""
    a = np.concatenate([np.zeros(16000, np.float32), audio / voiced_rms(audio) * 10 ** (speech_db / 20)])
    return (a + np.random.default_rng(seed).normal(0, 10 ** (room_db / 20), len(a))).astype(np.float32)


def quiet_speech(engine, clips):
    """The gate's decision at stated speech levels over a stated room: speech spoken quietly is still
    speech (else the app says "Didn't catch anything" and the dictation is gone), speech below the
    floor is not — asserted. The text's change against full level, and the clips made quieter than
    recorded — reported."""
    was, segments.USE_VAD = segments.USE_VAD, True  # as the app has it by default
    try:
        _quiet_speech(engine, clips)
    finally:
        segments.USE_VAD = was


def _quiet_speech(engine, clips):
    assert vad.model_path(), "quiet speech needs silero-vad (the app's voice detector) in mlx-env"
    voiced = [(name, audio) for name, audio in clips if voiced_rms(audio) > 0]
    assert voiced, "no clip has voiced speech"
    lost, heard, changed, chars = [], [], 0, 0
    for name, audio in voiced:
        full = engine.transcribe(audio).text
        for level in QUIET_KEPT_DB:
            quiet = at_level(audio, level)
            if not segments.has_speech(quiet, 16000):
                lost.append(f"{name} at {level} dBFS ({segments.levels(quiet, 16000)})")
            changed += char_errors(full, engine.transcribe(quiet).text); chars += len(full)
        if segments.has_speech(at_level(audio, TOO_QUIET_DB), 16000):
            heard.append(f"{name} at {TOO_QUIET_DB} dBFS")
    print(f"quiet speech: {len(voiced)} of {len(clips)} clips voiced; with {QUIET_ROOM_DB} dBFS room noise added, "
          + ", ".join(f"{level} dBFS" for level in QUIET_KEPT_DB) + f" speech: {len(lost)} lost as \"nothing heard\", "
          f"text {changed} chars different in {chars} ({changed / max(chars, 1):.1%}); "
          f"{TOO_QUIET_DB} dBFS: {len(voiced) - len(heard)} of {len(voiced)} dropped")
    recorded = {d: sum(not segments.has_speech((a * 10 ** (-d / 20)).astype("float32"), 16000) for _, a in voiced)
                for d in (0, *QUIETER_DB)}
    print("quiet speech (reported): at the level they were recorded at, \"nothing heard\" for " + ", ".join(
        f"{recorded[d]} of {len(voiced)} {'as recorded' if not d else f'{d} dB quieter'}" for d in recorded))
    assert not lost, "the gate drops quiet speech: " + "; ".join(lost)
    assert not heard, "the gate keeps speech below its floor: " + ", ".join(heard)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--real", action="store_true", help="also the real model: parity, loop cap, language hint")
    a = ap.parse_args()
    unit_checks()
    if not a.real:
        print("engine checks: ok (with the real model: --real)")
        return

    sys.path.insert(0, str(ASR_TEST))
    import bench
    ref = {}
    for line in REFERENCE.read_text().splitlines():
        r = json.loads(line)
        if r["language"] == "auto":
            ref[r["id"]] = r
    # auto language whatever your config.toml ranks or your keyboard says (the benchmark detected)
    # and the build REFERENCE was made with, whatever model your config.toml uses
    cfg = config.load()["engine"] | {"language": "auto", "languages": [], "follow_keyboard": False,
                                     "script": "as_is", "hotwords": [], "model": str(MODEL)}
    engine = Engine(cfg)
    t = time.time()
    engine.load()
    print(f"loaded {cfg['model']} in {time.time() - t:.1f}s")

    same = diff = 0
    secs = audio_s = 0.0
    with tempfile.TemporaryDirectory() as tmp:
        clips = [c for name, rel, col, _ in bench.SETS for c in bench.sample(name, rel, col, max(a.n, 39), tmp)]
        for c in clips:
            if int(c["id"][-3:]) >= a.n:
                continue
            r = engine.transcribe(read16k(c["wav"]))
            secs, audio_s = secs + r.secs, audio_s + r.audio_s
            want = ref[c["id"]]
            if r.text == want["text"] and r.language == want["tag"]:
                same += 1
            else:
                diff += 1
                print(f"DIFF {c['id']}\n  bench:  [{want['tag']}] {want['text']}\n  engine: [{r.language}] {r.text}")
        print(f"parity: {same} same, {diff} different; {secs:.1f}s for {audio_s:.0f}s of audio "
              f"({secs / max(1, same + diff):.2f}s per clip)")

        loop = next(c for c in clips if c["id"] == "ascend-mixed-038")
        r = engine.transcribe(read16k(loop["wav"]), language="Chinese")
        print(f"loop cap: {r.secs}s, cut={r.cut}, text: {r.text}")
        assert r.cut and r.secs < 10, "loop was not cut"

        # the language tag alone is a hint, strict is not: mixed speech comes out with no Chinese
        said = max((c for c in clips if c["id"].startswith("ascend-mixed")), key=lambda c: len(ref[c["id"]]["text"]))
        hint = engine.transcribe(read16k(said["wav"]), language="English")
        strict = engine.transcribe(read16k(said["wav"]), language="English", strict=True)
        assert re.search("[\u4e00-\u9fff]", hint.text) and strict.text and not re.search("[\u4e00-\u9fff]", strict.text), (hint.text, strict.text)

        quiet = [(c["id"], read16k(c["wav"])) for c in clips if int(c["id"][-3:]) < min(a.n, 10)]
        long_ones = sorted(LONG.glob("*.wav")) if LONG.is_dir() else []
        quiet += [(p.name, read16k(p)) for p in long_ones]
        if not long_ones:
            print(f"quiet speech: no long recording in {LONG} (2–3 min, 16 kHz wav) — short clips only")
        quiet_speech(engine, quiet)
    engine.unload()
    sys.exit(1 if diff else 0)


if __name__ == "__main__":
    main()
