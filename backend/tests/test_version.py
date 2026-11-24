#!/usr/bin/env python3
"""The version (VERSION at the repo root): its first line is the live version, the changelog line
for it is the newest, versions only go up (down the changelog and past every tag this commit holds),
and a tag says what its commit says. Every commit adds 0.0.1, a `# <version> = …` line and a tag.

  python3 backend/tests/test_version.py
"""
import re, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import waudio

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse(text):
    """VERSION's text -> (live version, [changelog versions, newest first])."""
    lines = text.splitlines()
    log = [m.group(1) for l in lines[1:] if (m := re.match(r"^# (\S+) = \S", l))]
    return lines[0].strip() if lines else "", log


def key(v):
    m = SEMVER.match(v)
    assert m, f"not a version: {v!r}"
    return tuple(map(int, m.groups()))


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def version_at(rev):
    """What the commit `rev` says its version is: VERSION's first line, or (before VERSION existed)
    the VERSION = "…" line of backend/waudio/__init__.py."""
    try:
        return parse(git("show", f"{rev}:VERSION"))[0]
    except subprocess.CalledProcessError:
        return re.search(r'^VERSION = "(.*)"', git("show", f"{rev}:backend/waudio/__init__.py"), re.M).group(1)


def the_live_version_heads_the_changelog(text):
    live, log = parse(text)
    key(live)
    assert log, "no `# <version> = …` line in VERSION"
    assert log[0] == live, f"VERSION says {live}, its newest changelog line {log[0]}"


def versions_only_go_up(text, tags):
    live, log = parse(text)
    assert log
    keys = [key(v) for v in log]
    assert all(a > b for a, b in zip(keys, keys[1:])), f"changelog not newest first, one line each: {log}"
    assert tags, "no v<version> tag in this commit's history"
    for tag, v in tags:
        assert key(live) >= key(v), f"VERSION says {live}, below tag {tag}"


def checks(text, tags):
    the_live_version_heads_the_changelog(text)
    versions_only_go_up(text, tags)


def the_checks_catch_a_broken_version():
    """Each way to break the rule is caught (the checks above can fail)."""
    good = "0.0.3\n# 0.0.3 = b\n# 0.0.2 = a\n"
    checks(good, [("v0.0.2", "0.0.2")])
    cases = [("0.0.4\n# 0.0.3 = b\n", [("v0.0.2", "0.0.2")]),                   # bumped, no changelog line
             ("0.0.3\n# 0.0.2 = a\n# 0.0.3 = b\n", [("v0.0.2", "0.0.2")]),      # changelog out of order
             ("0.0.3\n# 0.0.3 = b\n# 0.0.3 = a\n", [("v0.0.2", "0.0.2")]),      # one version twice
             (good, [("v0.1.0", "0.1.0")]),                                      # below a tag: went down
             ("v0.0.3\n# v0.0.3 = b\n", [("v0.0.2", "0.0.2")]),                  # not a version
             ("0.0.3\n", [("v0.0.2", "0.0.2")])]                                 # no changelog at all
    assert cases
    for text, tags in cases:
        try:
            checks(text, tags)
        except AssertionError:
            continue
        raise AssertionError(f"not caught: {text!r} {tags}")


def this_repo_follows_it():
    text = (ROOT / "VERSION").read_text()
    tags = [(t, version_at(t)) for t in git("tag", "--merged", "HEAD", "--list", "v*").split()]
    checks(text, tags)
    assert waudio.VERSION == waudio.__version__ == parse(text)[0]       # what the app reports
    assert tags and all(t == f"v{v}" for t, v in tags), f"a tag says another version than its commit: {tags}"


the_checks_catch_a_broken_version()
this_repo_follows_it()
print("version checks: ok")
