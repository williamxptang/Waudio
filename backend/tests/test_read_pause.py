#!/usr/bin/env python3
"""Pausing a reading: the speaker holds mid-sentence and goes on from the same sample (nothing played
twice, nothing skipped) and a long pause stops the speaker until resume, Stop still ends a paused reading, the reader says which part it is on, and
the read shortcut pauses and resumes while Esc stops. A fake voice model and a fake speaker that
plays in real time.

  python3 backend/tests/test_read_pause.py
"""
import sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from waits import nothing_more_for, wait_until
from waudio.output import player as player_module
from waudio.output.player import Player
from waudio.reader import Reader

RATE = 16000


class Speaker:
    """Records every sample written, in real time."""
    def __init__(self, rate):
        self.out = []
    def start(self): pass
    def stop(self): pass
    def abort(self): pass
    def close(self): pass
    def write(self, block):
        self.out.append(block[:, 0].copy())
        time.sleep(len(block) / RATE)


def ramp(n, start=0):
    return (np.arange(start, start + n, dtype=np.float32) % 1000) / 10000  # every sample different


def steady(count, gap=0.06):
    """count() the same across gap seconds: the block a speaker was writing has finished."""
    before = count(); nothing_more_for(gap); return count() == before


def the_speaker_holds_and_goes_on_from_the_same_sample():
    speakers, done = [], threading.Event()
    p = Player(RATE, 1.0, on_done=lambda *a: done.set(), stream_factory=lambda r: speakers.append(Speaker(r)) or speakers[-1])
    a = ramp(RATE)  # one second
    p.add(0, a); p.finish()
    heard = lambda: sum(len(b) for s in speakers for b in s.out)
    wait_until(lambda: heard() >= RATE // 4, what="a quarter of a second played")
    p.pause()
    assert p.paused
    wait_until(lambda: steady(heard), what="the block in flight to finish")
    held = heard()
    nothing_more_for(0.4)
    assert heard() == held, "played while paused"
    assert 0 < held < RATE
    p.resume()
    assert done.wait(5)
    played = np.concatenate(speakers[0].out)
    ending = int(RATE * player_module.FADE_S) + int(RATE * player_module.END_S)  # the ramp ends loud: faded, then silence
    assert len(played) == len(a) + ending, (len(played), len(a))  # nothing twice, nothing skipped
    voice = played[:len(a)]
    assert np.all(np.diff(voice[voice > 0]) != 0)


def stop_ends_a_paused_reading():
    done, how = threading.Event(), {}
    speakers = []
    p = Player(RATE, 1.0, on_done=lambda stopped, error=None: (how.update(stopped=stopped), done.set()),
               stream_factory=lambda r: speakers.append(Speaker(r)) or speakers[-1])
    p.add(0, ramp(RATE)); p.finish()
    wait_until(lambda: speakers and speakers[0].out, what="the first block played"); p.pause()
    p.stop()
    assert done.wait(2) and how["stopped"] is True


class Engine:
    loaded, loaded_model, sample_rate = True, "m", RATE
    def voice(self, name=None): return ("v.wav", "words")
    def load(self): pass
    def speak(self, text, should_stop=lambda: False, voice=None, persona_text=None):
        return ramp(int(0.25 * RATE))


def the_reader_pauses_says_its_part_and_resumes():
    cfg = {"model": "m", "volume": 0.5}
    r = Reader(cfg, player_factory=lambda rate, vol, on_piece=None, on_done=None: Player(rate, vol, on_piece, on_done, stream_factory=Speaker),
               engine=Engine())
    assert r.part is None and not r.paused
    r.read_parts([("First part. Still the first.", None, None), ("Second part.", None, None), ("Third part.", None, None)])
    wait_until(lambda: r.part == 0, what="the first part")
    wait_until(lambda: r.part == 1, timeout=3, what="the second part")  # the third piece is the second part's
    r.pause()
    assert r.paused and r.active
    at = r.part
    nothing_more_for(0.6)
    assert r.part == at and r.paused  # held
    r.resume()
    assert not r.paused
    wait_until(lambda: not r.active, what="the reading to end")
    assert r.part is None and not r.paused and r.finished  # played to its end: no part, not paused, finished
    r.read("Again.")
    wait_until(lambda: r.active, what="the new reading")
    assert not r.finished  # a new reading isn't finished
    r.pause(); r.stop()
    assert not r.active and not r.paused and r.part is None and not r.finished  # stopped, not finished


class SlowEngine(Engine):
    def speak(self, *a, **k):
        time.sleep(0.4)  # the first sentence takes a moment: no speaker yet
        return ramp(int(0.25 * RATE))


def a_pause_before_the_first_sound_holds_it():
    """⌥R / ⏸ right after a reading starts, while the first sentence is still being made: it starts paused."""
    played = []
    def speaker(rate):
        s = Speaker(rate); played.append(s); return s
    r = Reader({"model": "m", "volume": 0.5}, engine=SlowEngine(),
               player_factory=lambda rate, vol, on_piece=None, on_done=None: Player(rate, vol, on_piece, on_done, stream_factory=speaker))
    r.read("One. Two.")
    r.pause()
    assert r.paused, "a pause before the first sound counts"
    nothing_more_for(1.0)
    assert r.active and r.paused and sum(len(b) for s in played for b in s.out) == 0, "it played while paused"
    r.resume()
    wait_until(lambda: not r.active, what="the reading to end"); assert r.finished


def readings_have_an_id():
    """Each reading has its own id, so a page can tell its reading from another one."""
    r = Reader({"model": "m", "volume": 0.5}, engine=Engine(),
               player_factory=lambda rate, vol, on_piece=None, on_done=None: Player(rate, vol, on_piece, on_done, stream_factory=Speaker))
    r.read("One."); first = r.reading_id
    r.read("Two."); assert r.reading_id != first and r.reading_id is not None
    r.stop()


def the_shortcut_pauses_and_resumes_and_esc_stops():
    """App.on_read: reading -> pause; paused -> resume. Esc stops either."""
    from waudio import app as A
    calls = []
    class FakeReader:
        active, paused = True, False
        def pause(self): calls.append("pause"); self.paused = True
        def resume(self): calls.append("resume"); self.paused = False
        def stop(self): calls.append("stop"); self.active = False
    fake = type("App", (), {})()
    fake.reader, fake.is_locked = FakeReader(), lambda: False
    A.App.on_read(fake); A.App.on_read(fake)
    assert calls == ["pause", "resume"], calls


class CountingSpeaker(Speaker):
    """Also counts stop() and start() (a stopped speaker takes no blocks: write raises)."""
    def __init__(self, rate):
        super().__init__(rate); self.calls, self.running = [], False
    def start(self): self.calls.append("start"); self.running = True
    def stop(self): self.calls.append("stop"); self.running = False
    def write(self, block):
        assert self.running, "written to a stopped speaker"
        super().write(block)


def a_long_pause_stops_the_speaker_and_resume_starts_it_again():
    """Past PAUSE_IDLE_S a paused reading stops the speaker (no CPU, the computer may sleep); resume starts the same
    one and plays on from the same sample. A short pause leaves it running."""
    speakers, done = [], threading.Event()
    idle, player_module.PAUSE_IDLE_S = player_module.PAUSE_IDLE_S, 0.3
    try:
        p = Player(RATE, 1.0, on_done=lambda *a: done.set(), stream_factory=lambda r: speakers.append(CountingSpeaker(r)) or speakers[-1])
        a = ramp(RATE)
        p.add(0, a); p.finish()
        heard = lambda: sum(len(b) for s in speakers for b in s.out)
        wait_until(lambda: heard() >= RATE // 8, what="the first blocks played")
        p.pause(); nothing_more_for(0.1); p.resume()  # short: still running
        wait_until(lambda: heard() >= RATE // 4, what="playing on after a short pause")
        assert speakers[0].calls == ["start"], speakers[0].calls
        p.pause()
        wait_until(lambda: speakers[0].calls[-1:] == ["stop"], what="the speaker stopped in a long pause")
        p.resume()
        assert done.wait(5)
        assert len(speakers) == 1 and speakers[0].calls[:3] == ["start", "stop", "start"], speakers[0].calls
        played = np.concatenate(speakers[0].out)
        ending = int(RATE * player_module.FADE_S) + int(RATE * player_module.END_S)
        assert len(played) == len(a) + ending, (len(played), len(a))  # nothing twice, nothing skipped
    finally:
        player_module.PAUSE_IDLE_S = idle


def a_speaker_that_wont_start_again_is_replaced_and_the_reading_goes_on():
    """After a long pause the old speaker refuses to start (its device is gone): a new one opens and plays on."""
    speakers, done = [], threading.Event()
    class Refuses(CountingSpeaker):
        def start(self):
            if self.calls:  # started once already: the device is gone now
                self.calls.append("refused"); raise OSError("device gone")
            super().start()
    idle, player_module.PAUSE_IDLE_S = player_module.PAUSE_IDLE_S, 0.3
    try:
        make = lambda r: speakers.append((Refuses if not speakers else CountingSpeaker)(r)) or speakers[-1]
        p = Player(RATE, 1.0, on_done=lambda *a: done.set(), stream_factory=make)
        a = ramp(RATE)
        p.add(0, a); p.finish()
        heard = lambda: sum(len(b) for s in speakers for b in s.out)
        wait_until(lambda: heard() >= RATE // 8, what="the first blocks played")
        p.pause()
        wait_until(lambda: speakers[0].calls[-1:] == ["stop"], what="the speaker stopped in a long pause")
        p.resume()
        assert done.wait(5)
        assert len(speakers) == 2 and speakers[0].calls == ["start", "stop", "refused"], [s.calls for s in speakers]
        ending = int(RATE * player_module.FADE_S) + int(RATE * player_module.END_S)
        assert heard() == len(a) + ending, (heard(), len(a))  # the new speaker went on from the same sample
    finally:
        player_module.PAUSE_IDLE_S = idle


the_speaker_holds_and_goes_on_from_the_same_sample()
stop_ends_a_paused_reading()
the_reader_pauses_says_its_part_and_resumes()
a_pause_before_the_first_sound_holds_it()
readings_have_an_id()
the_shortcut_pauses_and_resumes_and_esc_stops()
a_long_pause_stops_the_speaker_and_resume_starts_it_again()
a_speaker_that_wont_start_again_is_replaced_and_the_reading_goes_on()
print("read pause checks: ok")
