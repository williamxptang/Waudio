"""Model runners: one module per way of running a model (mlx_audio.py for dictation, mlx_audio_tts.py for
the voice models; see README.md). TTS_RUNNERS (below) says the same for voice models: tts_runner()
gives engine/tts.py the module, in the model worker; tts_cant_run() says why a model can't run here.

RUNNERS below says what each runner can run, so a model scan never imports one (and with it the
model libraries): only the model worker does (waudio/worker). Each RUNNERS entry:

    model_types           config.json "model_type" values it can run
    formats               safetensors "__metadata__.format" values it can load ("mlx", "pt")
    platforms             sys.platform values it works on
    unmarked_format       optional: the format of its model types' weights when they carry no marker
    languages             optional: the languages it offers when config.json doesn't list them

Each runner module defines:

    Runner(model_path)    load the model (may take seconds)
    .transcribe(audio, language, max_tokens, context, should_stop, prefix="", strict=False) -> (language, text, raw[, capped])
        audio        float32 mono numpy array at 16 kHz
        language     None for auto-detect, or a language name the model knows
        max_tokens   hard cap on generated tokens
        context      text that biases spelling (special terms), or "" (may be ignored)
        should_stop  callable(text_so_far) -> bool; poll it and stop early when True
        prefix       earlier raw output to continue from ("" = none; may be ignored)
        raw          the answer as the model wrote it, prefix included
    .rollback(raw, tokens) -> str   raw minus its last tokens, for the next prefix ("" if unsupported)
    .close()              free the model

scan(folder) finds model folders and says which runner (if any) can use each one here.
"""
import importlib, json, struct, sys

from ... import NAME
from pathlib import Path

RUNNERS = {  # runner module name -> what it can run (add new runners here)
    "mlx_audio": {"model_types": {"qwen3_asr"}, "formats": {"mlx"}, "platforms": {"darwin"}},
}


PLATFORM = sys.platform  # the computer the voice-model runners are picked for (a check can play another)
TTS_RUNNERS = {  # voice-model runner module -> what it can run (the same keys as RUNNERS)
    "mlx_audio_tts": {"model_types": {"qwen3_tts", "breeze_tts"}, "formats": {"mlx"}, "platforms": {"darwin"}},
}


def tts_cant_run(model_type, fmt="mlx"):
    """Why a voice model of this type and format can't run on this computer, or "" if it can."""
    if any(model_type in r["model_types"] and fmt in r["formats"] and PLATFORM in r["platforms"] for r in TTS_RUNNERS.values()):
        return ""
    if not any(PLATFORM in r["platforms"] for r in TTS_RUNNERS.values()):
        return "Voice models run only on Apple Silicon Macs for now (MLX)."
    return f"{NAME} has no runner for {model_type} ({fmt}) on this computer"


def tts_runner(model_type, fmt="mlx"):
    """The runner module for a voice model of this type here (imports its model library): the model
    worker only. RuntimeError with the reason when there is none."""
    for name, r in TTS_RUNNERS.items():
        if model_type in r["model_types"] and fmt in r["formats"] and PLATFORM in r["platforms"]:
            return importlib.import_module(f"{__name__}.{name}")
    raise RuntimeError(tts_cant_run(model_type, fmt))


def load(name, model):
    """The runner for model, in a model worker process: closing it ends the process."""
    from ...worker.remote import RemoteRunner
    return RemoteRunner(name, model)


def load_here(name, model):
    """The runner for model in this process: what the model worker runs."""
    return importlib.import_module(f"{__name__}.{name}").Runner(model)


def weights_format(folder):
    """The safetensors metadata format of the first weights file ('mlx', 'pt', …) or None."""
    files = sorted(Path(folder).glob("*.safetensors"))
    if not files:
        return None
    try:
        with open(files[0], "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n > min(100_000_000, files[0].stat().st_size - 8):
                return None  # not real weights, e.g. a git-lfs pointer file before the download
            header = json.loads(f.read(n))
        return (header.get("__metadata__") or {}).get("format")
    except (OSError, ValueError, struct.error):
        return None


def describe(folder):
    """One model folder -> dict for the settings page, or None if it is not a model."""
    folder = Path(folder)
    try:
        cfg = json.loads((folder / "config.json").read_text())
    except (OSError, ValueError):
        return None
    mtype, fmt = cfg.get("model_type") if isinstance(cfg, dict) else None, weights_format(folder)
    if not mtype:
        return None
    size = sum(p.stat().st_size for p in folder.glob("*.safetensors") if p.is_file())
    if not fmt:  # no format marker: known from the runner that makes such builds, else unknown
        fmt = next((r.get("unmarked_format") for r in RUNNERS.values() if mtype in r["model_types"]), None)
        if not fmt:
            if size < 10_000_000:  # nothing but placeholders (git-lfs pointers): not a model here
                return None
            fmt = "unknown"
    runner, why, langs = None, f"{NAME} has no runner for {mtype} ({fmt}) on this computer", []
    for name, r in RUNNERS.items():
        if mtype in r["model_types"] and fmt in r["formats"] and sys.platform in r["platforms"]:
            runner, why, langs = name, "", r.get("languages", [])
            break
    q = cfg.get("quantization") or {}
    return {"path": str(folder), "name": folder.name, "model_type": mtype, "format": fmt,
            "quant": cfg.get("quant_recipe") or (f"{q.get('bits')}-bit" if q else "unquantized"),
            "size_gb": round(size / 1e9, 2), "languages": cfg.get("support_languages") or langs,
            "runner": runner, "unusable_reason": why}


def scan(root, depth=3, look=None):
    """Model folders under root (itself included), nearest first. look(folder) -> dict or None says
    what a folder holds (default: describe, a dictation model; engine/ocr passes its own)."""
    look = look or describe
    root = Path(root).expanduser()
    found, level = [], [root]
    for _ in range(depth + 1):
        nxt = []
        for d in level:
            if (info := look(d)):
                found.append(info)
                continue  # don't look inside a model folder
            try:
                nxt += sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
            except OSError:
                pass
        level = nxt
    return found
