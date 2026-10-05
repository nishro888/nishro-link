"""docs/social-preview.png: 1280x640, for the repository's Settings -> Social preview."""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
W, H = 1280, 640
BG = (28, 28, 28)
INK = (240, 240, 240)
DIM = (160, 160, 160)
ACCENT = (76, 194, 255)
F = r"C:\Windows\Fonts"

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

# A faint accent glow behind the screenshot.
glow = Image.new("L", (W, H), 0)
ImageDraw.Draw(glow).ellipse((620, 40, 1400, 700), fill=70)
glow = glow.filter(ImageFilter.GaussianBlur(120))
img.paste(Image.new("RGB", (W, H), (20, 70, 110)), (0, 0), glow)

# The screenshot, cut off at the right edge, with a thin frame and a shadow.
shot = Image.open(f"{REPO}/docs/screenshots/arrange.png").convert("RGB")
sw = 760
shot = shot.resize((sw, round(shot.height * sw / shot.width)), Image.LANCZOS)
sx, sy = 590, (H - shot.height) // 2
shadow = Image.new("L", (W, H), 0)
ImageDraw.Draw(shadow).rounded_rectangle(
    (sx - 4, sy + 10, sx + shot.width + 4, sy + shot.height + 18), 14, fill=150)
shadow = shadow.filter(ImageFilter.GaussianBlur(18))
img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), shadow)
mask = Image.new("L", shot.size, 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, shot.width - 1, shot.height - 1), 12,
                                       fill=255)
img.paste(shot, (sx, sy), mask)
d = ImageDraw.Draw(img)
d.rounded_rectangle((sx, sy, sx + shot.width - 1, sy + shot.height - 1), 12,
                    outline=(70, 70, 70), width=1)

# The icon, the name, what it does.
icon = Image.open(f"{REPO}/link/packaging/assets/nishro-link-128.png").convert("RGBA")
icon = icon.resize((96, 96), Image.LANCZOS)
x = 72
img.paste(icon, (x, 150), icon)
d.text((x, 270), "Nishro Link", font=ImageFont.truetype(f"{F}/seguisb.ttf", 60),
       fill=INK)
body = ImageFont.truetype(f"{F}/segoeui.ttf", 27)
for i, line in enumerate(("One mouse and keyboard", "across Windows and Linux.")):
    d.text((x, 352 + i * 38), line, font=body, fill=DIM)
small = ImageFont.truetype(f"{F}/segoeui.ttf", 20)
tags = ("Any mouse drives", "Wayland", "Encrypted", "Open source")
tx, ty = x, 460
for t in tags:
    w = d.textlength(t, font=small)
    if tx + w + 28 > sx - 24:
        tx, ty = x, ty + 44
    d.rounded_rectangle((tx, ty, tx + w + 24, ty + 34), 17, outline=ACCENT, width=2)
    d.text((tx + 12, ty + 4), t, font=small, fill=ACCENT)
    tx += w + 36

img.save(f"{REPO}/docs/social-preview.png", optimize=True)
print(img.size)
