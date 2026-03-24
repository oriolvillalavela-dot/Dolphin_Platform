from PIL import Image
from pathlib import Path

src = Path("static/logo.png")      # adjust path if needed
img = Image.open(src).convert("RGBA")
w, h = img.size

for s in (2, 3, 4):                # make @2x, @3x, @4x
    out = Path(f"static/logo@{s}x.png")
    img.resize((w*s, h*s), Image.LANCZOS).save(out, optimize=True)
    print("wrote", out)
