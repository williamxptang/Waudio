#!/usr/bin/env python3
"""Kept recordings in the dictation history (keep_audio), each check in its own temporary folder:
kept with its dictation and read back as the model hears it, not kept when switched off, removed
with its entry (delete, clear, keep_days), and "What I said" saved beside the text.

  python3 backend/tests/test_history_audio.py
"""
import json, os, stat, sys, tempfile, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.history import History

CHECKS = []
def check(fn):
    CHECKS.append(fn)
    return fn

def store(**cfg):
    return History(tempfile.mkdtemp(), {"keep": True, "keep_days": 0, "keep_audio": True, **cfg})

def voice(seconds=1.5):
    return (np.sin(np.arange(int(seconds * 16000)) / 5) * 0.3).astype(np.float32)


@check
def a_recording_is_kept_with_its_dictation():
    h = store(); audio = voice()
    e = h.add("hello there", "English", None, 1.5, audio)
    back = h.recording(e["id"])
    assert back is not None and len(back) == len(audio)
    assert np.abs(back - audio).max() <= 1 / 32767 + 1e-6            # 16-bit: within one step
    assert h.list()["entries"][0]["audio"] is True
    assert h.audio_bytes(e["id"])[:4] == b"RIFF"                       # a real .wav file
    assert stat.S_IMODE(os.stat(h.audio_dir / f"{e['id']}.wav").st_mode) == 0o600  # yours only
    assert stat.S_IMODE(os.stat(h.audio_dir).st_mode) == 0o700


@check
def no_recording_is_kept_when_switched_off():
    cases = [{"keep_audio": False}, {"keep": False}]                  # recordings off; history off
    assert cases
    for cfg in cases:
        h = store(**cfg)
        e = h.add("hello there", None, None, 1.5, voice())
        assert not h.audio_dir.exists() or not any(h.audio_dir.iterdir()), cfg
        if e:
            assert h.recording(e["id"]) is None and h.list()["entries"][0]["audio"] is False, cfg


@check
def deleting_a_dictation_removes_only_its_recording():
    h = store()
    a = h.add("first", None, None, 1.5, voice()); b = h.add("second", None, None, 1.5, voice())
    assert h.delete(a["id"])
    assert h.recording(a["id"]) is None and h.recording(b["id"]) is not None


@check
def clearing_the_history_removes_every_recording():
    h = store()
    h.add("first", None, None, 1.5, voice()); h.add("second", None, None, 1.5, voice())
    assert len(list(h.audio_dir.glob("*.wav"))) == 2
    h.clear()
    assert list(h.audio_dir.glob("*.wav")) == []


@check
def an_entry_past_keep_days_takes_its_recording_with_it():
    h = store(keep_days=30)
    old = h.add("from last month", None, None, 1.5, voice()); new = h.add("today", None, None, 1.5, voice())
    lines = [json.loads(l) for l in h.path.read_text().splitlines()]
    lines[0]["t"] = time.time() - 40 * 86400
    h.path.write_text("".join(json.dumps(l) + "\n" for l in lines))
    assert h.prune() == 1
    assert h.recording(old["id"]) is None and h.recording(new["id"]) is not None


@check
def only_an_entry_id_names_a_recording():
    h = store(); h.add("hello there", None, None, 1.5, voice())
    outside = h.path.parent / "secret.wav"; outside.write_bytes(b"RIFF outside")
    cases = ["../secret", str(outside.with_suffix("")), ""]
    assert cases
    for bad in cases:
        assert h.audio_bytes(bad) is None and h.recording(bad) is None and not h.has_audio(bad), bad


@check
def clearing_removes_a_half_written_recording():
    h = store(); h.add("first", None, None, 1.5, voice())
    (h.audio_dir / "0123456789ab.tmp").write_bytes(b"cut off by a crash")
    h.clear()
    assert list(h.audio_dir.iterdir()) == []


@check
def a_dictation_that_fails_to_save_leaves_no_recording():
    h = store(); h.path.mkdir()                                       # history.jsonl can't be written
    try:
        h.add("hello there", None, None, 1.5, voice())
    except OSError:
        pass
    assert list(h.audio_dir.glob("*")) == []


@check
def what_i_said_is_kept_beside_the_text():
    h = store(); e = h.add("hello the air", None, None, 1.5, voice())
    assert h.said(e["id"], "  hello there  ") and h.get(e["id"])["said"] == "hello there"
    assert h.update(e["id"], "hello there") and h.get(e["id"])["said"] == "hello there"  # transcribe again keeps it
    assert h.said(e["id"], "") and "said" not in h.get(e["id"])
    assert not h.said("nope", "x")


assert CHECKS
for fn in CHECKS:
    fn()
print(f"history audio checks: ok ({len(CHECKS)})")
