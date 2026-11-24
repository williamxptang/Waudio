#!/usr/bin/env python3
"""Draw Waudio's app icon in the app's palette and write AppIcon.icns + AppIcon.png here, and the
Chrome extension's icons (browser/icons/16, 32, 48, 128.png; backend/tests/test_browser.py checks them).

  python3 packaging/icon/make_icon.py   (needs Pillow; macOS iconutil)

Cream tile with a hairline edge (--bg / --line) holding a pixel-art ring drawn here from
geometry alone: on each side two sound arcs, an inner and an outer, terracotta on the left (--primary,
listening) and steel blue on the right (--accent, speaking), shaded as if lit from the top left,
around a glowing dot. Every pixel of the GRID x GRID ring is one of the colours below.
"""
import subprocess, sys, tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
GRID = 48                                  # the ring is GRID x GRID pixels
S = 2048                                   # drawn at 2x, scaled to 1024
CREAM_TOP, CREAM_BOTTOM = (251, 249, 242), (235, 232, 221)    # --bg-elev -> --bg-deep
LINE = (217, 211, 192)                     # --line
PRIMARY, PRIMARY_INK = (201, 100, 66), (251, 247, 238)        # --primary, --primary-ink
ACCENT = (58, 92, 122)                     # --accent
SHADES = {  # light -> dark, per side
    "left": [(236, 124, 86), PRIMARY, (120, 60, 74)],          # terracotta light, --primary, plum
    "right": [(146, 192, 214), (104, 150, 184), ACCENT, (44, 46, 76)],  # sky, steel light, --accent, deep navy
}
ARCS = [(9.5, 12.5, 50), (15.5, 21.0, 58)]  # (inner radius, outer radius, half-angle in degrees) in GRID pixels
DOT = [(5.2, (236, 124, 86)), (3.9, (250, 212, 140)), (2.0, PRIMARY_INK)]  # (radius in pixels, colour), outside in


def ring():
    """The GRID x GRID ring: two arcs a side with rounded ends, each pixel shaded by how much it faces the
    light (top left); outside the arcs transparent."""
    import math
    img = Image.new("RGBA", (GRID, GRID), (0, 0, 0, 0))
    c = (GRID - 1) / 2
    light = (-0.6, -0.8)
    for y in range(GRID):
        for x in range(GRID):
            dx, dy = x - c, y - c
            d = math.hypot(dx, dy)
            side = "left" if dx < 0 else "right"
            centre = 180.0 if dx < 0 else 0.0
            ang = math.degrees(math.atan2(dy, dx))
            off = abs((ang - centre + 180) % 360 - 180)
            for r0, r1, half in ARCS:
                mid, w = (r0 + r1) / 2, (r1 - r0) / 2
                inside = r0 <= d <= r1 and off <= half
                if not inside:  # rounded ends: a disc of the arc's width at each end
                    for sign in (-1, 1):
                        a = math.radians(centre + sign * half)
                        ex, ey = mid * math.cos(a), mid * math.sin(a)
                        if math.hypot(dx - ex, dy - ey) <= w:
                            inside = True
                if inside:
                    nx, ny = (dx / d, dy / d) if d else (0, 0)
                    facing = (nx * light[0] + ny * light[1] + 1) / 2          # 0 (away) .. 1 (towards)
                    depth = (d - r0) / max(r1 - r0, 1e-6)                    # 0 inner edge .. 1 outer edge
                    t = min(max(0.65 * (1 - facing) + 0.35 * depth, 0), 0.999)
                    shades = SHADES[side]
                    img.putpixel((x, y), shades[int(t * len(shades))] + (255,))
    return img


def with_dot(ring):
    """The ring with the glowing dot (DOT) at its centre, on the same pixel grid."""
    ring = ring.copy()
    c = (GRID - 1) / 2
    for y in range(GRID):
        for x in range(GRID):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            for r, colour in DOT:
                if d <= r:
                    ring.putpixel((x, y), colour + (255,))
    return ring


def tile_mask():
    # Apple's grid: body 824/1024 of the canvas, corner radius ~185/1024
    m, r = round(S * 100 / 1024), round(S * 185 / 1024)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((m, m, S - m, S - m), radius=r, fill=255)
    return mask, m, r


def draw():
    mask, m, r = tile_mask()
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # soft drop shadow under the tile
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((m, m + 24, S - m, S - m + 24), radius=r, fill=(60, 50, 30, 70))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(36)))

    # cream gradient tile + hairline edge
    tile = Image.new("RGBA", (S, S))
    td = ImageDraw.Draw(tile)
    for y in range(S):
        t = y / (S - 1)
        td.line((0, y, S, y), fill=tuple(round(a + (b - a) * t) for a, b in zip(CREAM_TOP, CREAM_BOTTOM)) + (255,))
    canvas.paste(tile, mask=mask)
    ImageDraw.Draw(canvas).rounded_rectangle((m, m, S - m, S - m), radius=r, outline=LINE + (255,), width=6)

    # the pixel ring, 80 % of the tile, every pixel a sharp square
    size = round((S - 2 * m) * 0.80) // GRID * GRID
    pixels = with_dot(ring()).resize((size, size), Image.NEAREST)
    canvas.alpha_composite(pixels, ((S - size) // 2, (S - size) // 2))
    return canvas.resize((1024, 1024), Image.LANCZOS)


def chrome_icons():
    """{size: image} for browser/icons: the whole icon at 48 and 128 (chrome://extensions, the install
    dialog); in the toolbar (16, 32) the ring and dot alone, no tile, so they fill the space."""
    whole = draw()
    small = with_dot(ring())  # GRID pixels square
    return {16: small.resize((16, 16), Image.BOX), 32: small.resize((32, 32), Image.LANCZOS),
            48: whole.resize((48, 48), Image.LANCZOS), 128: whole.resize((128, 128), Image.LANCZOS)}


def main():
    img = draw()
    img.save(HERE / "AppIcon.png")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            img.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            img.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "AppIcon.icns")], check=True)
    print("wrote", HERE / "AppIcon.icns", "and AppIcon.png")
    icons = HERE.parents[1] / "browser" / "icons"
    for size, icon in chrome_icons().items():
        icon.save(icons / f"{size}.png")
    print("wrote", icons)


if __name__ == "__main__":
    main()
