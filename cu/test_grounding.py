"""Grounding accuracy check: localize several fields on the OpenVSP Sect panel
and draw them on the image so we can eyeball accuracy."""
import os
from PIL import Image, ImageDraw
from holo_client import localize

HERE = os.path.dirname(__file__)
IMG = os.path.join(HERE, "..", "vendor/openvsp/vspaero_ex/Swept_Wing_API/Images/GUI_Sect.png")

TARGETS = [
    ("Span", "the wing Span numeric value field showing 63.63"),
    ("Sweep", "the Sweep numeric value field showing 45"),
    ("RootC", "the Root Chord (Root C) numeric value field showing 21.941"),
    ("Sect_tab", "the 'Sect' tab at the top of the panel"),
]

im = Image.open(IMG).convert("RGB")
draw = ImageDraw.Draw(im)
print(f"image size = {im.size}\n")
for label, desc in TARGETS:
    try:
        x, y, _ = localize(IMG, desc)
        print(f"  {label:9s} -> ({x:3d},{y:3d})")
        draw.ellipse([x - 9, y - 9, x + 9, y + 9], outline=(255, 0, 0), width=3)
        draw.text((x + 11, y - 7), label, fill=(255, 30, 30))
    except Exception as e:
        print(f"  {label:9s} -> FAILED: {e}")

out = "/tmp/grounding_check.png"
im.save(out)
print(f"\nsaved {out}")
