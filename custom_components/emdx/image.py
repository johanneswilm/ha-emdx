"""Image preparation for EMDX e-paper panels.

The EM32DX is a 2560x1440 colour e-paper panel. It renders JPEG best when the
file already matches its native resolution, so we letterbox/resize client-side.
E-paper has no smooth gradients: posterize + sharpen keeps text crisp.
"""
from __future__ import annotations

import io

from PIL import Image, ImageFilter, ImageOps

PANEL_W = 2560
PANEL_H = 1440

def prepare_image(raw: bytes, resize: bool = True, quality: int = 90) -> bytes:
    """Convert arbitrary image bytes to a panel-friendly JPEG."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)

    if resize and img.size != (PANEL_W, PANEL_H):
        img = img.convert("RGB")
        img.thumbnail((PANEL_W, PANEL_H), Image.LANCZOS)
        canvas = Image.new("RGB", (PANEL_W, PANEL_H), "white")
        offx = (PANEL_W - img.width) // 2
        offy = (PANEL_H - img.height) // 2
        canvas.paste(img, (offx, offy))
        img = canvas
    else:
        img = img.convert("RGB")

    # Mild posterize (8 levels/channel) + unsharp: e-paper dithering is coarse,
    # this avoids muddy banding on photos and keeps edges readable.
    img = img.quantize(colors=128, dither=Image.Dither.FLOYDSTEINBERG)
    img = img.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.2, percent=80))

    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()
