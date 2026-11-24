#!/usr/bin/env python3
"""Settings → Speech in a headless Chrome (needs Google Chrome and node; frontend/tools/speech-cards.mjs), on a
temporary config and voices folder: the voice cards with their tags (sex, race, accent, age, D·), voices without
tags apart, the age filter, choosing a card, setting an age on a card, the Persona menu's age guard, and a
mood's ages kept by Save moods.

  python3 backend/tests/test_speech_cards.py
"""
# needs: node, chrome
import json, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from waudio import config

tmp = Path(tempfile.mkdtemp()) / "config.toml"
shutil.copy(config.EXAMPLE, tmp)
config.PATH = tmp
from waudio import app as A  # noqa: E402  (after PATH is redirected)
A.osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)
A.osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)

vd = Path(tempfile.mkdtemp())
for n in ("ana", "bea", "high-elf[Cal]", "mine-20260101-120000"):
    (vd / f"{n}.wav").write_bytes(b"RIFF"); (vd / f"{n}.txt").write_text("words")
(vd / "voices.json").write_text(json.dumps({"ana": "[F][British][Ana][Shy]", "bea": "D·[F][Scottish][Bea]", "high-elf[Cal]": "[M][High Elf][Cal]"}))
(vd / "ana.age.txt").write_text("Young")
(vd / "personas.json").write_text(json.dumps({"Any": "Calm.", "Lover": {"text": "Close.", "ages": ["Adult", "Old"]}}))  # a leftover "ages" is ignored
(vd / "moods.json").write_text(json.dumps({"Lively": {"text": "Bright."}, "Grave": {"text": "Deep.", "ages": ["Adult", "Old"]}}))
md = Path(tempfile.mkdtemp()); (md / "config.json").write_text('{"model_type": "breeze_tts"}')  # a persona model, never loaded

a = A.App()
a.cfg["read"].update(voices_dir=str(vd), voice="bea", model=str(md))
p = subprocess.run(["node", str(ROOT / "frontend" / "tools" / "speech-cards.mjs"), a.server.url("speech")],
                   capture_output=True, text=True, timeout=60)
assert p.returncode == 0, p.stderr
seen = json.loads(p.stdout)

# tagged voices as cards in voices.json's order, each with its tags; the rest apart
assert seen["cards"] == ["ana", "bea", "high-elf[Cal]"], seen["cards"]
assert seen["tags"]["ana"] == ["Female", "Human", "British", "Young"], seen["tags"]["ana"]
assert seen["tags"]["bea"] == ["D·", "Female", "Human", "Scottish", "Adult"], seen["tags"]["bea"]
assert seen["tags"]["high-elf[Cal]"] == ["Male", "High Elf", "Adult"], seen["tags"]["high-elf[Cal]"]
assert seen["other"] == ["mine-20260101-120000"], seen["other"]
assert "Young1" in seen["chips"] and "High Elf1" in seen["chips"] and "Female2" in seen["chips"], seen["chips"]
# the Young filter: Young voices and the chosen one (Bea) only; off again, all of them
assert seen["young"] == ["ana", "bea"] and seen["unfiltered"] == seen["cards"], seen
assert seen["chosenAfterKey"] == "bea", seen["chosenAfterKey"]  # Enter on a card's age switch is the switch's
# a click chooses the card (saved), an age switch doesn't; the age is written beside the voice and shown
assert seen["chosen"] == "ana" and a.cfg["read"]["voice"] == "ana", seen["chosen"]
assert (vd / "bea.age.txt").read_text() == "Old\n" and "Old" in seen["beaTags"] and seen["chosenAfterAge"] == "ana", seen
# with Ana (Young) chosen every persona can be picked, none shows ages; the adults-only mood can't be picked, the other can
assert {k: v for k, v in seen["menu"] if not k.startswith("\x00")} == {"Any": False, "Lover": False}, seen["menu"]  # not Custom
assert seen["personaAges"] == [0, 0], seen["personaAges"]
assert dict(seen["moodMenu"]) == {"Lively": False, "Grave": True}, seen["moodMenu"]
# a mood's ages: Young off says "Adults only", Save moods keeps it, the last age stays on
assert "Adults only" in seen["guard"] and "Adults only" in seen["guardAfterSave"], seen
assert json.loads((vd / "moods.json").read_text())["Lively"] == {"text": "Bright.", "speed": 1.0, "ages": ["Adult", "Old"]}
assert seen["graveOn"] == ["Old"], seen["graveOn"]
a.server.stop()
print("speech cards checks: ok")
