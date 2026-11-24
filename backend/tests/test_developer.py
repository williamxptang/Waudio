#!/usr/bin/env python3
"""Settings → Developer's backend (server/api.py dev/*, harness/background.py): refused unless developer
mode is on; which models are loaded and each worker's memory (real worker processes, stand-in models);
the ASR model test and the TTS test sentence with their speed; the OCR harness started from the app — in
a process of its own (a stand-in harness script here), one at a time, its lines and summary read back,
stopped on request or after its time limit, its options checked.

  python3 backend/tests/test_developer.py
"""
import json, sys, tempfile, threading, time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waits import wait_until
import numpy as np
from waudio import config

tmp = Path(tempfile.mkdtemp()) / "config.toml"
tmp.write_text(config.EXAMPLE.read_text())
config.PATH = tmp
from waudio import app as A, checks  # noqa: E402  (after PATH is redirected)
from waudio.harness import background  # noqa: E402
from waudio.server.api import Api  # noqa: E402
from waudio.worker.remote import RemoteRunner, RemoteTts  # noqa: E402

A.osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)
A.osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)
CMD = ([sys.executable, str(Path(__file__).resolve()), "--worker"], {})
RATE = 24000


class FakeAsr:
    methods = ("transcribe", "rollback")
    def __init__(self, start): self.info = {}; self.ballast = np.ones(30_000_000, np.uint8)  # 30 MB to see
    def transcribe(self, args, arrays, ask): return ["English", "x", "x"], []
    def rollback(self, args, arrays, ask): return "", []


class FakeTts:
    methods = ("speak",)
    def __init__(self, start): self.info = {"sample_rate": RATE, "is_breeze": False}
    def speak(self, args, arrays, ask): return None, [np.zeros(RATE, np.float32)]


class Engine:
    """Hears the test sentence in 0.5 s; on the app's worker thread, as the real one."""
    loaded, loaded_model, runner = False, None, None
    def load(s): s.loaded = True
    def unload(s): s.loaded = False
    def transcribe(s, audio, language=None, script_name=None, prefix="", strict=False):
        return SimpleNamespace(text=checks.TEST_SENTENCE, secs=0.5, language="English", audio_s=len(audio) / 16000,
                               cut=False, raw=checks.TEST_SENTENCE)


def app():
    import threading
    a = A.App()
    a.cfg["ui"]["developer"] = True
    a.engine = Engine(); a.refresh = lambda: None
    threading.Thread(target=a.worker, daemon=True).start()
    return a, Api(a)


def refused(fn):
    try:
        fn()
    except ValueError as e:
        return "Developer mode is off" in str(e)
    return False


def everything_is_refused_unless_developer_mode_is_on():
    a, api = app()
    a.cfg["ui"]["developer"] = False
    calls = [api.dev_models, api.dev_asr_test, api.dev_tts_test, api.dev_harness, api.dev_harness_stop,
             lambda: api.dev_harness_run("ppocr", 1), api.dev_logs, api.hook_test]
    assert calls and all(refused(c) for c in calls)
    a.server.stop(); a.reader.close()


def loaded_models_and_their_workers_memory():
    a, api = app()
    got = api.dev_models()
    assert got["asr"]["model"] is None and got["tts"]["model"] is None and got["memory_gb"] == {"asr": None, "tts": None}
    assert got["process"]["port"] == a.server.port and got["process"]["version"] and got["process"]["last_error"] is None
    log = A.osd.log_dir() / A.LOG_FILE
    log.write_text("2026-11-05 10:00:01,000 ERROR transcribe: boom\n2026-11-05 10:00:02,000 INFO fine again\n"
                   "2026-11-05 10:00:03,000 ERROR read aloud: speak failed\n2026-11-05 10:00:04,000 INFO later\n")
    assert api.dev_models()["process"]["last_error"] == {"time": "2026-11-05 10:00:03", "text": "read aloud: speak failed"}  # the newest
    runner = RemoteRunner("fake", "/m/asr-x", cmd=CMD)
    voice = RemoteTts({**a.cfg["read"], "model": "/m/tts-y"}, cmd=CMD); voice.load()
    try:
        a.engine = SimpleNamespace(loaded=True, loaded_model="/m/asr-x", runner=runner)
        a.reader.engine = voice; a.reader.cfg["model"] = "/m/tts-y"
        got = api.dev_models()
        assert (got["asr"]["model"], got["tts"]["model"]) == ("asr-x", "tts-y")
        assert got["memory_gb"]["asr"] > got["memory_gb"]["tts"] > 0, got["memory_gb"]  # the ASR stand-in holds 30 MB more
    finally:
        runner.close(); voice.unload()
    a.engine = Engine()
    assert api.dev_models()["memory_gb"]["asr"] is None  # its worker is gone
    a.server.stop(); a.reader.close()


def the_asr_test_reports_what_it_heard_and_how_fast():
    a, api = app()
    A.osd.speak_audio = lambda text: np.zeros(48000, np.float32)  # 3 s of "speech"
    r = api.dev_asr_test()
    assert r["right"] and r["heard"] == checks.TEST_SENTENCE and r["audio_seconds"] == 3.0
    assert r["x_realtime"] == round(r["audio_seconds"] / r["seconds"], 1) and r["loaded_first"]
    a.recorder = SimpleNamespace(active=True)
    try:
        api.dev_asr_test()
        raise AssertionError("tested while dictating")
    except ValueError as e:
        assert "dictation" in str(e)
    a.server.stop(); a.reader.close()


def the_tts_test_makes_a_sentence_times_it_and_plays_it():
    a, api = app()
    played = []

    class Player:
        def __init__(s, rate, volume): played.append(("open", rate))
        def add(s, i, audio): played.append(("add", len(audio)))
        def finish(s): played.append(("finish",))

    class Voice:
        """Makes 2 s of speech in 1 s; records what happens to it, in order."""
        loaded, loaded_model, sample_rate = False, None, RATE
        def load(s): s.loaded, s.loaded_model = True, a.reader.cfg["model"]; played.append(("load",))
        def unload(s): s.loaded = False; played.append(("unload",))
        def voice(s, name=None): return None  # no warm-up sentence
        def speak(s, text, **kw): time.sleep(1.0); played.append(("spoke",)); return np.zeros(RATE * 2, np.float32)

    a.reader.engine = Voice()
    a.reader.player_factory = Player
    r = api.dev_tts_test()
    assert r["audio_seconds"] == 2.0 and r["seconds"] >= 1.0
    assert abs(r["x_realtime"] - 2.0 / r["seconds"]) <= 0.05, r  # speech seconds per second taken (both rounded)
    assert played == [("load",), ("spoke",), ("open", RATE), ("add", RATE * 2), ("finish",)] and r["loaded_first"]
    # an unload asked for mid-test (idle unload, Unload all models) waits for the test: one thing at a time
    played.clear()
    threading.Timer(0.3, a.reader.unload).start()
    r = api.dev_tts_test()
    wait_until(lambda: ("unload",) in played, what="the unload that waited for the test")
    assert played == [("spoke",), ("open", RATE), ("add", RATE * 2), ("finish",), ("unload",)], played
    assert not r["loaded_first"]
    a.reader.active = True
    try:
        api.dev_tts_test()
        raise AssertionError("tested while reading")
    except ValueError as e:
        assert "reading" in str(e)
    a.reader.active = False
    a.server.stop(); a.reader.close()


# A stand-in harness: prints its arguments and a line a second for --pages seconds, then writes summary.md
FAKE_HARNESS = '''
import json, sys, time
from pathlib import Path
args = sys.argv[sys.argv.index("waudio.harness") + 1:]
opt = dict(zip(args[::2], args[1::2]))
print("args", json.dumps(args), flush=True)
if opt["--pages"] == "9":  # stands for a model worker the harness started: must end with it
    import subprocess
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print("worker", worker.pid, flush=True)
for i in range(int(opt["--pages"])):
    print("page", i + 1, flush=True); time.sleep(1)
run = Path(opt["--runs-dir"]) / opt["--run-id"]; run.mkdir(parents=True)
(run / "summary.md").write_text("| all | " + opt["--pages"] + " |")
print("results:", run, flush=True)
'''


def harness(limit=None):
    script = Path(tempfile.mkdtemp()) / "fake_harness.py"
    script.write_text(FAKE_HARNESS)
    h = background.HarnessRun(Path(tempfile.mkdtemp()), command=lambda: ([sys.executable, str(script), "-m", "waudio.worker"], {}))
    return h


def wait(h, seconds=20):
    wait_until(lambda: not h.running, timeout=seconds, what="the harness run to end")


def ended(pid):
    """The process is gone (or only a zombie waiting to be reaped)."""
    import psutil
    try:
        return not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def the_harness_runs_in_its_own_process_and_reports_back():
    h = harness()
    s = h.start("glm", 2, 0.95, ("ocr",), ("original",))
    assert s["running"] and s["limit"] == background.START_SECONDS + 2 * background.SECONDS_PER_PAGE
    try:
        h.start("glm", 1)
        raise AssertionError("two runs at once")
    except ValueError as e:
        assert "going" in str(e)
    wait(h)
    s = h.status()
    args = json.loads(s["lines"][0].split(" ", 1)[1])
    assert args[:4] == ["--ocr", "glm", "--pages", "2"] and args[args.index("--route") + 1] == "0.95"
    assert args[args.index("--stages") + 1] == "ocr" and s["run"].endswith(args[args.index("--run-id") + 1])
    assert s["exit"] == 0 and s["summary"] == "| all | 2 |" and "page 2" in s["lines"]


def a_harness_run_stops_on_request_and_at_its_time_limit():
    h = harness()
    h.start("ppocr", 9)
    wait_until(lambda: any(l.startswith("worker") for l in h.status()["lines"]), timeout=10, what="the harness's worker")
    worker = int(next(l for l in h.status()["lines"] if l.startswith("worker")).split()[1])
    h.stop()
    assert not h.running and h.status()["lines"][-1] == "stopped" and h.status()["summary"] is None
    wait_until(lambda: ended(worker), what="its workers to go too")
    # a run started right after a stop shows only its own output
    h.start("ppocr", 10)
    wait_until(lambda: "page 2" in h.status()["lines"], timeout=15, what="the first run's second page")
    h.stop()
    h.start("ppocr", 1); wait(h)
    pages = [l for l in h.status()["lines"] if l.startswith("page")]
    assert pages == ["page 1"], h.status()["lines"]
    real = background.START_SECONDS, background.SECONDS_PER_PAGE
    background.START_SECONDS, background.SECONDS_PER_PAGE = 1, 0
    try:
        h.start("ppocr", 10)
        wait(h)
        assert h.status()["lines"][-1].startswith("stopped: over the time limit"), h.status()["lines"]
    finally:
        background.START_SECONDS, background.SECONDS_PER_PAGE = real


CHILD = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
from waudio.harness.__main__ import end_with_parent
end_with_parent(os.getppid())
print(os.getpid(), flush=True)
time.sleep(60)
"""
PARENT = """
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, start_new_session=True)
print(child.stdout.readline().strip(), flush=True)  # then this "app" ends, as if it crashed
"""


def a_harness_whose_app_is_gone_ends_itself():
    import subprocess
    out = subprocess.run([sys.executable, "-c", PARENT, CHILD, str(Path(__file__).resolve().parents[1])],
                         capture_output=True, text=True, timeout=30)
    child = int(out.stdout.strip())
    wait_until(lambda: ended(child), timeout=8, what="the run to end itself")


def harness_options_are_checked():
    h = harness()
    for bad in ((("nope", 1), {}), (("ppocr", 1, 0.95), {}), (("glm", 0), {}), (("glm", 18), {}),
                (("glm", 1), {"stages": ("ocr", "paint")}), (("glm", 1), {"variants": ("sideways",)})):
        try:
            h.start(*bad[0], **bad[1])
            raise AssertionError(f"started with {bad}")
        except ValueError:
            pass
    assert not h.running and h.run_id is None


if sys.argv[1:] == ["--worker"]:
    from waudio.worker.__main__ import main
    main({"asr": FakeAsr, "tts": FakeTts})
    sys.exit(0)

everything_is_refused_unless_developer_mode_is_on()
loaded_models_and_their_workers_memory()
the_asr_test_reports_what_it_heard_and_how_fast()
the_tts_test_makes_a_sentence_times_it_and_plays_it()
the_harness_runs_in_its_own_process_and_reports_back()
a_harness_run_stops_on_request_and_at_its_time_limit()
harness_options_are_checked()
a_harness_whose_app_is_gone_ends_itself()
print("developer checks: ok")
