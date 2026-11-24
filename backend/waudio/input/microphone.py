"""Microphone capture: 16 kHz mono float32, the format the engine wants.

Uses the system's current input (System Settings → Sound): PortAudio's device list is refreshed at
every start, so AirPods that connected since the last dictation are picked up. While recording,
health() tells when the device stopped delivering (unplugged, Bluetooth switching profile, lid
closed) or sends pure digital silence (a muted headset); the app then calls switch(), which
carries on with the current system input and keeps what was recorded.

Stopping the stream can hang for good: PortAudio 19.7 on macOS sometimes deadlocks with Core
Audio's own thread (its start/stop callback). So the stream is stopped on a thread of its own with
a time limit; if it doesn't return, what was recorded is still handed back, the recorder is marked
stuck, and on_stuck() is called — only a fresh process frees the microphone then.
"""
import logging, threading, time

import numpy as np

from .. import audio

from .. import NAME
from ..engine import SAMPLE_RATE

STALL_S = 1.0     # no audio for this long: the device is gone
FIRST_AUDIO_S = 2.5  # … but a Bluetooth input may take this long to deliver its first audio
SILENCE_S = 3.0   # exact zeros for this long: the device is muted or stuck
CLOSE_TIMEOUT_S = 1.5  # stopping the stream takes milliseconds; longer means it hung

log = logging.getLogger("waudio")
SILENT_PEAK = 0.01  # quieter than this everywhere: the microphone hears nothing
LEVEL_GAIN, LEVEL_POWER = 8.0, 0.75  # the pill's bars: speech RMS (~0.02-0.15) x 8, ^0.75 so quiet speech still shows


def level_of(rms):
    """A block's RMS -> the pill's bar height, 0..1 (OpenWhispr's curve)."""
    try:
        rms = float(rms)
    except (TypeError, ValueError):
        return 0.0
    if not rms > 0:  # zero, negative or NaN
        return 0.0
    return min(1.0, (rms * LEVEL_GAIN) ** LEVEL_POWER)


def probe(seconds=1.0):
    """Run checks: open the default input for `seconds` -> (its name, the loudest sample). Raises
    when there is no input device or it won't open (after reading the devices again: audio.py)."""
    rec = lambda: audio.sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocking=True)
    heard = audio.open_stream(rec)
    audio.closed()
    name = audio.sd.query_devices(kind="input")["name"]
    return name, float(np.abs(heard).max()) if len(heard) else 0.0


class Recorder:
    def __init__(self):
        self.stream = None
        self.chunks = []
        self.lock = threading.Lock()
        self.last_audio = 0.0     # monotonic time of the last callback
        self.opened_at = 0.0
        self.got_audio = False
        self.zero_since = None    # monotonic time exact zeros started, or None
        self.problems = 0         # PortAudio overflow / underflow flags seen
        self.device = ""
        self.loudest = 0.0        # the loudest block's RMS since take_level last asked (the pill's bars)
        self.recording = False    # from start() to stop(), also while switch() reopens the device
        self.silent_until = 0     # samples before this are returned as silence (see silence_before)
        self.stuck = False        # stopping a stream hung (PortAudio deadlock): the mic needs a fresh process
        self.on_stuck = None      # called once when that happens (the app restarts its engine)

    @property
    def active(self):
        return self.recording

    def _callback(self, data, frames, time_info, status):
        now = time.monotonic()
        mono = data[:, 0].copy()
        with self.lock:
            self.chunks.append(mono)
            if len(mono):
                self.loudest = max(self.loudest, float(np.sqrt(np.mean(mono * mono))))
            self.last_audio = now
            self.got_audio = True
            if status:
                self.problems += 1
            if mono.any():
                self.zero_since = None
            elif self.zero_since is None:
                self.zero_since = now

    def _open(self):
        audio.refresh_if_idle()  # forget devices that are gone and see new ones — never while a reading plays
        self.last_audio = self.opened_at = time.monotonic()
        self.got_audio = False
        self.zero_since = None

        def make():
            stream = audio.sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=self._callback)
            try:
                stream.start()
            except Exception:
                try:
                    stream.close()
                except Exception:
                    pass
                raise
            return stream
        self.stream = audio.open_stream(make)  # only once it runs: a failed start must not look like recording
        self.device = audio.sd.query_devices(kind="input")["name"]

    def _close(self):
        stream, self.stream = self.stream, None
        if not stream:
            return
        def close():
            try:
                stream.abort()  # stop() can wait on a device that has gone away
                stream.close()
            except Exception:
                pass
        closer = threading.Thread(target=close, daemon=True, name="mic-close")
        closer.start()
        closer.join(CLOSE_TIMEOUT_S)
        if not closer.is_alive():
            audio.closed()  # really closed (a stuck one stays counted: the engine restarts)
        if closer.is_alive() and not self.stuck:  # deadlocked inside PortAudio / Core Audio
            self.stuck = True
            log.error(f"microphone stop hung (> {CLOSE_TIMEOUT_S}s, PortAudio deadlock): the engine will restart")
            if self.on_stuck:
                threading.Thread(target=self.on_stuck, daemon=True).start()

    def start(self):
        if self.stuck:
            raise RuntimeError(f"the microphone got stuck — {NAME} is restarting")
        self.chunks = []
        self.silent_until = 0
        self.problems = 0
        self.loudest = 0.0
        self._open()
        self.recording = True

    def stop(self):
        """Stop and return the recording (empty array if nothing came in)."""
        self.recording = False
        self._close()
        with self.lock:
            audio = np.concatenate(self.chunks) if self.chunks else np.zeros(0, np.float32)
            self.chunks = []
        return self._quiet(audio)

    def silence_before(self, samples):
        """What was recorded before this point is returned as silence: the music playing until
        Waudio muted it (the start sound plays first) never reaches the transcript."""
        with self.lock:
            self.silent_until = max(self.silent_until, int(samples))

    def _quiet(self, audio):
        if self.silent_until and len(audio):
            audio = audio.copy()
            audio[: self.silent_until] = 0.0
        return audio

    def health(self):
        """(ok, problem) while recording: 'stalled' or 'silent' when the device needs switching."""
        if not self.recording or not self.stream:
            return True, ""
        now = time.monotonic()
        with self.lock:
            if not self.got_audio and now - self.opened_at > FIRST_AUDIO_S:
                return False, "stalled"
            if self.got_audio and now - self.last_audio > STALL_S:
                return False, "stalled"
            if self.zero_since is not None and now - self.zero_since > SILENCE_S:
                return False, "silent"
        return True, ""

    def switch(self):
        """Carry on recording from the system's current input; what was recorded is kept.
        Returns the new device's name."""
        self._close()
        if self.stuck:  # a fresh device list would wait on the same lock
            raise RuntimeError("the microphone got stuck")
        self._open()
        return self.device

    def take_level(self):
        """The loudest block since the last call as a bar height, 0..1 (level_of); then starts again."""
        with self.lock:
            rms, self.loudest = self.loudest, 0.0
        return level_of(rms)

    def position(self):
        """Samples recorded so far (cheap: no copy)."""
        with self.lock:
            return sum(len(c) for c in self.chunks)

    def snapshot(self):
        """The recording so far, without stopping it (live typing reads it every tick)."""
        with self.lock:
            audio = np.concatenate(self.chunks) if self.chunks else np.zeros(0, np.float32)
        return self._quiet(audio)

    def seconds(self):
        with self.lock:
            return sum(len(c) for c in self.chunks) / SAMPLE_RATE
