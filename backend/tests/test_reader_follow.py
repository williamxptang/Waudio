#!/usr/bin/env python3
"""Read aloud in the browser while it reads (browser/tools/reader-test.mjs, against a stand-in for the app, in a
throwaway headless Chrome): following the reading (Highlight / Scroll with the reading: off by default, the block
playing marked and kept in view, gone on ■). It also holds the run the other reader files share; a choice changed
mid-reading is test_reader_change.py, the rest test_reader_page.py. Each run within the fast set's 30 s.

  python3 backend/tests/test_reader_follow.py
"""
# needs: node, chrome
import re, subprocess, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]


def profiles():
    """The throwaway Chrome profiles in the temp folder (chrome.mjs: launch)."""
    prefix = re.search(r"PROFILE_PREFIX = '([^']+)'", (ROOT / "browser" / "tools" / "chrome.mjs").read_text()).group(1)
    return {p.name for p in Path(tempfile.gettempdir()).glob(prefix + "*")}


def reads_aloud(group):
    """reader-test.mjs --only=group: page (test_reader_page.py), follow (this file), change (test_reader_change.py)."""
    before = profiles()
    p = subprocess.run(["node", str(ROOT / "browser" / "tools" / "reader-test.mjs"), f"--only={group}"],
                       capture_output=True, text=True, timeout=28)
    assert p.returncode == 0 and "reader checks: ok" in p.stdout, p.stdout[-3000:] + p.stderr[-2000:]
    assert profiles() <= before, profiles() - before  # its throwaway profile went to the Trash


if __name__ == "__main__":
    reads_aloud("follow")
    print("reader follow checks: ok")
