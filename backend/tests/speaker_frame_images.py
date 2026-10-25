"""Synthetic screens for the screen study's tests (#653).

Drawn at run time: a flat page with a 2 x 2 grid of tiles, one outlined, the
shape of an empty meeting gallery. No image is committed and none is a real
screen.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw


def draw_frame(
    *, width: int = 1280, height: int = 720, highlight: int = 0, quality: int = 80
) -> bytes:
    """A synthetic screen: a grey page with a 2 x 2 grid, one tile outlined."""
    image = Image.new("RGB", (width, height), (32, 33, 36))
    draw = ImageDraw.Draw(image)
    tile_w, tile_h = width // 2 - 30, height // 2 - 30
    for index in range(4):
        left = 20 + (index % 2) * (tile_w + 20)
        top = 20 + (index // 2) * (tile_h + 20)
        draw.rectangle((left, top, left + tile_w, top + tile_h), fill=(60, 64, 67))
        if index == highlight:
            draw.rectangle(
                (left, top, left + tile_w, top + tile_h),
                outline=(138, 180, 248),
                width=4,
            )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
