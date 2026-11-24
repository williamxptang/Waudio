#!/usr/bin/env python3
"""Breeze as a voice model.

  python3 backend/tests/test_breeze.py [--real]

Without a model: a stand-in for the speech model (it stands in for the slow part only; files, the
voices folder and the engine are real). --real: the depth cache against mlx-audio's own decoder, a
fixed take against tests/data/breeze_reference.json (made by hand with --make-reference), and long
reading through TtsEngine, which must keep up with real time.
"""
import argparse, json, re, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace

import mlx.core as mx
import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.engine import breeze, tts
from waudio.engine.models import mlx_audio_tts as runner  # Breeze's depth cache is the runner's

MODEL = Path("~/Documents/Speech/TTS/Breeze-TTS-2/Breeze-TTS-2-mlx-4bit").expanduser()
REFERENCE = Path(__file__).resolve().parent / "data" / "breeze_reference.json"  # made by hand: --make-reference
PERSONA = "Warm narrator"
REFERENCE_TEXT = "The tea is still warm, and the letter is on the table."
BANDS = (80, 250, 500, 1000, 2000, 4000, 8000)  # Hz: the spectrum's shape, band by band


CHECKS = []


def check(f):
    CHECKS.append(f)
    return f


class StandIn:
    sample_rate = 24000
    def __init__(self, chunks=3, samples=2400): self.calls, self.chunks, self.samples, self.sent = [], chunks, samples, 0
    def generate(self, **kw):
        self.calls.append(kw)
        for _ in range(self.chunks):
            self.sent += 1
            yield SimpleNamespace(audio=np.full(self.samples, 0.1, np.float32))


def folder_with_voice(root):
    d = Path(root) / "voices"; d.mkdir()
    sf.write(d / "ivy.wav", np.zeros(24000, np.float32), 24000)
    (d / "ivy.txt").write_text("Her exact words.")
    return d


def engine(d):
    e = tts.TtsEngine({"model": "m", "voices_dir": str(d), "voice": "ivy"})
    e.model, e.loaded_model, e.is_breeze, e.takes_persona = StandIn(), "m", True, True
    return e


class Draws(StandIn):
    """Audio from the real random generator, as both voice models make it. Breeze sets a seed it is
    given; Qwen takes no seed and just draws."""
    def generate(self, **kw):
        if kw.get("seed") is not None:
            mx.random.seed(kw["seed"])
        yield SimpleNamespace(audio=np.array(mx.random.uniform(shape=(2400,)), np.float32))


@check
def same_take_repeats_a_read():
    with tempfile.TemporaryDirectory() as t:
        e = engine(folder_with_voice(t)); e.model = Draws()
        one, two = e.speak("One.", persona_text=""), e.speak("One.", persona_text="")
        assert len(one) and np.array_equal(one, two)


@check
def same_take_off_gives_a_new_read():
    with tempfile.TemporaryDirectory() as t:
        e = engine(folder_with_voice(t)); e.model = Draws(); e.cfg["same_take"] = False
        one, two = e.speak("One.", persona_text=""), e.speak("One.", persona_text="")
        assert len(one) and not np.array_equal(one, two)


@check
def a_first_read_that_bakes_matches_later_reads():
    with tempfile.TemporaryDirectory() as t:
        e = engine(folder_with_voice(t)); e.model = Draws()
        first, later = e.speak("One.", persona_text="Sultry."), e.speak("One.", persona_text="Sultry.")
        assert len(first) and np.array_equal(first, later)


@check
def a_persona_is_baked_once_and_read_without_direction():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); e = engine(d); m = e.model
        assert len(e.speak("One.", persona_text="Sultry.")) and len(m.calls) == 2
        bake, read = m.calls
        assert bake["cfg_scale"] > 1 and bake["instruct"] == "Sultry." and bake["text"] == "Her exact words."
        assert read["cfg_scale"] == 1 and not read.get("instruct") and read["text"] == "One."
        baked = sorted((d / breeze.FOLDER).iterdir())
        assert [Path(read["ref_audio"])] == baked and read["ref_text"] == "Her exact words."
        e.speak("Two.", persona_text="Sultry."); assert len(m.calls) == 3, "baked again"
        e.speak("Three.", persona_text="News."); assert len(m.calls) == 5 and len(list((d / breeze.FOLDER).iterdir())) == 2
        m.calls.clear(); e.speak("Four.", persona_text="")
        assert len(m.calls) == 1 and m.calls[0]["ref_audio"] == str(d / "ivy.wav")


@check
def speed_variants_of_a_persona_share_one_bake():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); e = engine(d); m = e.model
        e.speak("One.", persona_text="Sultry.")
        baked = sorted((d / breeze.FOLDER).iterdir())
        assert len(m.calls) == 2 and len(baked) == 1
        for speed in (0.85, 1.15):  # "Sultry · slow", "Sultry · fast": the same words, another speed
            assert len(e.speak("One.", persona_text="Sultry.", speed=speed))
        assert len(m.calls) == 4 and sorted((d / breeze.FOLDER).iterdir()) == baked, "a speed variant baked again"
        assert all(c["cfg_scale"] == 1 and Path(c["ref_audio"]) == baked[0] for c in m.calls[2:])


@check
def a_voices_accent_goes_before_its_persona_when_baked():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); e = engine(d); m = e.model
        e.speak("One.", persona_text="Sultry.")
        (d / "ivy.accent.txt").write_text("Speak with a Highland accent.\n")
        e.speak("One.", persona_text="Sultry.")
        assert len(m.calls) == 4, "the accent bakes again"
        assert m.calls[2]["instruct"] == "Speak with a Highland accent. Sultry.", m.calls[2]
        assert len(list((d / breeze.FOLDER).iterdir())) == 2
        m.calls.clear(); e.speak("Two.", persona_text="")
        assert len(m.calls) == 1 and not m.calls[0].get("instruct"), "no persona: not baked, accent or not"


@check
def a_line_with_a_mood_is_read_whole_and_told_its_mood():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); e = engine(d); m = e.model; chunks = []
        (d / "ivy.accent.txt").write_text("Speak with a Highland accent.\n")
        mood = tts.Moody("Sad now. " + tts.MOOD_LEAD + " Calm.")
        assert len(e.speak("One.", persona_text=mood, on_chunk=chunks.append)) and not chunks, "a mood: made whole"
        bake, read = m.calls
        assert bake["instruct"] == read["instruct"] == "Speak with a Highland accent. " + mood and bake["cfg_scale"] == breeze.BAKE_CFG
        assert read["cfg_scale"] == breeze.MOOD_CFG and read["text"] == "One." and read["ref_audio"] != str(d / "ivy.wav")
        m.calls.clear(); e.speak("One.", persona_text=str(mood), on_chunk=chunks.append)  # the same words as a persona
        assert chunks and len(m.calls) == 1 and m.calls[0]["cfg_scale"] == 1 and not m.calls[0].get("instruct"), m.calls


@check
def a_line_with_a_mood_keeps_its_pace_within_mood_wpm():
    low, high = tts.MOOD_WPM
    rows = [(12, low * 0.75, low, True), (12, (low + high) / 2, (low + high) / 2, True), (12, high * 1.25, high, True),
            (tts.MOOD_WPM_WORDS - 1, low / 4, low / 4, True),  # too few words to tell: its own pace
            (12, low / 8, low / 8 * tts.PART_SPEED[1], True),  # a stalled take: sped up no more than a part may be
            (12, low * 0.75, low * 0.75, False)]  # no mood: its own pace
    assert rows
    for words, made, want, moody in rows:
        with tempfile.TemporaryDirectory() as t:
            e = engine(folder_with_voice(t)); seconds = words / made * 60
            e.model = StandIn(chunks=3, samples=int(seconds * 24000 / 3))
            how = tts.Moody("Sad.") if moody else "Sad."
            audio = e.speak(" ".join(["word"] * words), persona_text=how)
            got = words / (len(audio) / 24000) * 60
            assert abs(got - want) / want < 0.03, (words, made, want, moody, round(got))


@check
def the_stream_chunk_setting_sets_the_chunks_the_model_makes():
    frames = lambda call: int(call["streaming_interval"] * 24000 / 1920)  # as mlx-audio's Breeze counts them
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); e = engine(d); m = e.model
        e.cfg["stream_chunk_s"] = 0.48
        e.speak("One.", persona_text="Sultry.")  # a bake, then the read
        e.speak("Two.", persona_text="", on_chunk=lambda a: None)  # streamed
        assert len(m.calls) == 3 and [frames(c) for c in m.calls] == [round(0.48 / breeze.FRAME_S)] * 3, m.calls
        del e.cfg["stream_chunk_s"]; e.speak("Three.", persona_text="")
        assert frames(m.calls[-1]) == round(breeze.STREAM_S / breeze.FRAME_S)  # none set: the default


@check
def a_stream_chunk_is_whole_frames_within_its_range():
    for value, seconds in [(0.48, 0.48), (1.0, 0.96), (0.5, 0.48), (0, breeze.STREAM_MIN), (9, breeze.STREAM_MAX),
                           ("long", breeze.STREAM_S), (None, breeze.STREAM_S), (float("nan"), breeze.STREAM_S)]:
        assert breeze.chunk_seconds(value) == seconds, (value, breeze.chunk_seconds(value))
    assert breeze.STREAM_MIN <= breeze.STREAM_S <= breeze.STREAM_MAX


@check
def a_new_recording_bakes_again():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); w = d / "ivy.wav"
        before = breeze.baked_path(w, "w", "p", d, "m")
        sf.write(w, np.zeros(48000, np.float32), 24000)
        assert breeze.baked_path(w, "w", "p", d, "m") != before


@check
def a_library_upgrade_bakes_again():
    """mlx or mlx-audio upgraded: the persona clip may sound different, so it is made again; the same
    versions find the clip already made."""
    real = breeze.library_versions
    try:
        with tempfile.TemporaryDirectory() as t:
            w = folder_with_voice(t) / "ivy.wav"
            breeze.library_versions = lambda: "mlx=0.30.0;mlx-audio=0.2.9"
            before = breeze.baked_path(w, "w", "p", t, "m")
            assert breeze.baked_path(w, "w", "p", t, "m") == before
            breeze.library_versions = lambda: "mlx=0.31.0;mlx-audio=0.2.9"
            assert breeze.baked_path(w, "w", "p", t, "m") != before
    finally:
        breeze.library_versions = real
    assert "mlx=" in real() and "mlx-audio=" in real(), real()


@check
def a_stop_leaves_no_half_bake():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); e = engine(d)
        assert not len(e.speak("One.", should_stop=lambda: True, persona_text="Sultry."))
        assert not list((d / breeze.FOLDER).glob("*"))
        assert len(e.model.calls) == 1, "read after a stopped bake"


@check
def a_stop_ends_reading_within_a_chunk():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); e = engine(d); left = [1]
        def stop():
            left[0] -= 1
            return left[0] < 0
        e.model.chunks = 100
        assert not len(e.speak("One.", should_stop=stop, persona_text="")) and e.model.sent == 2


@check
def a_blend_has_no_clip_to_copy():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t)
        (d / "mix.blend.json").write_text(json.dumps({"sources": {"ivy.wav": 1}}))
        try:
            engine(d).speak("One.", voice="mix")
        except tts.VoiceMissing:
            return
        raise AssertionError("a blend was read")


@check
def the_budget_lets_slow_speech_finish_and_stops_a_stall():
    assert breeze.budget("x" * 10000) == breeze.MAX_FRAMES
    slowest = 0.3  # seconds per character: slow Chinese
    for text in ("Hi.", "今天的天气很好", "The train leaves at half past nine, so we should have breakfast."):
        assert slowest * len(text) <= breeze.budget(text) * breeze.FRAME_S and breeze.budget(text) < breeze.MAX_FRAMES, text


@check
def a_voice_given_by_path_keeps_its_clips_beside_it():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t)
        assert breeze.baked_path(d / "ivy.wav", "w", "p", "", "m").parent == d / breeze.FOLDER


@check
def a_bake_that_cannot_be_kept_reads_the_plain_voice():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); (d / breeze.FOLDER).mkdir(); (d / breeze.FOLDER).chmod(0o555)  # read-only
        e = engine(d)
        try:
            assert len(e.speak("One.", persona_text="Sultry.")) and len(e.model.calls) == 1, "worked on a bake it couldn't keep"
            assert e.model.calls[0]["ref_audio"] == str(d / "ivy.wav")
        finally:
            (d / breeze.FOLDER).chmod(0o755)


@check
def a_stalled_or_empty_bake_is_not_kept():
    frames = breeze.budget("Her exact words.")
    for chunks, samples in ((frames, int(24000 * breeze.FRAME_S)), (0, 0)):
        with tempfile.TemporaryDirectory() as t:
            d = folder_with_voice(t); e = engine(d); e.model = StandIn(chunks, samples)
            e.speak("One.", persona_text="Sultry.")
            assert not list((d / breeze.FOLDER).glob("*.wav")) and e.model.calls[-1]["ref_audio"] == str(d / "ivy.wav")


@check
def a_full_disk_while_saving_reads_the_plain_voice():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); e = engine(d); real = sf.write
        def full(*a, **k): raise OSError("no space left")
        sf.write = full
        try:
            assert len(e.speak("One.", persona_text="Sultry.")) and e.model.calls[-1]["ref_audio"] == str(d / "ivy.wav")
        finally:
            sf.write = real


@check
def a_voice_given_by_path_is_read_with_no_voices_folder():
    with tempfile.TemporaryDirectory() as t:
        d = folder_with_voice(t); e = engine("")
        assert len(e.speak("One.", voice=str(d / "ivy.wav"), persona_text="Sultry."))
        assert len(list((d / breeze.FOLDER).glob("*.wav"))) == 1


@check
def a_decoder_that_moved_leaves_the_cache_off():
    assert runner.add_depth_cache(SimpleNamespace()) is False


@check
def the_scan_lists_breeze_as_taking_a_persona():
    with tempfile.TemporaryDirectory() as t:
        m = Path(t) / "b"; m.mkdir()
        (m / "config.json").write_text(json.dumps({"model_type": "breeze_tts", "quantization": {"bits": 4}}))
        (m / "model.safetensors").write_bytes(b"x" * 11_000_000)
        tts.forget_scans()
        found = tts.scan_models(t)
        assert [(f["name"], f["persona"], f["quant"]) for f in found] == [("b", True, "4-bit")], found
        assert tts.takes_persona(m) and breeze.is_breeze_model(m) and not breeze.is_breeze_model(Path(t) / "nope")


@check
def the_scan_names_each_models_family_and_its_bits():
    builds = {  # folder: (config.json, family, quant tag)
        "breeze4": ({"model_type": "breeze_tts", "quantization": {"bits": 4, "group_size": 32}}, "Breeze", "4-bit"),
        "breeze46": ({"model_type": "breeze_tts", "quantization": {"bits": 4, "group_size": 64,
                      "backbone_model.layers.0.mlp.down_proj": {"bits": 6, "group_size": 64},
                      "lm_head": {"bits": 6, "group_size": 64}}}, "Breeze", "4/6-bit"),
        "breezebf16": ({"model_type": "breeze_tts"}, "Breeze", "full size"),
        "qwen8": ({"model_type": "qwen3_tts", "quantization": {"bits": 8}}, "Qwen3-TTS", "8-bit"),
    }
    with tempfile.TemporaryDirectory() as t:
        for name, (cfg, _, _) in builds.items():
            (Path(t) / name).mkdir()
            (Path(t) / name / "config.json").write_text(json.dumps(cfg))
            (Path(t) / name / "model.safetensors").write_bytes(b"x" * 11_000_000)
        tts.forget_scans()
        found = {f["name"]: (f["family"], f["quant"]) for f in tts.scan_models(t)}
    assert found == {name: (family, quant) for name, (_, family, quant) in builds.items()}, found


def real_same_take(e, how):
    """The real model: a read repeats with same_take on, and is a new take with it off."""
    text = "The tea is still warm."
    once, again = e.speak(text, persona_text=how), e.speak(text, persona_text=how)
    assert len(once) and np.array_equal(once, again), "the same take differs"
    e.cfg["same_take"] = False
    once, again = e.speak(text, persona_text=how), e.speak(text, persona_text=how)
    e.cfg["same_take"] = True
    assert not np.array_equal(once, again), "same_take off repeats the take"


def fingerprint(audio, rate=24000):
    """Length, loudness and spectrum shape of a take, and its exact samples' hash."""
    import hashlib
    a = np.asarray(audio, np.float32)
    power = np.abs(np.fft.rfft(a)) ** 2
    freqs = np.fft.rfftfreq(len(a), 1 / rate)
    total = power.sum()
    bands = [10 * np.log10(power[(freqs >= lo) & (freqs < hi)].sum() / total + 1e-12) for lo, hi in zip(BANDS, BANDS[1:])]
    return {"seconds": round(len(a) / rate, 3), "rms_db": round(float(20 * np.log10(np.sqrt((a ** 2).mean()) + 1e-12)), 2),
            "bands_db": [round(float(b), 2) for b in bands], "sha": hashlib.sha256((a * 32767).astype(np.int16).tobytes()).hexdigest(),
            "libraries": breeze.library_versions()}


def real_reference(e, how, make=False):
    """The same take (same_take: a fixed seed) against the one listened to by hand: length within 2 %,
    loudness within 1.5 dB, each band within 3 dB. Bit-for-bit sameness isn't asked of MLX on the
    GPU: different samples only say "re-listen"."""
    take = e.speak(REFERENCE_TEXT, persona_text=how)
    got = fingerprint(take)
    if make:
        REFERENCE.parent.mkdir(exist_ok=True)
        REFERENCE.write_text(json.dumps(got, indent=1) + "\n")
        sf.write(REFERENCE.with_suffix(".wav"), take, 24000, subtype="PCM_16")  # what was listened to
        print(f"reference made with {got['libraries']}: {got['seconds']}s")
        return
    assert REFERENCE.is_file(), f"no reference take: listen, then make one with --make-reference ({REFERENCE})"
    ref = json.loads(REFERENCE.read_text())
    assert abs(got["seconds"] - ref["seconds"]) <= 0.02 * ref["seconds"], (got["seconds"], ref["seconds"], got["libraries"], ref["libraries"])
    assert abs(got["rms_db"] - ref["rms_db"]) <= 1.5, (got["rms_db"], ref["rms_db"], got["libraries"], ref["libraries"])
    worst = max(abs(a - b) for a, b in zip(got["bands_db"], ref["bands_db"]))
    assert worst <= 3.0, (got["bands_db"], ref["bands_db"], got["libraries"], ref["libraries"])
    if got["sha"] != ref["sha"]:
        print(f"reference take: output changed ({ref['libraries']} -> {got['libraries']}) — re-listen")
    else:
        print("reference take: the same")


def real_checks(make_reference=False):
    import mlx.core as mx
    from mlx_audio.tts.utils import load_model
    assert MODEL.is_dir(), f"no model at {MODEL}"
    m = load_model(str(MODEL))
    original = m._depth_tokens
    assert runner.add_depth_cache(m), "the cache failed its own check"
    # same maths as the original: at each codebook the same best token, for the same inputs
    hidden = mx.random.normal((1, m.depth_decoder.model.backbone_hidden_size), key=mx.random.key(3))
    def greedy(fn): return fn(5, hidden, unconditional_hidden=None, cfg_scale=1.0, temperature=0.0, top_p=1.0, top_k=0)
    assert greedy(original) == greedy(m._depth_tokens), "the cache changes the tokens"
    # guided (cfg > 1, a mood or a bake): the same tokens as mlx-audio's two passes, at about the cost of
    # one pass — both rows go through the decoder together (two passes cost ~1.75x a plain frame)
    size = m.depth_decoder.model.backbone_hidden_size
    states = [(mx.random.normal((1, size), key=mx.random.key(10 + i)), mx.random.normal((1, size), key=mx.random.key(50 + i)))
              for i in range(6)]
    assert states
    def guided(fn, h, u): return fn(5, h, unconditional_hidden=u, cfg_scale=breeze.BAKE_CFG, temperature=0.0, top_p=1.0, top_k=0)
    for h, u in states:
        assert guided(original, h, u) == guided(m._depth_tokens, h, u), "guidance changes the tokens"
    def frame_s(guide):
        h, u = states[0]
        t0 = time.perf_counter()
        m._depth_tokens(5, h, unconditional_hidden=u if guide else None, cfg_scale=breeze.BAKE_CFG if guide else 1.0,
                        temperature=0.9, top_p=1.0, top_k=50)
        return time.perf_counter() - t0
    for _ in range(3):  # warm-up
        frame_s(False); frame_s(True)
    plain, guide = [], []
    for _ in range(12):  # interleaved, so a busy moment hits both
        plain.append(frame_s(False)); guide.append(frame_s(True))
    ratio = sorted(guide)[6] / sorted(plain)[6]
    print(f"a guided frame costs {ratio:.2f}x a plain one")
    assert ratio < 1.35, f"a guided frame costs {ratio:.2f}x a plain one"
    # compiled per frame (one wait per frame, not one per codebook): the same tokens as the cached decoder
    # for the same seed, sampled too (so the same takes), and a plain frame at most 0.8x its time
    whole = m._depth_tokens
    m.__dict__.pop("_depth_tokens")
    assert runner.add_depth_cache(m, compiled=False)
    cached = m._depth_tokens
    assert cached is not whole
    def sampled(fn, h, u, seed):
        mx.random.seed(seed)
        return fn(5, h, unconditional_hidden=u, cfg_scale=breeze.BAKE_CFG if u is not None else 1.0, temperature=0.9, top_p=1.0, top_k=50)
    for i, (h, u) in enumerate(states):
        for guide in (None, u):
            assert sampled(whole, h, guide, i) == sampled(cached, h, guide, i), "the compiled decoder changes a take"
    def frame_of(fn):
        h, _ = states[0]
        t0 = time.perf_counter()
        fn(5, h, unconditional_hidden=None, cfg_scale=1.0, temperature=0.9, top_p=1.0, top_k=50)
        return time.perf_counter() - t0
    for _ in range(3):
        frame_of(whole); frame_of(cached)
    w, c = [], []
    for _ in range(12):  # interleaved
        w.append(frame_of(whole)); c.append(frame_of(cached))
    gain = sorted(w)[6] / sorted(c)[6]
    print(f"a compiled frame costs {gain:.2f}x a cached one")
    assert gain < 0.8, f"a compiled frame costs {gain:.2f}x a cached one"
    m.__dict__["_depth_tokens"] = whole
    # a decoder that doesn't add up leaves the original in place
    broken = m.depth_decoder.codebooks_head.weight
    m.__dict__.pop("_depth_tokens")
    m.depth_decoder.codebooks_head.weight = broken * 1.0
    real_next = m.depth_decoder.next_logits
    m.depth_decoder.__dict__["next_logits"] = lambda ids, h: real_next(ids, h) + 1
    assert not runner.add_depth_cache(m) and "_depth_tokens" not in m.__dict__
    m.depth_decoder.__dict__.pop("next_logits")
    del m
    mx.clear_cache()

    voices = Path("~/Documents/Speech/TTS/voices").expanduser()
    with tempfile.TemporaryDirectory() as t:
        for f in ("en-gb-rose.wav", "en-gb-rose.txt"):
            (Path(t) / f).write_bytes((voices / f).read_bytes())
        e = tts.TtsEngine({"model": str(MODEL), "voices_dir": t, "voice": "en-gb-rose"}); e.load()
        how = json.loads((voices / "personas.json").read_text())[PERSONA]  # one that ships in personas.json
        t0 = time.time(); e.speak("Hello.", persona_text=how); print(f"bake + first word {time.time() - t0:.1f}s")
        real_reference(e, how, make_reference)
        if make_reference:
            e.unload()
            return
        real_same_take(e, how)
        # a voice's reference clip goes through the codec's encoder once, not once a sentence (same codes)
        encoded, real_encode = [], e.model.audio_tokenizer.encode
        e.model.audio_tokenizer.encode = lambda *x, **k: encoded.append(1) or real_encode(*x, **k)
        for line in ("One.", "Two.", "Three."):
            e.speak(line, persona_text="")
        e.model.audio_tokenizer.encode = real_encode
        assert len(encoded) <= 1, f"the voice's clip was encoded {len(encoded)} times for 3 sentences"
        long_en = ("When the storm finally passed, the whole village came out to see what was left. Fences lay flat across the lanes, "
                   "and the old oak by the church had lost its largest branch. Nobody said very much at first, but within an hour the "
                   "neighbours were carrying ladders and tools from house to house. By evening the bakery had opened its doors, and the "
                   "smell of fresh bread drifted down the hill. It was, as my grandmother liked to say, the kind of day that reminds you "
                   "who your friends are. The children thought it was the best adventure of their lives, and even the old dog seemed pleased.")
        long_zh = "今天的天气很好，我们下午一起去公园走走吧。路上可以顺便买杯咖啡。公园里的花都开了，孩子们在草地上跑来跑去。傍晚的时候，我们再慢慢走回家，顺便买点菜做晚饭。"
        for name, text in (("English", long_en), ("Chinese", long_zh)):
            pieces = [s for s in re.split(r"(?<=[.。])\s*", text) if s]; total, took, first = 0.0, 0.0, None
            for p in pieces:
                t0 = time.time(); a = e.speak(p, persona_text=how); spent = time.time() - t0
                first = first if first is not None else spent
                took += spent; total += len(a) / 24000
                assert len(a) / 24000 < breeze.budget(p) / 12.0, (p, len(a))
            print(f"{name}: {len(pieces)} sentences, {total:.1f}s of speech in {took:.1f}s = {total / took:.2f}×, first sentence {first:.1f}s", flush=True)
            assert total / took > 1.0, f"{name} doesn't keep up with real time"
        e.unload()


def main():
    p = argparse.ArgumentParser(); p.add_argument("--real", action="store_true")
    p.add_argument("--make-reference", action="store_true", help="with --real: keep this take as the reference (after listening)")
    args = p.parse_args()
    assert CHECKS
    for c in CHECKS:
        c()
    print(f"breeze checks: ok ({len(CHECKS)})")
    if args.real:
        real_checks(args.make_reference)
        print("breeze real checks: ok")


if __name__ == "__main__":
    main()
