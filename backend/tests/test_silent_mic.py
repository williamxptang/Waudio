#!/usr/bin/env python3
"""A microphone that sends pure digital silence (a muted headset, a stuck device) says so. Exact zeros
for SILENCE_S make the recorder report "silent" (a quiet room is not silence); the app then reopens
the current input once, keeping what was recorded, and says where it carries on; still silent, it
says to check the input, once. Run checks says a microphone that heard nothing at all.

  python3 backend/tests/test_silent_mic.py
"""
import shutil, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from waits import wait_until
from waudio import audio, checks, config
from waudio.input import microphone
from waudio.engine import SAMPLE_RATE

microphone.SILENCE_S = 0.4  # the rule, not the wait: zeros for longer than this are "silent"
BLOCK = 320                 # 20 ms


class Stream:
    """An input that delivers blocks from its source until closed."""
    def __init__(self, callback, source):
        self.callback, self.source, self.closed = callback, source, False
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
    def _run(self):
        while not self.closed:
            self.callback(self.source(BLOCK).reshape(-1, 1).astype(np.float32), BLOCK, None, 0)
            time.sleep(BLOCK / SAMPLE_RATE)
    def abort(self): self.closed = True
    def close(self): self.closed = True


class FakeSD:
    """Each open takes the next source; the device is named after the open."""
    PortAudioError = RuntimeError
    def __init__(self, *sources): self.sources, self.opened = list(sources), 0
    def _terminate(self): pass
    def _initialize(self): pass
    def query_devices(self, kind=None): return {"name": f"Mic {self.opened}"}
    def InputStream(self, callback=None, **kw):
        self.opened += 1
        return Stream(callback, self.sources[min(self.opened, len(self.sources)) - 1])


zeros = lambda n: np.zeros(n)
voice = lambda n: np.sin(np.arange(n) / 5) * 0.3
room = lambda n, rng=np.random.default_rng(0): rng.normal(0, 10 ** (-70 / 20), n)  # ≈ −70 dBFS, never exact zeros


def recorder(*sources):
    audio.sd = FakeSD(*sources); audio._open = 0
    r = microphone.Recorder(); r.start()
    return r


def exact_zeros_are_silent_and_a_reopen_keeps_the_recording():
    r = recorder(zeros, voice)
    wait_until(lambda: r.health() == (False, "silent"), timeout=3, what="zeros to count as silent")
    before = len(np.concatenate(r.chunks))
    assert r.switch() == "Mic 2"
    wait_until(lambda: r.health() == (True, ""), timeout=3, what="the new input to be healthy")
    got = r.stop()
    assert len(got) > before and not got[:before].any() and got[before:].any(), (len(got), before)
    assert audio._open == 0


def a_quiet_room_is_not_silence():
    r = recorder(room)
    time.sleep(microphone.SILENCE_S * 2.5)
    assert r.health() == (True, ""), r.health()
    r.stop()


def run_checks_says_a_microphone_that_heard_nothing():
    class App:
        mic_lock, recording_voice = threading.Lock(), False
        class recorder: active = False
    real = microphone.probe
    try:
        for peak, level in ((0.0, checks.YELLOW), (0.2, checks.GREEN)):
            microphone.probe = lambda s, peak=peak: ("Headset", peak)
            row = checks.check_microphone(App())[0]
            assert row["level"] == level and ("heard nothing" in row["text"]) == (peak == 0.0), (peak, row)
    finally:
        microphone.probe = real


# ---- the app: health() says silent ----
tmp = Path(tempfile.mkdtemp()) / "config.toml"; shutil.copy(config.EXAMPLE, tmp); config.PATH = tmp
from waudio import app as A
osd = A.osd
osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)
osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)
for name in ("play", "hud", "paste", "copy_text"):
    setattr(osd, name, lambda *a, **k: None)
osd.output_muted = lambda m=None: False
osd.output_playing = lambda: False
osd.focus_target = lambda: (1, None)
osd.focused_field = lambda: "text"


class FakeRec:
    """health() goes through `states` one per switch: what the input is like after each reopen."""
    def __init__(self, states): self.states, self.switches, self.active = list(states), 0, False
    def start(self): self.active = True
    def stop(self): self.active = False; return np.zeros(SAMPLE_RATE, np.float32)
    def health(self): return self.states[min(self.switches, len(self.states) - 1)]
    def switch(self): self.switches += 1; return "Built-in Microphone"
    device = "Test Mic"
    def take_level(self): return 0.0
    def seconds(self): return 0.0
    def position(self): return 0
    def snapshot(self): return np.zeros(0, np.float32)


a = A.App(); a.cfg["dictation"]["typing"] = "end"
notes = []
a.notify = notes.append


def the_app_reopens_a_silent_input_once_and_says_so():
    #  health after 0 and 1 reopens               reopens  what you are told
    rows = [([(False, "silent"), (True, "")],          1, ["Microphone silent — switched to Built-in Microphone"]),
            ([(False, "silent"), (False, "silent")],   1, ["Microphone silent — switched to Built-in Microphone",
                                                           "The microphone is silent — check System Settings → Sound → Input"])]
    assert rows
    for states, switches, told in rows:
        notes.clear(); a.recorder = FakeRec(states)
        a.toggle()
        time.sleep(1.5)  # several health checks (every 0.25 s): still one reopen, each notice once
        assert a.recorder.switches == switches and notes == told, (states, a.recorder.switches, notes)
        assert a.recorder.active, "the dictation goes on after the reopen"
        a.cancel(); wait_until(lambda: not a.recorder.active and a.pending == 0, what="the dictation to end")


def a_silent_input_with_nothing_to_switch_to_stops_the_dictation():
    """The reopen fails (no input left): the dictation stops cleanly, what was recorded is finished as
    usual, and you are told there is no other input."""
    notes.clear(); a.recorder = FakeRec([(False, "silent")])
    def no_input(): raise RuntimeError("no input device")
    a.recorder.switch = no_input
    a.toggle()
    wait_until(lambda: any("silent and no other input" in n for n in notes), what="the notice")
    wait_until(lambda: not a.recorder.active and a.pending == 0, what="the dictation to stop")


exact_zeros_are_silent_and_a_reopen_keeps_the_recording()
a_quiet_room_is_not_silence()
run_checks_says_a_microphone_that_heard_nothing()
the_app_reopens_a_silent_input_once_and_says_so()
a_silent_input_with_nothing_to_switch_to_stops_the_dictation()
a.server.stop(); a.reader.close()
print("silent microphone: ok")
