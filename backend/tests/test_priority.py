#!/usr/bin/env python3
"""Keeping Waudio quick on a busy computer (waudio/priority.py): a work thread asks for user-interactive priority on
macOS (the speaker's and a model worker's threads do), and a model worker keeps what it loaded wired in RAM (with MLX), with room to spare.

  python3 backend/tests/test_priority.py
"""
import ctypes, sys, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import priority


def a_work_thread_runs_at_user_interactive_priority():
    if sys.platform != "darwin":
        assert priority.urgent_thread() is False
        return
    libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
    libc.pthread_self.restype = ctypes.c_void_p  # a pointer: as an int it is cut short
    libc.pthread_get_qos_class_np.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_int)]
    seen = {}
    def work():
        seen["took"] = priority.urgent_thread()
        qos, rel = ctypes.c_uint(), ctypes.c_int()
        libc.pthread_get_qos_class_np(libc.pthread_self(), ctypes.byref(qos), ctypes.byref(rel))
        seen["qos"] = qos.value
    t = threading.Thread(target=work); t.start(); t.join()
    assert seen == {"took": True, "qos": priority.QOS_USER_INTERACTIVE}, seen


def a_loaded_model_is_wired_with_room_to_spare():
    import mlx.core as mx
    weights = mx.ones((64, 1024, 1024), dtype=mx.float16); mx.eval(weights)  # 128 MB standing in for a model
    held = mx.get_active_memory()
    wired = priority.wire_model()
    try:
        most = mx.device_info()["max_recommended_working_set_size"]
        assert wired is not None and wired <= held * priority.WIRE_MARGIN + 1 and wired <= most, (held, wired, most)  # not more
        assert wired > held or wired == most, (held, wired, most)  # room to spare, unless the device has no more to give
        assert mx.set_wired_limit(0) == wired  # what MLX was holding to
    finally:
        mx.set_wired_limit(0)


def nothing_loaded_nothing_wired():
    import mlx.core as mx
    real = mx.get_active_memory
    mx.get_active_memory = lambda: 0  # weights not loaded yet
    try:
        assert priority.wire_model() is None
    finally:
        mx.get_active_memory = real


def without_mlx_nothing_is_wired():
    mods = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "mlx.core"}
    try:
        assert priority.wire_model() is None
    finally:
        sys.modules.update(mods)


def qos_here():
    libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
    libc.pthread_self.restype = ctypes.c_void_p
    libc.pthread_get_qos_class_np.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_int)]
    qos, rel = ctypes.c_uint(), ctypes.c_int()
    libc.pthread_get_qos_class_np(libc.pthread_self(), ctypes.byref(qos), ctypes.byref(rel))
    return qos.value


def the_speaker_thread_runs_at_user_interactive_priority():
    """The player opens its speaker on its own thread: that thread asked for it."""
    if sys.platform != "darwin":
        return
    import numpy as np
    from waudio.output.player import Player
    seen, done = [], threading.Event()
    class Speaker:
        def start(self): pass
        def stop(self): pass
        def abort(self): pass
        def close(self): pass
        def write(self, block): pass
    def make(rate):
        seen.append(qos_here()); return Speaker()
    p = Player(16000, on_done=lambda *a: done.set(), stream_factory=make)
    p.add(0, np.zeros(800, np.float32)); p.finish()
    assert done.wait(5) and seen == [priority.QOS_USER_INTERACTIVE], seen


def a_model_worker_wires_what_it_loaded_and_runs_urgently():
    """worker.serve: after "start" the model's memory is wired; worker.main's thread asked for urgent priority."""
    import io
    import mlx.core as mx
    from waudio.worker import __main__ as worker, protocol
    class Loads:
        methods = ()
        def __init__(self, start):
            self.w = mx.ones((32, 1024, 1024), dtype=mx.float16); mx.eval(self.w); self.info = {}
    inp, out = io.BytesIO(), io.BytesIO()
    protocol.send(inp, {"op": "start", "kind": "x"}); protocol.send(inp, {"op": "quit"}); inp.seek(0)
    mx.set_wired_limit(0)
    seen = {}
    real_serve = worker.serve
    worker.serve = lambda handlers, i, o: (seen.setdefault("qos", qos_here() if sys.platform == "darwin" else None), real_serve({"x": Loads}, inp, out))
    t = threading.Thread(target=lambda: worker.main({"x": Loads}))
    import os
    saved = os.dup(1)
    try:
        t.start(); t.join(30)
    finally:
        os.dup2(saved, 1); os.close(saved); sys.stdout = sys.__stdout__; worker.serve = real_serve
    wired = mx.set_wired_limit(0)
    assert wired > 0, wired
    if sys.platform == "darwin":
        assert seen["qos"] == priority.QOS_USER_INTERACTIVE, seen


def the_reader_and_dictation_workers_ask_for_it():
    """The threads that read aloud and transcribe call urgent_thread() themselves (it is per thread)."""
    import queue
    from waudio import app as A
    from waudio.reader import Reader
    asked, real = [], priority.urgent_thread
    priority.urgent_thread = lambda: asked.append(threading.current_thread().name) or True
    try:
        r = Reader({"model": "m", "volume": 0.5}, engine=object())
        fake = type("App", (), {})(); fake.jobs = queue.Queue(); fake.jobs.put(None)
        t = threading.Thread(target=A.App.worker, args=(fake,), name="dictation"); t.start(); t.join(5)
        r.jobs.put(None)
        deadline = __import__("time").monotonic() + 5
        while len(asked) < 2 and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.02)
    finally:
        priority.urgent_thread = real
    assert len(asked) == 2 and "dictation" in asked and all(n != threading.main_thread().name for n in asked), asked


a_work_thread_runs_at_user_interactive_priority()
without_mlx_nothing_is_wired()
nothing_loaded_nothing_wired()
a_loaded_model_is_wired_with_room_to_spare()
the_speaker_thread_runs_at_user_interactive_priority()
the_reader_and_dictation_workers_ask_for_it()
a_model_worker_wires_what_it_loaded_and_runs_urgently()
print("priority checks: ok")
