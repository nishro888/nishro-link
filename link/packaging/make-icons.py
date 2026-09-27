"""Draw the app icon at every size the two systems ask for.

    python link/packaging/make-icons.py

Writes link/packaging/assets/: nishro-link.ico (Windows: the program, the
installer, Explorer, shortcuts, the taskbar), nishro-link-<n>.png (Linux: the
app menu, the dock), deb/nishro-link.svg (the vector one), and link/icon.py -
small PNGs as base64 - for the window's own title bar and taskbar button, so the
running program needs no file beside it.

THE DESIGN. Two screens and the pointer crossing from one to the other, in
white on a blue-to-cyan tile. The first icon was outlines on a dark tile: it
disappeared on a dark taskbar and blurred to a smudge at 16 px. This one is
bright on dark and light alike, and it is DRAWN FOR EACH SIZE, not shrunk:

  16-24 px   two solid screens - all a taskbar corner has room for
  32-48 px   screens on stands, and a plain pointer between them
  64 px up   screens with their glass, stands, and the pointer outlined

Every shape is placed on the target size's own pixel grid and drawn 8 times
over, so edges are crisp instead of smeared by scaling.

Windows picks from 16, 20, 24, 32, 40, 48, 64 and 256 as the display scale
changes (100% to 300%): all are in the .ico, each drawn for itself.
"""
from __future__ import annotations

import base64
import io
import pathlib

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "link" / "packaging" / "assets"
SVG = ROOT / "link" / "packaging" / "deb" / "nishro-link.svg"

TOP, BOTTOM = (37, 99, 235), (6, 182, 212)          # the tile: blue to cyan
GLASS_TOP, GLASS_BOTTOM = (15, 40, 85), (14, 72, 110)
WHITE = (255, 255, 255, 255)
INK = (11, 30, 63, 255)                               # the pointer's outline
SS = 8                                                # drawn this many times over

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
EMBED = (16, 24, 32, 48, 64)                          # into link/icon.py


def _gradient(size, top, bottom):
    """A diagonal gradient, top-left to bottom-right."""
    g = Image.new("RGB", (2, 2))
    g.putpixel((0, 0), top)
    g.putpixel((1, 1), bottom)
    mid = tuple((a + b) // 2 for a, b in zip(top, bottom))
    g.putpixel((1, 0), mid)
    g.putpixel((0, 1), mid)
    return g.resize((size, size), Image.BILINEAR).convert("RGBA")


def draw(px: int) -> Image.Image:
    """The icon at px x px."""
    N = px * SS

    def u(v):                         # a length in target pixels, on the grid
        return round(v) * SS

    tier = "s" if px <= 24 else "m" if px <= 48 else "l"
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))

    # the tile
    inset = 0 if px <= 20 else max(1, round(px * 0.03))
    radius = max(2, round(px * 0.22))
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (u(inset), u(inset), N - u(inset) - 1, N - u(inset) - 1),
        radius=u(radius), fill=255)
    img.paste(_gradient(N, TOP, BOTTOM), (0, 0), mask)
    d = ImageDraw.Draw(img)

    # two screens side by side, a gap between them where the pointer crosses
    if tier == "s":
        # Whole pixels only: a screen, a pixel of air, a one-pixel stand.
        m = max(2, round(px * 0.14))                  # margin inside the tile
        gap = max(1, round(px * 0.07))
        sw = (px - 2 * m - gap) // 2
        sh = max(3, round(sw * 0.72))
        y0 = (px - (sh + 2)) // 2
        r = max(1, round(px * 0.05))
        for x0 in (m, px - m - sw):
            d.rounded_rectangle((u(x0), u(y0), u(x0 + sw) - 1, u(y0 + sh) - 1),
                                radius=u(r) if r > 1 else SS, fill=WHITE)
            foot = max(2, sw // 2)
            fx = x0 + (sw - foot) // 2
            d.rectangle((u(fx), u(y0 + sh + 1), u(fx + foot) - 1,
                         u(y0 + sh + 2) - 1), fill=WHITE)
        return img.resize((px, px), Image.LANCZOS)

    m = px * 0.11
    gap = px * 0.07
    sw = (px - 2 * m - gap) / 2
    sh = sw * 0.72
    y0 = (px - (sh + px * 0.11)) / 2                  # screens and stands, centred
    screens = []
    for x0 in (m, m + sw + gap):
        box = (x0, y0, x0 + sw, y0 + sh)
        screens.append(box)
        bez = max(1.0, px * 0.035)
        d.rounded_rectangle(tuple(u(v) for v in box), radius=u(max(1, px * 0.035)),
                            fill=WHITE)
        if tier == "l":
            # the glass inside the bezel
            gx0, gy0 = u(box[0] + bez), u(box[1] + bez)
            gx1, gy1 = u(box[2] - bez), u(box[3] - bez)
            glass = _gradient(max(1, gx1 - gx0), GLASS_TOP, GLASS_BOTTOM).resize(
                (max(1, gx1 - gx0), max(1, gy1 - gy0)))
            gm = Image.new("L", glass.size, 0)
            ImageDraw.Draw(gm).rounded_rectangle(
                (0, 0, glass.size[0] - 1, glass.size[1] - 1),
                radius=u(max(1, px * 0.015)), fill=255)
            img.paste(glass, (gx0, gy0), gm)
        # the stand: a neck and a foot
        cx = (box[0] + box[2]) / 2
        neck_w, neck_h = max(1, px * 0.06), max(1, px * 0.07)
        foot_w, foot_h = max(2, px * 0.2), max(1, px * 0.04)
        d.rectangle((u(cx - neck_w / 2), u(box[3]), u(cx + neck_w / 2),
                     u(box[3] + neck_h)), fill=WHITE)
        d.rounded_rectangle((u(cx - foot_w / 2), u(box[3] + neck_h),
                             u(cx + foot_w / 2), u(box[3] + neck_h + foot_h)),
                            radius=u(max(1, foot_h / 2)), fill=WHITE)

    # the pointer, crossing from the left screen onto the right one
    tip_x = screens[1][0] - gap * 0.5                 # in the gap: crossing
    tip_y = y0 + sh * 0.26
    s = px * 0.29 / 17.0                              # a 17-unit-tall arrow
    arrow = [(0, 0), (0, 14), (3.6, 10.8), (6.2, 16.6), (8.6, 15.5),
             (6.1, 9.9), (10.8, 9.9)]
    pts = [((tip_x + x * s) * SS, (tip_y + y * s) * SS) for x, y in arrow]
    # A white arrow with an ink outline: the arrow drawn fat in ink, then the
    # arrow itself in white over it - the ink shows only outside the edge.
    ring = int(max(1.0, px * 0.028) * SS * 2)
    d.polygon(pts, fill=INK)
    d.line(pts + [pts[0], pts[1]], fill=INK, width=ring, joint="curve")
    d.polygon(pts, fill=WHITE)
    return img.resize((px, px), Image.LANCZOS)


def svg() -> str:
    """The vector icon, the same design at its most detailed."""
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <defs>
    <linearGradient id="tile" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#2563eb"/><stop offset="1" stop-color="#06b6d4"/>
    </linearGradient>
    <linearGradient id="glass" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0f2855"/><stop offset="1" stop-color="#0e486e"/>
    </linearGradient>
  </defs>
  <rect x="8" y="8" width="240" height="240" rx="56" fill="url(#tile)"/>
  <g fill="#ffffff">
    <rect x="31" y="72" width="88" height="63" rx="9"/>
    <rect x="137" y="72" width="88" height="63" rx="9"/>
    <rect x="71" y="135" width="15" height="18"/><rect x="177" y="135" width="15" height="18"/>
    <rect x="53" y="151" width="51" height="10" rx="5"/><rect x="159" y="151" width="51" height="10" rx="5"/>
  </g>
  <rect x="40" y="81" width="70" height="45" rx="4" fill="url(#glass)"/>
  <rect x="146" y="81" width="70" height="45" rx="4" fill="url(#glass)"/>
  <path d="M133 83 v44 l11.3 -10.1 8.2 18.2 7.5 -3.4 -7.8 -17.6 h14.8 z"
        fill="#ffffff" stroke="#0b1e3f" stroke-width="6" stroke-linejoin="round"/>
</svg>
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frames = {n: draw(n) for n in sorted(set(ICO_SIZES) | set(PNG_SIZES))}
    big = frames[256]
    big.save(OUT / "nishro-link.ico", sizes=[(n, n) for n in ICO_SIZES],
             append_images=[frames[n] for n in ICO_SIZES if n != 256])
    for n in PNG_SIZES:
        frames[n].save(OUT / f"nishro-link-{n}.png", optimize=True)
    SVG.write_text(svg(), encoding="utf-8", newline="\n")
    lines = ['"""The window\'s icon: made by packaging/make-icons.py - do not edit.',
             "",
             "One PNG per size, so the title bar (16) and the taskbar (24-48) each",
             'get one drawn for them rather than one scaled down."""']
    for n in EMBED:
        buf = io.BytesIO()
        frames[n].save(buf, format="PNG", optimize=True)
        lines.append(f'PNG_{n} = "{base64.b64encode(buf.getvalue()).decode()}"')
    lines.append("SIZES = (" + ", ".join(f"PNG_{n}" for n in EMBED) + ")")
    (ROOT / "link" / "icon.py").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8", newline="\n")
    print(f"icons written to {OUT}, {SVG.name} and link/icon.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
