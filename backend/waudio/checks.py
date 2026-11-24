"""Run checks (Troubleshoot → Run checks): a deeper look than health.py's live list. Each check gives
a result {"id", "title", "level" (green / yellow / red), "text", "fix"}; the yellow and red ones join
health.py's list until the next run (the light checks also run at start).

  permissions      Accessibility, Microphone
  model files      the dictation model's weight files are all there and whole; it fits in free memory
  model test       your Mac's own voice (say) speaks a sentence offline; the model must write it back
  microphone       an input device opens and hears something in one second
  shortcut         the key listener is alive; Secure Input; shortcut warnings
  read aloud       its voice model and voice are there
  Chrome bridge    registered, and its helper is there (only for reading aloud from Chrome)
  files            the config, data and log folders can be written; free disk space

The model test and the microphone are the heavy ones (they load the model, open the mic): only on
Run checks, never at start."""
import json, os, re, shutil, struct, time
from pathlib import Path

from . import NAME, config, health
from . import platforms as osd
from .engine import models
from .health import GREEN, RED, YELLOW

TEST_SENTENCE = "The quick brown fox jumps over the lazy dog."
TEST_SLOW_S = 5.0      # the model test taking longer than this: yellow (slow)
TEST_MATCH = 0.6       # share of the sentence's words that must come back
MEMORY_MARGIN = 1.2    # a model needs about this times its weights in free memory
LOW_DISK_GB = 1.0


def result(id, title, level, text, label=None, do="pane", arg=None, pane="troubleshoot", model=None):
    """model: the model folder a result is about — it no longer counts once another one is chosen."""
    r = health.problem(id, level, pane, text, label, do, arg) if level != GREEN else \
        {"id": id, "level": GREEN, "pane": pane, "text": text, "fix": None}
    r["title"] = title
    if model:
        r["model"] = model
    return r


def weights_problem(folder):
    """Why the weight files in a model folder can't be loaded, or "" when they are all there and whole:
    each .safetensors file's header must read and its data must reach the end the header names; every
    file a model.safetensors.index.json names must exist."""
    folder = Path(folder)
    index = folder / "model.safetensors.index.json"
    if index.is_file():
        try:
            names = set(json.loads(index.read_text())["weight_map"].values())
        except (OSError, ValueError, KeyError, AttributeError):
            return f"{index.name} can't be read"
        missing = sorted(n for n in names if not (folder / n).is_file())
        if missing:
            return f"missing weight files: {', '.join(missing)}"
    files = sorted(folder.glob("*.safetensors"))
    if not files:
        return "no weight files (*.safetensors)"
    for f in files:
        try:
            size = f.stat().st_size
            with open(f, "rb") as fh:
                n = struct.unpack("<Q", fh.read(8))[0]
                if n > size - 8:
                    return f"{f.name} is cut short (or a download placeholder)"
                header = json.loads(fh.read(n))
        except (OSError, ValueError, struct.error):
            return f"{f.name} can't be read"
        end = max((v["data_offsets"][1] for k, v in header.items()
                   if k != "__metadata__" and isinstance(v, dict) and "data_offsets" in v), default=0)
        if 8 + n + end > size:
            return f"{f.name} is cut short ({size:,} of {8 + n + end:,} bytes)"
    return ""


def check_permissions(app):
    out = []
    for name, ok in osd.permissions().items():
        title = name.capitalize()
        if ok is True and name == "accessibility" and getattr(osd, "ax_works", lambda: None)() is False:
            out.append(result(f"permission:{name}", title, RED, "Ticked, but it doesn't work: untick and tick "
                              f"{NAME} again in System Settings, then restart it", "Open System Settings",
                              "permission", name, pane="permissions"))
        elif ok is True:
            out.append(result(f"permission:{name}", title, GREEN, "Allowed", pane="permissions"))
        else:
            level = RED if ok is False else YELLOW
            out.append(result(f"permission:{name}", title, level, "Not allowed" if ok is False else "Not asked yet",
                              "Open System Settings" if ok is False else "Allow…", "permission", name, pane="permissions"))
    return out


def check_model_files(app):
    path = str(app.cfg["engine"].get("model") or "").strip()
    info = models.describe(Path(path).expanduser()) if path else None
    if not info:
        return [result("model", "Dictation model", RED, "No dictation model chosen" if not path
                       else f"{Path(path).name} is not a model folder (or is gone)", "Choose a model", pane="model", arg="model/asr")]
    if not info["runner"]:
        return [result("model", "Dictation model", RED, info["unusable_reason"], "Choose another model", pane="model", arg="model/asr")]
    if (why := weights_problem(Path(path).expanduser())):
        return [result("model-files", "Dictation model files", RED, f"{info['name']}: {why} — download it again",
                       "Choose another model", pane="model", arg="model/asr", model=path)]
    out = [result("model-files", "Dictation model files", GREEN, f"{info['name']}: all weight files whole")]
    loaded = app.engine.loaded and app.engine.loaded_model == app.cfg["engine"]["model"]
    free = osd.free_memory_gb()
    need = sum(f.stat().st_size for f in Path(path).expanduser().glob("*.safetensors")) / 1e9 * MEMORY_MARGIN
    if not loaded and free is not None and need > free:
        out.append(result("model-memory", "Memory", YELLOW,
                          f"{info['name']} needs about {need:.1f} GB; {free:.1f} GB is free — "
                          "close other apps or choose a smaller model", "Choose a model", pane="model", arg="model/asr", model=path))
    else:
        out.append(result("model-memory", "Memory", GREEN, "Loaded" if loaded else f"Fits ({free:.1f} GB free)"
                          if free is not None else "Not measured"))
    return out


def words(text):
    return re.findall(r"[a-z]+", text.lower())


def heard(text):
    """Did the model write the test sentence back (most of its words)?"""
    want, got = words(TEST_SENTENCE), set(words(text))
    return sum(w in got for w in want) / len(want) >= TEST_MATCH


def check_model_test(app):
    """Your Mac speaks the test sentence; the model must write most of its words back."""
    if any(r["level"] == RED for r in check_model_files(app)):
        return []  # the model check already says why it can't be used
    try:
        audio = osd.speak_audio(TEST_SENTENCE)
    except Exception as e:
        return [result("model-test", "Model test", YELLOW, f"Couldn't make the test speech: {e}")]
    path = app.cfg["engine"].get("model")
    try:
        text, secs = app.test_model(audio)
    except Exception as e:
        return [result("model-test", "Model test", RED, f"The model failed: {e}", "Choose another model", pane="model", arg="model/asr", model=path)]
    if not heard(text):
        return [result("model-test", "Model test", RED, f"It heard “{text.strip()[:80]}” for “{TEST_SENTENCE}”",
                       "Choose another model", pane="model", arg="model/asr", model=path)]
    if secs > TEST_SLOW_S:
        return [result("model-test", "Model test", YELLOW, f"Right, but slow: {secs:.1f} s for 3 s of speech — "
                       "a smaller model may suit this Mac", "Choose a model", pane="model", arg="model/asr", model=path)]
    return [result("model-test", "Model test", GREEN, f"Heard right in {secs:.1f} s")]


def check_microphone(app):
    from .input import microphone
    if not app.mic_lock.acquire(blocking=False):  # a dictation is starting right now
        return [result("microphone", "Microphone", YELLOW, "Skipped: you were dictating — run the checks again")]
    try:  # held for the whole second: a dictation can't start meanwhile (App._start)
        if app.recorder.active or app.recording_voice:
            return [result("microphone", "Microphone", YELLOW, "Skipped: the microphone was in use — run the checks again")]
        name, peak = microphone.probe(1.0)
    except Exception as e:
        if osd.permissions().get("microphone") is not True:  # not allowed: that is what to fix
            return [result("microphone", "Microphone", RED, f"Can't open a microphone: {e}", "Allow…", "permission", "microphone")]
        return [result("microphone", "Microphone", RED, f"Can't open the microphone ({e}). Is one connected? "
                       "A headset that just came or went is found when the checks run again.", "Run again", "checks")]
    finally:
        app.mic_lock.release()
    if peak < microphone.SILENT_PEAK:
        return [result("microphone", "Microphone", YELLOW, f"{name} heard nothing for a second — is it muted, or the wrong input?")]
    return [result("microphone", "Microphone", GREEN, f"{name} hears you")]


def check_shortcut(app):
    out = []
    ok, why = app.keyboard.health() if app.keyboard else (False, "not started")
    out.append(result("listener", "Shortcut listener", GREEN if ok else RED, "Listening for your shortcut" if ok
                      else f"Not listening ({why}) — restart", None if ok else "Restart now", "restart", pane="shortcut"))
    found = [p for p in health.problems(app) if p["id"] in ("secure-input", "shortcut-warning", "shortcut")]
    out += [dict(p, title="Shortcut") for p in found] or [result("shortcut", "Shortcut", GREEN, "No Secure Input, no warnings")]
    return out


def check_read_aloud(app):
    found = [p for p in health.problems(app) if p["id"] in ("read-model", "read-voice")]
    return [dict(p, title="Read aloud") for p in found] or [result("read", "Read aloud", GREEN, "Voice model and voice found")]


def check_chrome_bridge(app):
    registered, helper = osd.chrome_bridge()
    if not registered:  # most people don't read aloud from Chrome: nothing to look at
        return [result("chrome-bridge", "Chrome bridge", GREEN, "Not set up — only needed to read aloud from Chrome "
                       "(packaging/chrome/install.sh)")]
    if not helper:
        return [result("chrome-bridge", "Chrome bridge", YELLOW, "Registered, but its helper is gone — run "
                       "packaging/chrome/install.sh again")]
    return [result("chrome-bridge", "Chrome bridge", GREEN, "Registered")]


def check_files(app):
    places = {"config": Path(config.PATH), "data": osd.data_dir(), "logs": osd.log_dir()}
    bad = [f"{name} ({path})" for name, path in places.items()
           if not os.access(path if path.exists() else path.parent, os.W_OK)]
    if bad:
        return [result("files", "Files", RED, f"Can't write: {', '.join(bad)}")]
    where = next(p for p in (osd.data_dir(), *osd.data_dir().parents) if p.exists())
    free = shutil.disk_usage(where).free / 1e9
    if free < LOW_DISK_GB:
        return [result("disk", "Disk", YELLOW, f"Only {free:.1f} GB free on the disk")]
    return [result("files", "Files", GREEN, f"Config, data and logs writable; {free:.0f} GB free")]


LIGHT = [check_permissions, check_model_files, check_shortcut, check_read_aloud, check_chrome_bridge, check_files]
HEAVY = [check_model_test, check_microphone]


def run(app, heavy=True):
    """Every check in order -> results (a check that crashes is a red result, never a crash)."""
    out = []
    for fn in LIGHT + (HEAVY if heavy else []):
        try:
            out += fn(app)
        except Exception as e:
            out.append(result(fn.__name__, fn.__name__.removeprefix("check_").replace("_", " ").capitalize(), RED,
                              f"The check itself failed: {e}"))
    return out
