#!/usr/bin/env python3
"""Read aloud in the browser: a voice, persona, mood or speed changed mid-reading is heard at once — read again
from the block playing; while paused, on ▶; two changes quickly; changed back; gone on with ⌥R
(browser/tools/reader-test.mjs --only=change; see test_reader_follow.py).

  python3 backend/tests/test_reader_change.py
"""
# needs: node, chrome
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reader_follow import reads_aloud

reads_aloud("change")
print("reader change checks: ok")
