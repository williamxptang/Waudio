"""Breeze TTS 2 as a voice model: it sounds better than Qwen3-TTS and takes any written direction,
but following one (cfg 4) needs two passes per sound and is too slow to read live on a Mac.

So a persona is baked: the voice's recording is said again once at cfg 4, in that persona, and that
clip (same words) is what the voice is copied from. A copy keeps the delivery of its source, so
reading runs at cfg 1, one pass. A plain cfg 1 instruction has no effect: calm and sultry came out
the same. Baked clips stay in <voices folder>/.baked; a new persona makes a new one (~15 s).

mlx-audio's depth decoder works out its whole sequence again for each of the 16 codebooks. Here it
remembers (add_depth_cache, in the runner: engine/models/mlx_audio_tts.py): twice as fast, 0.7× -> 1.3× real time on an M5, which is what makes
live reading possible. The cache is checked against the original at load: if an mlx-audio update
changes the decoder, it stays off and the log says so.

Needs a reference clip and its exact words: a blend has neither (TtsEngine raises VoiceMissing).
Weights: BreezeBlue Research and Non-Commercial License.
"""
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

import numpy as np

log = logging.getLogger("waudio")

MODEL_TYPE = "breeze_tts"
BAKE_CFG = 4.0        # the card's value for following an instruction well
BAKE_SEED = 1234      # the same persona bakes the same way
MOOD_CFG = 2.0        # a line with a mood: its reading pass is told it too, this strongly (twice the work a step)
FOLDER = ".baked"
FRAME_S = 0.08        # a frame of audio
MAX_FRAMES = 750      # mlx-audio's cap, ~60 s
FRAMES_PER_CHAR = 4   # ~0.3 s: above slow Chinese; a pass that runs past it has stalled
MIN_FRAMES = 40
STREAM_S = 0.96       # a chunk of speech this long at a time ([read] stream_chunk_s, 12 frames): each is decoded on its own,
STREAM_MIN, STREAM_MAX = 0.32, 2.0  # and a join is rougher than the rest — 0.64–1 s sounds cleanest; smaller plays sooner
CACHE_TOLERANCE = 0.1  # logits differ this much at most (rounding: ~0.01 of a spread of ~13)


def is_breeze_model(path):
    try:
        cfg = json.loads((Path(path).expanduser() / "config.json").read_text())
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(cfg, dict) and cfg.get("model_type") == MODEL_TYPE


def budget(text):
    """The most frames a text can take, so a stalled pass can't run on."""
    return min(MAX_FRAMES, MIN_FRAMES + FRAMES_PER_CHAR * len(text))



def library_versions():
    """The libraries that make a persona clip: a new mlx or mlx-audio can sound different, so the
    clip is made again rather than kept from the old one."""
    from importlib.metadata import PackageNotFoundError, version
    found = []
    for name in ("mlx", "mlx-audio"):
        try:
            found.append(f"{name}={version(name)}")
        except PackageNotFoundError:
            found.append(f"{name}=?")
    return ";".join(found)


def baked_path(wav, words, persona_text, folder, model_name):
    st = Path(wav).stat()
    key = "\0".join((Path(model_name).name, str(wav), str(st.st_size), str(st.st_mtime_ns), words, persona_text,
                      library_versions()))
    root = Path(folder).expanduser() if str(folder or "").strip() else Path(wav).parent  # no folder: beside the recording
    return root / FOLDER / f"{Path(wav).stem}-{hashlib.sha1(key.encode()).hexdigest()[:12]}.wav"


def chunk_seconds(value):
    """[read] stream_chunk_s as the model takes it: whole frames, STREAM_MIN to STREAM_MAX (STREAM_S if
    it isn't a number)."""
    try:
        s = float(value)
    except (TypeError, ValueError):
        s = STREAM_S
    s = min(max(s if s == s else STREAM_S, STREAM_MIN), STREAM_MAX)  # s != s: NaN
    return round(round(s / FRAME_S) * FRAME_S, 2)  # mlx-audio takes int(seconds / FRAME_S) frames


def _audio(model, text, wav, words, should_stop, on_chunk=None, chunk_s=STREAM_S, **how):
    """The chunks of one pass, chunk_s seconds each (each also handed to on_chunk as it is made); None if stopped."""
    parts = []
    for r in model.generate(text=text, ref_audio=str(wav), ref_text=words, max_tokens=budget(text),
                            stream=True, streaming_interval=chunk_seconds(chunk_s) + 0.001, **how):  # + 1 ms: never a frame short
        parts.append(np.array(r.audio, dtype=np.float32))
        if should_stop():
            return None
        if on_chunk:
            on_chunk(parts[-1])
    return parts


def accent(wav):
    """The voice's accent sentence (<stem>.accent.txt beside its recording), "" if it has none."""
    try:
        return " ".join(Path(wav).with_suffix(".accent.txt").read_text(encoding="utf-8").split())
    except (OSError, UnicodeDecodeError):
        return ""


def baked_sample(model, wav, words, persona_text, folder, model_name, should_stop=lambda: False, chunk_s=STREAM_S):
    """The clip of this voice in this persona, made once. None if stopped while making it. The
    recording itself if a clip can't be kept or came out wrong: reading goes on, without the persona.
    A persona that names no accent bakes in plain English, so the voice's <stem>.accent.txt, if any,
    goes before it."""
    persona_text = " ".join(filter(None, (accent(wav), persona_text)))
    path = baked_path(wav, words, persona_text, folder, model_name)
    if path.is_file():
        return path
    import soundfile as sf
    try:
        path.parent.mkdir(parents=True, exist_ok=True)  # before the work, not after it
        with tempfile.TemporaryFile(dir=path.parent):
            pass
    except OSError as e:
        log.warning(f"Breeze: no place to keep a persona ({type(e).__name__}): reading the plain voice")
        return wav
    log.info("Breeze: baking a persona")
    parts = _audio(model, words, wav, words, should_stop, chunk_s=chunk_s, instruct=persona_text, cfg_scale=BAKE_CFG, seed=BAKE_SEED)
    if parts is None:
        return None
    audio = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    frames = len(audio) / model.sample_rate / FRAME_S
    if not len(audio) or frames >= budget(words) - 1:  # empty, or it ran into the budget: stalled
        log.warning("Breeze: a persona came out wrong, not kept: reading the plain voice")
        return wav
    partial = path.with_suffix(".partial.wav")
    try:
        sf.write(partial, audio, model.sample_rate)
        os.replace(partial, path)
    except (OSError, RuntimeError) as e:  # a full disk (soundfile's own error is a RuntimeError)
        log.warning(f"Breeze: a persona could not be saved ({type(e).__name__}): reading the plain voice")
        return wav
    return path


def speak(model, text, wav, words, persona_text, folder, model_name, should_stop=lambda: False, seed=None, on_chunk=None,
          chunk_s=STREAM_S, mood=False):
    """[chunks of audio], [] if stopped: text in this voice, copied from its baked clip. on_chunk(audio):
    each chunk as soon as it is made (playing starts before the sentence is finished), chunk_s seconds long.
    mood: persona_text starts with a mood; the clip alone carries little of it, so the reading pass is told it
    too (with the voice's accent first, as when baking), at MOOD_CFG."""
    source = baked_sample(model, wav, words, persona_text, folder, model_name, should_stop, chunk_s) if persona_text else wav
    if source is None:
        return []
    how = {"instruct": " ".join(filter(None, (accent(wav), persona_text))), "cfg_scale": MOOD_CFG} if mood and persona_text \
        else {"cfg_scale": 1.0}
    return _audio(model, text, source, words, should_stop, on_chunk, chunk_s, seed=seed, **how) or []
