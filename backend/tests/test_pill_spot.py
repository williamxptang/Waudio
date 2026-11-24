#!/usr/bin/env python3
"""The pill's fixed spot (packaging/macos/Host.swift, HUD.spotFrame / spotOf / dragFrame), compiled
on their own with swiftc: a spot and the frame it gives map back to each other, 0.5 is centred
whatever the pill's width, 0 / 1 are the margins, the pill never leaves the screen's usable area,
a spot means the same place on a screen of another size, a dragged pill snaps to the middle and to
the edges, and the pill goes to the screen holding most of the active window.

  python3 backend/tests/test_pill_spot.py
"""
# needs: swiftc
import re, subprocess, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

host = (ROOT / "packaging" / "macos" / "Host.swift").read_text()
funcs = [re.search(rf"    static func {name}\(.*?\n    }}\n", host, re.S) for name in ("spotFrame", "dragFrame", "spotOf", "screenIndex")]
assert all(funcs), "spotFrame / dragFrame / spotOf / screenIndex not found in Host.swift"

CHECKS = r"""
func near(_ a: CGFloat, _ b: CGFloat) -> Bool { abs(a - b) < 0.001 }
var failed: [String] = []
func check(_ ok: Bool, _ what: String) { if !ok { failed.append(what) } }

let screens = [NSRect(x: 0, y: 0, width: 1440, height: 875), NSRect(x: 1440, y: -200, width: 2560, height: 1415)]
let sizes = [NSSize(width: 36, height: 36), NSSize(width: 300, height: 36), NSSize(width: 648, height: 46)]
let spots = [CGPoint(x: 0, y: 0), CGPoint(x: 0.5, y: 0), CGPoint(x: 1, y: 0), CGPoint(x: 0.5, y: 1),
             CGPoint(x: 0.25, y: 0.75), CGPoint(x: 1, y: 1)]
check(!screens.isEmpty && !sizes.isEmpty && !spots.isEmpty, "no cases")
for area in screens {
    for size in sizes {
        for s in spots {
            let f = HUD.spotFrame(s, size, in: area)
            let back = HUD.spotOf(f, in: area)
            check(near(back.x, s.x) && near(back.y, s.y), "round trip \(s) \(size) \(area)")
            check(f.minX >= area.minX + 8 - 0.001 && f.maxX <= area.maxX - 8 + 0.001
                  && f.maxY <= area.maxY - 4 + 0.001 && f.minY >= area.minY + 8 - 0.001, "inside \(s) \(size) \(area)")
            check(f.size == size, "size kept")
        }
        check(near(HUD.spotFrame(CGPoint(x: 0.5, y: 0.3), size, in: area).midX, area.midX), "0.5 is centred \(size)")
        check(near(HUD.spotFrame(CGPoint(x: 0, y: 0), size, in: area).minX, area.minX + 8)
              && near(HUD.spotFrame(CGPoint(x: 0, y: 0), size, in: area).maxY, area.maxY - 4), "0,0 top left")
        check(near(HUD.spotFrame(CGPoint(x: 1, y: 1), size, in: area).maxX, area.maxX - 8)
              && near(HUD.spotFrame(CGPoint(x: 1, y: 1), size, in: area).minY, area.minY + 8), "1,1 bottom right")
    }
}
// the same spot is the same share of the room on a small and a large screen
let small = HUD.spotFrame(CGPoint(x: 0.25, y: 0.75), sizes[1], in: screens[0])
let large = HUD.spotFrame(CGPoint(x: 0.25, y: 0.75), sizes[1], in: screens[1])
check(near((small.minX - screens[0].minX - 8) / (screens[0].width - 316), (large.minX - screens[1].minX - 8) / (screens[1].width - 316)),
      "same share across screens")
// dragged off the screen: kept at the edge
let off = HUD.spotOf(NSRect(x: -500, y: 5000, width: 300, height: 36), in: screens[0])
check(off.x == 0 && off.y == 0, "off-screen frame clamps to 0,0: \(off)")
// dragging: within 12 points of the middle it snaps there; further away it stays put; off screen it is kept inside
let area = screens[1], pill = sizes[1]
for (offset, snaps) in [(CGFloat(0), true), (11, true), (-11, true), (13, false), (-40, false)] {
    let f = HUD.dragFrame(NSRect(x: area.midX - pill.width / 2 + offset, y: area.midY, width: pill.width, height: pill.height), in: area)
    check(near(f.midX, area.midX) == snaps && (snaps || near(f.midX, area.midX + offset)), "drag snap at \(offset)")
}
let dragged = HUD.dragFrame(NSRect(x: area.maxX + 300, y: area.minY - 300, width: pill.width, height: pill.height), in: area)
check(near(dragged.maxX, area.maxX - 8) && near(dragged.minY, area.minY + 8), "dragged off screen: kept inside \(dragged)")
// ... and within 12 points of an edge (its margin) it snaps flush to it; further in it stays put
let free = NSRect(x: area.minX + 200, y: area.midY - 100, width: pill.width, height: pill.height)
for (dx, dy, snapsX, snapsY) in [(CGFloat(-190), CGFloat(0), true, false), (-180, 0, false, false),
                                 (0, CGFloat(area.maxY - 4 - free.maxY - 10), false, true), (0, area.maxY - 4 - free.maxY - 20, false, false)] {
    let f = HUD.dragFrame(free.offsetBy(dx: dx, dy: dy), in: area)
    check(near(f.minX, area.minX + 8) == snapsX && near(f.maxY, area.maxY - 4) == snapsY, "edge snap \(dx),\(dy): \(f)")
}
let right = HUD.dragFrame(NSRect(x: area.maxX - 8 - pill.width - 10, y: area.midY - 200, width: pill.width, height: pill.height), in: area)
let bottom = HUD.dragFrame(NSRect(x: area.minX + 300, y: area.minY + 8 + 10, width: pill.width, height: pill.height), in: area)
check(near(right.maxX, area.maxX - 8) && near(bottom.minY, area.minY + 8), "right and bottom edges snap")
// the screen holding most of the active window; none when it is on no screen
check(HUD.screenIndex(NSRect(x: 1300, y: 100, width: 600, height: 400), screens) == 1, "window mostly on the second screen")
check(HUD.screenIndex(NSRect(x: 1000, y: 100, width: 600, height: 400), screens) == 0, "window mostly on the first screen")
check(HUD.screenIndex(NSRect(x: 9000, y: 9000, width: 10, height: 10), screens) == nil, "window on no screen")
if failed.isEmpty { print("ok") } else { print(failed.joined(separator: "\n")); exit(1) }
"""

work = Path(tempfile.mkdtemp())
src = work / "spot.swift"
src.write_text("import AppKit\nenum HUD {\n" + "".join(m.group(0) for m in funcs) + "}\n" + CHECKS)
built = subprocess.run(["swiftc", "-o", str(work / "spot"), str(src)], capture_output=True, text=True)
assert built.returncode == 0, built.stderr
ran = subprocess.run([str(work / "spot")], capture_output=True, text=True, timeout=30)
assert ran.returncode == 0 and ran.stdout.strip() == "ok", ran.stdout + ran.stderr
print("pill spot checks: ok")
