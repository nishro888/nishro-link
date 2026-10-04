"""Draw the app icon at every size the two systems ask for.

    python link/packaging/make-icons.py

Writes link/packaging/assets/: nishro-link.ico (Windows: the program, the
installer, Explorer, shortcuts, the taskbar), nishro-link-<n>.png (Linux: the
app menu, the dock), deb/nishro-link.svg (the vector one), tray/*.svg (the
Linux tray icon in its three states), and link/icon.py - small PNGs as base64 -
for the window's own title bar and taskbar button, and the Windows tray icon in
its three states, so the running program needs no file beside it.

THE DESIGN. The doorway: two screens side by side, and between them a lit
seam - the edge the pointer crosses from one computer to the next, which is
the whole of what Nishro Link does. White and bright cyan on a deep-blue-to-sky
tile. Earlier designs had a pointer arrow (in a dock or a tray it looked like
the real pointer, on a program that moves the real pointer about) and then the
whole desk, screens, keyboard and mouse (busy; a picture more than a mark).

It is DRAWN FOR EACH SIZE, not shrunk. Up to 32 px everything sits on whole
pixels - screen, a pixel of air, a two-pixel seam, a pixel of air, screen - so
the seam stays a line of its own instead of blurring into the screens. From
32 px the seam glows. Every shape is drawn 8 times over, so edges are crisp.

THE TRAY. The same icon in three states: as it is (connected), grey (sharing
is off), and with an amber dot (not connected, or setup needed).

Windows picks from 16, 20, 24, 32, 40, 48, 64 and 256 as the display scale
changes (100% to 300%): all are in the .ico, each drawn for itself.
"""
from __future__ import annotations

import base64
import io
import pathlib

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "link" / "packaging" / "assets"
SVG = ROOT / "link" / "packaging" / "deb" / "nishro-link.svg"
TRAY_SVG = OUT / "tray"

TOP, BOTTOM = (30, 64, 175), (14, 165, 233)          # the tile: deep blue to sky
WHITE = (255, 255, 255, 255)
SEAM = (103, 232, 249, 255)                           # the lit seam: bright cyan
GLOW = (34, 211, 238, 255)
AMBER = (245, 166, 35, 255)
SS = 8                                                # drawn this many times over

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
EMBED = (16, 24, 32, 48, 64)                          # into link/icon.py
TRAY_SIZES = (16, 20, 24, 32)                         # Windows: 100% to 200%
STATES = ("ok", "paused", "alert")


def _gradient(size, top, bottom):
    """A diagonal gradient, top-left to bottom-right."""
    n = 64
    g = Image.new("RGB", (n, n))
    px = g.load()
    for y in range(n):
        for x in range(n):
            t = (x + y) / (2 * n - 2)
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
    return g.resize((size, size), Image.BILINEAR).convert("RGBA")


def layout(px: float) -> dict:
    """Where everything goes at px, in target pixels. One place, so the
    drawings and the SVGs cannot drift apart."""
    out = {"tile_inset": 0 if px <= 20 else max(1, round(px * 0.03)),
           "tile_radius": max(2, round(px * 0.22)),
           "glow": px >= 32}
    if px <= 32 and px == int(px):
        # Whole pixels: margin, screen, air, seam (2), air, screen, margin.
        px = int(px)
        m = round(px * 0.13)
        sw = (px - 2 * m - 4) // 2
        y0 = round(px * 0.27)
        sy0 = round(px * 0.37)
        out.update(screens=[(m, y0, m + sw, px - y0), (px - m - sw, y0, px - m, px - y0)],
                   screen_radius=1 if px < 32 else 2,
                   seam=(m + sw + 1, sy0, m + sw + 3, px - sy0))
        return out
    m, gap, w = px * 0.13, px * 0.10, px * 0.05
    sw = (px - 2 * m - gap) / 2
    y0, sy0 = px * 0.28, px * 0.38
    c = px / 2
    out.update(screens=[(m, y0, m + sw, px - y0), (px - m - sw, y0, px - m, px - y0)],
               screen_radius=px * 0.07,
               seam=(c - w / 2, sy0, c + w / 2, px - sy0))
    return out


def draw(px: int) -> Image.Image:
    """The icon at px x px."""
    N = px * SS

    def f(box):                       # a box in target pixels, as drawn
        return tuple(round(v * SS) for v in box)

    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    L = layout(px)

    # the tile
    inset, radius = L["tile_inset"] * SS, L["tile_radius"] * SS
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle((inset, inset, N - inset - 1, N - inset - 1),
                                           radius=radius, fill=255)
    img.paste(_gradient(N, TOP, BOTTOM), (0, 0), mask)
    d = ImageDraw.Draw(img)

    # the two screens
    for box in L["screens"]:
        d.rounded_rectangle(f(box), radius=round(L["screen_radius"] * SS), fill=WHITE)

    # the seam, and from 32 px its light spilling on to the screens' edges
    seam = f(L["seam"])
    r = (seam[2] - seam[0]) // 2
    if L["glow"]:
        light = Image.new("L", (N, N), 0)
        ImageDraw.Draw(light).rounded_rectangle(seam, radius=r, fill=255)
        light = light.filter(ImageFilter.GaussianBlur(N * 0.045)).point(
            lambda v: int(v * 0.9))
        img.paste(Image.new("RGBA", (N, N), GLOW), (0, 0), light)
        d = ImageDraw.Draw(img)
    d.rounded_rectangle(seam, radius=r, fill=SEAM)
    return img.resize((px, px), Image.LANCZOS)


def tray(px: int, state: str) -> Image.Image:
    """The tray icon: as it is, grey when paused, an amber dot when not connected."""
    img = draw(px)
    if state == "paused":
        alpha = img.split()[3]
        grey = ImageEnhance.Color(img.convert("RGB")).enhance(0.0)
        grey = ImageEnhance.Brightness(grey).enhance(0.8).convert("RGBA")
        grey.putalpha(alpha)
        return grey
    if state == "alert":
        N = px * SS
        big = img.resize((N, N), Image.LANCZOS)
        d = ImageDraw.Draw(big)
        r = px * 0.22
        cx, cy = px - r - px * 0.02, px - r - px * 0.02
        ring = max(1.0, px * 0.07)
        d.ellipse(((cx - r - ring) * SS, (cy - r - ring) * SS, (cx + r + ring) * SS,
                   (cy + r + ring) * SS), fill=(24, 24, 28, 255))
        d.ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS),
                  fill=AMBER)
        return big.resize((px, px), Image.LANCZOS)
    return img


def svg(detail: bool = True, state: str = "ok") -> str:
    """The vector icon. `detail` is the app icon, drawn at 256 with its glow;
    without it, it is the tray's, on the 16 px grid a top bar draws it at."""
    px = 256 if detail else 16
    L = layout(px)
    ins, rad = L["tile_inset"], L["tile_radius"]
    paused = state == "paused"
    top, bottom = ("#6b7280", "#9ca3af") if paused else ("#1e40af", "#0ea5e9")
    seam_fill = "#e5e7eb" if paused else "#67e8f9"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {px} {px}" '
           f'width="{px}" height="{px}">',
           '  <defs>',
           '    <linearGradient id="tile" x1="0" y1="0" x2="1" y2="1">',
           f'      <stop offset="0" stop-color="{top}"/><stop offset="1" '
           f'stop-color="{bottom}"/>',
           '    </linearGradient>']
    glow = detail and not paused
    if glow:
        out.append(f'    <filter id="glow" x="-2" y="-1" width="5" height="3">'
                   f'<feGaussianBlur stdDeviation="{px * 0.045:.1f}"/></filter>')
    out += ['  </defs>',
            f'  <rect x="{ins}" y="{ins}" width="{px - 2 * ins:g}" '
            f'height="{px - 2 * ins:g}" rx="{rad}" fill="url(#tile)"/>']

    def rect(box, r, fill, extra=""):
        x0, y0, x1, y1 = box
        out.append(f'  <rect x="{x0:g}" y="{y0:g}" width="{x1 - x0:g}" '
                   f'height="{y1 - y0:g}" rx="{r:g}" fill="{fill}"{extra}/>')

    for box in L["screens"]:
        rect(tuple(round(v, 1) for v in box), round(L["screen_radius"], 1), "#ffffff")
    seam = tuple(round(v, 1) for v in L["seam"])
    r = round((seam[2] - seam[0]) / 2, 1)
    if glow:
        rect(seam, r, "#22d3ee", ' opacity="0.9" filter="url(#glow)"')
    rect(seam, r, seam_fill)
    if state == "alert":
        r, ring = px * 0.22, px * 0.07
        c = px - r - px * 0.02
        out.append(f'  <circle cx="{c:.2f}" cy="{c:.2f}" r="{r + ring:.2f}" '
                   f'fill="#18181c"/>')
        out.append(f'  <circle cx="{c:.2f}" cy="{c:.2f}" r="{r:.2f}" fill="#f5a623"/>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frames = {n: draw(n) for n in sorted(set(ICO_SIZES) | set(PNG_SIZES))}
    big = frames[256]
    big.save(OUT / "nishro-link.ico", sizes=[(n, n) for n in ICO_SIZES],
             append_images=[frames[n] for n in ICO_SIZES if n != 256])
    for n in PNG_SIZES:
        frames[n].save(OUT / f"nishro-link-{n}.png", optimize=True)
    SVG.write_text(svg(), encoding="utf-8", newline="\n")
    TRAY_SVG.mkdir(parents=True, exist_ok=True)
    for state in STATES:
        (TRAY_SVG / f"nishro-link-{state}.svg").write_text(
            svg(detail=False, state=state), encoding="utf-8", newline="\n")
    lines = ['"""The window\'s and the tray\'s icons: made by packaging/make-icons.py -',
             "do not edit.",
             "",
             "One PNG per size, so the title bar (16) and the taskbar (24-48) each",
             "get one drawn for them rather than one scaled down. TRAY holds the",
             "Windows tray icon in its three states, at 100% to 200% display scale.",
             '"""']
    for n in EMBED:
        lines.append(f'PNG_{n} = "{_png(frames[n])}"')
    lines.append("SIZES = (" + ", ".join(f"PNG_{n}" for n in EMBED) + ")")
    lines.append("TRAY = {")
    for state in STATES:
        lines.append(f'    "{state}": {{')
        for n in TRAY_SIZES:
            lines.append(f'        {n}: "{_png(tray(n, state))}",')
        lines.append("    },")
    lines.append("}")
    (ROOT / "link" / "icon.py").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8", newline="\n")
    print(f"icons written to {OUT}, {SVG.name}, tray/ and link/icon.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
