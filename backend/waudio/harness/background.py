"""The OCR harness started from Settings → Developer → OCR: `python -m waudio.harness` in a process of its
own (started as model workers are: worker/client.command), one run at a time, ended after a hard time
limit or on stop / quit, its model workers with it (one process group). If this app dies, the harness
notices its parent is gone and ends itself (PARENT_ENV). Each run's output lines are kept for the page,
the end note last; the run's summary.md is read back.
"""
import collections, datetime, os, signal, subprocess, threading, time

from ..engine import ocr
from ..worker import client
from .__main__ import RUNS_DIR, STAGES, VARIANTS

SECONDS_PER_PAGE = 120  # a page through the whole chain takes ~30 s on an M5: four times that, then stopped
START_SECONDS = 300     # loading the models (first start after a reboot reads them from disk)
MAX_PAGES = 17          # the test set's pages per variant
PARENT_ENV = "WAUDIO_HARNESS_PARENT"  # the app's pid: the harness ends itself when its parent changes


class HarnessRun:
    def __init__(self, runs_dir=RUNS_DIR, command=client.command):
        self.runs_dir, self.command = runs_dir, command
        self.proc = None
        self.lines = collections.deque(maxlen=300)  # this run's (each run gets its own)
        self.run_id = self.exit = self.started = self.ended = self.end_note = None
        self.limit = 0
        self.lock = threading.Lock()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, setup="glm", pages=3, route=None, stages=STAGES, variants=("original",), reader=None):
        if setup not in ocr.SETUPS:
            raise ValueError(f"no OCR setup {setup!r}")
        if (setup == "custom") != bool(reader):
            raise ValueError("a page reader folder goes with the custom setup, and only with it")
        if route is not None and not (ocr.SETUPS[setup]["reader"] or reader):
            raise ValueError(f"{setup} has no page reader to route around")
        if not 1 <= int(pages) <= MAX_PAGES:
            raise ValueError(f"pages: 1 to {MAX_PAGES}")
        if not stages or any(s not in STAGES for s in stages) or not variants or any(v not in VARIANTS for v in variants):
            raise ValueError("unknown stage or variant")
        with self.lock:
            if self.running:
                raise ValueError("a harness run is going: stop it first")
            argv, env = self.command()
            run_id = "app-" + datetime.datetime.now().strftime("%y%m%d-%H%M%S")
            argv = argv[:-1] + ["waudio.harness", "--ocr", setup, "--pages", str(int(pages)), "--run-id", run_id,
                                "--runs-dir", str(self.runs_dir), "--stages", ",".join(stages), "--variants", ",".join(variants)]
            if route is not None:
                argv += ["--route", str(float(route))]
            if reader:
                argv += ["--reader", str(reader)]
            self.lines = collections.deque(maxlen=300)  # an earlier run's reader may still be draining into its own
            self.run_id, self.exit, self.ended, self.end_note, self.started = run_id, None, None, None, time.time()
            self.limit = START_SECONDS + SECONDS_PER_PAGE * int(pages) * len(variants)
            self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         cwd=client.BACKEND, start_new_session=True,  # its own group: workers too
                                         env={**os.environ, **env, PARENT_ENV: str(os.getpid())})
            threading.Thread(target=self._follow, args=(self.proc, self.lines), daemon=True, name="harness").start()
        return self.status()

    def _follow(self, proc, lines):
        killer = threading.Timer(self.limit, self._end, args=(proc, f"stopped: over the time limit ({self.limit} s)"))
        killer.daemon = True
        killer.start()
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
        code = proc.wait()
        killer.cancel()
        with self.lock:
            if proc is self.proc:
                if self.end_note:
                    lines.append(self.end_note)  # after all it printed
                self.exit, self.ended = code, time.time()

    def _end(self, proc, note):
        """Kill the run and its model workers (its process group); `note` ends its output."""
        with self.lock:
            if proc is not self.proc or proc.poll() is not None:
                return
            self.end_note = note
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

    def stop(self):
        with self.lock:
            proc = self.proc
        if proc is not None:
            self._end(proc, "stopped")
        end = time.time() + 5
        while proc is not None and self.ended is None and proc is self.proc and time.time() < end:
            time.sleep(0.02)  # until its output is read to the end and the note is in

    def status(self):
        folder = self.runs_dir / self.run_id if self.run_id else None
        summary = folder / "summary.md" if folder else None
        return {"running": self.running, "run": str(folder) if folder else None, "exit": self.exit,
                "seconds": round((self.ended or time.time()) - self.started) if self.started else None,
                "limit": self.limit, "lines": list(self.lines),
                "summary": summary.read_text() if summary and summary.is_file() else None}
