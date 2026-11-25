#!/usr/bin/env python3
"""While dictating, with a fake microphone on a copy of config.example.toml: the pill gets the
microphone's name and its level about every 80 ms (none with the waveform or the pill off, none once
it stops), the pill's settings name the dictation shortcut for its Stop hint, and Esc cancels a
dictation (kept in History) — at once while it is short, only on a second Esc once it is long.

  python3 backend/tests/test_dictation_pill.py
"""
import shutil, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waits import nothing_more_for, realtime, wait_until
from waudio import config
tmp = Path(tempfile.mkdtemp()) / "config.toml"; shutil.copy(config.EXAMPLE, tmp); config.PATH = tmp
from waudio import app as A
A.osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # never your real history
A.osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)
import numpy as np
osd = A.osd
sent = []
osd.play = lambda *a: None
osd.hud = lambda s, d="": sent.append(("hud", s))
osd.pill_level = lambda v: sent.append(("level", v))
osd.pill_mic = lambda n: sent.append(("mic", n))
osd.pill_hint = lambda t: sent.append(("hint", t))
osd.pill_style = lambda p, theme: sent.append(("style", p))
osd.output_muted = lambda m=None: False
osd.output_playing = lambda: False
osd.focus_target = lambda: (1, None)
osd.focused_field = lambda: "text"

class FakeRec:
    active, device, length = False, "Test Mic", 0.0
    def start(self): self.active = True
    def stop(self):
        self.active = False
        return np.zeros(int(1.5 * 16000), np.float32)
    def take_level(self): return 0.5
    def seconds(self): return self.length
    def health(self): return True, ""
    def position(self): return 0
    def snapshot(self): return np.zeros(0, np.float32)

a = A.App(); a.recorder = FakeRec()
a.cfg["audio"]["vad"] = False
levels = lambda: [v for k, v in sent if k == "level"]

def fresh(length=0.0):
    """A new dictation of `length` seconds so far."""
    if a.recorder.active:
        a.cancel()
    sent.clear(); a.recorder.length = length
    a.start(); assert a.recorder.active

# the microphone's name, then its level while dictating; nothing more once it stops
fresh()
assert ("mic", "Test Mic") in sent, sent
wait_until(lambda: len(levels()) >= 3, what="levels while dictating")
assert set(levels()) == {0.5}, levels()
if realtime("the pill's level about every 80 ms"):
    t0, n0 = time.monotonic(), len(levels()); nothing_more_for(0.5)
    per_s = (len(levels()) - n0) / (time.monotonic() - t0)
    assert 8 <= per_s <= 14, f"{per_s:.1f} levels a second (one about every 80 ms)"
a.cancel(); nothing_more_for(0.2); n = len(levels()); nothing_more_for(0.3)
assert len(levels()) == n, "levels went on after the dictation ended"

# none with the waveform off, or the pill off
for key in ("waveform", "enabled"):
    a.cfg["pill"][key] = False
    fresh(); nothing_more_for(0.4)
    assert not levels(), (key, levels())
    a.cfg["pill"][key] = True

# the pill's settings carry the dictation shortcut, for its Stop hint
sent.clear(); a.send_pill_style()
style = [p for k, p in sent if k == "style"]
assert style and style[-1]["stop_key"] == a.shortcut_label() and style[-1]["stop_key"], style

# Esc: a short dictation is cancelled at once, a long one only by a second Esc within 2 s
cancelled = lambda: wait_until(lambda: not a.recorder.active, what="Esc to cancel it")
fresh(length=5); kept = len(a.history); a.user_key("escape"); cancelled()
wait_until(lambda: len(a.history) == kept + 1, what="the cancelled dictation in History")
fresh(length=40); a.user_key("escape"); nothing_more_for(0.2)
assert a.recorder.active and ("hint", "Esc again to cancel") in sent, sent
a.user_key("escape"); cancelled()
fresh(length=40); a.user_key("escape"); a.esc_at -= 3   # the second Esc too late: it only asks again
a.user_key("escape"); nothing_more_for(0.2)
assert a.recorder.active and [k for k, _ in sent].count("hint") == 2, sent
a.cancel()
# Esc with no dictation running does nothing to the microphone
sent.clear(); a.user_key("escape"); nothing_more_for(0.2)
assert not a.recorder.active and not [k for k, _ in sent if k == "hint"], sent
# the keyboard hook: Esc held down (its auto-repeat) is one press, so it never counts as the second Esc;
# other keys' repeats still reach typing (Backspace held)
import Quartz
from waudio.platforms import macos
class Kb: paused, matchers = False, []
heard = []; Kb.on_user_key = staticmethod(heard.append)
hook = macos.listener_options(Kb)["darwin_intercept"]
def press(code, repeat):
    e = Quartz.CGEventCreateKeyboardEvent(None, code, True)
    Quartz.CGEventSetIntegerValueField(e, Quartz.kCGKeyboardEventAutorepeat, 1 if repeat else 0)
    assert hook(Quartz.kCGEventKeyDown, e) is not None, "the key was swallowed"
press(53, False); press(53, True); press(53, True); press(51, True)
assert heard == ["escape", None], heard
print("dictation pill: ok")
a.server.stop()
