#!/usr/bin/env python3
"""Read aloud in the browser: ■ stops at once, so a voice, mood or speed changed while the app is still being
told to stop reads nothing again; a reading another tab (or ⌥R) started right after this page's is let go
(browser/tools/reader-test.mjs --only=stop; see test_reader_follow.py).

  python3 backend/tests/test_reader_stop.py
"""
# needs: node, chrome
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reader_follow import reads_aloud

reads_aloud("stop")
print("reader stop checks: ok")
