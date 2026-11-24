"""Developer → TTS → Debug record ([read] debug_record, off by default): every piece read aloud, its sound
and how it was asked for, kept so an odd moment (a sound that isn't a word, a skipped word, a long
stall) can be found by its time, played again and analysed. This is the one place the text read is
written down (the log gets only lengths), which is why it is off unless switched on.

One folder per reading, the newest KEEP kept: <log folder>/readings/<YYMMDD-HHMMSS (UTC)>-<id>/
  reading.json   what made it: Waudio's version, the voice model, the libraries (mlx, mlx-audio and where
                 mlx was loaded from: to tell our changes from a changed MLX), same_take and its seed
  pieces.jsonl   one line per piece: n, part, at (local time it was finished), text, voice, persona,
                 mood (the persona carries a mood: Breeze is told it while reading), speed, seconds of
                 speech, took (seconds to make), lead_quiet / tail_quiet (seconds below -40 dBFS at
                 its start / end), stopped (the reading was stopped while it was made), flags
  NNN.wav        the piece's speech as made, 16-bit at the model's rate, without the pause after it

flags (cheap, from the sound alone; a word-level check is out of scope):
  long       more seconds than its text should take (LONG x the usual pace, + LONG_SLACK_S)
  lead_quiet / tail_quiet   more than QUIET_S of quiet before its first / after its last sound
"""
import json, logging, re, shutil, time
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger("waudio")
KEEP = 10                 # readings kept; older ones go
SILENT = 10 ** (-40 / 20)
QUIET_S = 1.0
LATIN_CPS, CJK_CPS = 14.0, 4.5  # usual pace: characters a second (Latin text with its spaces / CJK)
LONG, LONG_SLACK_S = 2.2, 1.0
CJK = re.compile("[぀-ヿ㐀-鿿豈-﫿]")


def folder():
    from .platforms import log_dir
    return log_dir() / "readings"


def context():
    """Where this reading's sound came from, besides the settings: versions and library locations."""
    import importlib.util
    from importlib import metadata
    from . import VERSION
    out = {"waudio": VERSION, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    for name in ("mlx", "mlx-metal", "mlx-audio"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    try:  # where mlx comes from, found without loading it (no model library outside the runners)
        spec = importlib.util.find_spec("mlx")
        out["mlx_from"] = (spec.submodule_search_locations or [spec.origin])[0] if spec else None
    except Exception:  # noqa: BLE001 — mlx missing or not loading: noted, never fatal
        out["mlx_from"] = None
    return out


def usual_seconds(text):
    """How long text usually takes to say: its CJK characters at CJK_CPS, the rest at LATIN_CPS."""
    cjk = len(CJK.findall(text))
    return cjk / CJK_CPS + (len(text.strip()) - cjk) / LATIN_CPS


def quiet_ends(audio, rate):
    """(seconds of quiet before the first sound, after the last); all quiet: (length, length)."""
    loud = np.flatnonzero(np.abs(audio) > SILENT)
    if not len(loud):
        return len(audio) / rate, len(audio) / rate
    return int(loud[0]) / rate, (len(audio) - 1 - int(loud[-1])) / rate


def flags_of(text, seconds, lead, tail):
    out = []
    if seconds > LONG * usual_seconds(text) + LONG_SLACK_S:
        out.append("long")
    if lead > QUIET_S:
        out.append("lead_quiet")
    if tail > QUIET_S:
        out.append("tail_quiet")
    return out


class Reading:
    """The record of one reading. Writing it never stops a reading: a failure is logged once and the
    record stops."""

    def __init__(self, root=None, reading_id=0, about=None):
        self.broken = False
        try:
            self.root = Path(root) if root else folder()
            self.dir = self.root / f"{time.strftime('%y%m%d-%H%M%S', time.gmtime())}-{reading_id:06d}"  # UTC: sorts by time
            self.dir.mkdir(parents=True, exist_ok=True)
            old = [p for p in sorted(p for p in self.root.iterdir() if p.is_dir()) if p != self.dir][:-(KEEP - 1) or None]
            for p in old:  # the oldest readings, this one and the KEEP - 1 newest kept
                shutil.rmtree(p, ignore_errors=True)
            (self.dir / "reading.json").write_text(json.dumps({**context(), **(about or {})}, ensure_ascii=False, indent=1, default=str))
        except Exception as e:  # noqa: BLE001 — a record must never stop a reading
            self._fail(e)

    def _fail(self, e):
        if not self.broken:
            log.warning(f"read record: stopped ({type(e).__name__}: {e})")
        self.broken = True

    def piece(self, n, part, text, voice, persona, mood, speed, audio, rate, took, stopped=False):
        if self.broken:
            return
        try:
            if isinstance(audio, (list, tuple)):  # the chunks as they came
                audio = np.concatenate([np.asarray(a, np.float32).reshape(-1) for a in audio] or [np.zeros(0, np.float32)])
            audio = np.asarray(audio, np.float32).reshape(-1)
            seconds = len(audio) / rate
            lead, tail = quiet_ends(audio, rate)
            sf.write(self.dir / f"{n:03d}.wav", audio, int(rate), subtype="PCM_16")
            line = {"n": n, "part": part, "at": time.strftime("%H:%M:%S"), "text": text, "voice": voice,
                    "persona": persona if persona is None else str(persona), "mood": bool(mood), "speed": speed,
                    "seconds": round(seconds, 2), "took": round(took, 2), "lead_quiet": round(lead, 2),
                    "tail_quiet": round(tail, 2), "stopped": stopped, "flags": flags_of(text, seconds, lead, tail)}
            with open(self.dir / "pieces.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001 — a record must never stop a reading
            self._fail(e)
