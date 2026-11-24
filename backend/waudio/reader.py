"""Read aloud: selected text -> sentences -> the voice model -> the speakers, one sentence playing
while the next is made. Its own worker thread owns the voice model (like the dictation worker
owns the speech model); a stop takes effect within one 50 ms block and drops everything queued.
The text read is never written to the log, only its length (the debug record, readlog.py, keeps it
when switched on in Developer → TTS).

Every pill message is sent while holding the lock, after checking the reading is still the current
one, so a late message from an old reading can never land after a stop or a newer reading's.
hud() and on_change() must not take this lock (they don't: a stdout line / a queued redraw)."""
import logging, queue, threading, time

import numpy as np

from . import priority, readlog
from .engine import sentences, spoken
from .engine.tts import READ_SEED, Moody, VoiceMissing
from .output.player import Player
from .worker.remote import RemoteTts

log = logging.getLogger("waudio")
# the pause after a piece, by how it ends ([read] setting, default ms): a voice model leaves almost none
PAUSES = {"sentence": ("sentence_pause_ms", 450), "clause": ("clause_pause_ms", 150),
          "paragraph": ("paragraph_pause_ms", 700)}
PAUSE_MAX_MS = 5000
SILENT = 10 ** (-40 / 20)  # quieter than -40 dBFS counts as the quiet a voice already left


class Reader:
    def __init__(self, cfg, hud=lambda state, detail="": None, on_change=lambda: None,
                 on_error=lambda kind, message: None, player_factory=Player, engine=None, record_dir=None):
        """cfg: the [read] table, shared with the app. hud(state, detail): the pill. on_change():
        reading started / stopped / model loaded (the app redraws its menu). on_error(kind,
        message): kind "voice" (no usable voice: the Speech page should open) or "error".
        record_dir: where the debug record goes when [read] debug_record is on (None: readlog.folder())."""
        self.record_dir = record_dir
        self.cfg = cfg
        self.engine = engine or RemoteTts(cfg)  # the voice model, in a worker process
        self.hud, self.on_change, self.on_error = hud, on_change, on_error
        self.player_factory = player_factory
        self.jobs = queue.Queue()
        self.lock = threading.Lock()
        self.generation = 0      # every read and stop bumps it; older work notices and gives up
        self.player = None
        self.active = False      # reading (making or playing speech)
        self.loading = False
        self.last_text = ""
        self.last_parts = []     # [(text, voice, persona, speed)] of the last reading, for Read again
        self.part = None         # which of the reading's parts is playing now (None: not reading)
        self.piece_parts = []    # for each piece of the reading, the part it belongs to
        self.finished = False    # the last reading played to its end (not stopped, not failed)
        self.hold = False        # paused before the first sound (no speaker yet): the speaker starts paused
        self.last_used = time.monotonic()
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def loaded(self):
        return self.engine.loaded

    def read(self, text, voice=None):
        """voice: a preset name for this text only (None: the chosen voice, Settings → Speech)."""
        return self.read_parts([(text, voice, None)])

    def read_parts(self, parts):
        """Several texts read one after another as one reading, each [(text, voice, persona[, speed])] in its own
        voice (None: the chosen one), persona (None: the voice's own; "": none) and speed (on top of the
        reading speed; left out: 1.0). One stop ends it all."""
        parts = [(*p, 1.0)[:4] for p in parts]
        kept = [(n, part) for n, part in enumerate(parts) if sentences.split(part[0])]  # n: as the caller counts
        pieces = [(s, v, p, sp) for _, (t, v, p, sp) in kept for s in sentences.split(t)]
        piece_parts = [n for n, (t, *_) in kept for _ in sentences.split(t)]
        # how each piece ends, for its pause; a part is a block of its own (the page reader's paragraph, heading…)
        ends = [e for _, (t, *_) in kept for _, e in sentences.split_ends(t)]
        ends = [e if j + 1 == len(ends) or piece_parts[j] == piece_parts[j + 1] else "paragraph" for j, e in enumerate(ends)]
        parts = [part for _, part in kept]
        if not pieces:
            return False
        text = "\n\n".join(t for t, *_ in parts)
        with self.lock:
            self._stop_locked()
            self.generation += 1
            self.active = True
            self.last_text, self.last_parts = text, parts
            self.piece_parts, self.part = piece_parts, piece_parts[0]
            self.finished = False
            self.jobs.put(("read", (self.generation, pieces, ends)))
            self.hud("reading", "")
        log.info(f"reading {len(text)} chars in {len(pieces)} pieces" + (f", {len(parts)} parts" if len(parts) > 1 else ""))
        self.on_change()
        return True

    def stop(self):
        with self.lock:
            was = self.active
            self._stop_locked()
            if was:
                self.hud("hide")
        if was:
            log.info("reading stopped")
            self.on_change()

    @property
    def paused(self):
        return bool(self.active and (self.player.paused if self.player else self.hold))

    @property
    def reading_id(self):
        """This reading's id (None when not reading): a page tells its own reading from another one."""
        return self.generation if self.active else None

    def pause(self):
        """Hold the reading mid-sentence (the speech already made waits; more is still made). Before
        the first sound it is held from the start."""
        with self.lock:
            if self.active and self.player:
                self.player.pause()
            elif self.active:
                self.hold = True
        log.info("reading paused")
        self.on_change()

    def resume(self):
        with self.lock:
            self.hold = False
            if self.player:
                self.player.resume()
        log.info("reading resumed")
        self.on_change()

    def _stop_locked(self):
        self.generation += 1
        self.active = False
        self.part = None
        self.finished = False
        self.hold = False
        if self.player:
            self.player.stop()
            self.player = None

    def load(self):
        self.jobs.put(("load", None))

    def unload(self):
        self.jobs.put(("unload", None))

    def test_sentence(self, text, timeout=300):
        """Settings → Developer → TTS: make `text` on this reader's own thread — in line with loading,
        reading and unloading, so none of them runs at the same time — timed, then play it.
        -> {"seconds", "audio_seconds", "loaded_first"}."""
        holder, done = {}, threading.Event()
        self.jobs.put(("test", (text, holder, done)))
        if not done.wait(timeout):
            raise TimeoutError("the voice model didn't answer")
        if "error" in holder:
            raise holder["error"]
        return holder["result"]

    def _test(self, text, holder, done):
        try:
            loaded = self.engine.loaded
            self._load()
            t = time.monotonic()
            audio = self.engine.speak(text)
            secs, rate = time.monotonic() - t, self.engine.sample_rate
            player = self.player_factory(rate, float(self.cfg.get("volume", 0.5)))
            player.add(0, audio)
            player.finish()
            self.last_used = time.monotonic()
            holder["result"] = {"seconds": secs, "audio_seconds": len(audio) / rate, "loaded_first": not loaded}
        except Exception as e:
            holder["error"] = e
        finally:
            done.set()

    def unload_if_idle(self):
        minutes = self.cfg.get("unload_after_minutes", 0)
        if minutes and self.loaded and not self.active and time.monotonic() - self.last_used > minutes * 60:
            self.unload()

    def close(self):
        self.stop()
        self.jobs.put(None)

    # ---------- worker ----------
    def _worker(self):
        priority.urgent_thread()  # a reading is waited for
        while (job := self.jobs.get()) is not None:
            kind, arg = job
            if kind == "test":
                self._test(*arg)  # reports its own errors to the caller
                continue
            try:
                if kind == "read" and arg[0] != self.generation:
                    continue  # stopped before it began
                if kind == "read":
                    for voice in dict.fromkeys(v for _, v, *_ in arg[1]):
                        if not self.engine.voice(voice):
                            raise VoiceMissing(voice or self.cfg.get("voice", ""))  # before a long model load
                if kind in ("load", "read"):
                    self._load()  # also swaps to a newly chosen model
                if kind == "unload" and not self.active:
                    self.engine.unload()
                    log.info("voice model unloaded")
                    self.on_change()
                elif kind == "read":
                    self._read(*arg)
            except VoiceMissing as e:
                log.warning(f"read aloud: voice {str(e)!r} not found")
                self._failed(arg, "voice", "Choose a voice for reading aloud (Settings → Speech)" if str(e) else
                             "No voice yet: add a recording (a .wav and a .txt of what it says) to the voices folder, "
                             "or pick a persona model's built-in voice (Settings → Speech)")
            except Exception as e:  # the type only: a message could quote the text being read
                log.error(f"read aloud: {kind} failed ({type(e).__name__})")
                self._failed(arg, "error", f"Reading aloud failed ({type(e).__name__}) — see the log")

    def _load(self):
        if self.engine.loaded and self.engine.loaded_model == self.cfg["model"]:
            return
        self.loading = True
        self.on_change()
        try:
            t = time.time()
            self.engine.load()
            log.info(f"voice model loaded in {time.time() - t:.1f}s: {self.cfg['model']}")
            if self.engine.voice():  # the first sentence after a load is ~5 s slower: pay it now
                t = time.time()
                self.engine.speak("Hello.")
                log.info(f"voice model warmed up in {time.time() - t:.1f}s")
        finally:
            self.loading = False
            self.on_change()

    def _failed(self, arg, kind, message):
        """A read job failed: stop it and tell, unless it was already stopped or replaced (then
        nobody is waiting for it any more). A load / unload failure is always told."""
        if isinstance(arg, tuple) and arg:
            if arg[0] != self.generation:
                return
            self.stop()
        self.on_error(kind, message)

    def pause_after(self, end):
        """Seconds of quiet a piece that ends this way ("sentence", "clause", "paragraph", None:
        sentences.split_ends) is followed by, at the reading speed. A setting that isn't a number
        counts as its default; none is longer than PAUSE_MAX_MS."""
        key, default = PAUSES.get(end, (None, 0))
        try:
            ms = float(self.cfg.get(key, default)) if key else 0
        except (TypeError, ValueError):
            ms = default
        return min(max(0.0, ms), PAUSE_MAX_MS) / 1000 / max(float(self.cfg.get("speed", 1.0) or 1.0), 0.1)

    def _read(self, gen, pieces, ends):
        stale = lambda: gen != self.generation
        player = None
        t0 = time.monotonic()
        quiet, sounded = 0, False  # the current piece: samples of silence at the end of what it has played; any played
        def play(i, audio):  # -> False once this reading is stale
            nonlocal player, quiet, sounded
            with self.lock:
                if stale():
                    return False
                loud = np.flatnonzero(np.abs(audio) > SILENT)
                quiet = quiet + len(audio) if not len(loud) else len(audio) - 1 - int(loud[-1])
                sounded = sounded or len(audio) > 0
                if player is None:
                    player = self.player = self.player_factory(
                        self.engine.sample_rate, float(self.cfg.get("volume", 0.5)),
                        on_piece=lambda n, g=gen, p=pieces: self._showing(g, p, n),
                        on_done=lambda stopped, error=None, g=gen: self._done(g, error))
                    if self.hold:  # paused before it began
                        player.pause()
                    log.info(f"first sound after {time.monotonic() - t0:.2f}s")
                player.add(i, audio)
                return True

        streams = getattr(self.engine, "streams", False)  # the model hands on chunks as it makes them
        rate = self.engine.sample_rate
        record = None
        if self.cfg.get("debug_record"):  # Developer → TTS: every piece kept, and what made this reading
            try:
                record = readlog.Reading(self.record_dir, gen, {
                    "model": self.cfg.get("model"), "voice": self.cfg.get("voice"), "speed": self.cfg.get("speed", 1.0),
                    "same_take": self.cfg.get("same_take", True), "seed": READ_SEED if self.cfg.get("same_take", True) else None})
            except Exception as e:  # noqa: BLE001 — a record must never stop a reading
                log.warning(f"read record: off for this reading ({type(e).__name__}: {e})")
        for i, (piece, voice, persona, speed) in enumerate(pieces):
            if stale():
                break
            quiet, sounded = 0, False
            made = []  # this piece's speech as it came, for the debug record
            chunked = {"on_chunk": lambda a, i=i: (made.append(a), play(i, a))[1]} if streams else {}
            own = {"speed": speed} if speed != 1.0 else {}  # stretched: the engine makes it whole
            started = time.monotonic()
            audio = self.engine.speak(spoken.money(piece), should_stop=stale, voice=voice, persona_text=persona, **chunked, **own)
            if record:  # Reading.piece catches its own failures
                parts = self.piece_parts
                record.piece(i, parts[i] if i < len(parts) else None, piece, voice, persona, isinstance(persona, Moody), speed,
                             made + [audio], rate, time.monotonic() - started, stopped=stale())
            if stale():
                break
            if len(audio) and not play(i, audio):
                break
            # the pause a speaker makes after it, less the quiet the voice already left (none after the last)
            if i < len(pieces) - 1 and sounded:
                missing = int(self.pause_after(ends[i]) * rate) - quiet
                if missing > 0 and not play(i, np.zeros(missing, np.float32)):
                    break
            self.last_used = time.monotonic()
        ended = False
        with self.lock:
            if player is not None:
                player.finish()  # plays what is queued, then _done
            elif not stale():
                self.active = False  # nothing sayable came out
                self.part = None
                self.hud("hide")
                ended = True
        if ended:
            self.on_change()
        self.last_used = time.monotonic()

    def _showing(self, gen, pieces, n):
        with self.lock:
            if gen == self.generation:
                self.part = self.piece_parts[n] if n < len(self.piece_parts) else self.part
                self.hud("reading", pieces[n][0])

    def _done(self, gen, error=None):
        """The player finished (or failed: no sound device…). A failure also ends the making of
        speech: the generation moves on, so the worker stops at its next check."""
        with self.lock:
            if gen != self.generation:
                return  # a stop or a newer reading already took over
            self.active = False
            self.player = None
            self.part = None
            self.finished = error is None
            if error is not None:
                self.generation += 1
            self.hud("hide")
        self.last_used = time.monotonic()
        self.on_change()
        if error is not None:
            log.error(f"read aloud: playback failed ({type(error).__name__})")
            self.on_error("error", f"Couldn't play the speech ({type(error).__name__}) — is a speaker or headphones connected?")
