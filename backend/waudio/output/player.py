"""Plays read-aloud speech on the default output device, piece after piece, and stops within one
block (50 ms) when asked. One player per reading; pieces are queued while earlier ones play. A piece
may come in chunks (a streaming voice model): chunks with the same index play as one piece. A
reading that ends by itself plays END_S of silence before the speaker closes, so the last word is
heard out; a stopped one ends at once.

It follows the system's output: a reading opens the output in use when it starts, and when the
output changes mid-reading (headphones connected, another output picked) the speaker is closed and
opened again on the new one, going on from the next block — what Chrome and Firefox do."""
import logging, queue, threading, time

import numpy as np

from .. import priority
from .. import audio as sound  # devices (audio.py); "audio" is a piece below
from ..engine.tts import PEAK_DB, soft_limit

log = logging.getLogger("waudio")
BLOCK_S = 0.05
DRY_S = 0.25              # waited longer than this for the next sound mid-reading: logged
PAUSE_IDLE_S = 2.0        # paused longer than this: the speaker is stopped until resume (no CPU, the computer may sleep)
FOLLOW_S = 0.5            # how often a reading asks which output the system uses
END_S = 0.4               # silence after a reading's last sound: the speaker closes in it, not on the last word
FADE_S = 0.008            # a voice that stops on a loud sample is brought down to silence over this (no click)
UNITY = 0.5               # the volume setting that plays pieces at their levelled loudness
LIMIT = 10 ** (PEAK_DB / 20)  # peaks never above -1 dBFS, however loud the volume


def loudness(audio, volume):
    """Volume 0.5 = as levelled, 1.0 = twice that; peaks above LIMIT are rounded off (the same
    soft limiter as levelling) so loud never crackles."""
    return soft_limit(np.asarray(audio, dtype=np.float32) * (float(volume) / UNITY), LIMIT)


class Player:
    def __init__(self, sample_rate, volume=0.5, on_piece=None, on_done=None, stream_factory=None, output_device=None):
        """on_piece(index) when a piece starts playing; on_done(stopped, error) once, after the
        last piece, a stop, or a failure (error: the exception, e.g. no output device; else None). stream_factory(sample_rate) -> an object with write(block), start(), abort(),
        close(): a sounddevice OutputStream unless a test passes a fake. output_device() -> the
        system's output now (platforms.output_fingerprint, else output_device, for a real speaker; a fake
        speaker follows none)."""
        self.rate, self.volume = sample_rate, volume
        self.on_piece, self.on_done = on_piece or (lambda i: None), on_done or (lambda stopped: None)
        self.real = stream_factory is None  # the device itself: its list is read again before opening
        self.stream_factory = stream_factory or _sounddevice_stream
        self.output_device = output_device or (_system_output if self.real else (lambda: None))
        self.pieces = queue.Queue()
        self.stopped = threading.Event()
        self.going = threading.Event()  # cleared while paused: no block is written
        self.going.set()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def add(self, index, audio):
        """A piece, or the next chunk of piece index."""
        self.pieces.put((index, audio))

    def finish(self):
        """No more pieces: end after the queued ones."""
        self.pieces.put(None)

    def stop(self):
        self.stopped.set()
        self.going.set()  # a paused reading ends too
        self.pieces.put(None)

    def pause(self):
        """Hold mid-sentence: the next block isn't written until resume (the device plays out the
        one it has, ~50 ms)."""
        self.going.clear()

    def resume(self):
        self.going.set()

    @property
    def paused(self):
        return not self.going.is_set()

    def _open(self):
        """A started speaker on the system's output now; also the output it is on."""
        def make():
            s = self.stream_factory(self.rate)
            try:
                s.start()
            except Exception:
                try:
                    s.close()  # the one that failed: never left open
                except Exception:
                    pass
                raise
            return s
        device = self.output_device()  # before the re-read: a switch in between is caught at the next ask
        if self.real:
            sound.refresh_if_idle()  # PortAudio keeps the default it read: a new one is opened only after this
        return sound.open_stream(make), device  # devices changed (a headset): read them again, once

    def _follow(self, stream, device):
        """The system's output changed: (stream, device) on the new one, or the same if it didn't —
        or if a dictation holds the microphone, as the device list can't be read again under it."""
        now = self.output_device()
        if now is None or now == device:
            return stream, device
        if sound.others_open():
            log.info("read aloud: the output changed while dictating; this reading stays where it is")
            return stream, now  # asked once per change; the next reading follows
        try:
            stream.abort()  # not stop(): it can wait on a device that has gone away
            stream.close()
        except Exception:
            pass
        sound.closed()
        self.stream = None  # closed: if the new one fails to open, nothing is closed twice
        self.stream, device = self._open()
        log.info("read aloud: the output changed; going on there")
        return self.stream, device

    def _hold(self):
        """Paused: hold. A short pause keeps the speaker going; past PAUSE_IDLE_S it is stopped, so a paused
        reading costs no CPU and doesn't keep the computer awake, and started again on resume (the same
        stream; if it can't start, e.g. the device is gone, a new one opens on the output there is now:
        -> that output, else None)."""
        if self.going.wait(PAUSE_IDLE_S) or self.stream is None:
            self.going.wait()
            return
        try:
            self.stream.stop()
        except Exception:
            self.going.wait()
            return
        log.info("read aloud: paused a while; the speaker is stopped")
        self.going.wait()
        if self.stopped.is_set():
            return
        try:
            self.stream.start()
        except Exception:
            try:
                self.stream.close()
            except Exception:
                pass
            sound.closed()
            self.stream = None
            self.stream, device = self._open()
            return device

    def _run(self):
        priority.urgent_thread()  # the sound itself: a late block is a gap
        self.stream, error = None, None
        device, asked = None, 0.0
        def write(a):  # in blocks: a pause holds between them, a stop ends after the one in flight
            nonlocal device, asked
            n = int(self.rate * BLOCK_S)
            for i in range(0, len(a), n):
                if not self.going.is_set():
                    device = self._hold() or device  # paused: hold here, then go on from this very block
                if self.stopped.is_set():
                    return
                if time.monotonic() - asked >= FOLLOW_S:
                    asked = time.monotonic()
                    self.stream, device = self._follow(self.stream, device)
                self.stream.write(a[i:i + n].reshape(-1, 1))
        try:
            playing, ended, last = None, None, 0.0
            while not self.stopped.is_set():
                item = self.pieces.get()
                if item is None or self.stopped.is_set():
                    break
                index, audio = item
                if ended is not None and (waited := time.monotonic() - ended) > DRY_S:
                    log.info(f"read aloud: waited {waited:.1f}s for the next sound (piece {index + 1})")
                if self.stream is None:
                    self.stream, device = self._open()
                    asked = time.monotonic()
                if index != playing:
                    playing = index
                    self.on_piece(index)
                a = loudness(audio, self.volume)
                write(a)
                if len(a):
                    last = float(a[-1])
                ended = time.monotonic()
            if self.stream is not None and not self.stopped.is_set():  # ended by itself: let the last word ring out
                fade = np.linspace(last, 0, int(self.rate * FADE_S) + 2, dtype=np.float32)[1:-1] if last \
                    else np.zeros(0, np.float32)  # not []: that makes the end float64, which the speaker refuses
                write(np.concatenate([fade, np.zeros(int(self.rate * END_S), np.float32)]))
        except Exception as e:  # no device, device gone, …: the reader is told
            error = e
        finally:
            stream = self.stream
            if stream is not None:
                try:
                    stream.abort() if self.stopped.is_set() else stream.stop()
                    stream.close()
                except Exception:
                    pass
                sound.closed()
            self.on_done(self.stopped.is_set(), error)


def _system_output():
    """What a reading follows: the output's fingerprint (its id, sample rate and format: a Bluetooth
    headset keeps its id while it settles after connecting or switches to call mode for its
    microphone, and a stream opened before that misses the change, e.g. its volume buttons), else
    its id."""
    try:
        from .. import platforms
        fingerprint = getattr(platforms, "output_fingerprint", lambda: None)()
        return fingerprint if fingerprint is not None else getattr(platforms, "output_device", lambda: None)()
    except Exception:  # a platform that can't tell: follow from the next reading only
        return None


def _sounddevice_stream(rate):
    return sound.sd.OutputStream(samplerate=rate, channels=1, dtype="float32", blocksize=int(rate * BLOCK_S))
