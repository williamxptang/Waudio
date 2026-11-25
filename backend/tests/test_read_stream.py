#!/usr/bin/env python3
"""Reading aloud while the voice model is still speaking (Breeze streams): each chunk is played as it
comes, so a long sentence after a short one leaves no pause; the chunks of one sentence count as one
piece; a voice's usual loudness lands at the reading level and a softer sentence stays softer; a stop ends it within a
chunk; at another reading speed (the stretch needs the whole sentence) it is made whole as before.
Fakes: a voice model that makes speech at 1.3x real time (the speed Breeze manages on a Mac), and an
output device that plays in real time. Each check builds its own reader and engine.

  python3 backend/tests/test_read_stream.py
"""
import sys, tempfile, threading, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.engine import tts
from waudio.output import player as playback
from waudio.output.player import Player
from waudio.reader import Reader
from waits import realtime

RATE = 1000          # samples per second: a small rate keeps the fakes cheap; time is real
SPEED = 1.3          # the fake model makes speech this many times faster than it plays
CHUNK_S = 0.32


class Device:
    """Plays in real time and notes when each block started."""
    def __init__(self, rate): self.rate, self.starts = rate, []
    def start(self): pass
    def write(self, block): self.starts.append((time.monotonic(), len(block))); time.sleep(len(block) / self.rate)
    def stop(self): pass
    def abort(self): pass
    def close(self): pass


def voice(seconds):
    return (np.sin(np.arange(int(seconds * RATE)) / 3) * 0.2).astype(np.float32)


class SlowModel:
    """A voice model at SPEED x real time: "short" is 0.6 s of speech, "long" 2.4 s. streams: hands each
    chunk on as it is made (Breeze), else returns the whole sentence when done."""
    sample_rate, loaded, loaded_model = RATE, True, "fake"

    def __init__(self, streams):
        self.streams, self.made, self.asked = streams, [], []

    def load(self): pass
    def voice(self, name=None): return ("v.wav", "words", None)

    def speak(self, text, should_stop=lambda: False, voice_name=None, persona_text=None, on_chunk=None, speed=1.0, **kw):
        self.asked.append((text, speed))
        whole = voice(0.6 if text == "short" else 2.4)
        n = int(CHUNK_S * RATE)
        parts = []
        for i in range(0, len(whole), n):
            time.sleep(n / RATE / SPEED)
            if should_stop():
                return np.zeros(0, np.float32)
            parts.append(whole[i:i + n])
            if on_chunk:
                on_chunk(parts[-1])
        self.made.append(text)
        return np.zeros(0, np.float32) if on_chunk else np.concatenate(parts)


def read(streams, texts, model=None):
    """Read texts (or whole parts) with the slow model -> (the device's block starts, pieces announced, reading time)."""
    device, shown, done = None, [], threading.Event()
    def player(rate, volume, on_piece=None, on_done=None):
        nonlocal device
        def make(r):
            nonlocal device
            device = Device(r); return device
        return Player(rate, volume, on_piece=lambda i: (shown.append(i), on_piece and on_piece(i)),
                      on_done=lambda *a: (done.set(), on_done and on_done(*a)), stream_factory=make)
    r = Reader({"volume": 0.5, "model": "fake"}, player_factory=player, engine=model or SlowModel(streams))
    t = time.monotonic()
    r.read_parts([(x, None, None) if isinstance(x, str) else x for x in texts])
    assert done.wait(20), "the reading never ended"
    return device.starts, shown, time.monotonic() - t


def longest_pause(starts):
    """The longest silence between one block ending and the next starting."""
    assert len(starts) > 1
    return max(b[0] - (a[0] + a[1] / RATE) for a, b in zip(starts, starts[1:]))


def a_long_sentence_after_a_short_one_leaves_no_pause():
    starts, shown, _ = read(True, ["short", "long", "short"])
    assert longest_pause(starts) < 0.15, longest_pause(starts)
    assert shown == [0, 1, 2]                                   # one announcement per sentence, not per chunk
    whole, _, _ = read(False, ["short", "long", "short"])       # made whole, as before: a pause
    assert longest_pause(whole) > 0.5, longest_pause(whole)


def chunks_of_one_piece_play_as_one():
    device, shown, done = [], [], threading.Event()
    p = Player(RATE, 0.5, on_piece=shown.append, on_done=lambda *a: done.set(),
               stream_factory=lambda r: device.append(Device(r)) or device[-1])
    for i, s in ((0, 0.1), (0, 0.1), (1, 0.1)):
        p.add(i, voice(s))
    p.finish()
    assert done.wait(5) and shown == [0, 1]
    ending = int(RATE * playback.FADE_S) + int(RATE * playback.END_S)  # the voice stops loud: faded, then silence
    assert sum(n for _, n in device[0].starts) == 3 * len(voice(0.1)) + ending


class FakeBreeze:
    """generate() as Breeze has it in mlx-audio: chunks of streaming_interval seconds."""
    sample_rate = RATE

    def __init__(self):
        self.stop_after = None

    def generate(self, text, ref_audio, ref_text, max_tokens, stream, streaming_interval, cfg_scale=1.0, seed=None, **kw):
        assert stream
        n = int(streaming_interval / 0.08) * int(0.08 * RATE)   # whole 80 ms frames, as mlx-audio counts them
        whole = voice(2.0) * {"quiet": 0.5, "low": 0.085, "hushed": 0.014, "faint": 0.0035}.get(text, 1.0)
        if text == "breath":                                    # a soft lead-in (-51 dBFS) before the words
            whole[:n] *= 0.02
        for k, i in enumerate(range(0, len(whole), n)):
            if self.stop_after is not None and k >= self.stop_after:
                return
            yield type("R", (), {"audio": whole[i:i + n]})()


def breeze_engine(**cfg):
    d = Path(tempfile.mkdtemp())
    (d / "rose.wav").write_bytes(b"RIFF"); (d / "rose.txt").write_text("what it says")
    e = tts.TtsEngine({"model": "breeze", "voices_dir": str(d), "voice": "rose", **cfg})
    e.load = lambda: None
    return loaded(e)


class NoLibrary:
    """Stands in for the voice-model runner (engine/models): no model library behind these fakes."""
    seed = clear_cache = set_cache_limit = staticmethod(lambda *a: None)


def loaded(e):
    """The engine as it is once Breeze has loaded (the fake model in its place)."""
    e.model, e.runner, e.loaded_model, e.is_breeze, e.takes_persona = FakeBreeze(), NoLibrary, "breeze", True, True
    return e


db = lambda a: 20 * np.log10(np.sqrt(np.mean(a ** 2)))


def heard(e, text):
    """What one streamed sentence sounds like, in dBFS."""
    got = []
    assert not len(e.speak(text, persona_text="", on_chunk=got.append))
    assert got, text
    return db(np.concatenate(got))


def a_streamed_voice_lands_at_the_reading_level_and_keeps_its_ups_and_downs():
    """The voice's usual loudness comes out where whole-sentence levelling puts it; a sentence the model
    spoke 6 dB softer stays about that much softer (it can't be measured before it plays)."""
    e = breeze_engine()
    assert e.streams
    db = lambda a: 20 * np.log10(np.sqrt(np.mean(a ** 2)))
    heard = {}
    for k, text in enumerate(("first", "first", "quiet", "first")):
        got = []
        assert not len(e.speak(text, persona_text="", on_chunk=got.append))
        assert len(got) > 1, text
        heard[(k, text)] = db(np.concatenate(got))
    usual = [v for (k, t), v in heard.items() if t == "first"]
    assert usual and all(abs(v - tts.LEVEL_DB) < 1.0 for v in usual), heard
    assert abs(heard[(2, "quiet")] - (tts.LEVEL_DB - 6)) < 1.5, heard


def a_voices_first_sentence_is_levelled_by_its_speech_not_a_breath_before_it():
    e = breeze_engine(stream_chunk_s=CHUNK_S)  # the breath fills the first chunk
    db = lambda a: 20 * np.log10(np.sqrt(np.mean(a ** 2)))
    got = []
    e.speak("breath", persona_text="", on_chunk=got.append)
    words = np.concatenate(got)[int(CHUNK_S * RATE):]
    assert len(words) and abs(db(words) - tts.LEVEL_DB) < 1.0, db(words)


def a_faint_voice_is_still_brought_to_the_reading_level():
    e = breeze_engine()
    got = []
    e.speak("faint", persona_text="", on_chunk=got.append)
    heard = np.concatenate(got)
    assert len(heard) and abs(20 * np.log10(np.sqrt(np.mean(heard ** 2))) - tts.LEVEL_DB) < 1.0


def a_voices_first_gain_turns_it_up_at_most_first_gain_max():
    e = breeze_engine()
    rms = float(np.sqrt(np.mean((voice(2.0) * 0.085) ** 2)))     # clear speech, but below the reading level
    assert tts.SPEECH_RMS <= rms and rms * tts.FIRST_GAIN_MAX < 10 ** (tts.LEVEL_DB / 20)
    assert abs(heard(e, "low") - 20 * np.log10(rms * tts.FIRST_GAIN_MAX)) < 0.5


def a_hushed_sentence_does_not_turn_up_the_next():
    e = breeze_engine()
    heard(e, "hushed")                                             # never clear speech: levelled on its own
    assert abs(heard(e, "first") - tts.LEVEL_DB) < 1.0


def one_odd_sentence_doesnt_move_the_next_but_a_voice_that_changed_is_followed():
    """The gain is the median of the voice's last GAIN_SENTENCES sentences: fewer than half of them
    spoken 6 dB softer leave the next at the reading level; more than half and it follows them."""
    n = tts.GAIN_SENTENCES
    for quiet, expect in ((n // 2, tts.LEVEL_DB), (n // 2 + 1, tts.LEVEL_DB + 6)):
        e = breeze_engine()
        for text in ["first"] * n + ["quiet"] * quiet:
            heard(e, text)
        level = heard(e, "first")
        assert abs(level - expect) < 1.0, (quiet, level)


def a_voices_gain_starts_again_when_the_model_unloads():
    e = breeze_engine()
    heard(e, "quiet")                                              # the voice's gain: 6 dB up
    e.unload(); loaded(e)
    assert abs(heard(e, "first") - tts.LEVEL_DB) < 1.0


class Reckless:
    """A streaming voice model that keeps handing on chunks after a stop (the reader must drop them)."""
    sample_rate, loaded, loaded_model, streams = RATE, True, "fake", True
    def load(self): pass
    def voice(self, name=None): return ("v.wav", "words", None)
    def speak(self, text, should_stop=lambda: False, on_chunk=None, **kw):
        whole, n = voice(2.4), int(CHUNK_S * RATE)
        for i in range(0, len(whole), n):
            time.sleep(n / RATE / SPEED)
            on_chunk(whole[i:i + n])
        return np.zeros(0, np.float32)


def no_chunk_of_a_stopped_reading_reaches_the_player():
    added, first = [], threading.Event()
    class Watched(Player):
        def add(self, index, audio):
            added.append(time.monotonic()); first.set(); super().add(index, audio)
    r = Reader({"volume": 0.5, "model": "fake"}, engine=Reckless(),
               player_factory=lambda rate, volume, on_piece=None, on_done=None:
                   Watched(rate, volume, on_piece=on_piece, on_done=on_done, stream_factory=Device))
    r.read_parts([("long", None, None)])
    assert first.wait(5)
    r.stop(); stopped = time.monotonic()
    time.sleep(2.4 / SPEED)                                        # the model carries on to the end
    assert added and all(t < stopped for t in added), (len(added), [t - stopped for t in added])


def the_player_notes_when_it_runs_dry():
    """A wait for the next sound longer than DRY_S is logged; pieces that keep up are not."""
    import logging
    for gap, logged in ((0.0, False), (playback.DRY_S + 0.3, True)):
        notes, done = [], threading.Event()
        h = logging.Handler(); h.emit = lambda rec: notes.append(rec.getMessage())
        logging.getLogger("waudio").addHandler(h); logging.getLogger("waudio").setLevel(logging.INFO)
        try:
            p = Player(RATE, 0.5, on_done=lambda *a: done.set(), stream_factory=Device)
            p.add(0, voice(0.2)); p.add(1, voice(0.2))
            time.sleep(0.2 + gap); p.add(2, voice(0.2)); p.finish()
            assert done.wait(5)
        finally:
            logging.getLogger("waudio").removeHandler(h)
        assert any("waited" in m for m in notes) == logged, (gap, notes)


def a_stop_ends_a_streamed_sentence_within_a_chunk():
    e = breeze_engine(); got = []
    asked = {"n": 0}
    def stop():
        asked["n"] += 1; return asked["n"] > 2
    e.speak("first", should_stop=stop, persona_text="", on_chunk=got.append)
    assert 1 <= len(got) <= 3, len(got)


def another_reading_speed_is_made_whole():
    e = breeze_engine(speed=1.25)
    assert not e.streams
    got = []
    audio = e.speak("first", persona_text="", on_chunk=got.append)
    assert not got and len(audio) < len(voice(2.0))               # stretched as one sentence


def a_part_with_its_own_speed_is_made_whole_at_that_speed_times_the_reading_speed():
    for reading_speed, part_speed in ((1.0, 1.25), (1.25, 0.8), (1.0, 0.9)):
        e = breeze_engine(speed=reading_speed)
        got = []
        audio = e.speak("first", persona_text="", on_chunk=got.append, speed=part_speed)
        expected = len(voice(2.0)) / (reading_speed * part_speed)
        assert not got and abs(len(audio) - expected) < 0.05 * len(voice(2.0)), (reading_speed, part_speed, len(audio), expected)


def the_reader_hands_each_part_its_own_speed():
    model = SlowModel(True)
    read(True, ["short", ("short", None, None, 1.2), ("short", None, None)], model)
    assert model.asked == [("short", 1.0), ("short", 1.2), ("short", 1.0)], model.asked


chunks_of_one_piece_play_as_one()
if realtime("no pause between a short sentence and a long one"):
    a_long_sentence_after_a_short_one_leaves_no_pause()
a_streamed_voice_lands_at_the_reading_level_and_keeps_its_ups_and_downs()
a_voices_first_sentence_is_levelled_by_its_speech_not_a_breath_before_it()
a_faint_voice_is_still_brought_to_the_reading_level()
a_voices_first_gain_turns_it_up_at_most_first_gain_max()
a_hushed_sentence_does_not_turn_up_the_next()
one_odd_sentence_doesnt_move_the_next_but_a_voice_that_changed_is_followed()
a_voices_gain_starts_again_when_the_model_unloads()
no_chunk_of_a_stopped_reading_reaches_the_player()
if realtime("a wait for the next sound longer than DRY_S is logged, shorter ones not"):
    the_player_notes_when_it_runs_dry()
a_stop_ends_a_streamed_sentence_within_a_chunk()
another_reading_speed_is_made_whole()
a_part_with_its_own_speed_is_made_whole_at_that_speed_times_the_reading_speed()
the_reader_hands_each_part_its_own_speed()
print("read stream checks: ok")
