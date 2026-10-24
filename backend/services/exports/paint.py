"""Paint a chart scene: as vector graphics on a ReportLab canvas, or as a PNG.

Both painters read the same scene, so a chart cannot look different in a PDF
and a Word document. The PNG exists because Word cannot take ReportLab's
drawings, and ReportLab's own bitmap renderer needs a Cairo binding the API
image does not carry, whereas Pillow is already there.
"""

from __future__ import annotations

import re
from functools import cache
from io import BytesIO
from typing import Any

from . import theme
from .charts import LineShape, PolygonShape, RectShape, Scene, TextShape

# Drawn at SUPERSAMPLE times the output size and downsampled, which is what
# gives Pillow's aliased polygons smooth edges. OUTPUT_SCALE pixels per point
# is 144 dpi, sharp on screen and in print at the width a chart is placed.
SUPERSAMPLE = 4
OUTPUT_SCALE = 2


# -- ReportLab -----------------------------------------------------------------


def paint_canvas(canvas: Any, scene: Scene) -> None:
    """Draw ``scene`` with its top left at the canvas origin's top left.

    The canvas origin is the flowable's bottom left, so y is flipped.
    """
    from reportlab.lib.colors import HexColor

    height = scene.height
    for shape in scene.shapes:
        if isinstance(shape, RectShape):
            canvas.setFillColor(HexColor(shape.fill))
            y = height - shape.y - shape.height
            if shape.radius:
                canvas.roundRect(
                    shape.x,
                    y,
                    shape.width,
                    shape.height,
                    shape.radius,
                    stroke=0,
                    fill=1,
                )
            else:
                canvas.rect(shape.x, y, shape.width, shape.height, stroke=0, fill=1)
        elif isinstance(shape, PolygonShape):
            _canvas_polygon(canvas, shape, height)
        elif isinstance(shape, LineShape):
            _canvas_line(canvas, shape, height)
        elif isinstance(shape, TextShape):
            _canvas_text(canvas, shape, height)


def _canvas_polygon(canvas: Any, shape: PolygonShape, height: float) -> None:
    from reportlab.lib.colors import HexColor

    path = canvas.beginPath()
    first, *rest = shape.points
    path.moveTo(first[0], height - first[1])
    for x, y in rest:
        path.lineTo(x, height - y)
    path.close()
    canvas.setFillColor(HexColor(shape.fill))
    if shape.stroke:
        canvas.setStrokeColor(HexColor(shape.stroke))
        canvas.setLineWidth(shape.stroke_width)
    canvas.drawPath(path, stroke=1 if shape.stroke else 0, fill=1)


def _canvas_line(canvas: Any, shape: LineShape, height: float) -> None:
    from reportlab.lib.colors import HexColor

    canvas.setStrokeColor(HexColor(shape.color))
    canvas.setLineWidth(shape.width)
    canvas.setDash([2, 2] if shape.dashed else [])
    canvas.line(shape.x1, height - shape.y1, shape.x2, height - shape.y2)
    canvas.setDash([])


def _canvas_text(canvas: Any, shape: TextShape, height: float) -> None:
    from reportlab.lib.colors import HexColor

    theme.register_fonts()
    canvas.setFont(shape.font, shape.size)
    canvas.setFillColor(HexColor(shape.color))
    y = height - shape.y
    if shape.anchor == "end":
        canvas.drawRightString(shape.x, y, shape.text)
    elif shape.anchor == "middle":
        canvas.drawCentredString(shape.x, y, shape.text)
    else:
        canvas.drawString(shape.x, y, shape.text)


# -- Pillow ----------------------------------------------------------------------

_PIL_ANCHORS = {"start": "ls", "middle": "ms", "end": "rs"}
# Pillow's raqm layout takes one paragraph and fails outright on a paragraph
# separator, so a speaker's name holding a carriage return would fail the Word
# export. A label is laid out as one line, so each separator draws as a space.
_PARAGRAPH_BREAKS = re.compile("[\n\r\x1c-\x1e\x85\u2029]")


@cache
def _pil_font(name: str, pixel_size: int) -> Any:
    from PIL import ImageFont

    return ImageFont.truetype(str(theme.font_path(name)), pixel_size)


def _pil_rect(draw: Any, shape: RectShape, scale: float) -> None:
    box = (
        shape.x * scale,
        shape.y * scale,
        (shape.x + shape.width) * scale,
        (shape.y + shape.height) * scale,
    )
    if box[2] - box[0] < 1 or box[3] - box[1] < 1:
        return
    radius = min(shape.radius * scale, (box[2] - box[0]) / 2, (box[3] - box[1]) / 2)
    draw.rounded_rectangle(box, radius=radius, fill=shape.fill)


def _pil_polygon(draw: Any, shape: PolygonShape, scale: float) -> None:
    points = [(x * scale, y * scale) for x, y in shape.points]
    draw.polygon(points, fill=shape.fill)
    if shape.stroke:
        draw.line(
            points + points[:1],
            fill=shape.stroke,
            width=max(1, round(shape.stroke_width * scale)),
        )


def _pil_line(draw: Any, shape: LineShape, scale: float) -> None:
    width = max(1, round(shape.width * scale))
    start = (shape.x1 * scale, shape.y1 * scale)
    end = (shape.x2 * scale, shape.y2 * scale)
    if not shape.dashed:
        draw.line([start, end], fill=shape.color, width=width)
        return
    # Horizontal gridlines only, which is all the charts draw dashed.
    dash = 2 * scale
    x = start[0]
    while x < end[0]:
        draw.line(
            [(x, start[1]), (min(x + dash, end[0]), start[1])],
            fill=shape.color,
            width=width,
        )
        x += dash * 2


def _pil_text(draw: Any, shape: TextShape, scale: float) -> None:
    font = _pil_font(shape.font, max(1, round(shape.size * scale)))
    draw.text(
        (shape.x * scale, shape.y * scale),
        _PARAGRAPH_BREAKS.sub(" ", shape.text),
        fill=shape.color,
        font=font,
        anchor=_PIL_ANCHORS[shape.anchor],
    )


def paint_png(scene: Scene) -> bytes:
    from PIL import Image, ImageDraw

    scale = SUPERSAMPLE * OUTPUT_SCALE
    size = (max(1, round(scene.width * scale)), max(1, round(scene.height * scale)))
    image = Image.new("RGB", size, "#ffffff")
    draw = ImageDraw.Draw(image)
    for shape in scene.shapes:
        if isinstance(shape, RectShape):
            _pil_rect(draw, shape, scale)
        elif isinstance(shape, PolygonShape):
            _pil_polygon(draw, shape, scale)
        elif isinstance(shape, LineShape):
            _pil_line(draw, shape, scale)
        elif isinstance(shape, TextShape):
            _pil_text(draw, shape, scale)
    output = image.resize(
        (
            max(1, round(scene.width * OUTPUT_SCALE)),
            max(1, round(scene.height * OUTPUT_SCALE)),
        ),
        Image.Resampling.LANCZOS,
    )
    buffer = BytesIO()
    output.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
