#!/usr/bin/env python3
"""Shortcut format and matching with simulated key events (no keyboard, no pynput needed).

  python3 backend/tests/test_shortcut.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.input.shortcut import Matcher, ShortcutError, check, describe, normalize


def run(spec, events, ms=400):
    """events: (t, 'p'|'r', key). Returns (downs, ups)."""
    downs, ups = [], []
    m = Matcher(spec, lambda: downs.append(1), lambda: ups.append(1), ms)
    for t, kind, key in events:
        (m.press if kind == "p" else m.release)(key, t)
    return len(downs), len(ups)


def tap(key, t, hold=0.08):
    return [(t, "p", key), (t + hold, "r", key)]


def main():
    # combos
    alt_d = [(0, "p", "alt"), (0.1, "p", "d"), (0.2, "r", "d"), (0.3, "r", "alt")]
    assert run("alt+d", alt_d) == (1, 1)
    assert run("alt+d", [(0, "p", "d"), (0.1, "r", "d")]) == (0, 0)                      # no modifier
    assert run("alt+d", [(0, "p", "alt"), (0, "p", "cmd"), (0.1, "p", "d")]) == (0, 0)   # extra modifier
    assert run("alt+d", [(0, "p", "alt_r"), (0.1, "p", "d")]) == (1, 0)                  # right Option counts
    held = [(0, "p", "alt")] + [(0.1 + i * 0.03, "p", "d") for i in range(20)] + [(0.8, "r", "d")]
    assert run("alt+d", held) == (1, 1)                                                  # auto-repeat ignored
    assert run("shift+cmd+space", [(0, "p", "cmd"), (0, "p", "shift_r"), (0.1, "p", "space")]) == (1, 0)
    assert run("f13", tap("f13", 0)) == (1, 1)
    assert run("f13", [(0, "p", "cmd")] + tap("f13", 0.1)) == (0, 0)                      # lone key means alone
    m = Matcher("alt+d", lambda: None)
    m.press("alt", 0)
    assert m.press("d", 0.1) is True and m.release("d", 0.2) is True                     # both swallowed
    assert m.press("x", 0.3) is False

    # caps lock: macOS sends press (turning on) and release (turning off), one per physical press
    assert run("caps_lock", [(0, "p", "caps_lock"), (2, "r", "caps_lock")]) == (2, 0)
    assert run("caps_lock", [(0, "p", "caps_lock"), (0.05, "r", "caps_lock"), (3, "p", "caps_lock")]) == (2, 0)

    # double tap
    assert run("double:alt", tap("alt", 0) + tap("alt", 0.25)) == (1, 0)
    assert run("double:alt", tap("alt", 0) + tap("alt", 0.6)) == (0, 0)                  # too slow
    assert run("double:alt", tap("alt", 0) + [(0.1, "p", "x")] + tap("alt", 0.2)) == (0, 0)
    assert run("double:alt", tap("alt", 0, hold=0.5) + tap("alt", 0.6)) == (0, 0)        # first was a hold
    assert run("double:alt", [(0, "p", "alt"), (0.05, "p", "c"), (0.1, "r", "c"), (0.15, "r", "alt")]
               + tap("alt", 0.3)) == (0, 0)                                              # ⌥C is not a tap
    assert run("double:alt", tap("alt", 0) + tap("alt", 0.2) + tap("alt", 0.4)) == (1, 0)
    assert run("double:alt", tap("alt", 0) + tap("alt", 0.2) + tap("alt", 1) + tap("alt", 1.2)) == (2, 0)
    assert run("double:alt_r", tap("alt", 0) + tap("alt", 0.2)) == (0, 0)                # left ≠ right
    assert run("double:alt_r", tap("alt_r", 0) + tap("alt_r", 0.2)) == (1, 0)

    # format, display, validation
    assert normalize("cmd+shift+space") == "shift+cmd+space"
    assert describe("alt+d") == "⌥D" and describe("alt+d", mac=False) == "Alt+D"
    assert describe("shift+cmd+space") == "⇧⌘Space"
    assert describe("caps_lock") == "Caps Lock"
    assert describe("double:alt") == "Option ×2" and describe("double:alt_r") == "Right Option ×2"
    # Fn / 🌐: only as a double tap; Fn held (or a lost Fn release) never blocks other shortcuts
    assert describe("double:fn") == "Fn ×2" and check("double:fn")[0] == "double:fn"
    assert run("double:fn", tap("fn", 0) + tap("fn", 0.2)) == (1, 0) and run("double:fn", tap("fn", 0) + tap("fn", 0.9)) == (0, 0)
    # double tap to start, single tap to stop (while it is running)
    running = {"v": False}; hits = []
    m = Matcher("double:fn", lambda: (hits.append(1), running.update(v=not running["v"])), single_when=lambda: running["v"])
    for key, t in (("fn", 0), ("fn", 0.2)):        # double tap: start
        m.press(key, t); m.release(key, t + 0.08)
    assert hits == [1] and running["v"]
    m.press("fn", 2); m.release("fn", 2.08)        # one tap: stop
    assert hits == [1, 1] and not running["v"]
    m.press("fn", 4); m.release("fn", 4.08)        # one tap while nothing runs: nothing
    assert hits == [1, 1]
    for t in (6, 6.2):                              # start again, then a habitual double tap to stop:
        m.press("fn", t); m.release("fn", t + 0.08)
    assert running["v"]
    running_check = []
    m.single_when = lambda: (running_check.append(1), running["v"])[1]
    for t in (8, 8.2):
        m.press("fn", t); m.release("fn", t + 0.08)
    assert not running["v"] and hits == [1, 1, 1, 1], hits  # stopped once, not restarted
    # what a real Mac sends: after each Fn release, the 🌐 key's own press + release (key code 179)
    assert run("double:fn", tap("fn", 0) + tap("globe", 0.07) + tap("fn", 0.19) + tap("globe", 0.22)) == (1, 0)
    hits = []
    m = Matcher("alt+d", lambda: hits.append("d"))
    m.press("fn", 0); m.press("alt", 0.1); m.press("d", 0.2); m.release("d", 0.3); m.release("alt", 0.3)  # Fn held, never released
    m.press("alt", 1); m.press("d", 1.1); m.release("d", 1.2); m.release("alt", 1.2)
    assert hits == ["d", "d"], hits
    n = Matcher("double:alt_r", lambda: hits.append("r"))
    n.press("fn", 0)  # lost release
    for t in (1, 1.2):
        n.press("alt_r", t); n.release("alt_r", t + 0.05)
    assert hits[-1] == "r", hits
    f = Matcher("double:fn", lambda: hits.append("fn"))
    f.press("alt", 0); f.press("fn", 0.1); f.release("fn", 0.15); f.press("fn", 0.3); f.release("fn", 0.35)
    assert hits[-1] != "fn"  # ⌥ held: not a clean Fn double tap
    for bad in ("fn+d", "fn+cmd+space"):
        try:
            check(bad); raise AssertionError(bad)
        except ShortcutError:
            pass
    try:
        check("double:fn", "hold"); raise AssertionError("hold with Fn ×2")
    except ShortcutError:
        pass
    assert describe("ctrl+alt+bracket_left") == "⌃⌥["
    assert check("caps_lock")[1] == ["Also used for Claude desktop's voice shortcut"]
    assert check("cmd+shift+4")[1] == ["Also used for screenshots"]
    assert check("alt+d")[1] == []
    assert check("f13", "hold")[0] == "f13"
    for bad, mode in (("d", "toggle"), ("alt+dd", "toggle"), ("double:x", "toggle"), ("alt", "toggle"),
                      ("shift+a", "toggle"), ("caps_lock", "hold"), ("double:alt", "hold"), ("alt+d", "push")):
        try:
            check(bad, mode)
            raise AssertionError(f"{bad} / {mode} should be rejected")
        except ShortcutError:
            pass
    print("shortcut checks: ok")


if __name__ == "__main__":
    main()
