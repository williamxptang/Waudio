#!/usr/bin/env python3
"""Sound devices that change while Waudio runs (a Bluetooth headset connected or taken off), with a
stand-in for sounddevice: PortAudio remembers the old device list, so every open fails until the
list is read again. The microphone, Run checks' microphone test and read aloud's speaker read it
again and carry on — but never while another stream is open, as that would cut it off. A device
error in the checks offers Run again, not the permission (which is fine).

PortAudio also keeps the default output it read: when the system's output changes (headphones
connected, another output picked) a reading must still play there — from its start, and, mid-reading,
from the next block on, unless a dictation holds the microphone.

  python3 backend/tests/test_audio_devices.py
"""
import sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from waits import wait_until
from waudio import audio, checks
from waudio import platforms as osd
from waudio.input import microphone
from waudio.output.player import Player


class PortAudioError(Exception):
    pass


class FakeSD:
    """Devices changed: opens fail (-9986) until _initialize; terminating closes every open stream."""
    PortAudioError = PortAudioError

    def __init__(self):
        self.changed, self.inits, self.streams = False, 0, []
        self.system = self.listed = "Speakers"  # the system's output / the default PortAudio read

    def _terminate(self):
        for s in self.streams:
            s.killed = True

    def _initialize(self):
        self.inits += 1
        self.changed = False
        self.listed = self.system

    def query_devices(self, kind=None):
        return {"name": "Headset" if not self.changed else "gone"}

    def _check(self):
        if self.changed:
            raise PortAudioError("Error opening InputStream: Internal PortAudio error [PaErrorCode -9986]")

    def InputStream(self, **kw):
        self._check()
        s = Stream(); self.streams.append(s); return s

    def OutputStream(self, **kw):
        self._check()
        s = Stream(); s.device = self.listed; self.streams.append(s); return s

    def rec(self, frames, **kw):
        self._check()
        return np.full((frames, 1), 0.2, np.float32)


class Stream:
    killed = closed = False
    fail_start = False
    def start(self):
        if Stream.fail_start:
            Stream.fail_start = False
            raise PortAudioError("start failed: devices changed")
    def stop(self): pass
    def abort(self): pass
    def close(self): self.closed = True
    def write(self, block):
        if self.killed or self.closed:
            raise PortAudioError("stream closed under it")
        self.__dict__.setdefault("written", []).append(block.copy())
        time.sleep(len(block) / 16000)  # plays in real time, like a speaker


def fresh():
    sd = FakeSD()
    audio.sd = sd
    audio._open = 0
    return sd


def play(seconds=0.2, on_done=None, audio=None):
    done = threading.Event(); result = {}
    sd = audio_sd()
    p = Player(16000, 0.5, on_done=lambda stopped, error: (result.update(error=error), done.set()),
               output_device=lambda: sd.system)
    p.add(0, np.zeros(int(16000 * seconds), np.float32) if audio is None else audio); p.finish()
    return p, done, result


def audio_sd():
    return audio.sd


def outputs(sd):
    return [s for s in sd.streams if hasattr(s, "device")]


def heard(stream):
    return np.concatenate(stream.__dict__.get("written", [np.zeros((0, 1), np.float32)]))[:, 0]


def the_microphone_test_reads_the_devices_again():
    sd = fresh(); sd.changed = True
    name, peak = microphone.probe(0.1)
    assert name == "Headset" and sd.inits == 1 and audio._open == 0, (name, sd.inits, audio._open)


def read_aloud_plays_on_the_new_device():
    sd = fresh(); sd.changed = True
    p, done, result = play()
    assert done.wait(5) and result["error"] is None and sd.inits == 1, result
    assert audio._open == 0


def dictating_never_cuts_off_a_reading():
    """Starting the microphone while a reading plays: no re-read (it would close the speaker)."""
    sd = fresh()
    p, done, result = play(seconds=1.0)
    wait_until(lambda: outputs(sd) and len(heard(outputs(sd)[0])) > 0, what="the speaker playing")
    before = sd.inits
    r = microphone.Recorder(); r.start()
    assert sd.inits == before and not any(s.killed for s in sd.streams), (sd.inits, before)
    r.stop()
    assert done.wait(5) and result["error"] is None, result


def a_reading_never_cuts_off_dictation():
    """The speaker can't open (devices changed) while dictating: it fails, the microphone stays open."""
    sd = fresh()
    r = microphone.Recorder(); r.start()  # nothing else open: it may read the devices again itself
    before = sd.inits
    sd.changed = True
    p, done, result = play()
    assert done.wait(5) and isinstance(result["error"], PortAudioError) and sd.inits == before, (result, sd.inits, before)
    assert not any(s.killed for s in sd.streams)
    r.stop()
    assert audio._open == 0


def a_speaker_that_failed_to_start_is_closed():
    sd = fresh(); Stream.fail_start = True
    p, done, result = play()
    assert done.wait(5) and result["error"] is None, result
    assert len(sd.streams) == 2 and sd.streams[0].closed, [s.closed for s in sd.streams]  # the failed one too


def the_microphone_test_counts_while_it_records():
    """A reading whose speaker fails during the check's one second must not re-read the devices under it."""
    sd = fresh()
    def slow_rec(frames, **kw):
        time.sleep(0.5); return np.full((frames, 1), 0.2, np.float32)
    sd.rec = slow_rec
    t = threading.Thread(target=microphone.probe, args=(0.1,)); t.start()
    wait_until(lambda: audio._open > 0, what="the microphone test recording")
    sd.changed = True
    p, done, result = play()
    assert done.wait(5) and isinstance(result["error"], PortAudioError) and sd.inits == 0, (result, sd.inits)
    t.join(); sd.changed = False
    assert audio._open == 0


def a_device_error_offers_run_again_not_the_permission():
    class App:
        mic_lock = threading.Lock(); recording_voice = False
        class recorder: active = False
    sd = fresh()
    sd.rec = lambda *a, **k: (_ for _ in ()).throw(PortAudioError("Error opening InputStream [PaErrorCode -9986]"))
    perms = osd.permissions
    osd.permissions = lambda: {"microphone": True, "accessibility": True}
    try:
        row = checks.check_microphone(App())[0]
        assert row["level"] == checks.RED and row["fix"]["do"] == "checks" and row["fix"]["label"] == "Run again", row
        osd.permissions = lambda: {"microphone": False, "accessibility": True}
        assert checks.check_microphone(App())[0]["fix"]["do"] == "permission"  # refused: then it is the permission
    finally:
        osd.permissions = perms


def a_reading_starts_on_the_output_in_use():
    """Headphones picked since PortAudio last read its list (no open fails): the reading plays on them."""
    sd = fresh(); sd.system = "Headset"
    p, done, result = play()
    assert done.wait(5) and result["error"] is None, result
    assert [s.device for s in outputs(sd)] == ["Headset"], [s.device for s in outputs(sd)]


def a_switch_mid_reading_moves_the_rest_there():
    """The output changes mid-reading: the rest plays on the new one, every block once, in order."""
    sd = fresh()
    speech = np.linspace(-0.4, 0.4, int(16000 * 1.6), dtype=np.float32)  # each sample tells its place
    p, done, result = play(audio=speech)
    wait_until(lambda: outputs(sd) and len(heard(outputs(sd)[0])) > 0, what="the speaker playing")
    sd.system = "Headset"
    assert done.wait(5) and result["error"] is None, result
    streams = outputs(sd)
    assert [s.device for s in streams] == ["Speakers", "Headset"], [s.device for s in streams]
    assert streams[0].closed and len(heard(streams[1])) > 0
    played = np.concatenate([heard(s) for s in streams])
    from waudio.output.player import loudness
    assert np.array_equal(played[:len(speech)], loudness(speech, 0.5)), (len(played), len(speech))  # then the end's silence
    assert audio._open == 0


def a_switch_while_dictating_stays_put():
    """The output changes while a dictation records: the reading stays where it is (re-reading the
    devices would cut the microphone off) and the microphone records on."""
    sd = fresh()
    r = microphone.Recorder(); r.start()
    p, done, result = play(seconds=1.2)
    wait_until(lambda: outputs(sd) and len(heard(outputs(sd)[0])) > 0, what="the speaker playing")
    sd.system = "Headset"
    assert done.wait(5) and result["error"] is None, result
    assert [s.device for s in outputs(sd)] == ["Speakers"] and not any(s.killed for s in sd.streams)
    r.stop()
    assert audio._open == 0


def the_reading_follows_what_the_platform_says_about_the_output():
    """With no output_device given, a real speaker follows the platform. A Bluetooth output keeps its id
    while it switches to call mode (its microphone opened) or settles after connecting, but its sample
    rate and channels change: the platform's fingerprint says so and the reading reopens there, once. A
    platform with no fingerprint (None) is followed by its id; nothing changing keeps the one speaker."""
    #  fingerprint before, after          id before, after        speakers
    rows = [((("Headset", 44100.0, 2), ("Headset", 16000.0, 1)), ("Headset", "Headset"), 2),
            ((("Headset", 44100.0, 2), ("Headset", 44100.0, 2)), ("Headset", "Headset"), 1),
            ((None, None),                                     ("Speakers", "Headset"), 2)]
    assert rows
    saved = osd.output_device, getattr(osd, "output_fingerprint", None)
    try:
        for (fp_before, fp_after), (id_before, id_after), speakers in rows:
            sd = fresh()
            now = {"fp": fp_before, "id": id_before}
            osd.output_device, osd.output_fingerprint = (lambda: now["id"]), (lambda: now["fp"])
            done = threading.Event(); result = {}
            p = Player(16000, 0.5, on_done=lambda stopped, error: (result.update(error=error), done.set()))
            p.add(0, np.zeros(int(16000 * 1.6), np.float32)); p.finish()
            wait_until(lambda: outputs(sd) and len(heard(outputs(sd)[0])) > 0, what="the speaker playing")
            now.update(fp=fp_after, id=id_after)
            assert done.wait(5) and result["error"] is None, result
            assert len(outputs(sd)) == speakers, (fp_after, id_after, len(outputs(sd)))
            assert all(s.closed for s in outputs(sd)[:-1]) and audio._open == 0
    finally:
        osd.output_device = saved[0]
        if saved[1] is None:
            del osd.output_fingerprint
        else:
            osd.output_fingerprint = saved[1]


the_microphone_test_reads_the_devices_again()
read_aloud_plays_on_the_new_device()
dictating_never_cuts_off_a_reading()
a_reading_never_cuts_off_dictation()
a_speaker_that_failed_to_start_is_closed()
the_microphone_test_counts_while_it_records()
a_device_error_offers_run_again_not_the_permission()
a_reading_starts_on_the_output_in_use()
a_switch_mid_reading_moves_the_rest_there()
a_switch_while_dictating_stays_put()
the_reading_follows_what_the_platform_says_about_the_output()
print("audio device checks: ok")
