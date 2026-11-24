"""The engine's side of a model worker (worker/__main__.py): start one, call it, close it.

Closing it ends the process, so all the memory the model took goes back to the system. A worker
that crashes, is killed or stops answering raises WorkerDied; the engine stays up.
"""
import logging, os, queue, subprocess, sys, threading
from pathlib import Path

from .. import NAME
from . import protocol

LOAD_TIMEOUT_S = 600    # loading a model from disk
QUIET_TIMEOUT_S = 120   # a call with no word from the worker for this long: it hangs, and is killed
CLOSE_WAIT_S = 3        # after quit, before it is killed
BACKEND = Path(__file__).resolve().parents[2]  # the folder holding the waudio package
CRASH_DIR = None        # where a worker's crash trace goes (worker-<kind>.crash.log): set by the app to
                        # its logs folder (app.setup_logging); None (tests, tools): no trace file
TRACE_LINES = 12        # of it, at most, in the error
TRACE_WIDTH = 160       # characters of each of those lines (its end: the file and the function)

log = logging.getLogger("waudio")


class WorkerDied(RuntimeError):
    """The model worker exited, crashed or stopped answering."""


class WorkerError(RuntimeError):
    """The model raised an error. Each one is a subclass named after the worker's own exception."""


def command():
    """(argv, extra env) that start a worker. In the app: "<NAME> Models" (packaging/macos/build_app.sh),
    Python under that name beside "<NAME> Engine", so Activity Monitor tells the two apart; it runs
    this venv as the engine's host does (__PYVENV_LAUNCHER__). Anywhere else: this Python."""
    launched = Path(sys.orig_argv[0]) if sys.orig_argv else Path()
    if launched.name == f"{NAME} Engine":
        exe = launched.parents[3] / f"{NAME} Models.app" / "Contents" / "MacOS" / f"{NAME} Models"
        if os.access(exe, os.X_OK):
            return [str(exe), "-m", "waudio.worker"], {"__PYVENV_LAUNCHER__": sys.executable}
    return [sys.executable, "-m", "waudio.worker"], {}


def crash_log(kind):
    """The file a worker of this kind writes its crash trace to, emptied now (an old crash's trace is
    never taken for a new one's), or None if there is no CRASH_DIR."""
    if CRASH_DIR is None:
        return None
    path = Path(CRASH_DIR) / f"worker-{kind}.crash.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    except OSError:
        return None
    return path


class Worker:
    def __init__(self, start, cmd=None, timeout=LOAD_TIMEOUT_S):
        """start: the start message ({"kind": "asr" | "tts", …}). Returns once the model is loaded."""
        argv, env = cmd or command()
        self.crash_log = crash_log(start.get("kind") or "model")
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=BACKEND,
                                     env={**os.environ, **env, **({"WAUDIO_CRASH_LOG": str(self.crash_log)} if self.crash_log else {})})
        self.inbox = queue.Queue()
        self.lock = threading.Lock()  # one call at a time
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self._send({"op": "start", **start})
            header, _ = self._next(timeout)
            if header["op"] == "error":
                raise worker_error(header)
        except BaseException:
            self.close()
            raise
        self.info = header.get("info") or {}
        log.info(f"model worker {self.proc.pid} started ({start.get('kind')})")

    @property
    def alive(self):
        return self.proc.poll() is None

    @property
    def pid(self):
        return self.proc.pid

    def call(self, method, args=None, arrays=(), callbacks=None, timeout=QUIET_TIMEOUT_S):
        """-> (value, arrays). callbacks: {name: fn(args, arrays) -> JSON value}, run here while the
        worker waits. An error in one stops the call and is raised here once the worker has stopped."""
        callbacks = callbacks or {}
        with self.lock:
            self._send({"op": "call", "method": method, "args": args or {}}, arrays)
            failed = None
            while True:
                header, got = self._next(timeout)
                if header["op"] in ("result", "error") and failed:
                    raise failed
                if header["op"] == "result":
                    return header.get("value"), got
                if header["op"] == "error":
                    raise worker_error(header)
                if header["op"] == "callback":
                    try:
                        reply = {"op": "reply", "value": callbacks[header["name"]](header.get("args") or {}, got)}
                    except Exception as e:
                        failed, reply = e, {"op": "error", "message": type(e).__name__}
                    self._send(reply)

    def close(self):
        """Quit (killed if it doesn't within CLOSE_WAIT_S) and reap the process. Safe to call twice."""
        if self.alive:
            if self.lock.acquire(blocking=False):  # a call in progress on another thread: just kill it
                try:
                    self._send({"op": "quit"})
                    self.proc.wait(CLOSE_WAIT_S)
                except (WorkerDied, subprocess.TimeoutExpired):
                    pass
                finally:
                    self.lock.release()
            if self.alive:
                self.proc.kill()
        self.proc.wait()
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except OSError:
                pass

    def _send(self, header, arrays=()):
        try:
            protocol.send(self.proc.stdin, header, arrays)
        except (OSError, ValueError):
            raise self._died() from None

    def _next(self, timeout):
        try:
            msg = self.inbox.get(timeout=timeout)
        except queue.Empty:
            self._end()
            raise WorkerDied(f"the model worker stopped answering for {timeout} s and was stopped") from None
        if msg is None:  # its output ended, or made no sense: a worker still running then is no use
            raise self._died()
        return msg

    def _died(self):
        """WorkerDied for a worker whose output ended: its exit code and, if it crashed in C (a
        segfault in a model library), the trace faulthandler left."""
        code = self._end()
        trace = ""
        try:
            lines = [l.rstrip() for l in self.crash_log.read_text(errors="replace").splitlines() if l.strip()]
            if lines:
                cut = lambda l: l if len(l) <= TRACE_WIDTH else "…" + l[-(TRACE_WIDTH - 1):]  # a frame's end names it
                trace = "\n" + "\n".join(cut(l) for l in lines[:TRACE_LINES]) + f"\n(all of it: {self.crash_log})"
        except (AttributeError, OSError):
            pass
        if trace:
            log.error(f"model worker {self.proc.pid} crashed (code {code}):{trace}")
        return WorkerDied(f"the model worker has exited (code {code}){trace}")

    def _end(self):
        """Kill it if it still runs, reap it -> its exit code."""
        if self.alive:
            self.proc.kill()
        return self.proc.wait()

    def _read(self):
        try:
            while (msg := protocol.recv(self.proc.stdout)) is not None:
                self.inbox.put(msg)
        except (OSError, ValueError, EOFError):
            pass
        self.inbox.put(None)


def worker_error(header):
    return type(str(header.get("type") or "Error"), (WorkerError,), {})(header.get("message", ""))
