#!/usr/bin/env python3
"""App paths with a fake microphone and model, on a copy of config.example.toml (no keyboard, no screen):
paste + Recent, no text box -> copied, password field refused, sleep cancels and keeps the audio,
shortcut ignored until wake, Transcribe again, only the newest recordings keep audio; live mode:
nothing typed after Cancel, back-to-back dictations keep their own key watch, a dead mic with no
other input stops the dictation cleanly.

  python3 backend/tests/test_app.py
"""
import shutil, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waits import nothing_more_for, wait_until
from waudio import config
tmp = Path(tempfile.mkdtemp()) / "config.toml"; shutil.copy(config.EXAMPLE, tmp); config.PATH = tmp
from waudio import app as A
A.osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # never your real history
A.osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # nor your real logs
import numpy as np
osd = A.osd
calls = []
osd.play = lambda *a: None
osd.hud = lambda s, d="": calls.append(("hud", s))
osd.paste = lambda t: calls.append(("paste", t))
osd.copy_text = lambda t: calls.append(("copy", t))
osd.output_muted = lambda m=None: False
osd.focus_target = lambda: (1, None)
osd.output_playing = lambda: False  # no music by default (real CoreAudio otherwise)
field = {"v": "text"}
osd.focused_field = lambda: field["v"]

class FakeRec:
    active = False
    def start(self): self.active = True; self.t = time.time()
    def stop(self):
        self.active = False
        return (np.sin(np.arange(int(1.5*16000))/5)*0.3).astype(np.float32)
    def health(self): return True, ""
    def position(self): return 0
    def snapshot(self): return np.zeros(0, np.float32)
    device = "Test Mic"
    def take_level(self): return 0.0
    def seconds(self): return 0.0

class R:  # engine result
    capped = False  # ran to the token budget
    def __init__(s, t): s.text, s.language, s.audio_s, s.secs, s.cut, s.raw = t, "English", 1.5, 0.1, False, t
class FakeEngine:
    loaded = True; loaded_model = "x"
    def transcribe(self, a, language=None, prefix="", strict=False, draft=""): return R("hello there")
    def load(self): pass

a = A.App(); a.recorder = FakeRec(); a.engine = FakeEngine()
a.cfg["dictation"]["typing"] = "end"
a.cfg["engine"]["follow_keyboard"] = False  # checked on its own below
a.cfg["audio"]["vad"] = False  # the fake microphone plays a tone: loudness alone takes it for speech
threading.Thread(target=a.worker, daemon=True).start()
def run(fn):
    """Stop (or redo) a dictation and wait until the worker has finished it."""
    fn(); wait_until(lambda: a.pending == 0 and not a.running, what="the dictation to finish")

# normal end-mode dictation -> paste + history
a.toggle(); run(a.toggle)
assert ("paste", "hello there ") in calls and a.history[-1]["text"] == "hello there", calls
# a dictation that ran to the token budget is pasted as heard, and the menu says the end may be missing
capped = R("hello there"); capped.capped = True
FakeEngine.transcribe = lambda self, a, language=None, prefix="", strict=False, draft="": capped
calls.clear(); a.toggle(); run(a.toggle)
assert ("paste", "hello there ") in calls and "length limit" in a.notice[0], (calls, a.notice)
FakeEngine.transcribe = lambda self, a, language=None, prefix="", strict=False, draft="": R("hello there")
a.notice = ("", 0.0); calls.clear(); a.toggle(); run(a.toggle)
assert "length limit" not in a.notice[0], a.notice
# a quiet room (noise + keyboard clicks, no voice) -> nothing transcribed, nothing pasted
room = np.random.default_rng(2).normal(0, 0.0005, 5 * 16000).astype(np.float32)
for at in range(1000, len(room) - 100, 7000): room[at:at + 80] += 0.3
asked = []; FakeEngine.transcribe = lambda self, a, language=None, prefix="", strict=False, draft="", f=FakeEngine.transcribe: asked.append(1) or f(self, a)
FakeRec.stop = lambda self, f=FakeRec.stop: (setattr(self, "active", False), room)[1]
calls.clear(); a.toggle(); run(a.toggle)
assert not asked and not [c for c in calls if c[0] == "paste"] and "catch" in a.status_text(), (calls, a.status_text())
FakeRec.stop = lambda self: (setattr(self, "active", False), (np.sin(np.arange(int(1.5*16000))/5)*0.3).astype(np.float32))[1]
# the gate's settings from a hand-edited config.toml: clamped, and nonsense falls back to the defaults
for floor, least, want in ((-10, 5, (-30.0, 1.0)), (-99, 0, (-70.0, 0.1)), ("x", None, (-50.0, 0.25)), (float("nan"), "0.5", (-50.0, 0.5))):
    a.cfg["audio"]["speech_floor_db"], a.cfg["audio"]["min_speech_s"] = floor, least
    assert a.speech_gate() == want, (floor, least, a.speech_gate())
a.cfg["audio"].pop("speech_floor_db"); a.cfg["audio"].pop("min_speech_s"); assert a.speech_gate() == (-50.0, 0.25)
# not a text box -> copied, notice
field["v"] = "none"; calls.clear(); a.toggle(); run(a.toggle)
assert ("copy", "hello there") in calls and "copied" in a.status_text(), (calls, a.status_text())
# password field -> refused
field["v"] = "secure"; a.toggle(); assert not a.recorder.active and "password" in a.status_text()
# sleep mid-recording -> cancelled, kept in the recent list with audio, shortcut ignored until wake
field["v"] = "text"; a.cfg["shortcut"]["mode"] = "toggle"; a.toggle(); assert a.recorder.active
a.on_sleep(); assert not a.recorder.active and a.locked and a.history[-1]["audio"] is not None
a.on_shortcut(); assert not a.recorder.active
locked = {"v": False}; real_locked = osd.screen_locked; osd.screen_locked = lambda: locked["v"]  # never your Mac's own lock screen
a.start_keyboard = lambda: None; a.on_wake(); assert not a.locked
locked["v"] = True
a.on_sleep(); a.on_wake(); assert a.is_locked()                 # woke onto the lock screen
locked["v"] = None; assert a.is_locked()                                 # macOS doesn't say: stays off
locked["v"] = False; assert not a.is_locked() and not a.locked           # the unlock notice came too early
a.on_sleep(); assert a.is_locked()                                       # just locked or asleep: no asking
osd.screen_locked = real_locked; a.locked = False
# transcribe again (kept recordings)
assert a.history[-1]["audio"] is not None  # the newest recordings are still kept (transcribe again)
calls.clear(); run(lambda: a.again(a.history[-1])); assert ("copy", "hello there") in calls and a.pending == 0
v = a.store.version; a.remember("one more", np.zeros(16000, np.float32)); assert a.store.version > v + 1  # add + touch
assert a.history[-1]["id"] in a.kept_recordings() and len(a.kept_recordings()) <= 3
kept = a.kept_recordings(); assert kept and kept <= {e["id"] for e in a.history if e["audio"] is not None}
# music until the mute: what the mic heard before it is returned as silence (live and at stop)
from waudio.input.microphone import Recorder
rec = Recorder(); rec.chunks = [np.full(16000, 0.3, np.float32), np.full(16000, 0.3, np.float32)]
rec.silence_before(8000)
snap = rec.snapshot(); assert not snap[:8000].any() and snap[8000:].all() and len(snap) == 32000
rec.recording = False; rec.stream = None
out = rec.stop(); assert not out[:8000].any() and out[8000:].all()
class MuteRec(FakeRec):
    def __init__(self): self.marks = []; self.active = True; self.pos = 16000
    def position(self): return self.pos
    def silence_before(self, n): self.marks.append(n)
saved = a.recorder; a.recorder = MuteRec(); osd.output_muted = lambda m=None: False
a.cfg["audio"]["others"] = "mute"
a.music_at_start = False; a._mute_others(a.session); a._unmute_others()
assert a.recorder.marks == [], a.recorder.marks          # nothing playing: nothing silenced
a.music_at_start = True; a._mute_others(a.session); a._unmute_others()
assert a.recorder.marks == [16000 + int(A.MUTE_LAG_S * 16000)], a.recorder.marks
# pause mode: a slow pause (2 s of AppleScript) silences only up to when it was asked for
a.recorder.marks.clear(); a.cfg["audio"]["others"] = "pause"
def slow_pause():
    a.recorder.pos += 32000  # the user kept talking for 2 s meanwhile
    return ["Music"]
osd.pause_players = slow_pause; osd.resume_players = lambda p: None
a._mute_others(a.session)
assert a.recorder.marks == [16000 + int(A.MUTE_LAG_S * 16000)], a.recorder.marks
a.paused_players = []; a.cfg["audio"]["others"] = "mute"; a.recorder = saved; a.music_at_start = False
# stopping the mic hangs (PortAudio deadlock): the recording still comes back, the engine restarts
import waudio.input.microphone as mic
class HungStream:
    def abort(self): threading.Event().wait()  # never returns, like the deadlock
    def close(self): pass
mic.CLOSE_TIMEOUT_S = 0.3
rec = Recorder(); stuck_calls = []; rec.on_stuck = lambda: stuck_calls.append(1)
rec.stream, rec.recording, rec.chunks = HungStream(), True, [np.full(16000, 0.3, np.float32)]
t0 = time.monotonic(); out = rec.stop(); took = time.monotonic() - t0
wait_until(lambda: stuck_calls, what="the stuck callback"); nothing_more_for(0.1)  # once
assert len(out) == 16000 and 0.25 < took < 1.0 and rec.stuck and stuck_calls == [1], (len(out), took, rec.stuck, stuck_calls)
try: rec.start(); raise AssertionError("a stuck recorder must not start")
except RuntimeError: pass
rec2 = Recorder(); rec2.stream, rec2.recording = HungStream(), True
try: rec2.switch(); raise AssertionError("a stuck recorder must not reopen")
except RuntimeError: pass
restarts = []; real_restart = a.restart; a.restart = lambda: restarts.append(1)
a._mic_stuck(wait_s=1); assert restarts == [1] and a.stuck_marker.exists()
b = A.App(); assert "microphone got stuck" in b.notice[0] and not b.stuck_marker.exists(), b.notice  # told after the restart
a.restart = real_restart; mic.CLOSE_TIMEOUT_S = 1.5
# start silence: the start sound the mic hears never becomes words (sounds off: only the mic's click)
r = MuteRec(); r.active = False; a.recorder = r; playing = osd.output_playing; osd.output_playing = lambda: False
field["v"] = "text"; a.cfg["audio"]["start_silence"] = 0.5; a.toggle()
assert r.active and r.marks == [8000], r.marks; a.cancel()
osd.output_playing = lambda: True; r.marks.clear(); a.toggle()  # music too: the longer music guard is added
assert r.marks == [8000, int(A.QUIET_START_S * 16000)], r.marks; a.cancel()
a.cfg["audio"]["sounds"] = False; assert a.start_silence() == 0.1
a.cfg["audio"]["start_silence"] = 0.05; assert a.start_silence() == 0.05
a.cfg["audio"]["sounds"] = True; a.cfg["audio"]["start_silence"] = 3; assert a.start_silence() == 1.0
a.cfg["audio"]["start_silence"] = 0.5; a.recorder = saved; osd.output_playing = playing; a.music_at_start = False

# the keyboard in use when a dictation starts sets its one language; the switches hand it back to
# detection (macOS is asked for real first: a read that fails would quietly mean detection for ever)
osd.refresh_paste_key(); assert osd.keyboard_language(), "macOS gave no keyboard language"
keyboard = {"code": "en"}; said = []; stricts = []; plain = FakeEngine.transcribe; real_keyboard = osd.keyboard_language, osd.run_on_main
FakeEngine.transcribe = lambda self, a, language=None, prefix="", strict=False, draft="": said.append(language) or stricts.append(strict) or plain(self, a)
osd.keyboard_language = lambda: keyboard["code"]; osd.run_on_main = lambda fn: fn()
two = {"languages": ["English", "Chinese"]}
for code, switches, want in (("en", {"follow_keyboard": True, "mix_languages": False}, "English"),
                             ("zh-Hans", {"follow_keyboard": True, "mix_languages": False}, "Chinese"),
                             ("zh-Hans", {"follow_keyboard": True, "mix_languages": True}, None),
                             ("zh-Hans", {"follow_keyboard": False, "mix_languages": False}, None)):
    a.cfg["engine"].update(two, **switches); keyboard["code"] = code; said.clear()
    a.toggle(); keyboard["code"] = "ja"; run(a.toggle)  # the keyboard at the start counts
    assert said == [want] and stricts[-1] == (want is not None), (code, switches, said, stricts)
# the keyboard is read fresh both ways: on the main thread (the menu), which nothing else can run
# on meanwhile, and from the shortcut's own thread, through the main thread
fresh = []; real_refresh = osd.refresh_paste_key; osd.refresh_paste_key = lambda: fresh.append(threading.current_thread())
a.cfg["engine"].update(two, follow_keyboard=True, mix_languages=False); keyboard["code"] = "en"
osd.run_on_main = lambda fn: None  # as when this is the main thread: it would only run afterwards
at = time.monotonic(); a.toggle(); took = time.monotonic() - at; run(a.toggle)
assert fresh == [threading.main_thread()] and took < A.KEYBOARD_WAIT_S, (fresh, took)
fresh.clear(); osd.run_on_main = lambda fn: threading.Thread(target=fn).start()
shortcut = threading.Thread(target=a.toggle); shortcut.start(); shortcut.join(); run(a.toggle)
wait_until(lambda: fresh, what="the keyboard read through the main thread")
assert len(fresh) == 1 and fresh[0] is not threading.main_thread(), fresh
osd.refresh_paste_key = real_refresh
FakeEngine.transcribe = plain; osd.keyboard_language, osd.run_on_main = real_keyboard
a.cfg["engine"].update(follow_keyboard=False, languages=[])
# history keeps audio only for the newest 3
for _ in range(5): a.remember("x", np.zeros(10, np.float32))
assert sum(e["audio"] is not None for e in a.history) == 3 and len(a.history) <= 10
# every dictation is in the history on disk (Settings → History); the menu has no Recent or Paste again
assert a.store.list("hello there")["total"] >= 1 and a.store.count() >= 6
labels = [str(i.text) for i in a.menu().items]
assert not any("Paste again" in l or l == "Recent" for l in labels), labels
# Recent comes back after a restart (from the history, without recordings)
b = A.App(); assert [e["text"] for e in b.history][-1] == a.history[-1]["text"] and all(e["audio"] is None for e in b.history)
# with Keep recordings on, a dictation can be transcribed again after a restart (from disk)
a.cfg["history"]["keep_audio"] = True; a.remember("kept on disk", np.full(8000, 0.1, np.float32))
c = A.App(); kept = c.history[-1]["id"]
assert c.history[-1]["audio"] is None and len(c.recording_entry(kept)["audio"]) == 8000
c.engine = FakeEngine(); c.notify = lambda m: None; calls.clear()
c._transcribe_again(c.recording_entry(kept))                     # the worker's job, run here
assert ("copy", "hello there") in calls and c.store.get(kept)["text"] == "hello there"
c.server.stop(); c.reader.close(); a.cfg["history"]["keep_audio"] = False
# deleting from the history takes it out of Recent too; history off keeps nothing new
gone = a.history[-1]["id"]; assert a.store.delete(gone); a.forget(gone)
assert all(e.get("id") != gone for e in a.history)
a.cfg["history"]["keep"] = False; n = a.store.count(); a.remember("secret"); assert a.store.count() == n
# a dictation finishing late is saved with its own app, not the one in front now
osd.app_name = lambda pid: {1: "TextEdit", 2: "Notes"}.get(pid)
a.cfg["history"]["keep"] = True; a.target_app = 2; a.remember("for textedit", app=1)
assert a.store.list("for textedit")["entries"][0]["app"] == "TextEdit"
a.cfg["history"]["keep"] = True
b.server.stop(); b.reader.close()
# ---- live mode (fake typing) ----
typed = []
osd.type_text = lambda t: typed.append(t) or 1
osd.delete_back = lambda n: typed.append(-n)
osd.clipboard_save = lambda: []
osd.clipboard_restore = lambda *a: None
osd.text_before_cursor = lambda: None
osd.focused_app = lambda: 1
osd.restore_delay = lambda: 0
tone = np.concatenate([np.zeros(16000), np.sin(np.arange(16000) / 5) * 0.3, np.zeros(16000)]).astype(np.float32)  # quiet, "speech", quiet
FakeRec.snapshot = lambda self: tone if self.active else np.zeros(0, np.float32)
FakeRec.position = lambda self: len(tone) if self.active else 0
a.cfg["dictation"]["typing"] = "instant"
FakeEngine.rollback = lambda self, raw, n=5: ""
class Kb:  # stands in for the keyboard listener
    on_user_key = None
    def health(self): return True, ""
a.keyboard = Kb()

# a live dictation uses the keyboard's language too, and the history says so
a.cfg["engine"].update(follow_keyboard=True, languages=["English", "Chinese"]); real_keyboard = osd.keyboard_language, osd.run_on_main
osd.keyboard_language = lambda: "zh-Hans"; osd.run_on_main = lambda fn: fn()
stricts = []; FakeEngine.transcribe = lambda self, a, language=None, prefix="", strict=False, draft="": stricts.append(strict) or plain(self, a)
a.toggle(); live = a.live; assert live.fixed == "Chinese", live.fixed
run(a.toggle); assert a.store.recent(1)[0]["lang"] == "Chinese" and stricts and all(stricts), (a.store.recent(1), stricts)
FakeEngine.transcribe = plain

# the slow-speaker switches reach the live session: on by default (config.example.toml), and off when set off
for trim, by_speech in ((None, None), (False, False)):
    for key, v in (("trim_pauses", trim), ("pause_by_speech", by_speech)):
        if v is not None: a.cfg["dictation"][key] = v
    a.toggle(); live = a.live
    assert (live.trim_pauses, live.pause_by_speech) == (trim is not False, by_speech is not False), (trim, live.trim_pauses, live.pause_by_speech)
    run(a.toggle)
a.cfg["dictation"].update(trim_pauses=True, pause_by_speech=True)
osd.keyboard_language, osd.run_on_main = real_keyboard; a.cfg["engine"].update(follow_keyboard=False, languages=[])

# a short dictation is done before the stop sound has been sent: the pill still hears "done" last
real_sound = a._sound; a._sound = lambda kind: time.sleep(0.3) if kind == "stop" else None
calls.clear(); a.toggle(); run(a.toggle)
pill = [c[1] for c in calls if c[0] == "hud"]; assert pill[-1] == "done" and "transcribing" in pill, pill
a._sound = real_sound

# a live dictation whose piece ran to the token budget says so too
plain_live = FakeEngine.transcribe
FakeEngine.transcribe = lambda self, a, language=None, prefix="", strict=False, draft="": capped
told = []; real_notify = a.notify; a.notify = lambda m: told.append(m) or real_notify(m)
talking = (np.sin(np.arange(3 * 16000) / 5) * 0.3).astype(np.float32)  # no pause: every tick transcribes it again
real_snapshot = FakeRec.snapshot; FakeRec.snapshot = lambda self: talking if self.active else np.zeros(0, np.float32)
passes = []; FakeEngine.transcribe = lambda self, a, language=None, prefix="", strict=False, draft="": passes.append(1) or capped
a.notice = ("", 0.0); a.toggle(); wait_until(lambda: len(passes) >= 3, what="several live passes"); run(a.toggle)
assert "length limit" in a.notice[0] and sum("length limit" in m for m in told) == 1, (a.notice, told)
a.notify = real_notify; FakeRec.snapshot = real_snapshot; FakeEngine.transcribe = plain_live

# cancel: nothing typed after it, no "done" pill, kept in Recent once
calls.clear(); a.history.clear(); a.toggle(); wait_until(lambda: a.live and a.live.text.strip(), what="the live session's first words")
n_before = len(a.history)
a.cancel(); at_cancel = len(typed); nothing_more_for(0.8)
wait_until(lambda: a.pending == 0, what="the cancelled dictation to finish")
assert len(typed) == at_cancel, f"typed after cancel: {typed[at_cancel:]}"
assert ("hud", "done") not in calls and a.pending == 0 and len(a.history) == n_before + 1, (calls, a.pending)

# back-to-back: your keys reach every session still typing or correcting
a.keyboard.on_user_key = a.user_key
a.toggle(); wait_until(lambda: a.live and a.live.text.strip(), what="the first session's words"); a.toggle()   # stop the first (it finishes on the worker)
a.toggle(); second = a.live              # start the next one at once
wait_until(lambda: second in a.running and second.text.strip(), what="the second session's words")
assert second is not None and second in a.running
class Heard:
    def __init__(self): self.keys = []
    def user_typed(self, key=None): self.keys.append(key)
spy = Heard(); a.running.append(spy)
a.user_key("globe"); a.user_key(None); a.user_key("enter")
assert spy.keys == [None, "enter"], spy.keys  # Fn's globe event isn't typing; Backspace/arrows (None) are
a.running.remove(spy)
a.user_key("a")
assert second.rebase_at is not None or second.pending_keys == [] and second.edited  # it heard the key
a.toggle(); wait_until(lambda: a.pending == 0 and not a.running, what="both sessions to finish")
assert a.pending == 0 and not a.running

# the mic dies and there is no other input: the dictation stops cleanly
def broken_switch(self): self.active = False; raise RuntimeError("no input device")
FakeRec.health = lambda self: (False, "stalled")
FakeRec.switch = broken_switch
a.toggle(); wait_until(lambda: not a.recorder.active and a.pending == 0 and not a.running, what="the dead mic to stop it")
assert not a.recorder.active and a.pending == 0 and not a.running and "dictation stopped" in a.status_text(), a.status_text()

# the browser's parts reach the reader as given; Read again reads them again in the same voices
class SpyReader:
    active, last_parts, last_text = False, [], ""
    def read_parts(self, parts): self.got = parts; self.last_parts = parts; return bool(parts)
real, a.reader = a.reader, SpyReader()
parts = [("She smiled.", "en-gb-rose", "warm"), ("Come in.", "builtin:ryan", "")]
assert a.read_parts(parts) is None and a.reader.got == parts
a.reader.got = None; a.read_again(); assert a.reader.got == parts
assert a.read_text("Hi.", "en-gb-rose") is None and a.reader.got == [("Hi.", "en-gb-rose", None)]
assert a.read_parts([]) == "Nothing to read in the selection"
a.reader = real

# the longest recording: a sound warn_s before the end (none when off, or longer than the limit),
# then it stops by itself and the menu says why
heard, real_sound, keep = [], a._sound, dict(a.cfg["audio"])
a._sound = lambda kind: heard.append((kind, time.monotonic()))
FakeRec.health = lambda self: (True, "")  # a working mic, whatever the checks above left
for warn, want in ((1, ["start", "warn", "stop"]), (0, ["start", "stop"]), (5, ["start", "stop"])):
    a.cfg["audio"]["max_seconds"], a.cfg["audio"]["warn_s"] = 2, warn
    heard.clear(); t0 = time.monotonic(); a.toggle()
    wait_until(lambda: not a.recorder.active and "stop" in [k for k, _ in heard], what="the limit to stop it")
    nothing_more_for(0.3)  # no late or second sound after it
    assert [k for k, _ in heard] == want and not a.recorder.active, (warn, heard)
    assert "2-second recording limit" in a.status_text(), a.status_text()
    if "warn" in want:
        assert 0.9 < dict(heard)["warn"] - t0 < 1.3, dict(heard)["warn"] - t0   # limit − warn_s
a.cfg["audio"]["max_seconds"] = 600                       # a whole number of minutes is said in minutes
a.toggle(); a._time_up(a.session)
wait_until(lambda: not a.recorder.active and "10-minute" in a.status_text(), what="the limit notice")
assert "10-minute recording limit" in a.status_text() and not a.recorder.active, a.status_text()
a._sound = real_sound; a.cfg["audio"].clear(); a.cfg["audio"].update(keep)
# all of the above (settings, model scans, the status) without one model library in the app: they
# belong in the model workers, which give them back on unload
heavy = sorted(m for m in ("mlx", "mlx_audio", "torch", "scipy", "transformers") if m in sys.modules)
assert not heavy, f"the app loaded {heavy}"
print("app paths: ok"); print("status:", a.status_text())
a.server.stop()
