#!/usr/bin/env python3
"""Accessibility that is ticked but dead, or switched off mid-run. Typing posts key events, which macOS
drops without a word when Accessibility doesn't work — so the app asks the platform for one real
Accessibility read (platforms.ax_works) and never types blind: a dictation doesn't start, a finished
one is copied instead of pasted (and is in History), a live one ends where it is, waking says so, and
Run checks shows it even when System Settings has it ticked. A platform that can't tell (None) types
as before.

  python3 backend/tests/test_accessibility.py
"""
import shutil, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from waits import nothing_more_for, wait_until
from waudio import checks, config
tmp = Path(tempfile.mkdtemp()) / "config.toml"; shutil.copy(config.EXAMPLE, tmp); config.PATH = tmp
from waudio import app as A
osd = A.osd
osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)
osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)
calls = []
osd.play = lambda *a: None
osd.hud = lambda s, d="": None
osd.paste = lambda t: calls.append(("paste", t))
osd.copy_text = lambda t: calls.append(("copy", t))
osd.type_text = lambda t: calls.append(("type", t))
osd.delete_back = lambda n: None
osd.clipboard_save = lambda: []
osd.clipboard_restore = lambda *a: None
osd.restore_delay = lambda: 0
osd.text_before_cursor = lambda: None
osd.output_muted = lambda m=None: False
osd.output_playing = lambda: False
osd.focus_target = lambda: (1, None)
osd.focused_app = lambda: 1
osd.focused_field = lambda: "text"
osd.screen_locked = lambda: False
ax = {"works": True}
osd.ax_works = lambda: ax["works"]

tone = np.concatenate([np.zeros(16000), np.sin(np.arange(16000) / 5) * 0.3, np.zeros(16000)]).astype(np.float32)


class FakeRec:
    active = False
    def start(self): self.active = True
    def stop(self): self.active = False; return tone
    def health(self): return True, ""
    def position(self): return len(tone) if self.active else 0
    device = "Test Mic"
    def take_level(self): return 0.0
    def seconds(self): return 0.0
    def snapshot(self): return tone if self.active else np.zeros(0, np.float32)


class R:
    capped = False  # ran to the token budget
    def __init__(s, t): s.text, s.language, s.audio_s, s.secs, s.cut, s.raw = t, "English", 1.5, 0.1, False, t


class FakeEngine:
    loaded = True; loaded_model = "x"
    def transcribe(self, a, language=None, prefix="", strict=False, draft=""): return R("hello there")
    def rollback(self, raw, n=5): return ""
    def load(self): pass


class Kb:
    on_user_key = None
    def health(self): return True, ""


a = A.App(); a.recorder = FakeRec(); a.engine = FakeEngine(); a.keyboard = Kb()
a.start_keyboard = lambda: None  # waking makes a new key listener: not in a test
a.cfg["engine"]["follow_keyboard"] = False
a.cfg["audio"]["vad"] = False
notes = []
a.notify = notes.append
threading.Thread(target=a.worker, daemon=True).start()


def finished():
    wait_until(lambda: a.pending == 0 and not a.running and not a.recorder.active, what="the dictation to finish")


def said_accessibility():
    return any("Accessibility" in n for n in notes)


def a_dictation_does_not_start_when_accessibility_is_dead():
    for typing in ("end", "instant"):
        a.cfg["dictation"]["typing"] = typing
        ax["works"] = False; notes.clear(); calls.clear()
        a.toggle(); nothing_more_for(0.3)
        assert not a.recorder.active and said_accessibility() and not calls, (typing, notes, calls)
        ax["works"] = True


def a_finished_dictation_is_copied_not_pasted_when_accessibility_died():
    a.cfg["dictation"]["typing"] = "end"
    rows = [(True, "paste"), (None, "paste"), (False, "copy")]  # works / can't tell / died before the paste
    assert rows
    for works, how in rows:
        ax["works"] = True; notes.clear(); calls.clear(); a.history.clear()
        a.toggle(); ax["works"] = works; a.toggle(); finished()
        assert [c[0] for c in calls] == [how], (works, calls)
        assert a.history and a.history[-1]["text"] == "hello there"
        assert said_accessibility() == (works is False), (works, notes)
    ax["works"] = True


def a_live_dictation_ends_where_it_is_when_accessibility_dies():
    a.cfg["dictation"]["typing"] = "instant"
    ax["works"] = True; notes.clear(); calls.clear(); a.history.clear()
    a.toggle()
    wait_until(lambda: ("type", "hello there ") in calls, what="the first words typed")
    ax["works"] = False
    wait_until(lambda: not a.recorder.active, timeout=3, what="the live dictation to end")
    finished(); typed = len(calls); nothing_more_for(0.5)
    assert len(calls) == typed and said_accessibility(), (calls, notes)
    assert a.history and "hello there" in a.history[-1]["text"], list(a.history)
    ax["works"] = True


def a_stale_end_leaves_a_newer_dictation_alone():
    """The live watcher's check can be half a second old: ending the dictation it saw never ends a newer one."""
    a.cfg["dictation"]["typing"] = "instant"
    ax["works"] = True
    a.toggle(); old = a.session; a.cancel(); finished()
    a.toggle(); assert a.session != old
    assert a.cancel(keep_audio=True, session=old) is False and a.recorder.active
    assert a.cancel(session=a.session) is True and not a.recorder.active
    finished()


def waking_says_when_accessibility_died():
    for works, told in ((False, True), (True, False), (None, False)):
        ax["works"] = works; notes.clear()
        a.on_wake()
        assert said_accessibility() == told, (works, notes)
    ax["works"] = True


def run_checks_shows_accessibility_ticked_but_dead():
    real = osd.permissions
    osd.permissions = lambda: {"accessibility": True, "microphone": True}
    try:
        for works, level in ((True, checks.GREEN), (None, checks.GREEN), (False, checks.RED)):
            ax["works"] = works
            row = next(r for r in checks.check_permissions(a) if r["id"] == "permission:accessibility")
            assert row["level"] == level, (works, row)
    finally:
        osd.permissions = real; ax["works"] = True


def accessibility_not_allowed_asks_for_the_permission_as_before():
    """Not allowed at all (not ticked): Run checks asks for the permission itself — the "ticked but dead"
    advice is only for an Accessibility that is allowed."""
    real = osd.permissions
    osd.permissions = lambda: {"accessibility": False, "microphone": True}
    try:
        for works in (False, None, True):
            ax["works"] = works
            row = next(r for r in checks.check_permissions(a) if r["id"] == "permission:accessibility")
            assert row["level"] == checks.RED and row["text"] == "Not allowed" and row["fix"]["do"] == "permission", (works, row)
    finally:
        osd.permissions = real; ax["works"] = True


a_dictation_does_not_start_when_accessibility_is_dead()
a_finished_dictation_is_copied_not_pasted_when_accessibility_died()
a_live_dictation_ends_where_it_is_when_accessibility_dies()
a_stale_end_leaves_a_newer_dictation_alone()
waking_says_when_accessibility_died()
run_checks_shows_accessibility_ticked_but_dead()
accessibility_not_allowed_asks_for_the_permission_as_before()
a.server.stop(); a.reader.close()
print("accessibility: ok")
