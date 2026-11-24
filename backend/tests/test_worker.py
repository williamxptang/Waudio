#!/usr/bin/env python3
"""Model workers (backend/waudio/worker): real worker processes speaking the real protocol, with a
stand-in model (Fake below, run by this file as the worker). Calls carry their arguments and audio
both ways; callbacks run in the engine while the worker waits and can stop it; an error in the model
or in a callback comes back without ending the worker; a crash, a hang, a failed load and an engine
that goes away each end the process; close ends it and leaves nothing behind; what a model prints
never gets into the messages.

  python3 backend/tests/test_worker.py
"""
import os, subprocess, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waits import wait_until
import numpy as np
import psutil
from waudio.worker import client, protocol
from waudio.worker.client import Worker, WorkerDied, WorkerError


class Fake:
    """A stand-in model: what each method does is in its name."""
    methods = ("echo", "count", "hang", "crash", "fail", "noisy")

    def __init__(self, start):
        if start.get("fail_load"):
            raise FileNotFoundError("no weights in that folder")
        self.info = {"pid": os.getpid()}

    def echo(self, args, arrays, ask):
        return args, [a * 2 for a in arrays]

    def count(self, args, arrays, ask):  # like a model making speech: a chunk, then may it go on?
        n = 0
        for n in range(1, args["upto"] + 1):
            ask("on_chunk", {"n": n}, [np.full(3, n, np.float32)])
            if ask("should_stop", {"n": n}):
                break
        return n, []

    def hang(self, args, arrays, ask):
        time.sleep(3600)

    def crash(self, args, arrays, ask):
        os._exit(3)

    def fail(self, args, arrays, ask):
        raise ValueError("bad input")

    def noisy(self, args, arrays, ask):
        print("a model library talking")
        os.write(1, b"\x00\x00\x00\x05junk!")  # C code writing straight to fd 1
        return "still fine", []


CMD = ([sys.executable, str(Path(__file__).resolve()), "--worker"], {})


def start(**extra):
    return Worker({"kind": "fake", **extra}, cmd=CMD, timeout=30)


def children():
    return [p for p in psutil.Process().children() if p.status() != psutil.STATUS_ZOMBIE]


def a_call_carries_its_arguments_and_audio_both_ways():
    w = start()
    try:
        audio = np.random.default_rng(0).normal(size=16000).astype(np.float32)
        stereo = np.arange(12, dtype=np.int16).reshape(2, 6)
        value, (a, s) = w.call("echo", {"language": "English", "prefix": "你好"}, [audio, stereo])
        assert value == {"language": "English", "prefix": "你好"}, value
        assert a.dtype == np.float32 and np.array_equal(a, audio * 2)
        assert s.dtype == np.int16 and s.shape == (2, 6) and np.array_equal(s, stereo * 2)
        assert w.info["pid"] == w.pid != os.getpid()  # the model runs in its own process
    finally:
        w.close()


def callbacks_run_here_while_the_worker_waits_and_can_stop_it():
    w = start()
    try:
        chunks = []
        on_chunk = lambda args, arrays: chunks.append((args["n"], arrays[0].tolist()))
        should_stop = lambda args, arrays: args["n"] >= 4
        value, _ = w.call("count", {"upto": 10}, callbacks={"on_chunk": on_chunk, "should_stop": should_stop})
        assert value == 4 and [n for n, _ in chunks] == [1, 2, 3, 4], (value, chunks)
        assert chunks[2][1] == [3.0, 3.0, 3.0]
    finally:
        w.close()


def an_error_in_the_model_comes_back_named_and_the_worker_carries_on():
    w = start()
    try:
        try:
            w.call("fail")
            raise AssertionError("no error")
        except WorkerError as e:
            assert type(e).__name__ == "ValueError" and str(e) == "bad input", repr(e)
        assert w.alive and w.call("echo", {"x": 1})[0] == {"x": 1}
    finally:
        w.close()


def an_error_in_a_callback_stops_the_call_and_is_raised_here():
    w = start()
    try:
        def on_chunk(args, arrays):
            if args["n"] == 2:
                raise KeyError("the player is gone")
        try:
            w.call("count", {"upto": 10}, callbacks={"on_chunk": on_chunk, "should_stop": lambda a, b: False})
            raise AssertionError("no error")
        except KeyError:
            pass
        assert w.alive and w.call("echo", {"x": 2})[0] == {"x": 2}  # the next call is answered as usual
    finally:
        w.close()


def a_crash_raises_worker_died_and_leaves_no_process():
    w = start()
    try:
        w.call("crash")
        raise AssertionError("no error")
    except WorkerDied:
        pass
    assert not w.alive and w.proc.returncode == 3
    try:
        w.call("echo")
        raise AssertionError("a dead worker answered")
    except WorkerDied:
        pass
    w.close()
    assert not children()


def a_hung_worker_is_stopped_after_the_quiet_timeout():
    w = start()
    t = time.monotonic()
    try:
        w.call("hang", timeout=1)
        raise AssertionError("no error")
    except WorkerDied:
        pass
    assert time.monotonic() - t < 5 and not w.alive
    w.close()
    assert not children()


def a_failed_load_raises_and_leaves_no_process():
    try:
        start(fail_load=True)
        raise AssertionError("no error")
    except WorkerError as e:
        assert type(e).__name__ == "FileNotFoundError" and "no weights" in str(e)
    assert not children()


def close_ends_the_process_even_mid_call():
    w = start()
    w.close()
    assert not w.alive and w.proc.returncode == 0 and not children()  # it quit when asked, and was reaped
    w.close()  # twice is fine
    busy, got = start(), []
    threading.Thread(target=lambda: got.append(_raises(lambda: busy.call("hang")))).start()
    wait_until(busy.lock.locked, what="the call in progress")
    t = time.monotonic()
    busy.close()  # a call in progress on another thread: killed, not waited on
    wait_until(lambda: got, what="the call to end")
    assert time.monotonic() - t < 3 and not busy.alive and got == [WorkerDied], got
    assert not children()


def a_worker_whose_engine_is_gone_exits():
    w = start()
    w.proc.stdin.close()  # what the engine dying looks like from the worker
    w.proc.wait(5)
    assert w.proc.returncode == 0
    w.close()
    # gone in the middle of a call, while the worker waits for a callback's answer
    p = subprocess.Popen(CMD[0], stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=client.BACKEND)
    protocol.send(p.stdin, {"op": "start", "kind": "fake"})
    assert protocol.recv(p.stdout)[0]["op"] == "ready"
    protocol.send(p.stdin, {"op": "call", "method": "count", "args": {"upto": 10 ** 9}})
    assert protocol.recv(p.stdout)[0]["name"] == "on_chunk"
    p.stdin.close()
    try:
        p.wait(5)
    finally:
        p.kill()
        p.wait()
        p.stdout.close()
    assert p.returncode == 0, p.returncode


GARBLED = """
import sys, time
sys.path.insert(0, ".")
from waudio.worker import protocol
inp, out = sys.stdin.buffer, sys.stdout.buffer
protocol.recv(inp)
protocol.send(out, {"op": "ready", "info": {}})
protocol.recv(inp)
out.write(b"\\x00\\x00\\x00\\x03{{{")  # a header that isn't JSON, then it lives on
out.flush()
time.sleep(3600)
"""


def a_garbled_message_ends_the_worker_instead_of_waiting_on_it():
    w = Worker({"kind": "fake"}, cmd=([sys.executable, "-c", GARBLED], {}), timeout=30)
    t = time.monotonic()
    try:
        w.call("echo", timeout=30)
        raise AssertionError("no error")
    except WorkerDied:
        pass
    assert time.monotonic() - t < 5 and not w.alive, time.monotonic() - t
    w.close()
    assert not children()


def what_a_model_prints_never_gets_into_the_messages():
    w = start()
    try:
        assert w.call("noisy")[0] == "still fine"
        assert w.call("echo", {"after": True})[0] == {"after": True}
    finally:
        w.close()


def the_app_starts_workers_as_waudio_models():
    """Inside the app (launched as "<NAME> Engine"), the worker is "<NAME> Models" beside it."""
    helpers = Path(tempfile.mkdtemp()) / "Helpers"
    engine = helpers / f"{client.NAME} Engine.app/Contents/MacOS/{client.NAME} Engine"
    models = helpers / f"{client.NAME} Models.app/Contents/MacOS/{client.NAME} Models"
    for exe in (engine, models):
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("")
        exe.chmod(0o755)
    real = sys.orig_argv
    try:
        sys.orig_argv = [str(engine), "-m", "waudio"]
        argv, env = client.command()
        assert argv == [str(models), "-m", "waudio.worker"] and env == {"__PYVENV_LAUNCHER__": sys.executable}
        models.chmod(0o644)  # no Models helper to run: this Python
        assert client.command() == ([sys.executable, "-m", "waudio.worker"], {})
    finally:
        sys.orig_argv = real
    assert client.command() == ([sys.executable, "-m", "waudio.worker"], {})  # not in the app


def _raises(fn):
    try:
        fn()
    except Exception as e:
        return type(e)


if sys.argv[1:] == ["--worker"]:
    from waudio.worker.__main__ import main
    main({"fake": Fake})
    sys.exit(0)

a_call_carries_its_arguments_and_audio_both_ways()
callbacks_run_here_while_the_worker_waits_and_can_stop_it()
an_error_in_the_model_comes_back_named_and_the_worker_carries_on()
an_error_in_a_callback_stops_the_call_and_is_raised_here()
a_crash_raises_worker_died_and_leaves_no_process()
a_hung_worker_is_stopped_after_the_quiet_timeout()
a_failed_load_raises_and_leaves_no_process()
close_ends_the_process_even_mid_call()
a_worker_whose_engine_is_gone_exits()
what_a_model_prints_never_gets_into_the_messages()
a_garbled_message_ends_the_worker_instead_of_waiting_on_it()
the_app_starts_workers_as_waudio_models()
print("worker checks: ok")
