#!/usr/bin/env python3
"""The check runner (run.py), on made-up test files in a throwaway folder: a file that fails or runs
over its limit fails the run, one over the limit is stopped with everything it started (a helper in
a session of its own too), a helper left running never holds the run, what a file printed before its
limit is kept, the fast set leaves out the real-model runs and --all adds them, only named files run
when named, an unknown option is refused, and every file runs at a lower priority.

  python3 backend/tests/test_runner.py
"""
import subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as runner

CHECKS = []


def check(f):
    CHECKS.append(f)
    return f


def folder(files):
    """A throwaway tests folder holding these test_*.py files (name -> source)."""
    d = Path(tempfile.mkdtemp())
    for name, source in files.items():
        (d / name).write_text(source)
    return d


def states(results):
    return {name: state for name, _, state, _ in results}


@check
def a_failing_file_fails_the_run():
    d = folder({"test_good.py": "print('ok')\n", "test_bad.py": "assert 1 == 2, 'broken'\n"})
    results = runner.run(runner.plan(d, slow={}), out=lambda line: None)
    assert states(results) == {"test_bad.py": "failed", "test_good.py": "ok"}, states(results)
    assert "broken" in dict((n, t) for n, _, _, t in results)["test_bad.py"]


@check
def a_file_whose_tool_is_missing_is_skipped_not_failed():
    """`# needs: <tool>` with the tool absent: the file never runs (its failing body isn't reached), it is
    reported as skipped with the tool named; a file whose tools are there runs as usual."""
    d = folder({"test_lacks.py": "# needs: no-such-tool-waudio\nassert False, 'ran'\n",
                "test_has.py": "# needs: python3\nprint('ok')\n"})
    results = runner.run(runner.plan(d, slow={}), out=lambda line: None)
    assert states(results) == {"test_lacks.py": "skipped", "test_has.py": "ok"}, states(results)
    assert "no-such-tool-waudio" in dict((n, t) for n, _, _, t in results)["test_lacks.py"]


@check
def a_skip_fails_a_strict_run_only():
    """Locally a skip is reported, not failed; with --strict or on CI it fails the run. Chrome counts as
    missing when it isn't where $CHROME (or its usual place) says."""
    skipped = [("test_x.py", 0.0, "skipped", "needs chrome"), ("test_y.py", 1.0, "ok", "")]
    assert not runner.fails(skipped) and runner.fails(skipped, strict=True)
    assert runner.fails([("test_z.py", 1.0, "failed", "")])
    d = folder({"test_c.py": "# needs: chrome\nprint('ok')\n"})
    was, runner.CHROME = runner.CHROME, str(Path(d) / "no-chrome-here")
    try:
        assert runner.missing(Path(d) / "test_c.py") == ["chrome"]
    finally:
        runner.CHROME = was


@check
def a_file_over_its_limit_is_stopped_with_what_it_started():
    """The file and a process it started (holding its output open) are both gone at the limit."""
    marker = Path(tempfile.mkdtemp()) / "child.pid"
    d = folder({"test_slow.py": f"import subprocess, sys, time, pathlib\n"
                                f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                                f"pathlib.Path({str(marker)!r}).write_text(str(c.pid))\n"
                                f"time.sleep(60)\n"})
    t = time.monotonic()
    results = runner.run(runner.plan(d, slow={}, fast_limit=2), out=lambda line: None)
    took = time.monotonic() - t
    assert states(results) == {"test_slow.py": "over the limit"}, states(results)
    assert took < 10, f"the run waited {took:.0f}s for a file limited to 2s"
    assert not alive(int(marker.read_text())), "the process it started is still running"


def alive(pid):
    time.sleep(0.2)
    state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
    return state not in ("", "Z")


@check
def a_process_in_a_session_of_its_own_neither_holds_the_run_nor_survives_the_limit():
    """What a harness does: start a helper in its own session, which inherits the file's output."""
    marker = Path(tempfile.mkdtemp()) / "child.pid"
    d = folder({"test_escape.py": f"import subprocess, sys, time, pathlib\n"
                                  f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\n"
                                  f"pathlib.Path({str(marker)!r}).write_text(str(c.pid))\n"
                                  f"time.sleep(60)\n"})
    t = time.monotonic()
    results = runner.run(runner.plan(d, slow={}, fast_limit=2), out=lambda line: None)
    assert states(results) == {"test_escape.py": "over the limit"} and time.monotonic() - t < 10, states(results)
    assert not alive(int(marker.read_text())), "the helper in its own session is still running"


@check
def a_file_that_ends_while_its_helper_runs_on_does_not_hold_the_run():
    marker = Path(tempfile.mkdtemp()) / "child.pid"
    d = folder({"test_leaves.py": f"import subprocess, sys, pathlib\n"
                                  f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)\n"
                                  f"pathlib.Path({str(marker)!r}).write_text(str(c.pid))\n"
                                  f"print('done')\n"})
    t = time.monotonic()
    results = runner.run(runner.plan(d, slow={}, fast_limit=20), out=lambda line: None)
    assert states(results) == {"test_leaves.py": "ok"} and time.monotonic() - t < 10, (states(results), time.monotonic() - t)
    subprocess.run(["kill", marker.read_text()])  # the helper this made-up file left behind


@check
def what_a_file_printed_before_its_limit_is_kept():
    d = folder({"test_talks.py": "import time\nprint('got as far as step 3')\ntime.sleep(60)\n"})
    results = runner.run(runner.plan(d, slow={}, fast_limit=2), out=lambda line: None)
    (_, _, state, tail), = results
    assert state == "over the limit" and "got as far as step 3" in tail, tail


@check
def every_file_and_what_it_starts_runs_at_a_lower_priority():
    """So a test run never slows the Waudio in use: the file, and a process it starts, are YIELD_NICE below the runner."""
    d = folder({"test_nice.py": "import os, subprocess, sys\nprint('file', os.nice(0))\n"
                                "print('child', subprocess.run([sys.executable, '-c', 'import os; print(os.nice(0))'], capture_output=True, text=True).stdout.strip())\n"})
    (_, _, state, tail), = runner.run(runner.plan(d, slow={}), out=lambda line: None)
    import os
    want = min(os.nice(0) + runner.YIELD_NICE, 19)  # 19: the lowest priority macOS and Linux give
    assert want > os.nice(0) or os.nice(0) == 19, ("a test run must start below the runner", runner.YIELD_NICE)
    assert state == "ok" and f"file {want}" in tail and f"child {want}" in tail, (want, tail)


@check
def an_unknown_option_is_refused_before_anything_runs():
    """A typo (--al) must not quietly run the fast set instead."""
    r = subprocess.run([sys.executable, str(runner.HERE / "run.py"), "--al"], capture_output=True, text=True, timeout=10)
    assert r.returncode != 0 and "unknown option: --al" in r.stderr and "passed" not in r.stdout, (r.returncode, r.stdout, r.stderr)


@check
def the_fast_set_leaves_out_the_slow_runs_and_all_adds_them():
    d = folder({"test_a.py": "", "test_b.py": "", "test_models_only.py": ""})
    slow = {("test_a.py", "--real"): 600, ("test_models_only.py", "--config", "x.toml"): 300}
    kw = dict(slow=slow, fast_limit=30, slow_only={"test_models_only.py"})
    fast = [(f.name, args, limit) for f, args, limit in runner.plan(d, **kw)]
    every = [(f.name, args, limit) for f, args, limit in runner.plan(d, every=True, **kw)]
    assert fast == [("test_a.py", (), 30), ("test_b.py", (), 30)], fast
    assert every == fast + [("test_a.py", ("--real",), 600), ("test_models_only.py", ("--config", "x.toml"), 300)], every


@check
def named_files_run_alone_with_their_slow_runs_on_all_and_an_unknown_name_is_refused():
    d = folder({"test_a.py": "", "test_b.py": ""})
    slow = {("test_a.py", "--real"): 600}
    named = [(f.name, args) for f, args, _ in runner.plan(d, names=["test_b"], slow=slow)]
    assert named == [("test_b.py", ())], named
    with_slow = [(f.name, args) for f, args, _ in runner.plan(d, every=True, names=["test_a"], slow=slow)]
    assert with_slow == [("test_a.py", ()), ("test_a.py", ("--real",))], with_slow
    try:
        runner.plan(d, names=["test_nope"])
    except SystemExit as e:
        assert "test_nope.py" in str(e), e
    else:
        raise AssertionError("an unknown test file was accepted")


@check
def every_slow_run_names_a_test_file_that_exists_and_every_file_runs():
    assert runner.SLOW, "the slow set is empty"
    for (name, *args), limit in runner.SLOW.items():
        assert (runner.HERE / name).is_file() and args and limit > runner.FAST_LIMIT_S, (name, args, limit)
    assert runner.SLOW_ONLY <= {name for name, *_ in runner.SLOW}, "a file left out of the fast set never runs"


assert CHECKS
for fn in CHECKS:
    fn()
print(f"runner checks: ok ({len(CHECKS)})")
