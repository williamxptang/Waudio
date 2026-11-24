"""The engine's stand-ins for a model: the same calls as a runner (engine/models), as TtsEngine
(engine/tts.py) and as a page reader (engine/ocr/page.py), answered by a model worker. Loading starts
the worker; unloading ends it.
"""
import numpy as np

from ..engine import tts
from .client import Worker, WorkerError


class RemoteRunner:
    """models.load's runner: a runner module's Runner, in a worker process."""

    def __init__(self, name, model, cmd=None):
        self.worker = Worker({"kind": "asr", "runner": name, "model": str(model)}, cmd=cmd)

    @property
    def alive(self):
        return self.worker.alive

    def transcribe(self, audio, language, max_tokens, context, should_stop, prefix="", strict=False, draft=""):
        value, _ = self.worker.call(
            "transcribe", {"language": language, "max_tokens": max_tokens, "context": context, "prefix": prefix,
                           "strict": strict, "draft": draft},
            [np.asarray(audio, np.float32)], {"should_stop": lambda args, _: bool(should_stop(args["text"]))})
        return tuple(value)

    def rollback(self, raw, tokens=5):
        return self.worker.call("rollback", {"raw": raw, "tokens": tokens})[0]

    def close(self):
        self.worker.close()


class RemoteTts:
    """TtsEngine for reader.py, its model in a worker process. Voices are looked up here (files only);
    the worker does the rest of speak() with the same TtsEngine code."""

    def __init__(self, cfg, cmd=None):
        self.cfg = cfg  # the [read] table of config.toml, shared with the app (changes apply live)
        self.cmd = cmd  # how to start the worker (None: client.command())
        self.worker = None
        self.loaded_model = None

    @property
    def loaded(self):
        return self.worker is not None and self.worker.alive

    @property
    def sample_rate(self):
        return self.worker.info["sample_rate"] if self.loaded else 24000

    @property
    def streams(self):
        return self.loaded and tts.streams(self.worker.info["is_breeze"], self.cfg)

    def voice(self, name=None):
        return tts.configured_voice(self.cfg, name)

    def load(self):
        if self.worker and (self.loaded_model != self.cfg["model"] or not self.worker.alive):
            self.unload()
        if not self.worker:
            self.worker = Worker({"kind": "tts", "cfg": dict(self.cfg)}, cmd=self.cmd)
            self.loaded_model = self.cfg["model"]

    def unload(self):
        if self.worker:
            self.worker.close()
            self.worker = None
            self.loaded_model = None

    def speak(self, text, should_stop=lambda: False, voice=None, persona_text=None, on_chunk=None, speed=1.0):
        """TtsEngine.speak, in the worker. A worker that has died (WorkerDied) took the model with it:
        loaded is then False, and the next piece loads it again."""
        self.load()
        callbacks = {"should_stop": lambda args, arrays: bool(should_stop())}
        if on_chunk:
            callbacks["on_chunk"] = lambda args, arrays: on_chunk(arrays[0])
        try:
            _, (audio,) = self.worker.call("speak", {"text": text, "voice": voice, "persona_text": persona_text,
                                                     "mood": isinstance(persona_text, tts.Moody),  # a str subclass: lost in the pipe
                                                     "speed": speed, "cfg": dict(self.cfg), "stream": on_chunk is not None},
                                           callbacks=callbacks)
        except WorkerError as e:
            if type(e).__name__ == "VoiceMissing":
                raise tts.VoiceMissing(str(e)) from None
            raise
        return audio


class RemoteOcr:
    """A page reader (engine/ocr/page.py) for one OCR setup (engine/ocr SETUPS), in a worker process.
    The worker reports after each block, so a long page never looks like a hung worker."""

    def __init__(self, setup, models_dir, cmd=None, reader=None):
        """reader: the custom setup's page reader folder (engine/ocr chosen)."""
        self.worker = Worker({"kind": "ocr", "setup": setup, "models_dir": str(models_dir), "reader": reader}, cmd=cmd)

    @property
    def alive(self):
        return self.worker.alive

    def read(self, page, progress=lambda info: None, route=None):
        """page: H x W x 3 uint8 BGR -> the page result (engine/ocr/page.py). route: its fast mode's
        TAU (page readers only), or None."""
        value, _ = self.worker.call("read", {"route": route}, [np.ascontiguousarray(page, np.uint8)],
                                    {"progress": lambda args, _: progress(args)})
        return value

    def close(self):
        self.worker.close()
