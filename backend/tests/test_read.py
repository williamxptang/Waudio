#!/usr/bin/env python3
"""Read aloud without the voice model or speakers (a fake engine and a fake sound stream that takes
real time to play): sentence splitting, levelling, speed change, the reader's pipeline and stops,
two shortcuts in one keyboard listener, new settings reaching an old config.toml, and the app
paths (selection, Esc, dictation stops reading, password field, while dictating).
With --real, also speaks two sentences with the real voice model, and with the persona model if it is built (no sound played).

  python3 backend/tests/test_read.py [--real]
"""
import json, shutil, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from waits import nothing_more_for, wait_until
from waudio import config
tmp = Path(tempfile.mkdtemp()) / "config.toml"; shutil.copy(config.EXAMPLE, tmp); config.PATH = tmp
from waudio.engine import sentences, tts
from waudio.engine.models import mlx_audio_tts as runner  # the MLX voice-model runner (this Mac's)
from waudio.input.keyboard import Keyboard
from waudio.output import player as player_module
from waudio.output.player import Player
from waudio import reader as reader_module
from waudio.reader import Reader

RATE = 24000

# ---------- sentences ----------
S = sentences.split
assert S("Hello there. How are you? I am fine!  Thanks.") == ["Hello there.", "How are you?", "I am fine!", "Thanks."]
assert S("你好。今天天气很好！我们去公园吧？好的。") == ["你好。", "今天天气很好！", "我们去公园吧？", "好的。"]
assert S("Mixed 中文 text. 然后是中文句子。And back to English.") == ["Mixed 中文 text.", "然后是中文句子。", "And back to English."]
assert S('He said "stop." Then he left.') == ['He said "stop."', "Then he left."]
assert S("Mr. Smith went to Washington. He arrived at 3.30 p.m. on Monday.") == \
    ["Mr. Smith went to Washington.", "He arrived at 3.30 p.m. on Monday."]
assert S("J. R. R. Tolkien wrote books. Version 2.5 is out.") == ["J. R. R. Tolkien wrote books.", "Version 2.5 is out."]
assert S("- first item\n- second item\n\n1. numbered one") == ["first item", "second item", "numbered one"]
assert S("「你好。」他说。") == ["「你好。」", "他说。"]
assert S("***  ---  ###") == [] and S("") == [] and S(None) == []
long = S("A " + "very long clause, " * 20 + "end.")
assert len(long) > 1 and len(long[0]) <= sentences.FIRST_MAX and all(len(p) <= sentences.MAX_CHARS for p in long)
assert all(len(p) <= sentences.MAX_CHARS for p in S("这" * 500))
# how each piece ends (the pause after it): the same pieces as split, each with its end
E = sentences.split_ends
for text, ends in [
        ("Hello there. How are you?", ["sentence", "sentence"]),
        ('He said "stop." Then', ["sentence", None]),
        ("First para.\n\n- an item\n- last item", ["paragraph", "paragraph", None]),
        ("「你好。」他说，然后走了；\n\n第二段", ["sentence", "paragraph", None]),
        ("A " + "very long clause, " * 20 + "end.", ["clause", "clause", "sentence"])]:
    assert E(text) == list(zip(S(text), ends)), (text, E(text))

# ---------- level, stretch ----------
quiet = (np.sin(np.arange(RATE) / 7) * 0.01).astype(np.float32)
lv = tts.level(quiet)
assert abs(20 * np.log10(np.sqrt(np.mean(lv ** 2))) - tts.LEVEL_DB) < 0.5 and np.abs(lv).max() <= 10 ** (tts.PEAK_DB / 20) + 1e-6
assert not tts.level(np.zeros(100, np.float32)).any()
lim = 10 ** (tts.PEAK_DB / 20); ramp = np.linspace(-2, 2, 4001).astype(np.float32); keep = ramp.copy()
sl = tts.soft_limit(ramp)
assert np.array_equal(ramp, keep) and np.all(np.diff(sl) >= 0) and np.abs(sl).max() <= lim  # no mutation, monotonic, bounded
assert np.array_equal(sl[np.abs(ramp) <= lim * 0.9], ramp[np.abs(ramp) <= lim * 0.9])     # quiet samples untouched
speech_like = np.random.default_rng(0).normal(0, 0.01, RATE).astype(np.float32)  # peaks ~4-5x its average, like speech
lsl = tts.level(speech_like)
assert abs(20 * np.log10(np.sqrt(np.mean(lsl ** 2))) - tts.LEVEL_DB) < 0.6 and np.abs(lsl).max() <= 10 ** (tts.PEAK_DB / 20) + 1e-6  # peaks rounded, loudness kept
spiky = np.zeros(RATE, np.float32); spiky[100] = 0.5; spiky[200:] = 0.001
assert np.abs(tts.level(spiky)).max() <= 10 ** (tts.PEAK_DB / 20) + 1e-6
for sp in (0.8, 1.25, 1.5):
    out = tts.stretch(quiet, sp, RATE)
    assert abs(len(out) / RATE - 1 / sp) < 0.05, (sp, len(out) / RATE)  # within one window
assert tts.stretch(quiet, 1.0, RATE) is not None and len(tts.stretch(quiet, 1.0, RATE)) == RATE

# ---------- volume: 0.5 = as levelled, 1.0 = twice; the limiter keeps peaks under -1 dBFS ----------
from waudio.output.player import loudness, LIMIT
lv16 = tts.level((np.sin(np.arange(RATE) / 7) * 0.01).astype(np.float32))
db = lambda x: 20 * np.log10(np.sqrt(np.mean(x ** 2)))
assert abs(db(loudness(lv16, 0.5)) - tts.LEVEL_DB) < 0.1 and abs(db(loudness(lv16, 1.0)) - tts.LEVEL_DB - 6.02) < 0.3
peaky = np.zeros(RATE, np.float32); peaky[::50] = 0.89  # a peak at -1 dBFS everywhere
assert np.abs(loudness(peaky, 1.0)).max() <= LIMIT + 1e-6 and np.abs(loudness(peaky, 0.5)).max() <= LIMIT + 1e-6

# ---------- voices ----------
vd = Path(tempfile.mkdtemp())
(vd / "calm.wav").write_bytes(b"RIFF"); (vd / "calm.txt").write_text("what it says\n")
(vd / "nowords.wav").write_bytes(b"RIFF")
assert list(tts.voices(vd)) == ["calm"] and tts.find_voice("calm", vd)[1] == "what it says"
assert tts.find_voice(str(vd / "calm.wav"), "/nowhere") and not tts.find_voice("nowords", vd)
assert not tts.find_voice("missing", vd)
(vd / "voices.json").write_text('{"calm": "British English, woman"}')
assert tts.voice_labels(vd) == {"calm": "British English, woman"} and tts.voice_labels("") == {}
(vd / "voices.json").write_text("not json"); assert tts.voice_labels(vd) == {}
(vd / "voices.json").write_text('{"calm": null}'); assert tts.voice_labels(vd) == {}
(vd / "voices.json").unlink(); assert tts.voice_labels(vd) == {}
# the list follows voices.json's order; a voice it doesn't name comes after, by name; a name with no voice is left out
od = Path(tempfile.mkdtemp())
for n in ("[F][Human][Rose]", "[M][Orc][Grok]", "mine-20261103-101500", "[F][Merfolk][Wenna]"):
    (od / f"{n}.wav").write_bytes(b"RIFF"); (od / f"{n}.txt").write_text("words")
assert tts.voice_order(od) == sorted(tts.voices(od))                              # no voices.json: by name
(od / "voices.json").write_text('{"[F][Merfolk][Wenna]": "W", "gone": "G", "[M][Orc][Grok]": "O", "[F][Human][Rose]": "R"}')
assert tts.voice_order(od) == ["[F][Merfolk][Wenna]", "[M][Orc][Grok]", "[F][Human][Rose]", "mine-20261103-101500"]
# a mix of recordings (<name>.blend.json): listed, weights add up to 1, wins over <name>.wav, broken ones skipped
import soundfile as sf
(vd / "src").mkdir(); sf.write(vd / "src" / "a.wav", np.zeros(2400, np.float32), 24000); sf.write(vd / "src" / "b.wav", np.zeros(4800, np.float32), 24000)
(vd / "calm.blend.json").write_text('{"sources": {"src/a.wav": 1, "src/b.wav": 3}}')
(vd / "bad.blend.json").write_text('{"sources": {"src/a.wav": 1, "src/gone.wav": 1}}')
(vd / "neg.blend.json").write_text('{"sources": {"src/a.wav": -1}}'); (vd / "junk.blend.json").write_text("[1]")
for bad in ('{"sources": {"src/a.wav": 1e999}}', '{"sources": {"src/a.wav": 1%s}}' % ("0" * 400), '{"sources": {"src/a.wav": true}}'):
    (vd / "odd.blend.json").write_text(bad); assert "odd" not in tts.voices(vd), bad  # never crashes the list
(vd / "odd.blend.json").unlink()
v = tts.voices(vd); assert list(v) == ["calm"] and v["calm"][1] is None, v  # the mix wins over calm.wav
wav, words, blend = tts.find_voice("calm", vd)
assert words is None and wav.endswith("a.wav") and [round(w, 2) for _, w in blend] == [0.25, 0.75], blend
class FakeTTS:  # generate() asks for the speaker fingerprint, as Qwen3-TTS does
    sample_rate = 24000
    def __init__(self): self.used = []
    def extract_speaker_embedding(self, audio, sr=24000): return float(audio.shape[0])
    def generate(self, text, ref_audio, ref_text, lang_code):
        self.used.append((ref_text, self.extract_speaker_embedding(np.zeros(10))))
        yield type("R", (), {"audio": np.ones(2400, np.float32) * 0.1})()
eng = tts.TtsEngine({"model": "fake", "voices_dir": str(vd), "voice": "calm", "speed": 1.0})
eng.model, eng.runner, eng.loaded_model = FakeTTS(), runner, "fake"
assert len(eng.speak("Hello.")) and eng.model.used[-1] == (None, 0.25 * 2400 + 0.75 * 4800), eng.model.used
assert "extract_speaker_embedding" not in vars(eng.model) and len(eng.fingerprints) == 2  # the model's own is back
(vd / "plain.wav").write_bytes(b"RIFF"); (vd / "plain.txt").write_text("words")
eng.speak("Hi.", voice="plain"); assert eng.model.used[-1] == ("words", 10.0)  # a plain preset: its own recording
eng.speak("Again."); assert len(eng.fingerprints) == 2  # worked out once
def boom(**kw): eng.model.extract_speaker_embedding(None); raise RuntimeError("model failed")
eng.model.generate = boom
try: eng.speak("Hello.")
except RuntimeError: pass
assert "extract_speaker_embedding" not in vars(eng.model)  # restored after a failure too
eng.unload(); assert eng.model is None and eng.fingerprints == {}  # unloading drops the fingerprints
for f in ("calm.blend.json", "bad.blend.json", "neg.blend.json", "junk.blend.json", "plain.wav", "plain.txt"): (vd / f).unlink()

# ---------- same take: a Qwen read repeats, or with same_take off is a new take ----------
import mlx.core as mx
class DrawsTTS:  # draws from the global random generator, as Qwen3-TTS samples
    sample_rate = 24000
    def generate(self, text, ref_audio, ref_text, lang_code):
        yield type("R", (), {"audio": np.array(mx.random.uniform(shape=(2400,)), np.float32)})()
def draws_engine(**cfg):
    e = tts.TtsEngine({"model": "fake", "voices_dir": str(vd), "voice": "take", "speed": 1.0, **cfg})
    e.model, e.runner, e.loaded_model = DrawsTTS(), runner, "fake"
    return e
(vd / "take.wav").write_bytes(b"RIFF"); (vd / "take.txt").write_text("words")
e = draws_engine()
one, two = e.speak("One."), e.speak("One.")
assert len(one) and np.array_equal(one, two)
e = draws_engine(same_take=False)
one, two = e.speak("One."), e.speak("One.")
assert len(one) and not np.array_equal(one, two)
# a part's own speed stretches it on top of the reading speed, on a model that makes it whole (not Breeze)
class SecondTTS:  # a second of speech per piece
    sample_rate = 24000
    def generate(self, text, ref_audio, ref_text, lang_code):
        yield type("R", (), {"audio": (np.sin(np.arange(24000) / 7) * 0.2).astype(np.float32)})()
for reading_speed in (1.0, 1.25):
    e = draws_engine(speed=reading_speed); e.model = SecondTTS()
    whole, faster = len(e.speak("One.")), len(e.speak("One.", speed=2.0))
    assert abs(faster - whole / 2) < 0.05 * whole, (reading_speed, whole, faster)
for f in ("take.wav", "take.txt"): (vd / f).unlink()

# ---------- persona: a persona model takes the voice's fingerprint + how it speaks ----------
from waudio.engine import persona
sf.write(vd / "solo.wav", np.zeros(7200, np.float32), 24000); (vd / "solo.txt").write_text("its words")
(vd / "calm.blend.json").write_text('{"sources": {"src/a.wav": 1, "src/b.wav": 3}}')
assert tts.persona_path("calm", vd) == vd / "calm.persona.txt" and tts.persona_path("nobody", vd) is None
assert tts.persona_path(str(vd / "solo.wav"), "") == vd / "solo.persona.txt"  # a voice given as a .wav path
assert tts.read_persona("calm", vd) == ""  # none yet
(vd / "calm.persona.txt").write_text("  British,\n soft  and\tslow.\n")
assert tts.read_persona("calm", vd) == "British, soft and slow."  # one line
(vd / "solo.persona.txt").write_text("x" * 900); assert len(tts.read_persona("solo", vd)) == tts.PERSONA_MAX
(vd / "solo.persona.txt").write_bytes(b"\xff\xfe\x00bad"); assert tts.read_persona("solo", vd) == ""  # never a crash
(vd / "solo.persona.txt").unlink()

# persona presets: the built-in list, or personas.json in the voices folder (its order, one line each, capped)
built_in = [(n, t, 1.0) for n, t in tts.PERSONA_PRESETS]  # at their own pace
assert tts.persona_presets(vd) == built_in and tts.persona_presets("") == built_in
assert all(len(t) <= tts.PERSONA_MAX and "normal speaking speed" in t for _, t in tts.PERSONA_PRESETS)
(vd / "personas.json").write_text(json.dumps({"Zed": "  Very\n happy. ", "Alpha": "Calm.", "bad": 3, "": "x", "Long": "y" * 900}))
assert [n for n, *_ in tts.persona_presets(vd)] == ["Zed", "Alpha", "Long"] and tts.persona_presets(vd)[0][1] == "Very happy."
assert len(tts.persona_presets(vd)[2][1]) == tts.PERSONA_MAX
for junk in ("[1, 2]", "{nope", '{"x": 1}', '{"x": {"speed": 1.2}}'):  # nothing usable: the built-in list
    (vd / "personas.json").write_text(junk); assert tts.persona_presets(vd) == built_in, junk
# a preset may carry a speed: speed variants of one persona; a speed outside PART_SPEED (or not a number) is 1.0
low, high = tts.PART_SPEED
(vd / "personas.json").write_text(json.dumps({"Narrator": "Calm.", "Narrator slow": {"text": "Calm.", "speed": 0.85},
                                              "Edge": {"text": "E.", "speed": high}, "Wild": {"text": "W.", "speed": high + 0.01},
                                              "Yes": {"text": "Y.", "speed": True}, "Low": {"text": "L.", "speed": low - 0.01}}))
assert tts.persona_presets(vd) == [("Narrator", "Calm.", 1.0), ("Narrator slow", "Calm.", 0.85), ("Edge", "E.", high),
                                   ("Wild", "W.", 1.0), ("Yes", "Y.", 1.0), ("Low", "L.", 1.0)], tts.persona_presets(vd)
(vd / "personas.json").unlink()

class FakePersona:  # CustomVoice-shaped: a built-in speaker name, an instruction; the fingerprint via ref_audio
    sample_rate = 24000
    config = type("C", (), {"talker_config": type("T", (), {"spk_is_dialect": {"eric": "sichuan_dialect"}})()})()
    def __init__(self): self.used = []
    def get_supported_speakers(self): return ["eric", "serena", "ryan"]
    def extract_speaker_embedding(self, audio, sr=24000): return float(audio.shape[0])
    def _prepare_generation_inputs(self, text, language="auto", speaker=None, ref_audio=None, ref_text=None, instruct=None):
        return self.extract_speaker_embedding(ref_audio) if ref_audio is not None else speaker
    def generate(self, text, voice, instruct, lang_code):
        self.used.append((self._prepare_generation_inputs(text=text, speaker=voice, instruct=instruct), voice, instruct, lang_code))
        yield type("R", (), {"audio": np.ones(2400, np.float32) * 0.1})()
assert persona.placeholder_speaker(FakePersona()) == "serena"  # never a dialect speaker (it would switch the language)
eng = tts.TtsEngine({"model": "fake", "voices_dir": str(vd), "voice": "calm", "speed": 1.0, "language": "english"})
eng.model, eng.runner, eng.loaded_model, eng.takes_persona = FakePersona(), runner, "fake", True
assert len(eng.speak("Hello."))
assert eng.model.used[-1] == (0.25 * 2400 + 0.75 * 4800, "serena", "British, soft and slow.", "english"), eng.model.used
eng.speak("Hi.", voice="solo"); assert eng.model.used[-1][:3] == (7200.0, "serena", None)  # its own recording; no persona
eng.speak("Hi.", persona_text="  Warm,\n  clear. "); assert eng.model.used[-1][2] == "Warm, clear."  # instead of the voice's own
eng.speak("Hi.", persona_text=""); assert eng.model.used[-1][2] is None  # none at all
eng.speak("Hi.", voice="solo", persona_text="x" * 900); assert len(eng.model.used[-1][2]) == tts.INSTRUCT_MAX
assert {"extract_speaker_embedding", "_prepare_generation_inputs"}.isdisjoint(vars(eng.model))  # the model's own are back
# the voice's own persona reads at its own speed (<name>.speed.txt), on top of the part's; a persona given
# instead of it does not; a speed file that isn't a number in PART_SPEED counts as none
plain_len = len(eng.speak("Hello."))
(vd / "calm.speed.txt").write_text("1.25\n")
assert tts.persona_speed("calm", vd) == 1.25 and tts.speed_path("calm", vd) == vd / "calm.speed.txt"
own = "British, soft and slow."
assert len(eng.speak("Hello.")) == len(eng.speak("Hello.", persona_text=own, speed=1.25)) < plain_len
assert len(eng.speak("Hello.", speed=1.2)) == len(eng.speak("Hello.", persona_text=own, speed=1.5))  # 1.25 × 1.2
assert len(eng.speak("Hello.", persona_text="Warm.")) == plain_len
for junk in ("fast", "9", ""):
    (vd / "calm.speed.txt").write_text(junk); assert tts.persona_speed("calm", vd) == 1.0, junk
(vd / "calm.speed.txt").write_text("1.25"); persona_text = (vd / "calm.persona.txt").read_text()
(vd / "calm.persona.txt").unlink()  # a speed left behind without its persona counts for nothing
assert tts.persona_speed("calm", vd) == 1.0 and len(eng.speak("Hello.", voice="calm")) == len(eng.speak("Hello.", voice="calm", persona_text=""))
(vd / "calm.persona.txt").write_text(persona_text); (vd / "calm.speed.txt").unlink()
assert tts.persona_speed("calm", vd) == 1.0 and tts.persona_speed("nobody", vd) == 1.0
def boom(**kw): eng.model._prepare_generation_inputs("x", ref_audio=np.zeros(1)); raise RuntimeError("model failed")
eng.model.generate = boom
try: eng.speak("Hello.")
except RuntimeError: pass
assert {"extract_speaker_embedding", "_prepare_generation_inputs"}.isdisjoint(vars(eng.model))  # also after a failure
eng.unload(); assert eng.takes_persona is False and eng.fingerprints == {}
for f in ("calm.blend.json", "calm.persona.txt", "solo.wav", "solo.txt"): (vd / f).unlink()

# the model list: Base and persona models only (CustomVoice / VoiceDesign have no way to copy a voice)
md = Path(tempfile.mkdtemp())
for name, kind, marker in (("b", "base", False), ("cv", "custom_voice", False), ("vd", "voice_design", False),
                           ("p", "custom_voice", True), ("old", None, False), ("p_old", "custom_voice", "old")):
    (md / name).mkdir()
    (md / name / "config.json").write_text(json.dumps({"model_type": "qwen3_tts", **({"tts_model_type": kind} if kind else {})}))
    with open(md / name / "model.safetensors", "wb") as f: f.truncate(11_000_000)  # sparse: big enough to count
    if marker: (md / name / (persona.OLD_MARKER if marker == "old" else persona.MARKER)).write_text("{}")
tts.forget_scans()
assert {m["name"]: m["persona"] for m in tts.scan_models(md)} == {"b": False, "old": False, "p": True, "p_old": True}  # built before the rename: still one
import struct  # an MLX build says so in its weights' header; an original says nothing (the Model page greys it)
head = json.dumps({"__metadata__": {"format": "mlx"}}).encode()
with open(md / "p" / "model.safetensors", "wb") as f: f.write(struct.pack("<Q", len(head)) + head); f.truncate(11_000_000)
tts.forget_scans()
assert {m["name"]: m["format"] for m in tts.scan_models(md)} == {"b": None, "old": None, "p": "mlx", "p_old": None}
assert persona.is_persona_model(md / "p") and not persona.is_persona_model(md / "cv") and not persona.is_persona_model("")

# building a persona model: Base's text input, sound-token rows and pad/bos/eos go into CustomVoice; the encoder beside it
import mlx.core as mx
bd = Path(tempfile.mkdtemp())
def fake_model(name, kind, k):
    d = bd / name; (d / "speech_tokenizer").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"model_type": "qwen3_tts", "tts_model_type": kind, "tts_pad_token_id": 5,
        "tts_bos_token_id": 6, "tts_eos_token_id": 7, "talker_config": {"hidden_size": 4, "spk_id": {"eric": 3000, "Serena": 3001}},
        "speaker_encoder_config": {"enc_dim": 4, "sample_rate": 24000}}))
    (d / "speech_tokenizer" / "model.safetensors").write_bytes(b"tokenizer")
    w = {"talker.text_projection.linear_fc1.weight": mx.full((4, 4), k), "talker.layers.0.weight": mx.full((4, 4), k),
         "talker.model.codec_embedding.weight": mx.full((persona.SOUND_ROWS + 3, 4), k),
         "talker.model.text_embedding.weight": mx.full((10, 4), k)}
    if kind == "base": w["speaker_encoder.fc.weight"] = mx.full((2, 2), k)
    mx.save_safetensors(str(d / "model.safetensors"), w)
    return d
cvd, based = fake_model("cv", "custom_voice", 1.0), fake_model("base", "base", 2.0)
out = persona.build(cvd, based, bd / "persona")
w = mx.load(str(out / "model.safetensors"))
assert float(w["talker.text_projection.linear_fc1.weight"].max()) == 2 and float(w["talker.layers.0.weight"].max()) == 1
ce = w["talker.model.codec_embedding.weight"]
assert float(ce[:persona.SOUND_ROWS].min()) == 2 and float(ce[persona.SOUND_ROWS:].max()) == 1  # control/speaker rows stay
te = w["talker.model.text_embedding.weight"]
assert [float(te[i, 0]) for i in range(10)] == [1, 1, 1, 1, 1, 2, 2, 2, 1, 1]  # only pad / bos / eos
assert "speaker_encoder.fc.weight" not in w and list(mx.load(str(out / persona.ENCODER))) == ["fc.weight"]
assert (out / "speech_tokenizer" / "model.safetensors").read_bytes() == b"tokenizer"  # the tokenizer's own weights are kept
assert persona.is_persona_model(out) and not any(p.name.endswith(".partial") for p in bd.iterdir())
# a persona model built before the rename (its marker under the old name) loads like a new one: its
# speaker encoder (a real one, saved where the build puts it) comes back with the same weights
from mlx.utils import tree_flatten
from mlx_audio.tts.models.qwen3_tts.config import Qwen3TTSSpeakerEncoderConfig
from mlx_audio.tts.models.qwen3_tts.speaker_encoder import Qwen3TTSSpeakerEncoder
old = Path(tempfile.mkdtemp())
(old / persona.ENCODER).parent.mkdir()
enc = Qwen3TTSSpeakerEncoder(Qwen3TTSSpeakerEncoderConfig(enc_dim=4, sample_rate=24000))
mx.save_safetensors(str(old / persona.ENCODER), dict(tree_flatten(enc.parameters())))
(old / persona.OLD_MARKER).write_text(json.dumps({"speaker_encoder_config": {"enc_dim": 4, "sample_rate": 24000}}))
back = dict(tree_flatten(runner.load_speaker_encoder(old).parameters()))
assert back and all(mx.array_equal(back[k], v).item() for k, v in tree_flatten(enc.parameters()))
for args in ((based, cvd, bd / "x"), (cvd, based, out)):  # wrong order; an existing folder is never overwritten
    try: persona.build(*args); raise AssertionError(args)
    except SystemExit: pass

# CustomVoice's own three parts are kept, so its built-in voices still sound like CustomVoice
own = mx.load(str(out / persona.CUSTOMVOICE))
assert sorted(own) == ["codec_embedding_rows", "text_embedding_rows", "text_projection.linear_fc1.weight"], sorted(own)
assert all(float(v.min()) == float(v.max()) == 1 for v in own.values())  # CustomVoice's (1), not the graft's (2)
assert persona.builtin_speakers(out) == ["eric", "serena"] and persona.builtin_speakers(cvd) == []
assert tts.builtin_voices(out) == [("builtin:serena", tts.BUILTIN_LABELS["serena"]), ("builtin:eric", tts.BUILTIN_LABELS["eric"])]
(out / persona.CUSTOMVOICE).rename(out / "x"); assert persona.builtin_speakers(out) == []  # built before this: none
(out / "x").rename(out / persona.CUSTOMVOICE)

# Layers: one model in memory, the right three parts swapped in before each sentence
import mlx.nn as nn
class Proj(nn.Module):
    def __init__(self): super().__init__(); self.linear_fc1 = nn.Linear(4, 4, bias=False)
fm = type("M", (), {})(); fm.config = type("C", (), {"tts_pad_token_id": 5, "tts_bos_token_id": 6, "tts_eos_token_id": 7})()
fm.talker = type("T", (), {})(); fm.talker.text_projection = Proj(); fm.talker.model = type("TM", (), {})()
fm.talker.text_projection.linear_fc1.weight = w["talker.text_projection.linear_fc1.weight"]
fm.talker.model.codec_embedding = nn.Embedding(persona.SOUND_ROWS + 3, 4); fm.talker.model.codec_embedding.weight = ce * 1
fm.talker.model.text_embedding = nn.Embedding(10, 4); fm.talker.model.text_embedding.weight = te * 1
lay = runner.Layers(fm, out)
def parts():
    t = fm.talker
    return (float(t.text_projection.linear_fc1.weight.max()), float(t.model.codec_embedding.weight[:persona.SOUND_ROWS].max()),
            float(t.model.codec_embedding.weight[persona.SOUND_ROWS:].max()), [float(t.model.text_embedding.weight[i, 0]) for i in range(10)])
grafted = ([2.0, 2.0, 1.0, [1, 1, 1, 1, 1, 2, 2, 2, 1, 1]])
assert list(parts()) == grafted
lay.use("builtin"); assert list(parts()) == [1.0, 1.0, 1.0, [1] * 10], parts()  # exactly CustomVoice again
lay.use("copied"); assert list(parts()) == grafted, parts()                       # and back, exactly
lay.use("copied"); assert lay.now == "copied"
real_load = fm.talker.text_projection.load_weights  # a swap that fails half way is redone in full next time
fm.talker.text_projection.load_weights = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mlx"))
try: lay.use("builtin"); raise AssertionError("no error")
except RuntimeError: pass
del fm.talker.text_projection.load_weights
lay.use("copied"); assert list(parts()) == grafted and lay.now == "copied", parts()

# built-in voices: found only for a persona model that has them; a persona each; spoken with CustomVoice's parts
assert tts.find_voice("builtin:serena", vd, out) == ("builtin:serena", None, None)
assert tts.find_voice("builtin:serena", vd, "") is None and tts.find_voice("builtin:nobody", vd, out) is None
assert tts.persona_path("builtin:serena", vd) == vd / "builtin-serena.persona.txt"
assert tts.persona_path("builtin:../x", vd) is None and tts.persona_path("builtin:serena", "") is None
(vd / "builtin-serena.persona.txt").write_text("Very happy.\n")
class FakeLayers:
    def __init__(self): self.used = []
    def use(self, kind): self.used.append(kind)
eng = tts.TtsEngine({"model": str(out), "voices_dir": str(vd), "voice": "builtin:serena", "speed": 1.0, "language": "english"})
eng.model, eng.runner, eng.loaded_model, eng.takes_persona, eng.layers = FakePersona(), runner, str(out), True, FakeLayers()
assert len(eng.speak("Hello."))
assert eng.model.used[-1] == ("serena", "serena", "Very happy.", "english"), eng.model.used  # its own speaker slot, no fingerprint
sf.write(vd / "solo.wav", np.zeros(7200, np.float32), 24000); (vd / "solo.txt").write_text("its words")
eng.speak("Hi.", voice="solo"); assert eng.model.used[-1][:3] == (7200.0, "serena", None)  # a copied voice
assert eng.layers.used == ["builtin", "copied"], eng.layers.used
eng.layers = None  # a model without CustomVoice's parts can't speak a built-in voice
try: eng.speak("Hello."); raise AssertionError("spoke without the layers")
except tts.VoiceMissing: pass
for f in ("builtin-serena.persona.txt", "solo.wav", "solo.txt"): (vd / f).unlink()


# ---------- reader pipeline with fakes ----------
def device_takes(block):
    """As sounddevice's float32 OutputStream does: any other kind of sample is refused (TypeError)."""
    if np.asarray(block).dtype != np.float32:
        raise TypeError(f"dtype mismatch: {np.asarray(block).dtype!r} vs 'float32'")


class FakeStream:
    """Plays in real time: write() takes as long as the block lasts."""
    log = []
    def __init__(self, rate): self.rate = rate
    def start(self): FakeStream.log.append("start")
    def write(self, block): device_takes(block); FakeStream.log.append(len(block)); time.sleep(len(block) / self.rate)
    def stop(self): FakeStream.log.append("stop")
    def abort(self): FakeStream.log.append("abort")
    def close(self): pass


class FakeEngine:
    loaded, loaded_model, sample_rate = False, None, RATE
    spoken = []
    missing = False
    def __init__(self, cfg): self.cfg = cfg
    def load(self): self.loaded, self.loaded_model = True, self.cfg["model"]; FakeEngine.loads += 1
    def unload(self): self.loaded = False
    def voice(self, name=None): return None if self.missing or name == "nobody" else ("v.wav", "words")
    voices_used = []
    personas_used = []
    speeds_used = []
    made_at = {}  # piece -> when it was made
    loads = 0
    def speak(self, text, should_stop=lambda: False, voice=None, persona_text=None, speed=1.0):
        FakeEngine.voices_used.append((text, voice))
        FakeEngine.personas_used.append((text, persona_text))
        FakeEngine.speeds_used.append((text, speed))
        if self.missing:
            raise tts.VoiceMissing("nobody")
        time.sleep(0.05)  # making speech takes a moment (much less than playing it)
        FakeEngine.spoken.append(text); FakeEngine.made_at[text] = time.monotonic()
        return np.full(int(0.3 * RATE), 0.1, np.float32)  # 0.3 s per piece


player = lambda rate, vol, on_piece=None, on_done=None: Player(rate, vol, on_piece, on_done, stream_factory=FakeStream)
huds, errors, changes = [], [], []
shown_at = {}  # piece -> when it started playing (the pill shows it)
def hud(s, d=""):
    huds.append((s, d))
    if s == "reading" and d:
        shown_at.setdefault(d, time.monotonic())
cfg = {"model": "m", "volume": 0.5, "unload_after_minutes": 1}
eng = FakeEngine(cfg)
messages = []  # what each error says
r = Reader(cfg, hud=hud, on_change=lambda: changes.append(1),
           on_error=lambda k, m: (errors.append(k), messages.append(m)), player_factory=player, engine=eng)

# reads every piece in order, pill shows each, ends by itself
assert r.read("One. Two. Three.")
wait_until(lambda: not r.active, what="the reading to end")
assert not r.active and FakeEngine.spoken == ["Hello.", "One.", "Two.", "Three."], FakeEngine.spoken  # warm-up after load
shown = [d for s, d in huds if s == "reading" and d]
assert shown == ["One.", "Two.", "Three."] and huds[-1] == ("hide", ""), huds
assert FakeStream.log[0] == "start" and FakeStream.log[-1] == "stop" and "abort" not in FakeStream.log
played = sum(x for x in FakeStream.log if isinstance(x, int)) / RATE
ending = player_module.FADE_S + player_module.END_S  # the voice stops on a loud sample: faded, then the end's silence
assert abs(played - (0.9 + 2 * r.pause_after("sentence") + ending)) < 0.01, played  # three pieces, a pause between each
# each piece is made while the one before it plays (0.3 s each): ready before that one ends
assert all(FakeEngine.made_at[b] < shown_at[a] + 0.3 for a, b in (("One.", "Two."), ("Two.", "Three."))), (FakeEngine.made_at, shown_at)

# money shorthand goes to the voice model written out; the pill shows it as written
FakeEngine.spoken.clear(); huds.clear()
assert r.read("It was a $1bn firm.")
wait_until(lambda: not r.active, what="the reading to end")
assert FakeEngine.spoken == ["It was a 1 billion dollars firm."], FakeEngine.spoken
assert [d for s, d in huds if s == "reading" and d] == ["It was a $1bn firm."], huds

# stop mid-way: sound stops within a block, nothing more is made or played
FakeEngine.spoken.clear(); FakeStream.log.clear(); huds.clear()
r.read("A " + ". ".join(f"Sentence {i}" for i in range(20)) + ".")
wait_until(lambda: sum(isinstance(x, int) for x in FakeStream.log) >= 3, what="the reading to be playing")
before = len(FakeStream.log); t = time.monotonic(); r.stop(); stop_s = time.monotonic() - t
writes = len(FakeStream.log); nothing_more_for(0.4)
assert stop_s < 0.2 and len(FakeStream.log) <= writes + 2 and "abort" in FakeStream.log, (stop_s, FakeStream.log[-5:])
after_stop = sum(x for x in FakeStream.log[before:] if isinstance(x, int))
assert after_stop <= RATE * player_module.BLOCK_S, after_stop  # the block in flight at most: no end silence added
assert not r.active and len(FakeEngine.spoken) < 20 and huds[-1][0] == "hide"

# a new reading right after a stop plays only the new text
FakeEngine.spoken.clear(); huds.clear()
r.read("First text. More of it. Even more."); wait_until(lambda: any(s == "reading" and d for s, d in huds), what="the first text showing")
r.read("Second text.")
wait_until(lambda: not r.active, timeout=3, what="the second reading to end")
shown = [d for s, d in huds if s == "reading" and d]
assert shown[-1] == "Second text." and "Even more." not in shown, shown

# ---------- pauses: after each piece, the quiet a speaker leaves for how it ends ----------
class Tape(FakeStream):
    """Keeps every sample played (no waiting: these checks are about what is played, not when)."""
    played = []
    def write(self, block): device_takes(block); Tape.played.append(np.array(block).ravel())


def gaps(sound):
    """The runs of silence between sounds, and the silence after the last, in samples."""
    edges = np.flatnonzero(np.diff((sound != 0).astype(np.int8)))  # where sound starts or stops
    starts, stops = edges[1::2] + 1, edges[0::2] + 1  # the tape starts with sound
    return [int(b - a) for a, b in zip(stops, starts)], len(sound) - int(stops[-1]) if len(stops) > len(starts) else 0


def read_on_tape(engine, text, **settings):
    Tape.played = []; failed = []
    rt = Reader({"model": "m", "volume": 0.5, **settings}, engine=engine, on_error=lambda kind, message: failed.append(message),
                player_factory=lambda rate, vol, on_piece=None, on_done=None:
                    Player(rate, vol, on_piece, on_done, stream_factory=Tape))
    assert rt.read_parts(text) if isinstance(text, list) else rt.read(text)  # a list: [(text, voice, persona)]
    wait_until(lambda: not rt.active, what="the reading to end")
    assert not failed, failed  # played to its end, the end's silence too
    return rt, np.concatenate(Tape.played)

class Trailing(FakeEngine):  # each piece is 0.3 s of voice, then the quiet this many seconds long
    def __init__(self, cfg, quiet): super().__init__(cfg); self.quiet = quiet
    def speak(self, text, should_stop=lambda: False, voice=None, persona_text=None, on_chunk=None):
        return np.concatenate([np.full(int(0.3 * RATE), 0.1, np.float32), np.zeros(int(self.quiet * RATE), np.float32)])

# a sentence, a clause and a paragraph each get their own pause; nothing after the last piece
text = "A " + "long clause, " * 12 + "end. Next one.\n\nNew paragraph. Last."
rt, sound = read_on_tape(Trailing(cfg, 0), text)
ends = [e for _, e in sentences.split_ends(text)]
assert {"clause", "sentence", "paragraph"} <= set(ends), ends
between, after = gaps(sound)
assert between == [round(rt.pause_after(e) * RATE) for e in ends[:-1]], (between, ends)
assert rt.pause_after("clause") < rt.pause_after("sentence") < rt.pause_after("paragraph")
# the quiet the voice already left counts: only the rest is added, and none when it left enough
rt, sound = read_on_tape(Trailing(cfg, 0.2), "One. Two.")
assert gaps(sound)[0] == [round(rt.pause_after("sentence") * RATE)], gaps(sound)
rt, sound = read_on_tape(Trailing(cfg, 1.0), "One. Two.")
assert gaps(sound)[0] == [RATE], gaps(sound)
# reading faster shortens the pauses in step
assert abs(read_on_tape(Trailing(cfg, 0), "One.", speed=2.0)[0].pause_after("sentence") * 2
           - read_on_tape(Trailing(cfg, 0), "One.")[0].pause_after("sentence")) < 1e-9
# a pause setting that isn't a number counts as its default; a huge one is capped
odd = Reader({"model": "m", "sentence_pause_ms": "long", "paragraph_pause_ms": 1e9}, engine=FakeEngine(cfg))
assert odd.pause_after("sentence") == rt.pause_after("sentence") and odd.pause_after("paragraph") == reader_module.PAUSE_MAX_MS / 1000
# parts (the page reader's blocks, voices taking turns): each ends in a paragraph's pause, whatever its last mark
rt, sound = read_on_tape(Trailing(cfg, 0), [("A heading", None, None), ("One. Two.", "x", None), ("Done.", None, None)])
assert gaps(sound)[0] == [round(rt.pause_after(e) * RATE) for e in ["paragraph", "sentence", "paragraph"]], gaps(sound)
# a streaming voice: chunks, the last one silent — still one sentence pause in all
class Chunks(Trailing):
    streams = True
    def speak(self, text, should_stop=lambda: False, voice=None, persona_text=None, on_chunk=None):
        whole = super().speak(text)
        if on_chunk is None:  # the warm-up after loading
            return whole
        for part in np.split(whole, [int(0.1 * RATE), int(0.3 * RATE)]):
            on_chunk(part)
        return np.zeros(0, np.float32)
rt, sound = read_on_tape(Chunks(cfg, 0.1), "One. Two.")
assert gaps(sound)[0] == [round(rt.pause_after("sentence") * RATE)], gaps(sound)

# ---------- the end of a reading: the speaker closes in silence, so the last word is heard out ----------
# (a speaker that closes as the last sample is written can lose the tail it still holds — Bluetooth most)
END = round(player_module.END_S * RATE)
assert player_module.END_S >= 0.4
rt, sound = read_on_tape(Trailing(cfg, 0), "One. Two.")  # the voice stops dead on a loud sample
assert gaps(sound)[1] == END, gaps(sound)
voice_end = int(np.flatnonzero(np.isclose(sound, 0.1))[-1])  # the last voice sample (volume 0.5 plays it as made)
fade = sound[voice_end:len(sound) - END]
assert 1 < len(fade) <= 0.01 * RATE and np.all(np.diff(fade) < 0), len(fade)  # no click: down to silence in ≤ 10 ms
rt, sound = read_on_tape(Chunks(cfg, 0.1), "One. Two.")  # the voice left its own quiet (ends on 0.0): nothing to fade
assert gaps(sound)[1] == round(0.1 * RATE) + END, gaps(sound)
# (a stopped reading adds no end silence: the stop check above)

# no usable voice: reported before any model load, reading stops
eng.missing = True; errors.clear(); eng.loaded = False; FakeEngine.loads = 0
r.read("Hello."); wait_until(lambda: errors and not r.active, what="the voice error")
assert errors == ["voice"] and not r.active and FakeEngine.loads == 0, (errors, FakeEngine.loads)
# this reader's settings choose no voice at all (a fresh install has none): the message says how to get one
assert "add a recording" in messages[-1] and "built-in voice" in messages[-1], messages
eng.missing = False
# a voice for one text only (the browser's choice); a missing one is told like the chosen one
FakeEngine.voices_used.clear(); r.read("With another voice.", "en-gb-isla"); wait_until(lambda: not r.active, what="the reading to end")
assert ("With another voice.", "en-gb-isla") in FakeEngine.voices_used, FakeEngine.voices_used
errors.clear(); r.read("Hello.", "nobody"); wait_until(lambda: errors and not r.active, what="the voice error")
assert errors == ["voice"] and not r.active and "Choose a voice" in messages[-1], (errors, messages)  # a named voice gone

# parts: one reading, in order, each sentence in its part's voice and persona; Read again keeps them
FakeEngine.voices_used.clear(); FakeEngine.personas_used.clear(); huds.clear()
assert r.read_parts([("She smiles. Then waits.", "en-gb-rose", "Warm narrator text"), ("  ", "nobody", None),
                     ("Come in.", "builtin:ryan", ""), ("Sit.", None, None)])
wait_until(lambda: not r.active, what="the parts to be read")
assert FakeEngine.voices_used == [("She smiles.", "en-gb-rose"), ("Then waits.", "en-gb-rose"), ("Come in.", "builtin:ryan"), ("Sit.", None)], FakeEngine.voices_used
assert [p for _, p in FakeEngine.personas_used] == ["Warm narrator text", "Warm narrator text", "", None]
assert [d for s, d in huds if s == "reading" and d] == ["She smiles.", "Then waits.", "Come in.", "Sit."]
assert r.last_text == "She smiles. Then waits.\n\nCome in.\n\nSit." and len(r.last_parts) == 3  # the empty part is dropped
assert not r.read_parts([(" ", None, None)]) and not r.read_parts([])
# a part's own speed reaches the engine, and Read again (the app replays last_parts) keeps it
FakeEngine.speeds_used.clear()
assert r.read_parts([("Fast now.", None, "Angry", 1.5), ("Plain.", None, None)])
wait_until(lambda: not r.active, what="the parts to be read")
assert r.read_parts(r.last_parts); wait_until(lambda: not r.active, what="the reading again")
assert FakeEngine.speeds_used == [("Fast now.", 1.5), ("Plain.", 1.0)] * 2, FakeEngine.speeds_used
# one stop ends a reading of several parts: nothing more is made, from this part or the next
FakeEngine.spoken.clear()
r.read_parts([(". ".join(f"Part one {i}" for i in range(15)) + ".", "en-gb-rose", None),
              (". ".join(f"Part two {i}" for i in range(15)) + ".", "builtin:ryan", "")])
wait_until(lambda: FakeEngine.spoken, what="the first part to be made"); r.stop(); made = len(FakeEngine.spoken); nothing_more_for(0.6)
assert not r.active and len(FakeEngine.spoken) <= made + 1 and "Part two 14." not in FakeEngine.spoken, FakeEngine.spoken
# a missing voice in any part is told before anything is made
errors.clear(); FakeEngine.voices_used.clear()
r.read_parts([("Fine.", None, None), ("Not fine.", "nobody", None)]); wait_until(lambda: errors and not r.active, what="the voice error")
assert errors == ["voice"] and not r.active and FakeEngine.voices_used == [], (errors, FakeEngine.voices_used)

# a reading stopped before its error shows nothing (no notice, no Settings opening)
class Boom(FakeEngine):
    working = threading.Event()
    def speak(self, text, should_stop=lambda: False, voice=None, persona_text=None):
        Boom.working.set(); time.sleep(0.2); raise RuntimeError("model broke")
errors.clear()
rb = Reader(cfg, on_error=lambda k, m: errors.append(k), player_factory=player, engine=Boom(cfg))
Boom.working.clear(); rb.read("Hello."); wait_until(Boom.working.is_set, what="the model at work"); rb.stop()
nothing_more_for(0.4)  # stopped while the model is still at work: its error comes after the stop
assert errors == [] and not rb.active, errors
rb.read("Hello."); wait_until(lambda: errors and not rb.active, what="the model's error")
assert errors == ["error"] and not rb.active, errors

# no sound device: reported, and no more speech is made for the dead player
class NoDevice(FakeStream):
    def __init__(self, rate): raise OSError("no output device")
errors.clear(); FakeEngine.spoken.clear(); huds.clear()
rd = Reader(cfg, hud=lambda s, d="": huds.append((s, d)), on_error=lambda k, m: errors.append(k),
            player_factory=lambda rate, vol, on_piece=None, on_done=None: Player(rate, vol, on_piece, on_done, stream_factory=NoDevice),
            engine=FakeEngine(cfg))
rd.read(". ".join(f"Sentence {i}" for i in range(10)) + "."); wait_until(lambda: errors and not rd.active, what="the device error")
nothing_more_for(0.6)  # nothing more is made for the dead player
assert errors == ["error"] and not rd.active and len(FakeEngine.spoken) <= 3 and huds[-1][0] == "hide", (errors, FakeEngine.spoken)

# nothing sayable
assert not r.read("--- ***")

# idle unload
eng.loaded = True; r.last_used = time.monotonic() - 120; r.unload_if_idle(); wait_until(lambda: not eng.loaded, what="the idle unload")
assert not eng.loaded

# ---------- two shortcuts in one listener ----------
fired = []
class P:
    def caps_lock_off(self): pass
kb = Keyboard("alt+d", "toggle", lambda: fired.append("dictate"), lambda: None, 400, P(),
              extra=[("alt+r", lambda: fired.append("read"))])
def tap(*keys):
    for k in keys:
        for m in kb.matchers: m.press(k)
    for k in reversed(keys):
        for m in kb.matchers: m.release(k)
tap("alt", "r"); tap("alt", "d"); tap("r"); tap("alt", "x"); wait_until(lambda: len(fired) >= 2, what="both shortcuts"); nothing_more_for(0.1)
assert sorted(fired) == ["dictate", "read"], fired
# Fn ×2 for reading (the macOS hook feeds Fn itself: keyboard.feed)
fired.clear()
kb2 = Keyboard("alt+d", "toggle", lambda: fired.append("dictate"), lambda: None, 400, P(),
               extra=[("double:fn", lambda: fired.append("read"))])
for down in (True, False, True, False):
    kb2.feed("fn", down)
wait_until(lambda: fired, what="Fn ×2"); nothing_more_for(0.1)
assert fired == ["read"], fired
kb2.paused = True; kb2.feed("fn", True); kb2.feed("fn", False); kb2.feed("fn", True); kb2.feed("fn", False)
nothing_more_for(0.1); assert fired == ["read"]  # paused while the settings page records

# ---------- new settings reach an old config.toml ----------
old = Path(tempfile.mkdtemp()) / "config.toml"
old.write_text("[engine]\nmodel = \"/x\"   # mine\n\n[shortcut]\nkeys = \"f13\"\n")
added = config.add_missing(old)
c = config.load(old)
assert "[read]" in added and c["read"]["keys"] == "alt+r" and c["shortcut"]["keys"] == "f13" \
    and c["engine"]["model"] == "/x" and "mode" in c["shortcut"], added
assert config.add_missing(old) == [] and "# mine" in old.read_text()

# ---------- app paths ----------
from waudio import app as A
A.osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # never your real history
A.osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # nor your real logs
osd = A.osd
calls = []
osd.play = lambda *a: None
osd.hud = lambda s, d="": None
osd.output_muted = lambda m=None: False
osd.focus_target = lambda: (1, None)
osd.focused_field = lambda: "text"
osd.run_on_main = lambda fn: None
selection = {"v": "Read me. Please."}
osd.selected_text = lambda: selection["v"]
a = A.App()
a.reader.close()
a.reader = Reader(a.cfg["read"], on_change=a.refresh, on_error=a._read_failed, player_factory=player,
                  engine=FakeEngine(a.cfg["read"]))
FakeEngine.spoken.clear()
a.on_read(); wait_until(lambda: a.reader.active, what="the reading to start")
assert a.reader.active and "Reading aloud" in a.status_text(), a.status_text()
a.on_read(); assert a.reader.active and a.reader.paused and "paused" in a.status_text()   # same shortcut pauses it
a.on_read(); assert a.reader.active and not a.reader.paused    # and goes on
a.user_key("escape"); wait_until(lambda: not a.reader.active, what="Esc to stop it")
assert not a.reader.active                                     # Esc stops it
a.on_read(); wait_until(lambda: a.reader.active, what="the reading to start")


class Rec:
    active = False
    def start(self): self.active = True
    def stop(self): self.active = False; return np.zeros(100, np.float32)
    def health(self): return True, ""
    device = "Test Mic"
    def take_level(self): return 0.0
    def seconds(self): return 0.0
a.recorder = Rec(); a.cfg["dictation"]["typing"] = "end"
a.toggle(); assert a.recorder.active and not a.reader.active   # dictating stops the reading
a.on_read(); assert not a.reader.active and "Stop dictating" in a.status_text()
a.recorder.active = False
selection["v"] = "   "; a.on_read(); assert not a.reader.active and "Select some text" in a.status_text()
a.on_sleep(); selection["v"] = "Hi."; a.on_read(); assert not a.reader.active and a.locked
a.locked = False

# macOS Services: the host's `WAUDIO_READ ["text"]` line reads that text; Stop Reading stops it
import json as _json
from waudio.platforms import macos as mac
# a Service sent before the app registered its commands waits for them (macOS started Waudio)
osd.HOST_COMMANDS.clear(); mac.HOST_COMMANDS_READY.clear()
early = mac._host_line("WAUDIO_READ " + _json.dumps(["Early bird."]) + "\n")
assert early is not None
t_early = threading.Thread(target=early); t_early.start(); nothing_more_for(0.2); assert not a.reader.active
osd.HOST_COMMANDS.update(read=a.read_text, stop_reading=a.reader.stop); mac.HOST_COMMANDS_READY.set()
t_early.join(5); wait_until(lambda: a.reader.last_text == "Early bird.", what="the early Service's text")
assert a.reader.last_text == "Early bird.", a.reader.last_text
a.reader.stop()
job = mac._host_line("WAUDIO_READ " + _json.dumps(["Line one.\nLine two, 第二行。"]) + "\n")
assert job is not None; job(); wait_until(lambda: a.reader.active and a.reader.last_text.startswith("Line one."), what="the Service's reading")
assert a.reader.active and a.reader.last_text == "Line one.\nLine two, 第二行。"
mac._host_line("WAUDIO_DO stop_reading\n")(); assert not a.reader.active
for bad in ("WAUDIO_READ not json\n", "WAUDIO_READ []\n", "WAUDIO_READ [42]\n", "WAUDIO_DO nothing\n"):
    assert mac._host_line(bad) is None, bad
a.locked = True; mac._host_line("WAUDIO_READ " + _json.dumps(["Hi."]))(); assert not a.reader.active  # locked: nothing
a.locked = False

# record your own voice: words written down by the dictation model (raw, no clean-up), saved, chosen
class VoiceRec(Rec):
    def stop(self):
        self.active = False
        return (np.sin(np.arange(16000 * 4) / 9) * 0.05).astype(np.float32)
class Said:
    text, raw, cut = "Hello, comma test", "language English<asr_text>Hello comma test", False
class Dict:
    loaded = True; loaded_model = "x"
    def transcribe(self, audio, language=None, script_name=None, prefix="", strict=False, draft=""): return Said()
    def load(self): pass
a.recorder = VoiceRec(); a.engine = Dict(); a._sound = lambda kind: None
a.cfg["read"]["voices_dir"] = str(vd)
threading.Thread(target=a.worker, daemon=True).start()
real_sleep = time.sleep
time.sleep = lambda s: None if s >= 1 else real_sleep(s)  # no real 8 s wait
res = a.record_voice(4)
assert res["text"] == "Hello comma test" and a.cfg["read"]["voice"] == res["name"], res
again = a.record_voice(4)  # same second: a new name, the first one kept
assert again["name"] != res["name"] and (vd / f"{res['name']}.wav").is_file(), (res, again)
# while a sample records, the dictation's stop / cancel / toggle leave it alone
a.recording_voice = True; a.recorder.active = True
a.stop(); a.cancel(); a.toggle()
assert a.recorder.active and "voice sample" in a.status_text(), a.status_text()
a.recording_voice = False; a.recorder.active = False
# something fails mid-recording: the microphone is stopped, the flag cleared
class Fails(VoiceRec):
    def start(self): self.active = True; raise RuntimeError("device vanished")
a.recorder = Fails()
try:
    a.record_voice(4); raise AssertionError("no error")
except RuntimeError:
    pass
assert not a.recorder.active and not a.recording_voice
assert (vd / f"{res['name']}.txt").read_text().strip() == "Hello comma test" and (vd / f"{res['name']}.wav").is_file()
a.recorder = Rec(); a.recorder.active = True
try:
    a.record_voice(4); raise AssertionError("recorded while dictating")
except ValueError:
    pass
a.recorder.active = False
assert a.set_voice_text(res["name"], "Hello, this is a test")["name"] == res["name"]
assert tts.find_voice(res["name"], vd)[1] == "Hello, this is a test"
for bad in (("../x", "t"), (res["name"], "  "), ("missing", "t")):
    try:
        a.set_voice_text(*bad); raise AssertionError(bad)
    except ValueError:
        pass
snap = a.read_snapshot()
assert res["name"] in snap["voices"] and snap["voice_text"] == "Hello comma test" and "models" in snap  # the chosen (second) one
time.sleep = real_sleep
a.server.stop()

# ---------- the real voice model (optional) ----------
if "--real" in sys.argv:
    import tomllib
    real = tts.TtsEngine(tomllib.loads(config.EXAMPLE.read_text())["read"])  # the defaults, not the test's changes
    t = time.time(); real.load(); load_s = time.time() - t
    for text in ("Hello! This is a test of reading aloud.", "你好，这是朗读的测试。"):
        t = time.time(); audio = real.speak(text); dt = time.time() - t
        rms = 20 * np.log10(np.sqrt(np.mean(audio ** 2)))
        print(f"real: {len(audio) / real.sample_rate:.1f}s of speech in {dt:.1f}s, level {rms:.1f} dBFS")
        assert len(audio) > real.sample_rate and abs(rms - tts.LEVEL_DB) < 1
    print(f"real: model loaded in {load_s:.1f}s")
    def same_take_holds(e):
        """The real model: a read repeats with same_take on, and is a new take with it off."""
        text = "The first bus went past the window."
        once, again = e.speak(text), e.speak(text)
        assert len(once) and np.array_equal(once, again), "the same take differs"
        e.cfg["same_take"] = False
        once, again = e.speak(text), e.speak(text)
        e.cfg["same_take"] = True
        assert not np.array_equal(once, again), "same_take off repeats the take"
    same_take_holds(real)
    pm = Path("~/Documents/Speech/TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice/Qwen3-TTS-12Hz-0.6B-Persona-mlx-int4").expanduser()
    if persona.is_persona_model(pm):  # the persona model, if it has been built: Rose + her persona
        import mlx.core as mx
        real.unload()
        pe = tts.TtsEngine({**tomllib.loads(config.EXAMPLE.read_text())["read"], "model": str(pm), "voice": "en-gb-rose", "language": "english"})
        pe.load(); assert pe.takes_persona
        same_take_holds(pe)
        print(f"real persona: {tts.read_persona('en-gb-rose', pe.cfg['voices_dir'])!r}")
        takes = []
        for _ in range(2):
            mx.random.seed(11); t = time.time(); takes.append(pe.speak("Close your eyes for a moment, and just breathe.")); dt = time.time() - t
            print(f"real persona: {len(takes[-1]) / pe.sample_rate:.1f}s of speech in {dt:.1f}s")
        assert len(takes[0]) > pe.sample_rate and np.array_equal(takes[0], takes[1])  # the same seed gives the same speech
        assert {"extract_speaker_embedding", "_prepare_generation_inputs"}.isdisjoint(vars(pe.model))
        if pe.layers:  # a built-in voice on the same model, then back to Rose: same speech as before
            t = time.time(); serena = pe.speak("Close your eyes for a moment, and just breathe.", voice="builtin:serena")
            print(f"real persona, built-in Serena: {len(serena) / pe.sample_rate:.1f}s of speech in {time.time() - t:.1f}s")
            mx.random.seed(11); again = pe.speak("Close your eyes for a moment, and just breathe.")
            assert len(serena) > pe.sample_rate and pe.layers.now == "copied" and np.array_equal(again, takes[0])
        pe.unload()

print("read checks: ok")
