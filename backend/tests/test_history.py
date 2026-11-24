#!/usr/bin/env python3
"""Dictation history on disk, in a temporary folder: add, newest first, search, delete, clear,
keep off, keep_days pruning, file readable by you only, a broken line skipped, update after
"transcribe again".

  python3 backend/tests/test_history.py
"""
import json, os, stat, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.history import History

d = Path(tempfile.mkdtemp())
cfg = {"keep": True, "keep_days": 0}
h = History(d, cfg)
assert h.count() == 0 and h.list()["entries"] == []

a = h.add("Hello there", "English", "TextEdit", 2.04)
b = h.add("你好，今天天气很好", "Chinese", "Notes", 3)
c = h.add("Buy milk and eggs", None, None, None)
assert h.add("   ") is None and h.count() == 3
assert stat.S_IMODE(os.stat(h.path).st_mode) == 0o600              # yours only

r = h.list()
assert [e["text"] for e in r["entries"]] == ["Buy milk and eggs", "你好，今天天气很好", "Hello there"]  # newest first
assert r["entries"][2]["app"] == "TextEdit" and r["entries"][2]["secs"] == 2.0 and r["total"] == 3
assert [e["text"] for e in h.list("milk EGGS")["entries"]] == ["Buy milk and eggs"]   # all words, any case
assert h.list("天气")["total"] == 1 and h.list("nothing")["total"] == 0
assert [e["text"] for e in h.list(offset=1, limit=1)["entries"]] == ["你好，今天天气很好"]
assert [e["text"] for e in h.recent(2)] == ["你好，今天天气很好", "Buy milk and eggs"]  # oldest first, for the recent list

v = h.version
assert h.update(a["id"], "Hello there, friend") and h.list("friend")["total"] == 1 and h.version > v
assert h.delete(b["id"]) and not h.delete(b["id"]) and h.count() == 2
assert stat.S_IMODE(os.stat(h.path).st_mode) == 0o600              # still yours only after a rewrite

with open(h.path, "a") as f:                                          # a half-written line from a crash
    f.write('{"id": "broken", "text": "unfinished\n')
h2 = History(d, cfg)
assert h2.count() == 2 and h2.add("after the crash") and h2.count() == 3

cfg["keep"] = False
assert h2.add("not kept") is None and h2.count() == 3

old = json.loads(h2.path.read_text().splitlines()[0])
old["t"] = time.time() - 40 * 86400; old["id"] = "old"; old["text"] = "from last month"
with open(h2.path, "a") as f:
    f.write(json.dumps(old) + "\n")
h3 = History(d, cfg)
assert h3.prune() == 0                                                # keep_days 0: forever
cfg["keep_days"] = 30
assert h3.prune() == 1 and h3.list("last month")["total"] == 0

assert h3.clear() == 3 and h3.count() == 0 and h3.list()["entries"] == []

# a crash left half a line without a newline: the next dictation is still kept, the count right
cfg["keep"] = True
with open(h3.path, "a") as f:
    f.write('{"id": "half", "text": "cut o')
h4 = History(d, cfg)
assert h4.add("kept after a broken tail") and h4.list("broken tail")["total"] == 1
assert h4.count() == 1 and History(d, cfg).count() == 1
assert h4.get(h4.list()["entries"][0]["id"])["text"] == "kept after a broken tail" and h4.get("nope") is None
fresh = History(tempfile.mkdtemp(), cfg); fresh.add("x")
assert stat.S_IMODE(os.stat(fresh.path).st_mode) == 0o600
assert History(tempfile.mkdtemp()).count() == 0                       # no file yet

print("history checks: ok")
