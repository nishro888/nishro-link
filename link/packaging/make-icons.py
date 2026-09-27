"""Draw the app icon at every size the two systems ask for.

    python link/packaging/make-icons.py

Writes link/packaging/assets/: nishro-link.ico (Windows: the program, the
installer, Explorer), nishro-link-<n>.png (Linux: the app menu, the dock), and
link/icon.py - a small PNG as base64 - for the window's own title bar and
taskbar button, so the running program needs no file beside it.

The design is deb/nishro-link.svg's: two screens joined by a bright link, on
a dark rounded square. Drawn at 1024 px and scaled down, with thicker strokes
for the small sizes so the link still reads at 16 px.
"""
from __future__ import annotations

import base64
import io
import pathlib

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "link" / "packaging" / "assets"

BG = (13, 18, 32, 255)
BLUE, BLUE_FILL = (96, 165, 250, 255), (21, 34, 58, 255)
GREEN, GREEN_FILL = (52, 211, 153, 255), (18, 40, 31, 255)
CYAN, GLOW = (34, 211, 238, 255), (14, 74, 90, 255)


def draw(px: int) -> Image.Image:
    S = 1024
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    heavy = px <= 32                            # small: bolder lines, no diamond
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=224, fill=BG)
    stroke = 56 if heavy else 32
    top, bottom = (340, 632) if heavy else (288, 536)
    d.rounded_rectangle((104, top, 480, bottom), radius=36, fill=BLUE_FILL,
                        outline=BLUE, width=stroke)
    d.rounded_rectangle((544, top, 920, bottom), radius=36, fill=GREEN_FILL,
                        outline=GREEN, width=stroke)
    mid = (top + bottom) // 2
    d.line((440, mid, 584, mid), fill=GLOW, width=110 if heavy else 80)
    d.line((440, mid, 584, mid), fill=CYAN, width=64 if heavy else 36)
    if not heavy:
        d.polygon([(512, 616), (608, 712), (512, 808), (416, 712)], fill=CYAN)
    return img.resize((px, px), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = {n: draw(n) for n in sizes + [512]}
    frames[256].save(OUT / "nishro-link.ico", sizes=[(n, n) for n in sizes],
                     append_images=[frames[n] for n in sizes if n != 256])
    for n in (16, 24, 32, 48, 64, 128, 256, 512):
        frames[n].save(OUT / f"nishro-link-{n}.png")
    buf = io.BytesIO()
    frames[64].save(buf, format="PNG")
    (ROOT / "link" / "icon.py").write_text(
        '"""The window\'s icon: made by packaging/make-icons.py - do not edit."""\n'
        f'PNG_64 = "{base64.b64encode(buf.getvalue()).decode()}"\n',
        encoding="utf-8", newline="\n")
    print(f"icons written to {OUT} and link/icon.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
