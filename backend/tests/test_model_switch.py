#!/usr/bin/env python3
"""Choosing a model tests it first: its files whole, room in memory, and the Mac's test sentence
written back. Only a model that passes is switched to (already loaded, the old one closed, the setting
saved); any failure keeps the current model loaded and the setting unchanged. When it fits only without
the current model, nothing changes until you agree to unload that one — and a failed test then loads the
current one again. Stand-ins: the model runner (by folder), the test speech, free memory. Each check
builds its own app on a config copy.

  python3 backend/tests/test_model_switch.py
"""
import json, shutil, struct, sys, tempfile, threading, tomllib
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import config
from waudio import app as A, checks
from waudio.engine import models
from waudio.server.api import Api
osd = A.osd
BEHAVIOUR = {}  # model folder -> what its runner does: "hears", "deaf", "crashes"
CLOSED = []


class Runner:
    def __init__(s, path): s.path = path
    def transcribe(s, audio, language, budget, context, looping, prefix="", strict=False):
        how = BEHAVIOUR[s.path]
        if how == "crashes": raise RuntimeError("Metal ran out of memory")
        text = checks.TEST_SENTENCE if how == "hears" else "uh"
        return language, text, text
    def close(s): CLOSED.append(s.path)

models.load = lambda backend, path: Runner(path)


def model_folder(name, how="hears", cut=False, size=16):
    d = Path(tempfile.mkdtemp()) / name; d.mkdir()
    (d / "config.json").write_text(json.dumps({"model_type": "qwen3_asr"}))
    header = json.dumps({"__metadata__": {"format": "mlx"}, "w": {"dtype": "F32", "shape": [4], "data_offsets": [0, size]}}).encode()
    (d / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * (size // 2 if cut else size))
    BEHAVIOUR[str(d)] = how
    return str(d)


def app_with_current(loaded=True, free=64.0):
    """An app whose current model works (loaded or not), on its own config copy."""
    tmp = Path(tempfile.mkdtemp()) / "config.toml"; shutil.copy(config.EXAMPLE, tmp); config.PATH = tmp
    osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d); osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)
    osd.speak_audio = lambda text: np.zeros(16000, np.float32)
    osd.free_memory_gb = lambda: free
    a = A.App(); a.refresh = lambda: None
    current = model_folder("current")
    a.cfg["engine"].update(model=current, backend="mlx_audio")
    threading.Thread(target=a.worker, daemon=True).start()
    if loaded:
        a.engine.load()
    CLOSED.clear()
    return a, current, tmp


def saved_model(tmp):
    return tomllib.loads(tmp.read_text())["engine"]["model"]


def a_model_that_passes_becomes_the_model():
    a, current, tmp = app_with_current()
    new = model_folder("new")
    r = Api(a).try_model(new)
    assert r["ok"], r
    assert a.engine.loaded and a.engine.loaded_model == new and a.engine.runner.path == new   # loaded, not reloaded
    assert CLOSED == [current] and a.cfg["engine"]["model"] == new and saved_model(tmp) == new


def a_failing_model_changes_nothing():
    def deaf(): return model_folder("deaf", how="deaf")
    def crashes(): return model_folder("crashes", how="crashes")
    def cut(): return model_folder("cut", cut=True)
    def not_a_model(): return tempfile.mkdtemp()
    cases = [(deaf, "heard"), (crashes, "failed to run"), (cut, "cut short"), (not_a_model, "not a model")]
    assert cases
    for make, why in cases:
        a, current, tmp = app_with_current()
        new = make()
        r = Api(a).try_model(new)
        assert not r["ok"] and why in r["why"], (make.__name__, r)
        assert a.engine.loaded and a.engine.loaded_model == current, make.__name__   # still loaded
        assert current not in CLOSED and a.cfg["engine"]["model"] == current and saved_model(tmp) != new, make.__name__


def no_room_asks_before_unloading_the_current_model():
    a, current, tmp = app_with_current(free=0.0)
    new = model_folder("big")
    r = Api(a).try_model(new)
    assert r == {"ok": False, "needs_unload": True, "why": r["why"]} and "GB" in r["why"], r
    assert a.engine.loaded_model == current and not CLOSED                          # nothing changed yet
    osd.free_memory_gb = lambda: 0.0 if a.engine.loaded else 64.0                    # room once it's unloaded
    r = Api(a).try_model(new, unload_current=True)
    assert r["ok"] and a.engine.loaded_model == new and saved_model(tmp) == new, r


def a_failed_test_after_unloading_loads_the_current_model_again():
    a, current, tmp = app_with_current(free=0.0)
    osd.free_memory_gb = lambda: 0.0 if a.engine.loaded else 64.0
    new = model_folder("big-deaf", how="deaf")
    r = Api(a).try_model(new, unload_current=True)
    assert not r["ok"] and a.engine.loaded and a.engine.loaded_model == current and saved_model(tmp) != new, r


def without_test_speech_a_model_that_runs_is_still_chosen():
    """The Mac can't make the test sentence (no voice): a model that loads and runs is taken, with a note."""
    a, current, tmp = app_with_current()
    def no_voice(text): raise RuntimeError("say: voice not found")
    osd.speak_audio = no_voice
    new = model_folder("unspoken", how="deaf")
    r = Api(a).try_model(new)
    assert r["ok"] and "not tested on speech" in r["note"] and a.engine.loaded_model == new and saved_model(tmp) == new, r
    broken = model_folder("unspoken-crash", how="crashes")
    r = Api(a).try_model(broken)
    assert not r["ok"] and a.engine.loaded_model == new                       # still has to load and run


def with_nothing_loaded_a_tight_fit_is_just_tried():
    a, current, tmp = app_with_current(loaded=False, free=0.0)
    r = Api(a).try_model(model_folder("big2"))
    assert not r["ok"] and "needs_unload" not in r and "Not enough memory" in r["why"], r
    assert not a.engine.loaded and a.cfg["engine"]["model"] == current


a_model_that_passes_becomes_the_model()
a_failing_model_changes_nothing()
no_room_asks_before_unloading_the_current_model()
a_failed_test_after_unloading_loads_the_current_model_again()
with_nothing_loaded_a_tight_fit_is_just_tried()
without_test_speech_a_model_that_runs_is_still_chosen()
print("model switch checks: ok")
