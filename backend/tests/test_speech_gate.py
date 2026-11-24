#!/usr/bin/env python3
"""The speech gate (engine/segments.py) keeps quiet speech and drops what is too quiet to be any. The
loudness gate alone (USE_VAD off: the stand-in "speech" is tones, not a voice). Speech-like bursts
over a stated background: at −40 dBFS over a −70 dBFS room they are kept; at −60 dBFS (below the
fixed SPEECH_FLOOR_DB) they are not, however quiet the room. The same recording made quieter as a
whole keeps the same speech, down to where it falls below the floor.

The real benchmark clips at −12 / −20 dB are checked by test_engine.py --real.

  python3 backend/tests/test_speech_gate.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from waudio.engine import segments

SR = 16000
segments.USE_VAD = False


def db(x):
    return 10 ** (x / 20)


def speech(rms_db, room_db, rng=None):
    """3 s: room noise, then 1.5 s of syllables (0.2 s tones with 0.1 s gaps) at rms_db, then room."""
    rng = rng or np.random.default_rng(1)
    audio = rng.normal(0, db(room_db), 3 * SR)
    t = np.arange(int(0.2 * SR)) / SR
    syllable = np.sin(2 * np.pi * 180 * t) * np.sqrt(2) * db(rms_db)  # a sine's RMS is its peak / √2
    for k in range(5):
        start = int((0.75 + 0.3 * k) * SR)
        audio[start:start + len(syllable)] += syllable
    return audio.astype(np.float32)


SPOKEN_S = 5 * 0.2


def quiet_speech_is_kept_and_too_quiet_is_not():
    #  speech dBFS  room dBFS  kept
    rows = [(-25, -70, True), (-40, -70, True), (-45, -55, True), (-60, -90, False), (-60, -70, False)]
    assert rows
    for rms_db, room_db, kept in rows:
        heard = segments.speech_seconds(speech(rms_db, room_db), SR)
        assert segments.has_speech(speech(rms_db, room_db), SR) == kept, (rms_db, room_db, heard)
        if kept:
            assert abs(heard - SPOKEN_S) <= 0.15 * SPOKEN_S, (rms_db, room_db, heard)  # what was said, not the room


def a_quieter_recording_keeps_its_speech():
    loud = speech(-25, -70)
    full = segments.speech_seconds(loud, SR)
    assert full > 0
    for down in (12, 20):
        quieter = (loud * db(-down)).astype(np.float32)
        assert abs(segments.speech_seconds(quieter, SR) - full) <= 0.1 * full, (down, full)


def the_floor_setting_is_the_floor():
    """Speech just below the default floor is dropped by it and kept once the floor setting
    (speech_floor_db, -70 .. -30) is lower than the speech, over a stated room below both: nothing
    else in the gate stops short of the setting."""
    #  speech dBFS  room dBFS  floor dBFS  kept
    rows = [(-55, -90, -50, False), (-55, -90, -60, True), (-62, -90, -65, True),
            (-66, -95, -70, True), (-66, -95, -65, False)]
    assert rows
    for rms_db, room_db, floor_db, kept in rows:
        audio = speech(rms_db, room_db)
        assert segments.has_speech(audio, SR, floor_db=floor_db) == kept, (rms_db, room_db, floor_db, segments.speech_seconds(audio, SR, floor_db))


quiet_speech_is_kept_and_too_quiet_is_not()
a_quieter_recording_keeps_its_speech()
the_floor_setting_is_the_floor()
print("speech gate: ok")
