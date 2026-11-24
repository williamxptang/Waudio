#!/usr/bin/env python3
"""The pill's waveform: how loud a block of the microphone shows (level_of), the recorder handing the
loudest block since it was last asked, the line to the host carrying a number only, and the host's
own bar rules (packaging/macos/Host.swift WaveView.next, HUD.hintLine, compiled on their own).

  python3 backend/tests/test_pill_level.py
"""
# needs: swiftc
import math, re, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import numpy as np
from waudio import platforms as osd
from waudio.input import microphone
from waudio.input.microphone import Recorder, level_of

# the curve: louder never shows lower; it stays within 0..1; quiet speech still shows and loud speech
# fills the bar (speech RMS ~0.02-0.15, the bound the curve is made for); nothing or garbage shows nothing
grid = [i / 1000 for i in range(0, 400)]
assert grid, "no levels to check"
shown = [level_of(r) for r in grid]
assert all(a <= b for a, b in zip(shown, shown[1:])), "a louder block showed lower"
assert all(0 <= v <= 1 for v in shown)
assert level_of(0.02) > 0.2 and level_of(0.15) == 1.0, (level_of(0.02), level_of(0.15))
for bad in (0, -0.5, float("nan"), None, "loud"):
    assert level_of(bad) == 0.0, bad

# the recorder: the loudest block since the last ask, then it starts again
rec = Recorder()
block = lambda amp: (np.full((640, 1), amp, np.float32))
for amp in (0.01, 0.12, 0.03):
    rec._callback(block(amp), 640, None, None)
assert math.isclose(rec.take_level(), level_of(0.12), rel_tol=1e-5)
assert rec.take_level() == 0.0, "the level did not start again after it was taken"
rec._callback(block(0.05), 640, None, None)
assert math.isclose(rec.take_level(), level_of(0.05), rel_tol=1e-5)
rec._callback(np.zeros((0, 1), np.float32), 0, None, None)  # an empty block: no level, no error
assert rec.take_level() == 0.0

# the line to the host: two decimals in 0..1, whatever it is given
for v in (-1, 0, 0.333, 1, 5, float("nan"), float("inf"), "x", None):
    assert re.fullmatch(r"(0\.\d\d|1\.00)", osd.level_text(v)), (v, osd.level_text(v))
assert osd.level_text(0.4567) == "0.46" and osd.level_text(9) == "1.00" and osd.level_text(-3) == "0.00"
said = []
real_say, real_hosted = osd.say, osd.HOSTED
import waudio.platforms.macos as mac
mac.say, mac.HOSTED = said.append, True
osd.pill_level(0.5); osd.pill_mic("MacBook Pro\nMicrophone"); osd.pill_hint("Esc  again to cancel")
mac.say, mac.HOSTED = real_say, real_hosted
assert said == ["WAUDIO_LEVEL=0.50", "WAUDIO_PILL_MIC=MacBook Pro Microphone", "WAUDIO_PILL_HINT=Esc again to cancel"], said

# the host's bar rules, compiled with swiftc
host = (ROOT / "packaging" / "macos" / "Host.swift").read_text()
parts = [re.search(p, host, re.S) for p in (r"    static let release: CGFloat = [\d.]+.*?\n",
                                             r"    static func next\(.*?\n    }\n",
                                             r"    static func hintLine\(.*?\n    }\n",
                                             r"    static func hintShown\(.*?\n    }\n",
                                             r"    static func barCount\(.*?\n    }\n",
                                             r"    static func rowWidth\(.*?\n    }\n",
                                             r"    static func pillHeight\(.*?\n    }\n",
                                             r"    struct Metrics \{.*?\n",
                                             r"    static func metrics\(.*?\n    }\n",
                                             r"    static func letGoHides\(.*?\n",
                                             r"    struct Alphas \{.*?\n",
                                             r"    static func alphas\(.*?\n    }\n")]
assert all(parts), "WaveView.release / next / barCount / rowWidth or HUD.hintLine / hintShown / pillHeight / metrics / alphas not found in Host.swift"
CHECKS = r"""
var failed: [String] = []
func check(_ ok: Bool, _ what: String) { if !ok { failed.append(what) } }
let levels: [CGFloat] = [0, 0.1, 0.5, 1]
check(!levels.isEmpty, "no cases")
for prev in levels {
    for v in levels {
        let n = W.next(v, after: prev)
        check(n >= v, "a bar is at least as loud as its level: \(v) after \(prev)")
        check(n <= max(v, prev), "a bar never outgrows both its level and the one before")
        check(n >= prev * W.release - 0.0001, "a bar falls off no faster than release")
    }
}
check(W.next(.nan, after: 0) == 0 && W.next(7, after: 0) == 1 && W.next(-2, after: 0) == 0, "out of range kept in 0...1")
var bar: CGFloat = 1
for _ in 0..<20 { bar = W.next(0, after: bar) }
check(bar < 0.01, "silence falls back to nothing: \(bar)")
check(W.hintLine(mic: "AirPods", stopKey: "⌥D", note: "") == "AirPods   ·   Stop ⌥D   Cancel Esc", "mic and keys")
check(W.hintLine(mic: "", stopKey: "⌥D", note: "") == "Stop ⌥D   Cancel Esc", "no mic yet")
check(W.hintLine(mic: "AirPods", stopKey: "⌥D", note: "Esc again to cancel") == "Esc again to cancel", "a note wins")
check(W.hintShown(dictating: true, hints: true, note: "") && !W.hintShown(dictating: false, hints: true, note: ""), "hints while dictating only")
check(!W.hintShown(dictating: true, hints: false, note: "") && W.hintShown(dictating: true, hints: false, note: "Esc again to cancel"),
      "with Key hints off, only a note shows")
// the waveform fills its row: as many bars as fit, one more would not, never none
let widths: [CGFloat] = [0, 1, 4, 207, 300, 460, 620]
check(!widths.isEmpty, "no widths")
for w in widths {
    for (bw, gap) in [(CGFloat(2), CGFloat(2)), (3, 2), (4, 2)] {
        let n = W.barCount(width: w, barWidth: bw, gap: gap)
        let used = CGFloat(n) * bw + CGFloat(n - 1) * gap
        check(n >= 1, "at least one bar at \(w)")
        check(n == 1 || used <= w + 0.001, "\(n) bars fit \(w)")
        check(CGFloat(n + 1) * bw + CGFloat(n) * gap > w, "one more bar would not fit \(w)")
    }
}
check(W.barCount(width: .nan, barWidth: 3, gap: 2) == 1 && W.barCount(width: 100, barWidth: 0, gap: 2) == 1, "nonsense: one bar")
// ... and its row is the pill's widest one, never narrower than its least
check(W.rowWidth(least: 200, words: 0, hints: 150) == 200, "nothing said yet: the least width")
check(W.rowWidth(least: 200, words: 410, hints: 150) == 410 && W.rowWidth(least: 200, words: 120, hints: 260) == 260, "the widest row")
// one line keeps the size's own height whatever its text measures; more rows grow to fit, never below it
for (line, text, pad) in [(CGFloat(32), CGFloat(15), CGFloat(6)), (40, 19, 8), (72, 34, 14)] {
    check(W.pillHeight(line: line, fitting: text, pad: pad, oneLine: true) == line, "one line at \(line)")
    check(W.pillHeight(line: line, fitting: text + 40, pad: pad, oneLine: false) == (text + 40 + 2 * pad).rounded(.up), "rows grow at \(line)")
    check(W.pillHeight(line: line, fitting: 2, pad: pad, oneLine: false) == line, "never below one line at \(line)")
}
// one size setting: every measure grows with it and keeps its proportion to the words, over the whole range
let scales = stride(from: CGFloat(0.8), through: 1.8, by: 0.05).map { $0 }
check(scales.count > 10, "no scales")
for (a, b) in zip(scales, scales.dropFirst()) {
    let x = W.metrics(a), y = W.metrics(b)
    for (what, p, q) in [("font", x.font, y.font), ("hint", x.hint, y.hint), ("wave", x.wave, y.wave), ("height", x.height, y.height),
                         ("width", x.width, y.width), ("bar", x.bar, y.bar), ("pad", x.pad, y.pad), ("vpad", x.vpad, y.vpad)] {
        check(q >= p, "\(what) shrank from \(a) to \(b)")
    }
}
for k in scales {
    let m = W.metrics(k)
    check(m.hint / m.font >= 0.72 && m.hint / m.font <= 0.88, "hints ~0.8 of the words at \(k)")
    check(m.wave / m.font >= 1.65 && m.wave / m.font <= 1.85, "waveform ~1.75 of the words at \(k)")
    check(m.height > m.font * 1.8 && m.height >= m.wave, "a line holds the words and the waveform's height at \(k)")
    check(m.pad / m.font >= 0.75 && m.pad / m.font <= 1.0 && m.vpad / m.font >= 0.4 && m.vpad / m.font <= 0.6,
          "margins in proportion to the words at \(k)")
    check(m.bar / m.font >= 0.12 && m.bar / m.font <= 0.25 && m.width / m.font >= 26 && m.width / m.font <= 31,
          "bars and the longest line in proportion to the words at \(k)")
}
check(W.metrics(0.8).font < W.metrics(1.8).font * 0.5, "the range spans more than double")
for (out, edge) in [(CGFloat(0.1), CGFloat(0.8)), (9, 1.8), (.nan, 1)] {
    check(W.metrics(out).font == W.metrics(edge).font && W.metrics(out).height == W.metrics(edge).height, "\(out) kept to \(edge)")
}
// a slider let go hides only its own live preview, never a dictation or a reading in progress
check(W.letGoHides("sizing"), "the live preview goes")
for other in ["listening", "live", "transcribing", "reading", "preview", "done", "hide"] {
    check(!W.letGoHides(other), "\(other) stays when a slider is let go")
}
// transparency: the background and the waveform at the setting itself, the words and hints half as transparent;
// from none down to 90 % (0.1), anything else kept inside that
let settings: [CGFloat] = [0.1, 0.3, 0.5, 0.75, 1]
check(!settings.isEmpty, "no transparencies")
for o in settings {
    let a = W.alphas(o)
    check(a.background == o && a.wave == o, "the background and the waveform at \(o)")
    check(abs(a.words - (1 + o) / 2) < 0.0001, "the words half as transparent at \(o)")
}
for (out, edge) in [(CGFloat(0.05), CGFloat(0.1)), (0, 0.1), (3, 1), (.nan, 1)] {
    check(W.alphas(out).background == edge && W.alphas(out).wave == edge, "\(out) kept to \(edge)")
}
if failed.isEmpty { print("ok") } else { print(failed.joined(separator: "\n")); exit(1) }
"""
work = Path(tempfile.mkdtemp())
src = work / "wave.swift"
src.write_text("import AppKit\nenum W {\n" + "".join(m.group(0) for m in parts) + "}\n" + CHECKS)
built = subprocess.run(["swiftc", "-o", str(work / "wave"), str(src)], capture_output=True, text=True)
assert built.returncode == 0, built.stderr
ran = subprocess.run([str(work / "wave")], capture_output=True, text=True, timeout=20)
assert ran.returncode == 0 and ran.stdout.strip() == "ok", ran.stdout + ran.stderr
print("pill level checks: ok")
