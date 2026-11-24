"""Original app icon: a friendly retro robot head wearing a chef's toque."""

# Regenerate: pip install pillow; python tools/make_icon.py src/rosie/static/icons
# (also writes icon-180.png and preview.png there; only 192/512 are used).
import sys

from PIL import Image, ImageDraw

N = 1024
BG, FACE, FACE_D, EYE, EYE_HI, HAT, HAT_D, MOUTH, CHEEK = (
    "#1f3b2d",
    "#c9d3d8",
    "#9eabb2",
    "#2bb3c0",
    "#e8fbfd",
    "#ffffff",
    "#dfe3e0",
    "#263238",
    "#f2a65a",
)

img = Image.new("RGB", (N, N), BG)
d = ImageDraw.Draw(img)

# Everything stays inside the central ~76% (maskable-icon safe zone).
# Ear bolts
for x in (238, 786):
    d.rounded_rectangle([x - 34, 560, x + 34, 680], 22, fill=FACE_D)
# Head
d.rounded_rectangle([262, 430, 762, 830], 110, fill=FACE)
# Visor band
d.rounded_rectangle([312, 500, 712, 650], 75, fill=MOUTH)
# Eyes
for cx in (412, 612):
    d.ellipse([cx - 52, 523, cx + 52, 627], fill=EYE)
    d.ellipse([cx - 4, 545, cx + 26, 575], fill=EYE_HI)
# Smile (grille)
d.rounded_rectangle([392, 700, 632, 760], 30, fill=MOUTH)
for x in (452, 512, 572):
    d.line([x, 708, x, 752], fill=FACE_D, width=10)
# Cheek lights
for cx in (322, 702):
    d.ellipse([cx - 22, 690, cx + 22, 734], fill=CHEEK)
# Chef's toque: band + three puffs
d.rounded_rectangle([352, 372, 672, 446], 24, fill=HAT, outline=HAT_D, width=8)
for cx, cy, r in ((402, 300, 92), (622, 300, 92), (512, 250, 112)):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=HAT)
d.rectangle([340, 300, 684, 380], fill=HAT)
d.line([352, 374, 672, 374], fill=HAT_D, width=8)

out = sys.argv[1]
for size in (512, 192, 180):
    img.resize((size, size), Image.LANCZOS).save(f"{out}/icon-{size}.png", optimize=True)
img.resize((256, 256), Image.LANCZOS).save(f"{out}/preview.png")
