#!/usr/bin/env python3
"""The extension tests' throwaway Chrome (browser/tools/chrome.mjs) leaves no profile behind when it closes:
not when one of Chrome's helpers outlives it for a moment and still writes into the profile (close() waits
for it), nor when a helper never ends (close() stops it). A stand-in Chrome (a shell script, as $CHROME)
plays both, so no real Chrome is needed.

  python3 backend/tests/test_chrome_close.py
"""
# needs: node
import os, re, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from waits import nothing_more_for

ROOT = Path(__file__).resolve().parents[2]
CHROME_MJS = ROOT / "browser" / "tools" / "chrome.mjs"
# quits when told to; its helper (its own process, the profile in its command line, as Chrome's helpers have)
# writes into the profile after HELPER_WRITES_AFTER seconds
FAKE = """#!/bin/bash
for a in "$@"; do case "$a" in --user-data-dir=*) d="${a#--user-data-dir=}";; esac; done
perl -e 'select(undef, undef, undef, $ARGV[1]); mkdir $ARGV[0]; mkdir "$ARGV[0]/late"' "$d" "$HELPER_WRITES_AFTER" &
touch "$d/up"
exec sleep 60
"""
CLOSE = (f"import {{ launch }} from {str(CHROME_MJS)!r}; import {{ existsSync }} from 'node:fs'; const c = await launch();"
         " if (!await c.until(async () => existsSync(c.profile + '/up'), 5000, 10)) throw new Error('the stand-in never came up');"
         " await c.close(); console.log(c.profile);")


def closed_with_helper_writing_after(seconds, tmp):
    """Launch and close the stand-in Chrome whose helper writes after `seconds`: the profile's path."""
    fake = Path(tmp) / "chrome"
    fake.write_text(FAKE)
    fake.chmod(0o700)
    p = subprocess.run(["node", "--input-type=module", "-e", CLOSE], capture_output=True, text=True, timeout=20,
                       env={**os.environ, "CHROME": str(fake), "HELPER_WRITES_AFTER": str(seconds)})
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout.strip()


def left_behind(profile):
    """Whether the profile is still there; a left one goes to the Trash (never deleted outright) after a look."""
    if not Path(profile).exists():
        return None
    what = sorted(os.listdir(profile))
    subprocess.run(["trash", profile])
    return what


def helpers_of(profile):
    return subprocess.run(["pgrep", "-f", re.escape(profile)], capture_output=True, text=True).stdout.split()


def a_helper_writing_just_after_chrome_quits_leaves_no_profile(tmp):
    profile = closed_with_helper_writing_after(1.0, tmp)  # after the profile went to the Trash, as close() was
    nothing_more_for(1.5)  # past the helper's write
    assert profile and (left := left_behind(profile)) is None, f"left behind: {profile}: {left}"


def a_helper_that_never_ends_is_stopped_and_leaves_no_profile(tmp):
    profile = closed_with_helper_writing_after(30, tmp)
    assert profile and (left := left_behind(profile)) is None, f"left behind: {profile}: {left}"
    assert not helpers_of(profile), f"still running: {helpers_of(profile)}"


tmp = tempfile.mkdtemp()  # the stand-in Chrome's folder: to the Trash afterwards, never deleted outright
try:
    a_helper_writing_just_after_chrome_quits_leaves_no_profile(tmp)
    a_helper_that_never_ends_is_stopped_and_leaves_no_profile(tmp)
finally:
    subprocess.run(["trash", tmp])
print("chrome close checks: ok")
