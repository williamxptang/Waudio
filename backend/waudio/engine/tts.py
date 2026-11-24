"""Text in, speech out: the read-aloud voice model (Qwen3-TTS or Breeze, run by the voice-model runner
engine/models picks for this computer: TTS_RUNNERS), the voice it copies, and levelling every piece to
the same loudness. Nothing here calls a model library itself.

Not thread-safe, like Engine: load, speak and unload run on the reader's own worker thread. TtsEngine
runs in a model worker process (worker/__main__.py); the reader uses worker/remote.py's RemoteTts.
"""
import re
from pathlib import Path

import numpy as np

from . import breeze, persona
from .models import tts_runner

LEVEL_DB = -16.0    # average loudness of every spoken piece (the player's 50 % volume)
PEAK_DB = -1.0      # and its loudest moment at most this
LANGUAGES = {"auto": "auto", "english": "english", "chinese": "chinese"}
PERSONA_MAX = 500   # characters of a voice's persona (how it speaks), for a persona model
MOOD_MAX = 200      # characters of a mood (moods.json), said before the persona for one line
MOOD_LEAD = "This feeling outweighs everything that follows."  # between a mood and its persona, so the mood wins
KEEP_VOICE = "Keep your own natural pitch and voice."  # said before a preset persona, so a persona written for
# one sex (Rowdy trooper's "booming", "his mates") styles the voice without turning a woman into a man
INSTRUCT_MAX = MOOD_MAX + 1 + len(MOOD_LEAD) + 1 + len(KEEP_VOICE) + 1 + PERSONA_MAX  # a mood, its lead, the guard and the persona
MOOD_WPM = (120, 190)  # a line with a mood is stretched back into this pace (words a minute), pitch kept
MOOD_WPM_WORDS = 4     # lines shorter than this are left at their own pace: too few words to measure


class Moody(str):
    """A persona text with a mood (part_way; after KEEP_VOICE when a preset is used): Breeze is told it while reading too, not only when
    baking, and the line is made whole so its pace can be kept within MOOD_WPM."""
PART_SPEED = (0.5, 2.0)  # the speeds a read part may ask for, on top of the reading speed
PERSONA_PRESETS = [  # the Persona box's dropdown; personas.json in the voices folder replaces it ({name: text})
    ("Calm & close", "Speak with a standard British English accent, calm and unhurried, softly and close to the microphone, "
                     "with a gentle smile in your voice, at normal speaking speed."),
    ("Soft & breathy", "Speak with a standard British English accent, softly and intimately, slightly breathy, at normal speaking speed."),
    ("Sultry", "Speak with a standard British English accent in a low, sultry, slightly teasing tone, intimate and relaxed, "
               "at normal speaking speed."),
    ("Warm narrator", "Speak like a warm, clear audiobook narrator with a standard British English accent, at a steady, normal speaking speed."),
    ("News presenter", "Speak like a calm, crisp news presenter with a standard British English accent, clear and neutral, "
                       "at normal speaking speed."),
    ("Bright & cheerful", "Speak in a bright, cheerful and friendly tone with a standard British English accent, at normal speaking speed."),
    ("Bedtime story", "Speak gently and quietly, like reading a bedtime story, soothing and warm, at a relaxed but normal speaking speed."),
]
# A voice's age (<name>.age.txt): Young = a young adult, about 18-25; Adult = the default; Old = elderly.
# There are no child voices. A persona (a way of speaking) suits every age; a mood says which ages it suits.
AGES = ("Young", "Adult", "Old")
DEFAULT_AGE = "Adult"
READ_SEED = 1234    # same_take: the same text reads the same way each time
BUILTIN = "builtin:"  # a persona model's own voice, e.g. "builtin:serena" (never a preset name: no ":")
BUILTIN_LABELS = {  # Qwen's descriptions; the language is the one it speaks natively
    "serena": "Serena · woman, warm, gentle (Chinese)", "vivian": "Vivian · woman, bright (Chinese)",
    "uncle_fu": "Uncle Fu · man, low, mellow (Chinese)", "dylan": "Dylan · man (Beijing Chinese)",
    "eric": "Eric · man, lively (Sichuan Chinese)", "ryan": "Ryan · man, dynamic (English)",
    "aiden": "Aiden · man, American (English)", "ono_anna": "Ono Anna · woman, playful (Japanese)",
    "sohee": "Sohee · woman, warm (Korean)"}


def soft_limit(a, limit=10 ** (PEAK_DB / 20)):
    """Round off only the peaks above 90 % of limit (tanh), so nothing passes limit and the rest
    — and so the loudness — stays as it is."""
    a = np.asarray(a, dtype=np.float32).copy()
    knee = limit * 0.9
    loud = np.abs(a) > knee
    if loud.any():
        over = (np.abs(a[loud]) - knee) / (limit - knee)
        a[loud] = np.sign(a[loud]) * (knee + (limit - knee) * np.tanh(over))
        np.clip(a, -limit, limit, out=a)  # float32 rounding at the very top
    return a


def level(audio):
    """Bring a piece to LEVEL_DB average; its few peaks above PEAK_DB are rounded off rather than
    turning the whole piece down (speech lands on LEVEL_DB; only very spiky sound, mostly
    spikes, comes out a little quieter). Silence stays silence."""
    a = np.nan_to_num(np.asarray(audio, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    rms = float(np.sqrt(np.mean(a ** 2))) if a.size else 0.0
    if rms < 1e-5:
        return a
    return soft_limit(a * (10 ** (LEVEL_DB / 20) / rms))


GAIN_SENTENCES = 5      # a streamed voice's gain: the median over its last this many sentences
SPEECH_RMS = 0.01       # -40 dBFS: a voice's first gain is taken from the first chunk this loud (not a breath)
FIRST_GAIN_MAX = 10.0   # ... and never turns it up more than this
CACHE_LIMIT = 1 << 30  # MLX keeps at most this much freed memory for reuse (uncapped it held ~6 GB)


def gain_for(audio, floor=1e-3):
    """The gain that brings this audio to LEVEL_DB (what level() applies), or None if it is quieter than
    floor (silence; SPEECH_RMS: not yet clear speech)."""
    a = np.nan_to_num(np.asarray(audio, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    rms = float(np.sqrt(np.mean(a ** 2))) if a.size else 0.0
    return 10 ** (LEVEL_DB / 20) / rms if rms >= floor else None


def stretch(audio, speed, sr):
    """Play speech faster or slower without changing its pitch (WSOLA: overlap 30 ms windows,
    each nudged by up to 10 ms to line up with the last one). speed 1.0 returns it unchanged.
    The voice model has no speed control of its own."""
    a = np.asarray(audio, dtype=np.float32)
    if abs(speed - 1.0) < 0.01 or a.size < sr // 10:
        return a
    n = int(0.03 * sr)                  # window
    hop = n // 2                        # output step
    tol = int(0.01 * sr)                # search range for the best fit
    win = np.hanning(n).astype(np.float32)
    out_len = int(len(a) / speed) + n
    out = np.zeros(out_len, np.float32)
    norm = np.zeros(out_len, np.float32)
    prev = a[:n].copy()                 # what the last placed window continues into
    pos_out, pos_in = 0, 0.0
    while True:
        centre = int(pos_in)
        lo, hi = max(0, centre - tol), min(len(a) - n, centre + tol)
        if hi < lo:
            break
        if pos_out:                     # pick the offset whose window best continues the last one
            seg = a[lo:hi + n]
            scores = np.correlate(seg, prev, mode="valid")
            best = lo + int(np.argmax(scores))
        else:
            best = lo
        frame = a[best:best + n]
        out[pos_out:pos_out + n] += frame * win
        norm[pos_out:pos_out + n] += win
        prev = a[best + hop:best + hop + n]
        if len(prev) < n:
            break
        pos_out += hop
        pos_in += hop * speed
        if pos_out + n > out_len:
            break
    end = pos_out + n
    return (out[:end] / np.maximum(norm[:end], 1e-3)).astype(np.float32)


_scan_cache = {}   # root -> (time, result): the settings page asks every second
SCAN_TTL_S = 30


def forget_scans():
    """Rescan now (Model → Rescan, or a new models folder)."""
    _scan_cache.clear()


def quant_label(q):
    """A config.json "quantization" as a tag: "4-bit", "4/6-bit" for a mixed build (its layers' own
    bits beside the default), "full size" for none."""
    if not isinstance(q, dict) or not q:
        return "full size"
    bits = {q.get("bits")} | {v.get("bits") for v in q.values() if isinstance(v, dict)}
    bits = sorted(b for b in bits if isinstance(b, int))
    return "/".join(map(str, bits)) + "-bit" if bits else "full size"


def scan_models(root, depth=3):
    """Qwen3-TTS and Breeze model folders under root, nearest first: [{path, name, quant, size_gb, persona,
    format, family, why}] (format: the weights' marker, "mlx" for a build this Mac runs, None for an original;
    family: "Qwen3-TTS" or "Breeze"; why: the reason no runner here can run it, or ""). Cached
    for SCAN_TTL_S; a folder it can't read is skipped, never an error."""
    import json, time
    from .models import tts_cant_run, weights_format
    if not str(root or "").strip():
        return []
    root = Path(root).expanduser().resolve()
    hit = _scan_cache.get(str(root))
    if hit and time.monotonic() - hit[0] < SCAN_TTL_S:
        return hit[1]
    found, level = [], [root]
    for _ in range(depth + 1):
        nxt = []
        for d in level:
            try:
                cfg = json.loads((d / "config.json").read_text())
                cfg = cfg if isinstance(cfg, dict) else None
            except (OSError, ValueError):
                cfg = None
            try:
                if cfg and cfg.get("model_type") in ("qwen3_tts", breeze.MODEL_TYPE) and any(d.glob("*.safetensors")):
                    q = cfg.get("quantization") or {}
                    size = sum(f.stat().st_size for f in d.rglob("*.safetensors") if f.is_file())
                    takes = takes_persona(d)
                    # not a git-lfs placeholder; and one that copies a voice (CustomVoice / VoiceDesign can't)
                    if size > 10_000_000 and (cfg.get("tts_model_type", "base") == "base" or takes):
                        found.append({"path": str(d), "name": d.name, "size_gb": round(size / 1e9, 2), "persona": takes,
                                      "quant": quant_label(q), "format": weights_format(d),
                                      "family": "Breeze" if cfg.get("model_type") == breeze.MODEL_TYPE else "Qwen3-TTS",
                                      "why": tts_cant_run(cfg.get("model_type"))})
                    continue
                nxt += sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
            except OSError:
                pass
        level = nxt
    _scan_cache[str(root)] = (time.monotonic(), found)
    return found


def voices(folder):
    """Voice presets in a folder: name -> (wav path, what it says, blend). A preset is <name>.wav
    with <name>.txt beside it (blend None), or <name>.blend.json — a mix of recordings by
    "voice fingerprint" (blend [(wav, weight), …], what it says None; it wins over a <name>.wav)."""
    if not str(folder or "").strip():
        return {}  # not set: never the current folder
    d = Path(folder).expanduser()
    out = {}
    for wav in sorted(d.glob("*.wav")) if d.is_dir() else []:
        txt = wav.with_suffix(".txt")
        if txt.is_file() and txt.read_text().strip():
            out[wav.stem] = (str(wav), txt.read_text().strip(), None)
    for f in sorted(d.glob("*.blend.json")) if d.is_dir() else []:
        blend = read_blend(f)
        if blend:
            out[f.name[:-len(".blend.json")]] = (blend[0][0], None, blend)
    return dict(sorted(out.items()))


def read_blend(path):
    """<name>.blend.json: {"sources": {"<wav, relative to the voices folder>": weight, …}} ->
    [(wav path, weight), …] with weights adding up to 1, or None if unusable (no source, a missing
    wav, a weight that isn't a positive number). Only the speaker's sound is mixed, not the delivery."""
    import json, math
    try:
        src = json.loads(Path(path).read_text()).get("sources")
        folder = Path(path).parent
        if any(isinstance(w, bool) for w in src.values()):
            return None
        mix = [((folder / k).resolve(), float(w)) for k, w in src.items()]
    except (OSError, ValueError, AttributeError, TypeError, OverflowError):
        return None
    if not mix or any(not math.isfinite(w) or w <= 0 or p.suffix != ".wav" or not p.is_file() for p, w in mix):
        return None
    total = sum(w for _, w in mix)
    return [(str(p), w / total) for p, w in mix]


def persona_presets(folder):
    """[(name, text, speed)] for the Persona dropdown: personas.json in the voices folder ({"name": "text"
    or {"text": "…", "speed": 0.9}, …}, in its order) when it has any usable entry, else PERSONA_PRESETS
    (speed 1.0). Texts are made one line, at most PERSONA_MAX; a speed outside PART_SPEED (or none) is 1.0.
    Entries may share a text and differ in speed only: speed variants of one persona, one bake for all."""
    return _personas_in_file(folder) or [(n, t, 1.0) for n, t in PERSONA_PRESETS]


def _personas_in_file(folder):
    """personas.json's usable entries [(name, text, speed)] (see persona_presets); [] for none."""
    import json
    try:
        data = json.loads((Path(folder).expanduser() / "personas.json").read_text()) if str(folder or "").strip() else None
    except (OSError, ValueError):
        data = None
    found = []
    for k, v in (data.items() if isinstance(data, dict) else ()):
        text, speed = (v.get("text"), v.get("speed", 1.0)) if isinstance(v, dict) else (v, 1.0)
        if str(k).strip() and isinstance(text, str) and text.strip():
            found.append((str(k).strip(), " ".join(text.split())[:PERSONA_MAX], _speed_or_1(speed)))
    return found


def _ages_or_all(ages):
    """The ages as listed (["Adult", "Old"]), in AGES order; none, an empty list or anything else: every age."""
    ok = isinstance(ages, list) and ages and all(a in AGES for a in ages)
    return [a for a in AGES if a in ages] if ok else list(AGES)


def persona_ages(folder):
    """{persona name: every age}: compatibility only. A persona has no age gate (moods do, see mood_ages), but an
    external client still reads persona_ages, so every persona says it suits every age."""
    return {n: list(AGES) for n, *_ in persona_presets(folder)}


def _speed_or_1(speed):
    ok = isinstance(speed, (int, float)) and not isinstance(speed, bool) and PART_SPEED[0] <= speed <= PART_SPEED[1]
    return float(speed) if ok else 1.0


def _mood_entries(folder):
    """moods.json's usable entries {name: (text, speed, ages)}; {} for none."""
    import json
    try:
        data = json.loads((Path(folder).expanduser() / "moods.json").read_text()) if str(folder or "").strip() else None
    except (OSError, ValueError):
        data = None
    found = {}
    for k, v in (data.items() if isinstance(data, dict) else ()):
        text = v.get("text") if isinstance(v, dict) else None
        if str(k).strip() and isinstance(text, str) and text.strip():
            found[str(k).strip()] = (" ".join(text.split())[:MOOD_MAX], _speed_or_1(v.get("speed", 1.0)), _ages_or_all(v.get("ages")))
    return found


def mood_ages(folder):
    """{mood name: the ages it suits} for every mood in moods(folder): moods.json
    ({"text": …, "ages": ["Adult", "Old"]}; none, empty or unusable: every age)."""
    return {n: ages for n, (_, _, ages) in _mood_entries(folder).items()}


def moods(folder):
    """{name: (text, speed)} from moods.json in the voices folder ({"Angry": {"text": "…", "speed": 1.08}, …},
    in its order): how one line is said on top of its persona, and how fast. No file: no moods. Texts
    are made one line, at most MOOD_MAX; a speed outside PART_SPEED (or none) is 1.0."""
    return {n: (t, sp) for n, (t, sp, _) in _mood_entries(folder).items()}


def part_pace(speed, mood, read_cfg):
    """A line's own speed, on top of its persona's and the reading speed: the speed it asks for (a
    client's line); else, with a mood, [read] mood_speed when mood_speed_fixed is on, else the mood's
    own speed (mood: its (text, speed) from moods()); 1.0 with neither."""
    if speed is not None:
        return float(speed)
    if not mood:
        return 1.0
    return float(read_cfg.get("mood_speed", 1.0)) if read_cfg.get("mood_speed_fixed") else mood[1]


def clamp_speed(speed):
    return min(max(float(speed), PART_SPEED[0]), PART_SPEED[1])


def mood_pace(text, parts, sr):
    """The stretch that brings a line read with a mood back into MOOD_WPM (1.0 if it is within it, or too
    short to tell): a mood should change how a line feels, not how fast it goes. It is measured on the take
    as made, before the reading speed and the line's own; kept within PART_SPEED, so a stalled take is not
    squeezed into noise."""
    words, seconds = len(text.split()), sum(len(p) for p in parts) / sr
    if words < MOOD_WPM_WORDS or seconds <= 0:
        return 1.0
    wpm = words / seconds * 60
    return clamp_speed(min(max(wpm, MOOD_WPM[0]), MOOD_WPM[1]) / wpm)


def part_way(persona_name, mood, speed, voice, read_cfg, presets=None, found_moods=None, pace=1.0):
    """(how, pace) for a read part. persona_name: a preset's name, "" (none) or None (the voice's own);
    mood: a name from moods.json or None; speed: the line's own or None; pace: the part's own persona
    speed, on top of the persona's (1.0 when left out). how is None when the voice's own persona is left
    to the engine (which adds that persona's speed itself): not with a mood or a pace, which name it here.
    The pace returned = pace × the persona's
    speed × the line's (part_pace), kept within PART_SPEED. Names must be known (checked by the caller)."""
    folder = read_cfg.get("voices_dir", "")
    presets = {n: (t, sp) for n, t, sp in (presets if presets is not None else persona_presets(folder))}
    found_moods = found_moods if found_moods is not None else moods(folder)
    how, own = (presets[persona_name] if persona_name else (persona_name, 1.0))
    if persona_name:  # the preset's own copy of KEEP_VOICE, if any, is dropped: it is said first, below
        how = " ".join(re.sub(re.escape(KEEP_VOICE), "", how, flags=re.I).split())
    if how is None and (mood or pace != 1.0):  # the voice's own, made explicit, so its speed joins the one clamp
        v = voice or read_cfg.get("voice", "")
        how, own = read_persona(v, folder), persona_speed(v, folder)
    if mood:  # said before the persona, and said to outweigh it, which may be calm where the mood is not
        how = Moody(" ".join(filter(None, (found_moods[mood][0], MOOD_LEAD, how))))
    if persona_name:  # before everything, the mood too, so no mood's lead outweighs the voice's own pitch
        how = (Moody if mood else str)(" ".join(filter(None, (KEEP_VOICE, how))))
    return how, clamp_speed(float(pace) * own * part_pace(speed, found_moods[mood] if mood else None, read_cfg))


def _save_named(folder, file, entries, longest, kind, plain, ages=False):
    """Write <folder>/<file> from [{"name", "text", "speed"[, "ages"]}, …] in that order, after checking each: a
    name (unique, any case), its text one line of 1..longest characters, its speed in PART_SPEED, and (when
    ages, moods only; personas ignore an "ages" key) the ages it suits, some of AGES (left out or all of them: every age, not written). An entry at
    speed 1.0 for every age is written as plain text when plain. Raises ValueError, writing nothing."""
    import json
    import os
    if not str(folder or "").strip() or not Path(folder).expanduser().is_dir():
        raise ValueError(f"no voices folder for the {kind}s")
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ValueError(f"the {kind}s are a list")
    out, seen = {}, set()
    for e in entries:
        name, text, speed = e.get("name"), e.get("text"), e.get("speed", 1.0)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"every {kind} needs a name")
        name = " ".join(name.split())
        if name.lower() in seen:
            raise ValueError(f"two {kind}s are called {name!r}")
        seen.add(name.lower())
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{name}: say how it is read")
        text = " ".join(text.split())
        if len(text) > longest:
            raise ValueError(f"{name}: a {kind} is at most {longest} characters")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not PART_SPEED[0] <= speed <= PART_SPEED[1]:
            raise ValueError(f"{name}: speed between {PART_SPEED[0]} and {PART_SPEED[1]}")
        speed = round(float(speed), 2)
        suits = e.get("ages") if ages else None
        if suits is not None and (not isinstance(suits, list) or not suits or not all(a in AGES for a in suits)):
            raise ValueError(f"{name}: the ages it suits are some of {', '.join(AGES)}")
        suits = _ages_or_all(suits)
        if plain and speed == 1.0 and suits == list(AGES):
            out[name] = text
        else:
            out[name] = {"text": text, "speed": speed, **({"ages": suits} if suits != list(AGES) else {})}
    path = Path(folder).expanduser() / file
    partial = path.with_suffix(".partial.json")
    partial.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)  # whole or not at all


def save_moods(folder, entries):
    """moods.json from Settings → Speech → Moods (see _save_named). -> moods(folder)"""
    _save_named(folder, "moods.json", entries, MOOD_MAX, "mood", plain=False, ages=True)
    return moods(folder)


def save_personas(folder, entries):
    """personas.json from Settings → Speech → Personas (see _save_named); none left: the built-in presets
    again. -> persona_presets(folder)"""
    _save_named(folder, "personas.json", entries, PERSONA_MAX, "persona", plain=True)
    return persona_presets(folder)


def takes_persona(model):
    """Does this voice model follow a persona: a persona model, or Breeze."""
    return persona.is_persona_model(model) or breeze.is_breeze_model(model)


def builtin_voices(model):
    """[("builtin:<name>", label)] for a persona model's own voices, in Qwen's order (none otherwise)."""
    names = persona.builtin_speakers(model)
    order = list(BUILTIN_LABELS) + sorted(set(names) - set(BUILTIN_LABELS))
    return [(BUILTIN + n, BUILTIN_LABELS.get(n, n.replace("_", " ").title())) for n in order if n in names]


def persona_path(voice, folder):
    """Where a voice's persona is kept: <name>.persona.txt beside the preset, builtin-<name>.persona.txt
    for a built-in voice (or beside a .wav given by path); None for anything else."""
    import re
    if str(voice or "").startswith(BUILTIN):
        name = voice[len(BUILTIN):]
        ok = re.fullmatch(r"[a-z0-9_]+", name) and str(folder or "").strip()
        return Path(folder).expanduser() / f"builtin-{name}.persona.txt" if ok else None
    if voice and voice in voices(folder):
        return Path(folder).expanduser() / f"{voice}.persona.txt"
    p = Path(str(voice or "")).expanduser()
    return p.with_suffix(".persona.txt") if p.suffix == ".wav" else None


def read_persona(voice, folder):
    """A voice's persona — how it speaks, e.g. "a British accent, soft, at normal speaking speed" —
    as one line ("" if it has none). Only a persona model follows it."""
    p = persona_path(voice, folder)
    try:
        return " ".join(p.read_text().split())[:PERSONA_MAX] if p else ""
    except (OSError, UnicodeDecodeError):
        return ""


def speed_path(voice, folder):
    """Where a voice's persona speed is kept: <name>.speed.txt beside its persona (None where it has none)."""
    p = persona_path(voice, folder)
    return p.with_name(p.name[:-len(".persona.txt")] + ".speed.txt") if p else None


def persona_speed(voice, folder):
    """How fast a voice's own persona reads, on top of the reading speed (1.0 if it has no speed of its own,
    or no persona: a speed belongs to its persona)."""
    p = speed_path(voice, folder)
    if not read_persona(voice, folder):
        return 1.0
    try:
        return _speed_or_1(float(p.read_text().strip())) if p else 1.0
    except (OSError, UnicodeDecodeError, ValueError):
        return 1.0


def voice_labels(folder):
    """Optional voices.json beside the presets: {name: "British English, woman", …}."""
    import json
    if not str(folder or "").strip():
        return {}
    try:
        labels = json.loads((Path(folder).expanduser() / "voices.json").read_text())
        return {str(k): v for k, v in labels.items() if isinstance(v, str) and v.strip()} if isinstance(labels, dict) else {}
    except (OSError, ValueError):
        return {}


def voice_tags(label):
    """A voice's label (voices.json) -> its tags: an optional "D·" (one of the default voices), then
    [F|M][group: accent or race][Name][mood, optional] -> {"sex": "F"|"M"|None, "default": bool,
    "group", "name", "mood": str or None}. A label without tags (a recording, a built-in voice) -> {}.
    The one place labels are read: the API, Settings and the extension all use it."""
    import re
    m = re.match(r"\s*(D·)?\s*((?:\[[^\[\]]*\]\s*)+)", str(label or ""))
    parts = [p.strip() or None for p in re.findall(r"\[([^\[\]]*)\]", m.group(2))] if m else []
    if not any(parts):
        return {}  # no tags, or only empty brackets
    sex = parts[0].upper() if (parts[0] or "").upper() in ("F", "M") else None
    rest = parts[1:] if sex else parts
    rest += [None] * (3 - len(rest))
    return {"sex": sex, "default": bool(m.group(1)), "group": rest[0], "name": rest[1], "mood": rest[2]}


def age_path(voice, folder):
    """Where a voice's age is kept: <name>.age.txt beside its persona (None where it has none)."""
    p = persona_path(voice, folder)
    return p.with_name(p.name[:-len(".persona.txt")] + ".age.txt") if p else None


def voice_age(voice, folder):
    """A voice's age, one of AGES (DEFAULT_AGE when it has no <name>.age.txt or an unknown word in it)."""
    p = age_path(voice, folder)
    try:
        said = p.read_text().strip().capitalize() if p else ""
    except (OSError, UnicodeDecodeError):
        said = ""
    return said if said in AGES else DEFAULT_AGE


def _slug(text):
    import re
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower().replace("'", "")).strip("-")


def voice_info(name, label, folder):
    """A voice's tags for the API and Settings: voice_tags(label) and, for a tagged voice, its race (the
    voice name's prefix, "high-elf[Lhirena]" -> "high-elf"; no prefix: "human"), its accent (the label's
    group, unless that group only names the race: "[High Elf]" is no accent) and its age (voice_age).
    A voice without tags (a recording, a built-in voice) -> {}."""
    import re
    tags = voice_tags(label)
    if not tags:
        return {}
    m = re.match(r"([\w-]+)\[[^\[\]]+\]$", str(name))
    race = _slug(m.group(1)) if m else "human"
    group = tags.get("group")
    accent = group if group and _slug(group) != race else None
    return {**tags, "race": race, "accent": accent, "age": voice_age(name, folder)}


def voice_order(folder):
    """The presets as Settings lists them: those named in voices.json in its order (yours to arrange),
    then the rest (a new recording …) by name."""
    names, labels = voices(folder), voice_labels(folder)
    return [n for n in labels if n in names] + [n for n in names if n not in labels]


def find_voice(voice, folder, model=""):
    """A preset name or a .wav path (with its .txt beside it) -> (wav, text, blend), or None. A built-in
    voice of the persona model `model` -> ("builtin:<name>", None, None)."""
    if str(voice or "").startswith(BUILTIN):
        return (voice, None, None) if voice in dict(builtin_voices(model)) else None
    presets = voices(folder)
    if voice in presets:
        return presets[voice]
    p = Path(str(voice)).expanduser()
    if p.suffix == ".wav" and p.is_file() and p.with_suffix(".txt").is_file():
        text = p.with_suffix(".txt").read_text().strip()
        return (str(p), text, None) if text else None
    return None


def streams(is_breeze, cfg):
    """Pieces come out chunk by chunk while they are made (speak's on_chunk): Breeze at the normal
    reading speed (another speed is stretched, which needs the whole sentence)."""
    return bool(is_breeze) and abs(float(cfg.get("speed", 1.0)) - 1.0) < 0.01


def configured_voice(cfg, name=None):
    """The voice as (wav, words, blend), or None if it isn't there. name: a preset for this text only
    (None: the configured voice). cfg: the [read] table."""
    return find_voice(name or cfg.get("voice", ""), cfg.get("voices_dir", ""), cfg.get("model", ""))


class TtsEngine:
    def __init__(self, cfg):
        self.cfg = cfg  # the [read] table of config.toml, shared with the app (changes apply live)
        self.model = None
        self.runner = None  # the voice-model runner module the model was loaded with (engine/models)
        self.loaded_model = None
        self.takes_persona = False  # the loaded model is a persona model (persona.py)
        self.is_breeze = False  # the loaded model is Breeze (breeze.py)
        self.layers = None  # its two sets of layers: copied voices / built-in voices (the runner's Layers)
        self.fingerprints = {}  # (wav, size, mtime) -> its voice fingerprint, for blends and persona models
        self.gains = {}  # (voice clip, persona) -> the gains its last streamed sentences needed (_stream)

    @property
    def loaded(self):
        return self.model is not None

    @property
    def sample_rate(self):
        return self.model.sample_rate if self.model else 24000

    def load(self):
        if self.model and self.loaded_model != self.cfg["model"]:
            self.unload()
        if not self.model:
            path = str(Path(self.cfg["model"]).expanduser())
            is_breeze = breeze.is_breeze_model(path)
            runner = tts_runner(breeze.MODEL_TYPE if is_breeze else "qwen3_tts")
            model = runner.load_model(path)
            takes = persona.is_persona_model(path)
            layers = None
            self.is_breeze = is_breeze
            if self.is_breeze:
                runner.add_depth_cache(model)
                runner.add_reference_cache(model)
                takes = True
            elif takes:
                model.speaker_encoder = runner.load_speaker_encoder(path)
                layers = runner.Layers(model, path) if persona.builtin_speakers(path) else None
            self.model, self.runner, self.loaded_model, self.takes_persona, self.layers = model, runner, self.cfg["model"], takes, layers
            runner.set_cache_limit(CACHE_LIMIT)

    def unload(self):
        if self.model:
            runner = self.runner
            self.model = self.runner = None
            self.loaded_model = None
            self.takes_persona = self.is_breeze = False
            self.layers = None
            self.fingerprints = {}
            self.gains = {}
            runner.clear_cache()

    @property
    def streams(self):
        return streams(self.is_breeze, self.cfg)

    def voice(self, name=None):
        return configured_voice(self.cfg, name)

    def speak(self, text, should_stop=lambda: False, voice=None, persona_text=None, on_chunk=None, speed=1.0):
        """One piece of text -> levelled float32 audio at sample_rate (empty if stopped).
        voice: a preset name for this piece (None: the configured voice). persona_text: how to speak this
        piece instead of the voice's own persona ("": none; None: the voice's own); it may end in a mood.
        on_chunk(audio), when the model streams: each levelled chunk as it is made, and nothing is returned.
        speed: this piece's own, on top of the reading speed; another than 1.0 is stretched, so made whole."""
        self.load()
        model, layers, runner = self.model, self.layers, self.runner  # the same ones throughout, even if unloaded meanwhile
        found = self.voice(voice)
        if found is None:
            raise VoiceMissing(voice or self.cfg.get("voice", ""))
        wav, ref_text, blend = found
        if wav.startswith(BUILTIN) and not layers:  # the model in memory isn't the persona model (yet)
            raise VoiceMissing(wav)
        lang = LANGUAGES.get(self.cfg.get("language", "auto"), "auto")
        parts = []
        if persona_text is None:  # the voice's own persona, at its own speed
            persona_text = read_persona(voice or self.cfg.get("voice", ""), self.cfg.get("voices_dir", ""))
            speed = clamp_speed(speed * persona_speed(voice or self.cfg.get("voice", ""), self.cfg.get("voices_dir", "")))
        moody = isinstance(persona_text, Moody)
        how = " ".join(str(persona_text).split())[:INSTRUCT_MAX] or None
        seed = READ_SEED if self.cfg.get("same_take", True) else None
        if self.is_breeze:
            if blend:
                raise VoiceMissing(voice or self.cfg.get("voice", ""))  # no one clip and its words to copy
            if on_chunk and self.streams and abs(speed - 1.0) < 0.01 and not moody:  # a mood: made whole, for its pace
                return self._stream(model, text, wav, ref_text, how, should_stop, seed, on_chunk)
            parts = breeze.speak(model, text, wav, ref_text, how or "", self.cfg.get("voices_dir", ""), self.loaded_model, should_stop, seed,
                                 chunk_s=self.cfg.get("stream_chunk_s", breeze.STREAM_S), mood=moody)
            return self._finish(parts, speed * (mood_pace(text, parts, self.sample_rate) if moody else 1.0))
        if wav.startswith(BUILTIN) and layers:  # the model's own voice, with CustomVoice's own layers
            layers.use("builtin")
            swapped = {}
            pieces = lambda: model.generate(text=text, voice=wav[len(BUILTIN):], instruct=how, lang_code=lang)
        elif self.takes_persona:  # the voice's fingerprint goes where the built-in speaker would, + its persona
            if layers:
                layers.use("copied")
            mixed = self.blend_fingerprint(model, runner, blend or [(wav, 1.0)])
            speaker = persona.placeholder_speaker(model)
            prepare = model._prepare_generation_inputs
            swapped = {"extract_speaker_embedding": lambda audio, sr=24000: mixed,
                       "_prepare_generation_inputs": lambda *a, **k: prepare(*a, **{**k, "ref_audio": np.zeros(1, np.float32)})}
            pieces = lambda: model.generate(text=text, voice=speaker, instruct=how, lang_code=lang)
        else:
            swapped = {}
            if blend:  # the model takes the speaker from this mix instead of the one recording
                mixed = self.blend_fingerprint(model, runner, blend)
                swapped = {"extract_speaker_embedding": lambda audio, sr=24000: mixed}
            pieces = lambda: model.generate(text=text, ref_audio=wav, ref_text=ref_text, lang_code=lang)
        try:
            for name, f in swapped.items():
                setattr(model, name, f)
            if seed is not None:
                runner.seed(seed)
            for r in pieces():
                parts.append(np.array(r.audio, dtype=np.float32))
                if should_stop():
                    return np.zeros(0, np.float32)
        finally:
            for name in swapped:
                model.__dict__.pop(name, None)  # back to the model's own
        return self._finish(parts, speed)

    def _stream(self, model, text, wav, ref_text, how, should_stop, seed, on_chunk):
        """Breeze, chunk by chunk, at one gain for this voice: a sentence can't be measured before it plays,
        so the voice's usual loudness is brought to LEVEL_DB (the median gain of its last GAIN_SENTENCES
        sentences, so one odd sentence doesn't move the next; for its first sentence, the first chunk of
        clear speech, at most FIRST_GAIN_MAX), and a sentence spoken softer or louder stays so. Chunks
        before the gain is known wait for it; a sentence that never gets that loud is levelled whole at
        the end and doesn't count toward the voice's gain. Peaks are rounded off per chunk."""
        key = (wav, how)
        recent = self.gains.get(key) or []
        gain, waiting = (float(np.median(recent)) if recent else None), []
        def each(chunk):
            nonlocal gain
            if gain is None:
                waiting.append(chunk)
                if (first := gain_for(chunk, SPEECH_RMS)) is None:
                    return
                gain = min(first, FIRST_GAIN_MAX)
                chunk, waiting[:] = np.concatenate(waiting), []
            on_chunk(soft_limit(np.nan_to_num(chunk) * gain))
        parts = breeze.speak(model, text, wav, ref_text, how or "", self.cfg.get("voices_dir", ""), self.loaded_model,
                             should_stop, seed, on_chunk=each, chunk_s=self.cfg.get("stream_chunk_s", breeze.STREAM_S))
        if parts and (whole := gain_for(np.concatenate(parts), SPEECH_RMS)) is not None:  # a hush doesn't count
            self.gains[key] = (recent + [whole])[-GAIN_SENTENCES:]
        if waiting and not should_stop():
            on_chunk(level(np.concatenate(waiting)))  # never clear speech: levelled whole, as before
        return np.zeros(0, np.float32)

    def _finish(self, parts, speed=1.0):
        if not parts:
            return np.zeros(0, np.float32)
        audio = stretch(np.concatenate(parts), float(self.cfg.get("speed", 1.0)) * speed, self.sample_rate)
        return level(audio)

    def blend_fingerprint(self, model, runner, blend):
        """The weighted mix of the recordings' voice fingerprints (each worked out once)."""
        mixed = 0
        for wav, weight in blend:
            st = Path(wav).stat()
            key = (wav, st.st_size, st.st_mtime)
            if key not in self.fingerprints:
                self.fingerprints[key] = model.extract_speaker_embedding(runner.load_audio(wav, 24000))
            mixed = mixed + weight * self.fingerprints[key]
        return mixed


class VoiceMissing(Exception):
    """The configured voice preset or file isn't there (or has no .txt with its words)."""
