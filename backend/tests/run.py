#!/usr/bin/env python3
"""Runs Waudio's checks, each test file in its own process under a time limit.

  python3 backend/tests/run.py              the fast set: every change
  python3 backend/tests/run.py --all        fast + slow: once before merging to main
  python3 backend/tests/run.py test_read    only these files (with --all, also their slow runs)

The fast set is every test_*.py run plainly (but SLOW_ONLY); each must finish within FAST_LIMIT_S.
The slow set is the runs with real models (SLOW), each within its own limit; they read your models
from the main checkout's config.toml. A file that fails or runs over its
limit fails the run; nothing is ever run twice to get it green (a flaky check is a bug). Exit 0
only if every file passed.
A file that needs a tool says so in a line `# needs: node, chrome` (a command on PATH, or "chrome":
Google Chrome, at $CHROME or its usual place); without the tool it is skipped, said loudly, never failed —
except with --strict or on CI ($CI set): there a skip fails the run, so a missing tool can't pass unseen.
"""
import os, re, shutil, signal, subprocess, sys, tempfile, time
from pathlib import Path

import psutil

import waits  # its SLOW (how much slower this machine is), not this file's SLOW runs

HERE = Path(__file__).resolve().parent
FAST_LIMIT_S = 30
YIELD_NICE = 10  # every run at this lower priority: a test run never slows the Waudio you are using


def main_config():
    """The main checkout's config.toml (your models): a worktree's own copy is only the example's."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=HERE,
                                capture_output=True, text=True, timeout=10).stdout.strip()
        return str(Path(common).parent / "config.toml") if common else str(HERE.parents[1] / "config.toml")
    except (OSError, subprocess.SubprocessError):
        return str(HERE.parents[1] / "config.toml")


CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
NEEDS = re.compile(r"^# needs: (.+)$", re.M)


def missing(f, have=None):
    """The tools file f says it needs (`# needs: ...`) that this machine lacks."""
    have = have or (lambda tool: Path(CHROME).exists() if tool == "chrome" else shutil.which(tool))  # CHROME: read now
    m = NEEDS.search(Path(f).read_text(errors="replace"))
    return [t for t in (s.strip() for s in m.group(1).split(",")) if t and not have(t)] if m else []


SLOW = {  # file and its arguments -> limit in seconds: the runs with real models
    ("test_live.py", "--real"): 900,
    ("test_engine.py", "--real"): 900,
    ("test_asr_draft.py", "--real"): 900,
    ("test_breeze.py", "--real"): 300,
    ("test_tricky_text.py", "--real"): 900,
    ("test_worker_crash.py", "--real"): 60,
    ("test_memory.py", "--config", main_config()): 300,
}
SLOW_ONLY = {"test_memory.py",  # nothing to check without the real models: not in the fast set
             "test_worker_crash.py"}  # a real segfault leaves a macOS crash report each run


def plan(folder=HERE, every=False, names=(), slow=SLOW, fast_limit=FAST_LIMIT_S, slow_only=SLOW_ONLY):
    """[(file, args, limit)]: the fast set (or only the named files), plus with every=True the slow
    runs (of the named files only, when named)."""
    files = sorted(Path(folder).glob("test_*.py"))
    wanted = {n if n.endswith(".py") else n + ".py" for n in names}
    if wanted:
        missing = wanted - {f.name for f in files}
        if missing:
            raise SystemExit(f"no such test file: {', '.join(sorted(missing))}")
        files = [f for f in files if f.name in wanted]
    else:
        files = [f for f in files if f.name not in slow_only]
    runs = [(f, (), fast_limit) for f in files]
    if every:
        runs += [(Path(folder) / name, tuple(args), limit) for (name, *args), limit in slow.items()
                 if not wanted or name in wanted]
    return runs


def _kill(p, family):
    """The file, its process group and everything it started (even in a session of its own)."""
    for proc in family:
        try:
            proc.kill()
        except psutil.Error:
            pass
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:  # the group is gone already
        pass
    p.wait()


def _yield():
    """In the run's own process, before the file starts: lower its priority (and so everything it starts)."""
    os.nice(YIELD_NICE)


def run(runs, python=sys.executable, out=print):
    """Each run in its own process: [(name, seconds, "ok" | "failed" | "over the limit" | "skipped", output tail)]."""
    results = []
    for f, args, limit in runs:
        name = " ".join([f.name, *args])
        lacking = missing(f)
        if lacking:
            results.append((name, 0.0, "skipped", "needs " + ", ".join(lacking)))
            out(f"{'skipped':>15s}  {0:6.1f}s  {name}  (needs {', '.join(lacking)})")
            continue
        t = time.monotonic()
        # output into a file, not a pipe: a process it started can't keep the run waiting by holding
        # it open; unbuffered, so what it printed before being stopped is kept
        with tempfile.TemporaryFile("w+") as log:
            p = subprocess.Popen([python, "-u", str(f), *args], cwd=f.parent, stdout=log, stderr=subprocess.STDOUT,
                                 text=True, start_new_session=True, preexec_fn=_yield if hasattr(os, "nice") else None)
            try:
                p.wait(timeout=limit)
                state = "ok" if p.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                try:
                    family = psutil.Process(p.pid).children(recursive=True)
                except psutil.Error:
                    family = []
                _kill(p, family)
                state = "over the limit"
            log.seek(0)
            output = log.read() + (f"\nstopped after {limit}s" if state == "over the limit" else "")
        tail = output[-2000:] + "".join("\n" + line for line in output[:-2000].splitlines() if line.startswith(waits.SKIPPED_REALTIME))
        took = time.monotonic() - t
        results.append((name, took, state, tail))
        out(f"{state:>15s}  {took:6.1f}s  {name}")
        if state != "ok":
            out("    " + tail.strip().replace("\n", "\n    "))
    return results


def realtime_skips(results):
    """How many real-time checks the files skipped (waits.realtime): said in the summary, never a failure."""
    return sum(t.count(waits.SKIPPED_REALTIME) for _, _, _, t in results)


def fails(results, strict=False):
    """The run fails: a file failed or ran over, or (strict) one was skipped for a missing tool."""
    return any(r[2] not in ("ok", "skipped") or (strict and r[2] == "skipped") for r in results)


def fast_limit():
    """Each fast file's time limit: FAST_LIMIT_S, times WAUDIO_TEST_SLOW on a slow machine."""
    return FAST_LIMIT_S * waits.SLOW


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    unknown = [a for a in argv if a.startswith("-") and a not in ("--all", "--strict")]
    if unknown:
        raise SystemExit(f"unknown option: {' '.join(unknown)} (run.py [--all] [--strict] [test files])")
    every = "--all" in argv
    strict = "--strict" in argv or bool(os.environ.get("CI"))
    results = run(plan(every=every, names=[a for a in argv if not a.startswith("-")], fast_limit=fast_limit()))
    bad = [r for r in results if r[2] not in ("ok", "skipped")]
    skipped = [r for r in results if r[2] == "skipped"]
    print(f"{len(results) - len(bad) - len(skipped)} of {len(results)} passed in {sum(r[1] for r in results):.0f}s"
          + (f", {len(skipped)} SKIPPED for a missing tool" if skipped else "")
          + (f", {n} real-time checks skipped (WAUDIO_NO_REALTIME)" if (n := realtime_skips(results)) else "")
          + ("" if every else " (fast set; --all adds the real-model runs)"))
    return 1 if fails(results, strict) else 0


if __name__ == "__main__":
    sys.exit(main())
