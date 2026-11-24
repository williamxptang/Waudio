"""Persona voice model: a cloned voice that also follows a written persona ("a British woman, soft and
breathy, at normal speaking speed"). Qwen3-TTS Base copies a voice but takes no instruction;
CustomVoice takes one but only has its 9 built-in speakers. The persona model is CustomVoice with
three small parts of Base grafted in, so a voice fingerprint made by Base's speaker encoder can sit
where the built-in speaker goes (recipe from gabriele-mastrapasqua/qwen3-tts, MIT — its "graft"):

- Base's text_projection (how the words enter the model),
- Base's codec_embedding rows 0-2047 (the sound tokens; the control and speaker rows stay CustomVoice's),
- Base's text_embedding rows for tts_pad / tts_bos / tts_eos,

plus Base's speaker encoder, kept beside it in ENCODER (mlx-audio builds a speaker encoder only for
Base models), and CustomVoice's own versions of those three parts in CUSTOMVOICE, so its 9 built-in
voices still sound exactly like CustomVoice: the runner's Layers swaps the right set in before each
sentence (engine/models/mlx_audio_tts.py holds the MLX parts: encoder, Layers, the build).
A model folder is a persona model when MARKER (or, built before the rename, OLD_MARKER) is in it.

Build one (once, ~2.3 GB, from the two mlx int4 builds of the 1.7B models):
    python -m waudio.engine.persona <CustomVoice dir> <Base dir> <new dir>
"""
import json
import sys
from pathlib import Path

from .. import NAME, OLD_PACKAGE

MARKER = "waudio_persona.json"
OLD_MARKER = f"{OLD_PACKAGE}_persona.json"  # a persona model built before the rename: still one
ENCODER = "persona/speaker_encoder.safetensors"  # in a subfolder: mlx-audio loads every *.safetensors at the top
CUSTOMVOICE = "persona/customvoice.safetensors"  # CustomVoice's own text_projection, sound rows, pad/bos/eos rows
SOUND_ROWS = 2048  # codec_embedding rows that are sound tokens (above: think/language/speaker/pad ids)


def marker(path):
    """The persona model's marker file (MARKER, or OLD_MARKER if built before the rename), or None."""
    if not str(path or "").strip():
        return None  # no model set: never the current folder
    try:
        folder = Path(str(path)).expanduser()
        return next((folder / m for m in (MARKER, OLD_MARKER) if (folder / m).is_file()), None)
    except OSError:
        return None


def is_persona_model(path):
    return marker(path) is not None


def meta(path):
    """What the persona model's marker says (its speaker encoder's config, the two models it came from)."""
    m = marker(path)
    if m is None:
        raise FileNotFoundError(f"{path} is not a persona model (no {MARKER} or {OLD_MARKER})")
    return json.loads(m.read_text())



def builtin_speakers(path):
    """The built-in voices a persona model can speak with CustomVoice's own sound (none if it was built
    before CUSTOMVOICE existed, or isn't a persona model)."""
    d = Path(str(path or "")).expanduser()
    if not is_persona_model(d) or not (d / CUSTOMVOICE).is_file():
        return []
    try:
        spk = json.loads((d / "config.json").read_text())["talker_config"]["spk_id"]
        return [str(k).lower() for k in spk] if isinstance(spk, dict) else []
    except (OSError, ValueError, KeyError, TypeError):
        return []



def placeholder_speaker(model):
    """The built-in speaker name mlx-audio insists on: its sound is never used (the voice's fingerprint
    takes its place), but a dialect speaker would switch the language, so take a plain one."""
    dialect = model.config.talker_config.spk_is_dialect or {}
    return next(s for s in model.get_supported_speakers() if not dialect.get(s))



def build(cv_dir, base_dir, out_dir):
    """Build a persona model (above) with the runner that can: an MLX one, on Apple Silicon."""
    from .models import tts_runner
    return tts_runner("qwen3_tts").build_persona_model(cv_dir, base_dir, out_dir)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    print(build(*sys.argv[1:]))
