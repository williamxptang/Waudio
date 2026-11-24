"""A model worker: python -m waudio.worker (started by worker/client.py, never by hand).

It loads one model and runs it for the engine until told to quit or until its stdin closes; then the
process exits, and everything the model took goes back to the system: the weights, the model
libraries (mlx, torch, …) and the memory they kept. The engine itself never imports them.
"""
import faulthandler, logging, os, sys

import numpy as np

from .. import priority
from . import protocol


class CallbackFailed(RuntimeError):
    """The engine's side of a callback raised; the call stops here and the engine raises its own error."""


class Asr:
    """Speech to text: a runner from engine/models (models.load_here)."""
    methods = ("transcribe", "rollback")

    def __init__(self, start):
        from ..engine import models
        self.runner = models.load_here(start["runner"], start["model"])
        self.info = {}

    def transcribe(self, args, arrays, ask):
        should_stop = lambda so_far: bool(ask("should_stop", {"text": so_far}))
        lang, text, raw, *capped = self.runner.transcribe(arrays[0], args["language"], args["max_tokens"], args["context"],
                                                          should_stop, prefix=args["prefix"], strict=args["strict"],
                                                          draft=args.get("draft", ""))
        return [lang, text, raw, bool(capped and capped[0])], []

    def rollback(self, args, arrays, ask):
        return self.runner.rollback(args["raw"], args["tokens"]), []


class Tts:
    """Text to speech: engine/tts.py's TtsEngine, model and all."""
    methods = ("speak",)

    def __init__(self, start):
        from ..engine.tts import TtsEngine
        self.engine = TtsEngine(start["cfg"])
        self.engine.load()
        self.info = {"sample_rate": int(self.engine.sample_rate), "is_breeze": self.engine.is_breeze}

    def speak(self, args, arrays, ask):
        self.engine.cfg = args["cfg"]  # the settings as they are now (they change while it is loaded)
        from ..engine.tts import Moody
        on_chunk = (lambda a: ask("on_chunk", {}, [np.asarray(a, np.float32)])) if args["stream"] else None
        persona = Moody(args["persona_text"]) if args.get("mood") and args["persona_text"] else args["persona_text"]
        audio = self.engine.speak(args["text"], should_stop=lambda: bool(ask("should_stop", {})), voice=args["voice"],
                                  persona_text=persona, on_chunk=on_chunk, speed=args.get("speed", 1.0))
        return None, [np.asarray(audio, np.float32)]


class Ocr:
    """Page reading: engine/ocr/page.py's PageReader, the models of one setup."""
    methods = ("read",)

    def __init__(self, start):
        from ..engine.ocr.page import PageReader
        self.reader = PageReader.load(start["setup"], start["models_dir"], start.get("reader"))
        self.info = self.reader.info

    def read(self, args, arrays, ask):
        return self.reader.read(arrays[0], progress=lambda info: ask("progress", info), route=args.get("route")), []


HANDLERS = {"asr": Asr, "tts": Tts, "ocr": Ocr}


def serve(handlers, inp, out):
    """Answer the engine's messages until quit or the end of inp."""
    handler = None

    def ask(name, args, arrays=()):
        protocol.send(out, {"op": "callback", "name": name, "args": args}, arrays)
        msg = protocol.recv(inp)
        if msg is None:
            raise SystemExit(0)  # the engine is gone
        if msg[0]["op"] != "reply":
            raise CallbackFailed(msg[0].get("message", ""))
        return msg[0].get("value")

    while (msg := protocol.recv(inp)) is not None:
        header, arrays = msg
        try:
            if header["op"] == "quit":
                return
            if header["op"] == "start":
                handler = handlers[header["kind"]](header)
                priority.wire_model()  # its weights stay in RAM while it is loaded
                protocol.send(out, {"op": "ready", "info": handler.info})
            elif header["op"] == "call":
                if header["method"] not in handler.methods:
                    raise ValueError(f"no method {header['method']!r}")
                value, outs = getattr(handler, header["method"])(header.get("args") or {}, arrays, ask)
                protocol.send(out, {"op": "result", "value": value}, outs)
        except Exception as e:
            log.exception(f"{header.get('op')} {header.get('method') or header.get('kind', '')} failed")
            protocol.send(out, {"op": "error", "type": type(e).__name__, "message": str(e)})


_crash_file = None  # kept open: faulthandler writes to it from a signal handler


def trace_crashes(path):
    """A crash in C (a segfault in a model library) writes Python's stacks here: the app's side puts
    them in its error instead of an exit code alone (worker/client.py)."""
    global _crash_file
    if not path:
        return
    try:
        _crash_file = open(path, "w")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError as e:
        log.warning(f"no crash trace ({e})")


def main(handlers=HANDLERS):
    # the messages get their own copy of stdout; fd 1 itself then goes to stderr, so whatever a model
    # library prints (Python or C) lands in the log instead of in the middle of a message
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format=f"%(asctime)s model worker {os.getpid()}: %(message)s")
    trace_crashes(os.environ.get("WAUDIO_CRASH_LOG"))
    priority.urgent_thread()  # this thread runs the model: its work is waited for
    serve(handlers, sys.stdin.buffer, out)


log = logging.getLogger("waudio.worker")

if __name__ == "__main__":
    main()
