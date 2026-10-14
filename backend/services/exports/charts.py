"""The Analytics tab's charts, laid out once as shapes for any painter.

A chart is described by a spec (what to plot) and laid out into a ``Scene``:
rectangles, polygons, lines and text in points, with the origin at the top
left. The PDF renderer paints a scene as vector graphics and the DOCX renderer
paints the same scene into a PNG, so the two formats cannot draw a chart
differently.

The rules from docs/DEVELOPMENT.md (Charts) hold here too: a series that is a
person takes that person's colour, every series is labelled directly, and the
talk-share bars are scaled to 100% rather than to the longest bar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Union

from . import theme
from .formatting import format_timestamp

Anchor = Literal["start", "middle", "end"]


@dataclass(frozen=True)
class RectShape:
    x: float
    y: float
    width: float
    height: float
    fill: str
    radius: float = 0.0


@dataclass(frozen=True)
class PolygonShape:
    points: tuple[tuple[float, float], ...]
    fill: str
    stroke: str | None = None
    stroke_width: float = 0.0


@dataclass(frozen=True)
class LineShape:
    x1: float
    y1: float
    x2: float
    y2: float
    color: str
    width: float = 0.5
    dashed: bool = False


@dataclass(frozen=True)
class TextShape:
    """Text positioned by its baseline."""

    x: float
    y: float
    text: str
    size: float
    color: str
    font: str = theme.FONT_REGULAR
    anchor: Anchor = "start"


Shape = Union[RectShape, PolygonShape, LineShape, TextShape]


@dataclass(frozen=True)
class Scene:
    width: float
    height: float
    shapes: tuple[Shape, ...]
    description: str


# -- specs -------------------------------------------------------------------


@dataclass(frozen=True)
class ShareBar:
    label: str
    share: float
    share_text: str
    detail_text: str
    color: str


@dataclass(frozen=True)
class TalkShareChart:
    bars: tuple[ShareBar, ...]


@dataclass(frozen=True)
class TimelineSeries:
    label: str
    color: str
    values_ms: tuple[int, ...]


@dataclass(frozen=True)
class TimelineChart:
    starts_ms: tuple[int, ...]
    series: tuple[TimelineSeries, ...]


@dataclass(frozen=True)
class DensityChart:
    bins: tuple[float, ...]
    color: str = theme.CHART_OTHER


Chart = Union[TalkShareChart, TimelineChart, DensityChart]


# -- layout ------------------------------------------------------------------

LABEL_SIZE = 8.5
AXIS_SIZE = 7.0
_ELLIPSIS = "…"


def fit_text(text: str, font: str, size: float, max_width: float) -> str:
    """The text, cut with an ellipsis if it would overrun ``max_width``."""
    if theme.text_width(text, font, size) <= max_width:
        return text
    for end in range(len(text) - 1, 0, -1):
        candidate = text[:end].rstrip() + _ELLIPSIS
        if theme.text_width(candidate, font, size) <= max_width:
            return candidate
    return _ELLIPSIS


def _dot(x: float, centre_y: float, color: str, diameter: float = 6.0) -> RectShape:
    return RectShape(
        x=x,
        y=centre_y - diameter / 2,
        width=diameter,
        height=diameter,
        fill=color,
        radius=diameter / 2,
    )


def _share_row(bar: ShareBar, top: float, width: float) -> list[Shape]:
    detail_width = theme.text_width(bar.detail_text, theme.FONT_REGULAR, LABEL_SIZE)
    share_width = theme.text_width(bar.share_text, theme.FONT_SEMIBOLD, LABEL_SIZE)
    name_room = width - 10 - detail_width - share_width - 16
    baseline = top + 9
    fill_width = max(bar.share * width, 1.0 if bar.share > 0 else 0.0)
    shapes: list[Shape] = [
        _dot(0, top + 6, bar.color),
        TextShape(
            10,
            baseline,
            fit_text(bar.label, theme.FONT_REGULAR, LABEL_SIZE, name_room),
            LABEL_SIZE,
            theme.FOREGROUND,
        ),
        TextShape(
            width - detail_width - 6,
            baseline,
            bar.share_text,
            LABEL_SIZE,
            theme.FOREGROUND,
            font=theme.FONT_SEMIBOLD,
            anchor="end",
        ),
        TextShape(
            width, baseline, bar.detail_text, LABEL_SIZE, theme.HELPER, anchor="end"
        ),
        RectShape(0, top + 13, width, 5, theme.INSET, radius=2.5),
    ]
    if fill_width:
        shapes.append(RectShape(0, top + 13, fill_width, 5, bar.color, radius=2.5))
    return shapes


def layout_talk_share(chart: TalkShareChart, width: float) -> Scene:
    row_height = 26.0
    shapes: list[Shape] = []
    for index, bar in enumerate(chart.bars):
        shapes.extend(_share_row(bar, index * row_height, width))
    description = "; ".join(
        f"{bar.label} {bar.share_text} ({bar.detail_text})" for bar in chart.bars
    )
    return Scene(
        width=width,
        height=max(row_height * len(chart.bars) - 8, 0),
        shapes=tuple(shapes),
        description=f"Share of speaking time: {description}.",
    )


_TICK_STEPS_S = (5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600, 900, 1800)


def _y_scale(peak_ms: float) -> tuple[float, int]:
    """The axis top in ms and the tick step in seconds, at most four ticks."""
    peak_s = max(peak_ms / 1000, 1.0)
    step = next((s for s in _TICK_STEPS_S if peak_s / s <= 4), _TICK_STEPS_S[-1])
    return math.ceil(peak_s / step) * step * 1000.0, step


def _stack(chart: TimelineChart) -> list[tuple[list[float], list[float]]]:
    """Each series' lower and upper edge across the buckets."""
    lower = [0.0] * len(chart.starts_ms)
    edges = []
    for series in chart.series:
        upper = [base + value for base, value in zip(lower, series.values_ms)]
        edges.append((lower, upper))
        lower = upper
    return edges


@dataclass(frozen=True)
class _Plot:
    left: float
    top: float
    width: float
    height: float
    y_top_ms: float
    count: int

    def x(self, index: int) -> float:
        return self.left + self.width * index / max(self.count - 1, 1)

    def y(self, value_ms: float) -> float:
        return self.top + self.height * (1 - value_ms / self.y_top_ms)


def _timeline_axes(plot: _Plot, step_s: int, starts_ms: tuple[int, ...]) -> list[Shape]:
    shapes: list[Shape] = []
    ticks = int(plot.y_top_ms / 1000 / step_s)
    for tick in range(ticks + 1):
        value = tick * step_s * 1000
        y = plot.y(value)
        shapes.append(
            LineShape(
                plot.left, y, plot.left + plot.width, y, theme.HAIRLINE, dashed=True
            )
        )
        shapes.append(
            TextShape(
                plot.left - 4,
                y + 2.5,
                f"{tick * step_s}s",
                AXIS_SIZE,
                theme.FAINT,
                anchor="end",
            )
        )
    label_count = min(5, len(starts_ms))
    for position in range(label_count):
        index = round(position * (len(starts_ms) - 1) / max(label_count - 1, 1))
        anchor: Anchor = (
            "start"
            if position == 0
            else "end"
            if position == label_count - 1
            else "middle"
        )
        shapes.append(
            TextShape(
                plot.x(index),
                plot.top + plot.height + 10,
                format_timestamp(starts_ms[index]),
                AXIS_SIZE,
                theme.FAINT,
                anchor=anchor,
            )
        )
    return shapes


def _legend(
    series: tuple[TimelineSeries, ...], top: float, width: float
) -> tuple[list[Shape], float]:
    """Direct labels for the bands, wrapped to the chart width."""
    shapes: list[Shape] = []
    x, y = 0.0, top
    for item in series:
        label = fit_text(item.label, theme.FONT_REGULAR, LABEL_SIZE, width / 2)
        item_width = 10 + theme.text_width(label, theme.FONT_REGULAR, LABEL_SIZE) + 12
        if x and x + item_width > width:
            x, y = 0.0, y + 13
        shapes.append(_dot(x, y - 3, item.color))
        shapes.append(TextShape(x + 9, y, label, LABEL_SIZE, theme.MUTED))
        x += item_width
    return shapes, y + 4


def layout_timeline(chart: TimelineChart, width: float) -> Scene:
    edges = _stack(chart)
    peak = max(edges[-1][1]) if edges else 0.0
    y_top, step = _y_scale(peak)
    plot = _Plot(
        left=30,
        top=4,
        width=width - 30,
        height=120,
        y_top_ms=y_top,
        count=len(chart.starts_ms),
    )

    shapes = _timeline_axes(plot, step, chart.starts_ms)
    for series, (lower, upper) in zip(chart.series, edges):
        top_edge = [(plot.x(i), plot.y(value)) for i, value in enumerate(upper)]
        bottom_edge = [
            (plot.x(i), plot.y(value)) for i, value in reversed(list(enumerate(lower)))
        ]
        # A white seam between bands keeps two neighbours from reading as one
        # shape, as the 2px surface-coloured stroke does in the app.
        shapes.append(
            PolygonShape(
                tuple(top_edge + bottom_edge),
                series.color,
                stroke="#ffffff",
                stroke_width=0.8,
            )
        )
    legend, bottom = _legend(chart.series, plot.top + plot.height + 26, width)
    shapes.extend(legend)
    return Scene(
        width=width,
        height=bottom,
        shapes=tuple(shapes),
        description="Speaking time per speaker across the meeting.",
    )


def layout_density(chart: DensityChart, width: float) -> Scene:
    strip_height = 26.0
    count = max(len(chart.bins), 1)
    gap = 1.0
    bar_width = (width - gap * (count - 1)) / count
    shapes: list[Shape] = []
    for index, value in enumerate(chart.bins):
        if value <= 0:
            continue
        height = max(value * strip_height, 0.75)
        shapes.append(
            RectShape(
                index * (bar_width + gap),
                strip_height - height,
                bar_width,
                height,
                chart.color,
                radius=0.6,
            )
        )
    shapes.append(TextShape(0, strip_height + 9, "Start", AXIS_SIZE, theme.FAINT))
    shapes.append(
        TextShape(width, strip_height + 9, "End", AXIS_SIZE, theme.FAINT, anchor="end")
    )
    return Scene(
        width=width,
        height=strip_height + 11,
        shapes=tuple(shapes),
        description="Where overlapping speech clustered across the meeting.",
    )


def layout(chart: object, width: float) -> Scene:
    if isinstance(chart, TalkShareChart):
        return layout_talk_share(chart, width)
    if isinstance(chart, TimelineChart):
        return layout_timeline(chart, width)
    if isinstance(chart, DensityChart):
        return layout_density(chart, width)
    raise TypeError(f"Unknown chart spec: {type(chart).__name__}")
