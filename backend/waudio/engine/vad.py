"""Voice detector: is someone speaking in this 32 ms of audio? Silero VAD (MIT), its ONNX model
from the silero-vad package, run with onnxruntime (no torch). Loudness alone takes music, a TV,
a keyboard or a breath for speech; this tells a voice apart.

segments.py asks it (when switched on, [audio] vad) on top of loudness: a frame is speech only if
it is loud enough and voiced. Missing onnxruntime or silero-vad: off, loudness alone, as before.

Live typing asks about the open sentence again every few tenths of a second as it grows, so the
model's state is kept and only the new audio is run (`probs`).
"""
import importlib.util
import logging
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger("waudio")

SR = 16000
CHUNK = 512          # samples per step (32 ms at 16 kHz), what the model takes
CONTEXT = 64         # samples of the previous step it hears again
CHUNK_S = CHUNK / SR
ON, OFF = 0.5, 0.35  # voiced above ON, unvoiced again below OFF (Silero's own defaults)

_lock = threading.Lock()
_vad = None
_failed = False


def model_path():
    spec = importlib.util.find_spec("silero_vad")  # found without importing it (that would load torch)
    if not spec or not spec.origin:
        return None
    path = Path(spec.origin).parent / "data" / "silero_vad.onnx"
    return path if path.is_file() else None


class Vad:
    def __init__(self, path):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1  # tiny model: threads only cost
        self.session = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.reset()  # state, and the audio it belongs to

    def reset(self):
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros((1, CONTEXT), np.float32)
        self.done, self.out, self.seen = 0, [], np.zeros(0, np.float32)

    def probs(self, audio):
        """Chance of a voice in each CHUNK of audio (16 kHz). Reuses the last call's answer as far
        as audio is the same samples (the open sentence grown longer, or a piece cut from its
        start), else starts over."""
        audio = np.asarray(audio, dtype=np.float32)
        same = min(len(audio), self.done)
        if not np.array_equal(audio[:same], self.seen[:same]):  # every recording starts silent: compare it all
            self.reset()
        sr = np.array(SR, np.int64)
        for i in range(self.done, len(audio) // CHUNK * CHUNK, CHUNK):
            x = np.concatenate([self.context, audio[None, i:i + CHUNK]], axis=1)
            p, self.state = self.session.run(None, {"input": x, "state": self.state, "sr": sr})
            self.context = x[:, -CONTEXT:]
            self.out.append(float(p[0, 0]))
            self.done = i + CHUNK
        if self.done > len(self.seen):
            self.seen = audio[: self.done].copy()
        return np.array(self.out[: len(audio) // CHUNK], np.float32)


def voiced(audio, sr, n_frames, frame_s):
    """A voiced yes/no for each of n_frames frames of frame_s seconds, or None when the voice
    detector can't run (then loudness alone decides)."""
    global _vad, _failed
    if sr != SR or _failed:
        return None
    with _lock:
        if _vad is None:
            try:
                path = model_path()
                if not path:
                    raise FileNotFoundError("silero-vad's silero_vad.onnx")
                _vad = Vad(path)
            except Exception as e:  # noqa: BLE001 — any failure: loudness alone
                _failed = True
                log.warning(f"voice detector off ({e}): pauses and speech by loudness alone")
                return None
        try:
            p = _vad.probs(audio)
        except Exception as e:  # noqa: BLE001 — a dictation must never fail on it: loudness alone from now on
            _failed = True
            log.warning(f"voice detector failed ({e}): pauses and speech by loudness alone")
            return None
    on, now = np.zeros(len(p), bool), False
    for i, x in enumerate(p):  # hysteresis: a voice starts above ON and ends below OFF
        now = x >= ON if not now else x >= OFF
        on[i] = now
    if not len(on):
        return np.zeros(n_frames, bool)
    at = np.minimum(((np.arange(n_frames) + 0.5) * frame_s / CHUNK_S).astype(int), len(on) - 1)
    return on[at]
