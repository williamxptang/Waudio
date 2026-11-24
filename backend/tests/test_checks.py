#!/usr/bin/env python3
"""Run checks (Troubleshoot): each check is green when things are right and red or yellow, with the
right fix, when they are not; its yellow and red results join the live problem list until the next run;
a check that crashes is a red result, not a crash; at start only the light ones run (no model test, no
microphone). Stand-ins: the microphone and the Mac's test speech (hardware), the model (a fake engine
on the real worker), memory and the Chrome bridge (machine state). Each check builds its own app.

  python3 backend/tests/test_checks.py
"""
import json, os, shutil, stat, struct, sys, tempfile, threading
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import config
tmp = Path(tempfile.mkdtemp()) / "config.toml"; shutil.copy(config.EXAMPLE, tmp); config.PATH = tmp
from waudio import app as A, checks, health
from waudio.input import microphone
from waudio.server.api import Api
osd = A.osd


def model_folder(cut=False, index_names=None):
    """A tiny real model folder: one weights file of 16 bytes (cut=True: 8 of them missing)."""
    d = Path(tempfile.mkdtemp()) / "asr"; d.mkdir()
    (d / "config.json").write_text(json.dumps({"model_type": "qwen3_asr"}))
    header = json.dumps({"__metadata__": {"format": "mlx"}, "w": {"dtype": "F32", "shape": [4], "data_offsets": [0, 16]}}).encode()
    (d / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * (8 if cut else 16))
    if index_names:
        (d / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {f"w{i}": n for i, n in enumerate(index_names)}}))
    return str(d)


class R:
    capped = False  # ran to the token budget
    def __init__(s, text, secs): s.text, s.secs, s.language, s.audio_s, s.cut, s.raw = text, secs, "English", 3, False, text

class FakeEngine:
    """Hears `said` in `secs` (or raises `fails`)."""
    loaded_model = None
    def __init__(s, said=checks.TEST_SENTENCE, secs=1.0, fails=None): s.said, s.secs, s.fails, s.loaded = said, secs, fails, False
    def load(s): s.loaded = True
    def unload(s): s.loaded = False
    def transcribe(s, audio, language=None, script_name=None, prefix="", strict=False):
        if s.fails: raise RuntimeError(s.fails)
        return R(s.said, s.secs)

class Listener:
    def __init__(s, ok=True): s.ok = ok
    def health(s): return (True, "") if s.ok else (False, "the hook was switched off")


def fine_app():
    """Everything right: a whole model, permissions, memory, a mic that hears, a live listener, the bridge."""
    osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d); osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)
    osd.permissions = lambda: {"microphone": True, "accessibility": True}
    osd.free_memory_gb = lambda: 64.0
    osd.speak_audio = lambda text: np.zeros(16000, np.float32)
    osd.chrome_bridge = lambda: (True, True)
    microphone.probe = lambda seconds=1.0: ("Test Mic", 0.3)
    a = A.App()
    a.cfg["engine"]["model"] = model_folder()
    a.cfg["read"]["model"] = ""; a.cfg["read"]["voice"] = ""
    a.engine = FakeEngine(); a.keyboard = Listener(); a.refresh = lambda: None
    threading.Thread(target=a.worker, daemon=True).start()
    return a


def levels(results):
    return {r["id"]: r["level"] for r in results}


def the_weight_files_must_be_whole():
    cases = [(model_folder(), ""), (model_folder(cut=True), "cut short"),
             (model_folder(index_names=["model.safetensors", "model-00002.safetensors"]), "missing weight files")]
    assert cases
    for folder, want in cases:
        why = checks.weights_problem(folder)
        assert (want in why) if want else why == "", (folder, why)
    empty = Path(tempfile.mkdtemp()); assert "no weight files" in checks.weights_problem(empty)


def all_green_when_all_is_right():
    a = fine_app()
    results = checks.run(a)
    assert results and all(r["level"] == "green" for r in results), [r for r in results if r["level"] != "green"]
    assert {"model-files", "model-memory", "model-test", "microphone", "listener", "chrome-bridge", "files"} <= set(levels(results))


def def_engine(**kw):
    return lambda a: setattr(a, "engine", FakeEngine(**kw))

def each_failure_is_named_and_coloured():
    def cut(a): a.cfg["engine"]["model"] = model_folder(cut=True)
    def no_memory(a): osd.free_memory_gb = lambda: 0.0
    def deaf_mic(a): microphone.probe = lambda seconds=1.0: ("Test Mic", 0.0)
    def no_mic(a):
        def gone(seconds=1.0): raise RuntimeError("no input device")
        microphone.probe = gone
    def dictating(a):
        class Busy: active = True
        a.recorder = Busy()
    def dead_listener(a): a.keyboard = Listener(ok=False)
    def secure_input(a): a.secure = (True, "Safari")
    def warned_shortcut(a): a.cfg["shortcut"].update(keys="cmd+c", mode="toggle")  # also copy
    def bridge_without_helper(a): osd.chrome_bridge = lambda: (True, False)
    def read_only_data(a):
        d = Path(tempfile.mkdtemp()); os.chmod(d, stat.S_IRUSR | stat.S_IXUSR); osd.data_dir = lambda: d
    cases = [  # (what goes wrong, the result that names it, its level, what its fix does)
        (def_engine(said="nothing like it"), "model-test", "red", "pane"),
        (def_engine(secs=9.0), "model-test", "yellow", "pane"),
        (def_engine(fails="out of memory"), "model-test", "red", "pane"),
        (cut, "model-files", "red", "pane"), (no_memory, "model-memory", "yellow", "pane"),
        (deaf_mic, "microphone", "yellow", "pane"), (no_mic, "microphone", "red", "checks"),  # allowed, but no device: run again (refused: test_audio_devices.py)
        (dictating, "microphone", "yellow", "pane"), (dead_listener, "listener", "red", "restart"),
        (secure_input, "secure-input", "yellow", "pane"), (warned_shortcut, "shortcut-warning", "yellow", "pane"),
        (bridge_without_helper, "chrome-bridge", "yellow", "pane"),
        (read_only_data, "files", "red", "pane")]
    assert cases
    for wrong, id, level, do in cases:
        a = fine_app(); wrong(a)
        got = {r["id"]: r for r in checks.run(a)}
        assert got[id]["level"] == level and got[id]["fix"]["do"] == do, (wrong.__name__, id, got[id])
        if got[id]["pane"] == "model":  # every dictation-model result opens the ASR page
            assert got[id]["fix"]["arg"] == "model/asr", (wrong.__name__, got[id]["fix"])
        others = [r for r in got.values() if r["level"] != "green" and r["id"] != id]
        assert not others, (wrong.__name__, others)              # only what went wrong is flagged


def an_unusable_model_is_named_once():
    a = fine_app(); a.cfg["engine"]["model"] = model_folder(cut=True)
    got = levels(checks.run(a))
    assert got["model-files"] == "red" and "model-test" not in got


def results_join_the_live_list_until_the_next_run():
    a = fine_app(); a.engine = FakeEngine(said="nothing like it")
    assert a.run_checks()
    assert [p["id"] for p in health.problems(a)] == ["model-test"] and a.state == "blocked"
    assert a.snapshot()["checks"]["results"] and a.snapshot()["attention"] == {"model": "red"}
    found = [r for r in a.snapshot()["checks"]["results"] if r["pane"] == "model" and r.get("fix")]
    assert found and all(r["fix"]["arg"] == "model/asr" for r in found), found  # its fix opens the ASR page
    a.engine = FakeEngine(); assert a.run_checks()
    assert health.problems(a) == [] and a.state in ("idle", "off")


def no_chrome_bridge_is_not_a_problem():
    """Most people never read aloud from Chrome: a bridge that was never set up is fine."""
    a = fine_app(); osd.chrome_bridge = lambda: (False, False)
    assert levels(checks.run(a))["chrome-bridge"] == "green"


def an_old_result_gives_way_to_what_changed_since():
    """A permission given, or another model chosen, after a run: its old red no longer counts."""
    a = fine_app(); osd.permissions = lambda: {"microphone": True, "accessibility": False}
    a.engine = FakeEngine(said="nothing like it")
    a.run_checks()
    assert {p["id"] for p in health.problems(a)} == {"permission:accessibility", "model-test"}
    osd.permissions = lambda: {"microphone": True, "accessibility": True}
    assert [p["id"] for p in health.problems(a)] == ["model-test"]            # the permission was given
    a.cfg["engine"]["model"] = model_folder()
    assert health.problems(a) == []                                          # about the old model
    a.run_checks()                                                           # what a new run finds counts again
    assert [p["id"] for p in health.problems(a)] == ["model-test"]


def a_live_problem_is_listed_once_and_goes_when_fixed():
    """What the live list also watches (a shortcut warning) shows once after a run, and goes once fixed."""
    a = fine_app(); a.cfg["shortcut"].update(keys="cmd+c", mode="toggle")
    a.run_checks()
    assert [p["id"] for p in health.problems(a)] == ["shortcut-warning"]
    a.cfg["shortcut"].update(keys="alt+d")
    assert health.problems(a) == []


def the_microphone_check_and_a_dictation_never_overlap():
    a = fine_app(); started = []
    class Rec:  # notes that a dictation reached the microphone, then stops it there (an error the app handles)
        active = False
        def start(s): started.append(1); raise RuntimeError("test microphone")
    a.recorder = Rec()
    osd.focused_field = lambda: "text"; osd.keyboard_language = lambda: "en"; osd.hud = lambda *x: None
    a._sound = lambda *x: None; a._read_keyboard = lambda: None
    inside = threading.Event(); go_on = threading.Event()
    def slow_probe(seconds=1.0):
        inside.set(); go_on.wait(5); return ("Test Mic", 0.3)
    microphone.probe = slow_probe
    t = threading.Thread(target=lambda: checks.check_microphone(a)); t.start()
    assert inside.wait(5)
    a._start()                                                               # a dictation while the check listens
    assert not started and "Checking the microphone" in a.notice[0]
    go_on.set(); t.join(5)
    a._start(); assert started == [1]                                        # once it's done, dictation starts
    assert checks.check_microphone(a)[0]["level"] == "green"                # and the lock was let go


def a_crashing_check_is_a_red_result():
    a = fine_app()
    def boom(): raise OSError("disk on fire")
    osd.chrome_bridge = boom
    got = levels(checks.run(a))
    assert got["check_chrome_bridge"] == "red" and got["model-test"] == "green"


def start_runs_only_the_light_checks():
    a = fine_app(); spoken = []
    osd.speak_audio = lambda text: spoken.append(text) or np.zeros(16000, np.float32)
    microphone.probe = lambda seconds=1.0: spoken.append("mic") or ("Test Mic", 0.3)
    a.run_checks(heavy=False)
    assert not spoken and "model-test" not in levels(a.checks["results"]) and not a.checks["heavy"]
    a.run_checks(); assert spoken == [checks.TEST_SENTENCE, "mic"] and a.checks["heavy"]


def start_opens_where_a_light_check_found_red():
    """A model file cut short is seen only by the checks: Settings still opens on Model at start."""
    a = fine_app(); a.cfg["engine"]["model"] = model_folder(cut=True)
    assert a.first_pane() is None                                            # the live list can't see it
    assert a.opening_pane() == "model/asr" and a.checks["at"] and not a.checks["heavy"]  # the ASR page, not the last used


def one_run_at_a_time():
    a = fine_app(); api = Api(a)
    a.checks_lock.acquire(); a.checks["running"] = True                      # as during a run
    assert api.run_checks() == {"started": False} and a.run_checks() is False
    a.checks["running"] = False; a.checks_lock.release()
    assert api.run_checks() == {"started": True}


the_weight_files_must_be_whole()
all_green_when_all_is_right()
each_failure_is_named_and_coloured()
an_unusable_model_is_named_once()
results_join_the_live_list_until_the_next_run()
no_chrome_bridge_is_not_a_problem()
an_old_result_gives_way_to_what_changed_since()
the_microphone_check_and_a_dictation_never_overlap()
a_live_problem_is_listed_once_and_goes_when_fixed()
a_crashing_check_is_a_red_result()
start_runs_only_the_light_checks()
start_opens_where_a_light_check_found_red()
one_run_at_a_time()
print("check suite checks: ok")
