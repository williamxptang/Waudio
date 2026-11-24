#!/usr/bin/env python3
"""A model worker that really crashes in C (a segfault, as a model library can) leaves Python's stacks in
its crash file, and the app's error carries them — faulthandler itself, end to end.

In the slow set only (tests/run.py SLOW_ONLY): a real segfault makes macOS write a crash report to
~/Library/Logs/DiagnosticReports on every run, so it runs once before main, not on every change.
test_remote.py checks the same error from a trace written without crashing.

  python3 backend/tests/test_worker_crash.py --real
"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.worker import client
from waudio.worker.client import WorkerDied

client.CRASH_DIR = Path(tempfile.mkdtemp())  # never your real logs folder
SEGFAULT = ("import ctypes, os, sys; sys.path.insert(0, %r); from waudio.worker import __main__ as w; "
            "w.trace_crashes(os.environ['WAUDIO_CRASH_LOG']); ctypes.string_at(0)") % str(Path(__file__).resolve().parents[1])


def a_segfault_in_a_worker_leaves_its_stacks_in_the_error():
    try:
        client.Worker({"kind": "asr"}, cmd=([sys.executable, "-c", SEGFAULT], {}))
        raise AssertionError("no error")
    except WorkerDied as died:
        assert "Segmentation fault" in str(died) and "code -11" in str(died), str(died)
    assert "Segmentation fault" in (client.CRASH_DIR / "worker-asr.crash.log").read_text()


if "--real" not in sys.argv[1:]:
    sys.exit("a real segfault leaves a macOS crash report: run with --real (tests/run.py --all does)")
a_segfault_in_a_worker_leaves_its_stacks_in_the_error()
print("worker crash: ok")
