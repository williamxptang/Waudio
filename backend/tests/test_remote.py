#!/usr/bin/env python3
"""Dictation and read aloud with their model in a worker process (backend/waudio/worker/remote.py):
the real Engine and RemoteTts, real worker processes, stand-in models (FakeAsr, FakeTts below, run
by this file as the worker). The repeat-loop stop reaches the model across the pipe; speech comes
back whole or chunk by chunk and stops when told; a missing voice is still VoiceMissing; a worker
that dies leaves the model unloaded and the next use loads it again; unload ends the process; the
engine itself never imports a model library.

  python3 backend/tests/test_remote.py
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import psutil
from waudio.engine import Engine, models, tts
import tempfile
from waudio.worker import client
from waudio.worker.client import WorkerDied, WorkerError
client.CRASH_DIR = Path(tempfile.mkdtemp())  # never your real logs folder
from waudio.worker.remote import RemoteRunner, RemoteTts

CMD = ([sys.executable, str(Path(__file__).resolve()), "--worker"], {})
RATE = 24000


class FakeAsr:
    """Hears "hello" in any audio; context "crash" ends the process; context "loop" says the same
    word until should_stop agrees (as a model stuck in a repeat loop would)."""
    methods = ("transcribe", "rollback")

    def __init__(self, start):
        self.info = {}

    def transcribe(self, args, arrays, ask):
        if args["context"] == "crash":
            os._exit(9)
        if args["context"] == "dump":  # what faulthandler writes when a model library crashes in C
            import faulthandler
            from waudio.worker import __main__ as worker
            faulthandler.dump_traceback(file=worker._crash_file, all_threads=True); worker._crash_file.flush()
            os._exit(11)  # not a real segfault: that would leave a macOS crash report (test_worker_crash.py does it)
        text = "hello"
        if args["context"] == "loop":
            for _ in range(10_000):
                text += " again"
                if ask("should_stop", {"text": text}):
                    break
        if args["context"] == "cap":  # ran to max_tokens
            return [args["language"] or "English", text, args["prefix"] + text, True], []
        if args["context"] == "caploop":  # ran to max_tokens saying the same thing over and over
            text = "hello " + "again " * 30
            return [args["language"] or "English", text, args["prefix"] + text, True], []
        return [args["language"] or "English", text, args["prefix"] + text], []

    def rollback(self, args, arrays, ask):
        return args["raw"][:-args["tokens"]], []


class FakeTts:
    """Speaks a second of tone per piece (in 4 chunks when streaming); text "crash" ends the process,
    voice "nobody" isn't there."""
    methods = ("speak",)

    def __init__(self, start):
        self.info = {"sample_rate": RATE, "is_breeze": start["cfg"]["model"] == "breeze"}

    def speak(self, args, arrays, ask):
        if args["text"] == "crash":
            os._exit(9)
        if args["voice"] == "nobody":
            raise tts.VoiceMissing("nobody")
        made = []
        for i in range(4):
            if ask("should_stop", {}):
                return None, [np.zeros(0, np.float32)]
            chunk = np.full(RATE // 4, 0.1 * (i + 1), np.float32)
            made.append(chunk)
            if args["stream"]:
                ask("on_chunk", {}, [chunk])
        whole = np.concatenate(made)[:int(RATE / args.get("speed", 1.0))]  # stands in for the stretch
        return None, [np.zeros(0, np.float32) if args["stream"] else whole]


def workers():
    return [p for p in psutil.Process().children() if p.status() != psutil.STATUS_ZOMBIE]


real_load = models.load
models.load = lambda name, model: RemoteRunner(name, model, cmd=CMD)  # engine.py calls models.load


def dictation_runs_in_a_worker_and_unload_ends_it():
    e = Engine({"model": "m", "backend": "fake", "hotwords": [], "language": "auto"})
    r = e.transcribe(np.zeros(16000, np.float32))
    assert r.text == "hello" and r.language == "English" and e.loaded, r
    assert e.rollback("abcdefgh", 3) == "abcde"
    assert len(workers()) == 1
    e.unload()
    assert not e.loaded and not workers()


def a_repeat_loop_is_stopped_across_the_pipe():
    e = Engine({"model": "m", "backend": "fake", "hotwords": ["loop"], "language": "auto", "script": "as_is"})
    r = e.transcribe(np.zeros(16000, np.float32))
    assert r.cut and r.text.count("again") == 1, r  # stopped by the engine's loop check, then cut
    assert len(r.raw) < 200, len(r.raw)  # the model stopped soon after the loop began, not at 10,000
    e.unload()


def a_worker_that_crashes_leaves_its_trace_in_the_error():
    """A model library crashing in C: the error says where (the stacks faulthandler writes to the worker's
    crash file in the logs folder), not just an exit code — when the answer never comes, and when the
    worker is already gone as a request is sent. An old trace is never blamed for a new end."""
    e = Engine({"model": "m", "backend": "fake", "hotwords": ["dump"], "language": "auto"})
    try:
        e.transcribe(np.zeros(16000, np.float32))
        raise AssertionError("no error")
    except WorkerDied as died:
        assert "code 11" in str(died) and "test_remote.py" in str(died) and "transcribe" in str(died), str(died)
    assert not workers()
    trace = client.CRASH_DIR / "worker-asr.crash.log"
    assert trace.is_file()
    # already gone when the next request is sent
    w = client.Worker({"kind": "asr", "runner": "fake", "model": "m"}, cmd=CMD)
    w.proc.kill(); w.proc.wait()
    trace.write_text('Fatal Python error: Segmentation fault\n  File "model.py", line 1 in run\n')
    try:
        w.call("transcribe", {}, [np.zeros(1, np.float32)])
        raise AssertionError("no error")
    except WorkerDied as died:
        assert "Segmentation fault" in str(died) and "model.py" in str(died), str(died)
    w.close()
    # a frame deep in a long folder path keeps its end: the file and the function are what tell
    w = client.Worker({"kind": "asr", "runner": "fake", "model": "m"}, cmd=CMD)
    w.proc.kill(); w.proc.wait()
    trace.write_text('Fatal Python error: Segmentation fault\n  File "/' + "very long folder/" * 20 + 'model_lib.py", line 7 in decode_step\n')
    try:
        w.call("transcribe", {}, [np.zeros(1, np.float32)])
        raise AssertionError("no error")
    except WorkerDied as died:
        assert "model_lib.py" in str(died) and "decode_step" in str(died), str(died)
    w.close()
    # a worker that dies before it could empty the file itself (at import, say): the old trace isn't its
    trace.write_text("Fatal Python error: Segmentation fault\n")
    try:
        client.Worker({"kind": "asr"}, cmd=([sys.executable, "-c", "import sys; sys.exit(3)"], {}))
        raise AssertionError("no error")
    except WorkerDied as died:
        assert "code 3" in str(died) and "Segmentation fault" not in str(died), str(died)


def a_worker_that_dies_mid_dictation_leaves_the_model_unloaded_and_it_loads_again():
    cfg = {"model": "m", "backend": "fake", "hotwords": ["crash"], "language": "auto"}
    e = Engine(cfg)
    try:
        e.transcribe(np.zeros(16000, np.float32))
        raise AssertionError("no error")
    except WorkerDied:
        pass
    assert not e.loaded and not workers()
    cfg["hotwords"] = []
    assert e.transcribe(np.zeros(16000, np.float32)).text == "hello" and e.loaded
    e.runner.worker.proc.kill()  # killed while idle (by the system, say): seen as unloaded
    e.runner.worker.proc.wait()
    assert not e.loaded
    assert e.transcribe(np.zeros(16000, np.float32)).text == "hello"
    e.unload()
    assert not workers()


def voices(folder):
    (folder / "calm.wav").write_bytes(b"")
    (folder / "calm.txt").write_text("Words of the clip.")
    return {"voices_dir": str(folder), "voice": "calm", "speed": 1.0}


def read_aloud_runs_in_a_worker_whole_or_in_chunks():
    import tempfile
    cfg = {"model": "plain", **voices(Path(tempfile.mkdtemp()))}
    t = RemoteTts(cfg, cmd=CMD)
    assert not t.loaded and t.sample_rate == RATE and t.voice() is not None
    audio = t.speak("One sentence.")
    assert t.loaded and len(audio) == RATE and not t.streams
    assert len(t.speak("One sentence.", speed=2.0)) == RATE // 2  # a part's own speed reaches the worker
    cfg["model"] = "breeze"  # another model chosen: the next piece swaps the worker
    old = t.worker.pid
    chunks = []
    assert t.streams is False  # not known until it is loaded
    t.load()
    assert t.worker.pid != old and not psutil.pid_exists(old) and t.streams
    rest = t.speak("One sentence.", on_chunk=lambda a: chunks.append(len(a)))
    assert chunks == [RATE // 4] * 4 and len(rest) == 0, chunks
    asked = []
    stop = lambda: asked.append(1) or len(asked) > 2
    assert len(t.speak("Stopped.", should_stop=stop)) == 0 and len(asked) == 3
    t.unload()
    assert not t.loaded and not workers()


def a_missing_voice_is_still_voice_missing_and_a_dead_worker_loads_again():
    import tempfile
    t = RemoteTts({"model": "plain", **voices(Path(tempfile.mkdtemp()))}, cmd=CMD)
    try:
        t.speak("Hello.", voice="nobody")
        raise AssertionError("no error")
    except tts.VoiceMissing as e:
        assert "nobody" in str(e)
    assert t.loaded  # the worker carries on
    try:
        t.speak("crash")
        raise AssertionError("no error")
    except WorkerDied:
        pass
    assert not t.loaded and not workers()
    assert len(t.speak("Hello.")) == RATE
    t.unload()


def the_real_worker_loads_a_runner_in_its_own_process():
    """models.load as the app uses it: python -m waudio.worker, the real runner module. A folder
    with no model: the runner's error comes back by name, and no worker is left."""
    try:
        real_load("mlx_audio", "/nonexistent/model")
        raise AssertionError("no error")
    except WorkerError as e:
        assert type(e).__name__ == "FileNotFoundError" and "nonexistent" in str(e), repr(e)
    assert not workers()


def the_engine_never_imports_a_model_library():
    heavy = sorted(m for m in ("mlx", "mlx_audio", "torch", "scipy", "transformers") if m in sys.modules)
    assert not heavy, f"the engine side loaded {heavy}"


def the_worker_hands_a_parts_speed_to_its_engine():
    from waudio.worker.__main__ import Tts
    class Engine:  # stands in for the voice model: notes what it was asked
        def speak(self, text, **how): self.how = how; return np.zeros(RATE, np.float32)
    w = Tts.__new__(Tts); w.engine = Engine()  # without loading a model
    args = {"text": "a", "voice": None, "persona_text": None, "cfg": {}, "stream": False}
    w.speak({**args, "speed": 1.5}, [], lambda *a: False)
    assert w.engine.how["speed"] == 1.5, w.engine.how
    w.speak(args, [], lambda *a: False)  # an app from before part speeds: as before
    assert w.engine.how["speed"] == 1.0, w.engine.how



def a_dictation_cut_at_the_token_cap_says_so_across_the_pipe():
    """The runner says it ran to max_tokens: the real worker handler passes it on, and the engine's
    Result has capped; a runner from before (three answers) is not capped."""
    from waudio.worker.__main__ import Asr
    class Runner:
        def __init__(self, answer): self.answer = answer
        def transcribe(self, *a, **k): return self.answer
    w = Asr.__new__(Asr)
    args = {"language": None, "max_tokens": 8, "context": "", "prefix": "", "strict": False}
    for answer, capped in ((("English", "hi", "hi", True), True), (("English", "hi", "hi", False), False),
                           (("English", "hi", "hi"), False)):
        w.runner = Runner(answer)
        assert w.transcribe(args, [np.zeros(1, np.float32)], lambda *a: False)[0][3] is capped, answer
    e = Engine({"model": "m", "backend": "fake", "hotwords": ["cap"], "language": "auto"})  # the context reaches FakeAsr in the worker
    assert e.transcribe(np.zeros(16000, np.float32)).capped is True
    e.cfg["hotwords"] = ["caploop"]  # the budget ran out inside a repeat loop: the loop is cut, nothing is missing
    r = e.transcribe(np.zeros(16000, np.float32))
    assert r.cut is True and r.capped is False, (r.cut, r.capped, r.text)
    e.cfg["hotwords"] = []
    assert e.transcribe(np.zeros(16000, np.float32)).capped is False
    e.unload()


def a_lines_mood_reaches_the_engine_in_the_worker():
    import json
    from waudio.worker.__main__ import Tts
    class Pipe:  # stands in for the worker process: keeps what is sent, as the pipe carries it (JSON)
        alive = True
        def call(self, method, args, callbacks=None): self.args = json.loads(json.dumps(args)); return None, [np.zeros(0, np.float32)]
    class Engine:  # stands in for the voice model: notes what it was asked
        def speak(self, text, **how): self.how = how; return np.zeros(RATE, np.float32)
    w = Tts.__new__(Tts); w.engine = Engine()  # without loading a model
    for persona, moody in ((tts.Moody("Sad now. " + tts.MOOD_LEAD), True), ("Calm.", False), (None, False)):
        t = RemoteTts({"model": "plain"}, cmd=CMD); t.worker, t.loaded_model = Pipe(), "plain"
        t.speak("a", persona_text=persona)
        w.speak(t.worker.args, [], lambda *a: False)
        assert w.engine.how["persona_text"] == persona and isinstance(w.engine.how["persona_text"], tts.Moody) == moody, (persona, w.engine.how)


if sys.argv[1:] == ["--worker"]:
    from waudio.worker.__main__ import main
    main({"asr": FakeAsr, "tts": FakeTts})
    sys.exit(0)

dictation_runs_in_a_worker_and_unload_ends_it()
a_repeat_loop_is_stopped_across_the_pipe()
a_worker_that_crashes_leaves_its_trace_in_the_error()
a_worker_that_dies_mid_dictation_leaves_the_model_unloaded_and_it_loads_again()
read_aloud_runs_in_a_worker_whole_or_in_chunks()
a_missing_voice_is_still_voice_missing_and_a_dead_worker_loads_again()
the_real_worker_loads_a_runner_in_its_own_process()
the_engine_never_imports_a_model_library()
the_worker_hands_a_parts_speed_to_its_engine()
a_dictation_cut_at_the_token_cap_says_so_across_the_pipe()
a_lines_mood_reaches_the_engine_in_the_worker()
assert not workers()
print("remote checks: ok")
