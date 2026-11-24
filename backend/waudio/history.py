"""Dictation history: every dictated text, kept on this Mac only (Settings → History).

One JSON line per dictation in history.jsonl, appended as it happens; deleting rewrites the file
in one step (temp file + rename). The file is readable by your user only. With keep_audio on, each
dictation's recording is kept too, as recordings/<id>.wav (16 kHz mono, readable by you only), and
goes when its entry goes. The text never goes to the log. Corrupt lines are skipped, never fatal."""
import json, logging, os, re, threading, time, uuid, wave
from pathlib import Path

import numpy as np

RATE = 16000  # what the microphone records and the model hears
ID = re.compile(r"[0-9a-f]{12}")  # an entry id (add): nothing else names a recording file

log = logging.getLogger("waudio")


class History:
    def __init__(self, folder, cfg=None):
        """cfg: the [history] table (keep, keep_days), shared with the app (changes apply live)."""
        self.path = Path(folder).expanduser() / "history.jsonl"
        self.audio_dir = self.path.parent / "recordings"
        self.cfg = cfg if cfg is not None else {}
        self.lock = threading.Lock()
        self.version = 0  # bumps on every change (the settings page re-reads when it moves)
        self._n = None    # entry count, cached (the settings page asks every second)

    @property
    def keep(self):
        return bool(self.cfg.get("keep", True))

    @property
    def keep_audio(self):
        return self.keep and bool(self.cfg.get("keep_audio", False))

    def add(self, text, language=None, app=None, seconds=None, audio=None):
        """Save a dictation (when history is on), and its recording (float32 at 16 kHz, when
        keep_audio is on). -> the entry, or None."""
        if not self.keep or not str(text or "").strip():
            return None
        entry = {"id": uuid.uuid4().hex[:12], "t": time.time(), "text": str(text).strip(),
                 "lang": language, "app": app, "secs": round(float(seconds), 1) if seconds else None}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if audio is not None and len(audio) and self.keep_audio:
                try:  # first, under the lock: a delete can't come between the entry and its file
                    self._save_audio(entry["id"], audio)
                except OSError:
                    log.exception("saving the recording failed")
            try:
                tail_ok = not self.path.exists() or self.path.stat().st_size == 0 or self._ends_with_newline()
                with self._open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND) as f:
                    if not tail_ok:
                        f.write("\n")  # a crash left half a line: start a fresh one
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                self._drop_audio([entry["id"]])  # no recording without its entry
                raise
            self.version += 1
            if self._n is not None:
                self._n += 1
        return entry

    def _wav(self, entry_id):
        """recordings/<id>.wav; anything that isn't an entry id (a path, "..") names no file."""
        if not ID.fullmatch(str(entry_id)):
            raise ValueError("not a history entry id")
        return self.audio_dir / f"{entry_id}.wav"

    def _save_audio(self, entry_id, audio):
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.audio_dir, 0o700)
        pcm = (np.clip(np.asarray(audio, np.float32), -1, 1) * 32767).astype("<i2").tobytes()
        tmp = self._wav(entry_id).with_suffix(".tmp")
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
            with wave.open(f, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
                w.writeframes(pcm)
        os.replace(tmp, self._wav(entry_id))

    def has_audio(self, entry_id):
        try:
            return self._wav(entry_id).is_file()
        except ValueError:
            return False

    def audio_bytes(self, entry_id):
        """The kept recording as a .wav file's bytes, or None."""
        try:
            return self._wav(entry_id).read_bytes()
        except (OSError, ValueError):
            return None

    def recording(self, entry_id):
        """The kept recording as float32 at 16 kHz (what the model takes), or None."""
        try:
            with wave.open(str(self._wav(entry_id)), "rb") as w:
                pcm = w.readframes(w.getnframes())
        except (OSError, ValueError, wave.Error, EOFError):
            return None
        return np.frombuffer(pcm, "<i2").astype(np.float32) / 32767

    def _drop_audio(self, ids):
        for i in ids:
            if ID.fullmatch(str(i)):
                self._wav(i).unlink(missing_ok=True)

    @staticmethod
    def _open(path, flags):
        """Created readable by you only from the start (no moment with the default permissions)."""
        return os.fdopen(os.open(path, flags, 0o600), "a" if flags & os.O_APPEND else "w", encoding="utf-8")

    def _ends_with_newline(self):
        with open(self.path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            return f.read(1) == b"\n"

    def _read(self):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                e = json.loads(line)
                if isinstance(e, dict) and e.get("id") and isinstance(e.get("text"), str):
                    out.append(e)
            except ValueError:
                continue  # a half-written line from a crash: skip it
        return out

    def _write(self, entries):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.unlink(missing_ok=True)
        with self._open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC) as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)
        self.version += 1
        self._n = len(entries)

    def count(self):
        with self.lock:
            if self._n is None:
                self._n = len(self._read())
            return self._n

    def list(self, query="", offset=0, limit=200):
        """Newest first. query: words that must all appear in the text (case-insensitive).
        Each entry says whether its recording is kept (audio)."""
        with self.lock:
            entries = self._read()
        words = str(query or "").lower().split()
        if words:
            entries = [e for e in entries if all(w in e["text"].lower() for w in words)]
        entries.reverse()
        page = entries[offset:offset + limit]
        for e in page:
            e["audio"] = self.has_audio(e["id"])
        return {"total": len(entries), "entries": page}

    def get(self, entry_id):
        with self.lock:
            return next((e for e in self._read() if e["id"] == entry_id), None)

    def recent(self, n=10):
        with self.lock:
            return self._read()[-n:]

    def touch(self):
        """Nothing on disk changed, but what the page shows did (which recordings are kept)."""
        with self.lock:
            self.version += 1

    def update(self, entry_id, text):
        """A dictation transcribed again: its new text replaces the old."""
        return self._set(entry_id, "text", str(text).strip())

    def said(self, entry_id, text):
        """What you really said, typed in by you (to measure how well the model heard you).
        "" removes it."""
        return self._set(entry_id, "said", str(text or "").strip() or None)

    def _set(self, entry_id, key, value):
        with self.lock:
            entries = self._read()
            for e in entries:
                if e["id"] == entry_id:
                    if value is None:
                        e.pop(key, None)
                    else:
                        e[key] = value
                    self._write(entries)
                    return True
        return False

    def delete(self, entry_id):
        with self.lock:
            entries = self._read()
            kept = [e for e in entries if e["id"] != entry_id]
            if len(kept) == len(entries):
                return False
            self._write(kept)
            self._drop_audio([entry_id])
        log.info("history: 1 entry deleted")
        return True

    def clear(self):
        with self.lock:
            n = len(self._read())
            self._write([])
            for p in [*self.audio_dir.glob("*.wav"), *self.audio_dir.glob("*.tmp")]:  # strays from a crash too
                p.unlink(missing_ok=True)
        log.info(f"history cleared ({n} entries)")
        return n

    def prune(self):
        """Drop entries older than keep_days (0 = keep forever)."""
        days = int(self.cfg.get("keep_days", 0) or 0)
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        with self.lock:
            entries = self._read()
            kept = [e for e in entries if e.get("t", 0) >= cutoff]
            if len(kept) != len(entries):
                self._write(kept)
                self._drop_audio(e["id"] for e in entries if e.get("t", 0) < cutoff)
        if len(kept) != len(entries):
            log.info(f"history: {len(entries) - len(kept)} entries older than {days} days removed")
        return len(entries) - len(kept)
