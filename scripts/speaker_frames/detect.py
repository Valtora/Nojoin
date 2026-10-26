"""Which meeting tile is lit in each frame, from the active-speaker ring alone.

numpy and scipy only, no model. Over one meeting's frames:

1. Rings of any colour are found in a sample of frames: thin lines that
   stand out of a top-hat on chroma, two straight sides of the same height
   a tile's width apart, closed by a top and a bottom, with an empty middle
   and one colour all round. Their hues, counted by slot, calibrate the
   meeting's ring colour: the histogram peak within 20 degrees of the
   platform's prior colour, or the prior itself when too few are found.
2. Rings are found again in every frame on the projection onto that colour,
   which keeps faint rings that chroma loses. Rings at the same place form
   a candidate slot, whose sides are then put on the ridge the rings share.
3. Every candidate is scored in every frame: the weakest side's ridge above
   the bands beside it. A slot is lit where its scores fall in the upper of
   two well-separated groups, so a ring too faint to find on its own still
   reads wherever its slot is known.
4. A slot must be lit in at least 3 frames and not in nearly all of them,
   match the ring colour, sit on a dark stage, stand clear of both sides as
   a ring does (not as JPEG's overshoot along a bright tile or the edge of a
   filled icon does), not nest in another, not stay lit through other
   tiles' turns as an outline in a shared slide does, and have a tile of its
   size beside it or a tile's shape.
5. Places on each gallery's lattice, a known tile's size apart, are scored
   too, for tiles whose ring was never found on its own.

Lit slots then split the meeting into layouts: a slot lit on top of a slot of
the current layout starts a new one, and a layout that returns keeps its
number. A layout's meeting window is the union of its slots.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

import numpy as np
from scipy import ndimage

Box = tuple[float, float, float, float]

# Each app's ring colour from its public look. Calibration starts here and
# moves to whatever the meeting's own rings show.
PRIORS: dict[str, tuple[int, int, int]] = {
    "teams": (127, 133, 245),
    "zoom": (35, 217, 89),
    "meet": (138, 180, 248),
}


@dataclass(frozen=True)
class Params:
    tophat: int = 13
    chroma_threshold: float = 40.0
    project_threshold: float = 12.0
    min_width: int = 40
    min_height: int = 22
    min_run: int = 13
    max_tile_width: int = 900
    side_cover: float = 0.6
    aspect: tuple[float, float] = (0.9, 2.6)
    tile_aspect: tuple[float, float] = (1.1, 2.1)
    line_cover: float = 0.3
    calibration_sightings: int = 6
    furniture: float = 0.9
    lone_width: float = 0.2
    prominence: float = 0.3
    static_frames: int = 6
    interior: float = 0.35
    max_thickness: float = 10.0
    match_iou: float = 0.7
    min_sightings: int = 3
    hue_window: float = 20.0
    hue_tolerance: float = 35.0
    size_tolerance: float = 0.12
    colit_limit: float = 0.5
    stage_luma: float = 140.0
    lit_floor: float = 1.5
    lit_separation: float = 3.0
    lit_contrast: float = 4.0
    calibration_frames: int = 1500
    settle_frames: int = 8
    grid_reach: int = 7
    grid_slack: int = 7
    # How far a rounded corner may reach, and how far around a side its
    # ridge is looked for.
    corner_cap: float = 24.0
    side_reach: float = 30.0
    refine_reach: int = 6
    settle_reach: int = 8
    grid_strength: float = 0.5
    side_chroma: float = 4.0
    side_hue_spread: float = 60.0


DEFAULT = Params()
# The pixel sizes above were tuned on frames 1280 px on the long edge;
# frames of another size, such as the sampler's 1920 px, scale them.
REFERENCE_EDGE = 1280


def scaled(params: Params, long_edge: int) -> Params:
    """``params`` with every pixel size scaled to frames of ``long_edge``."""
    factor = long_edge / REFERENCE_EDGE
    if abs(factor - 1) < 1e-6:
        return params
    tophat = int(round(params.tophat * factor)) | 1
    return replace(
        params,
        tophat=tophat,
        min_width=round(params.min_width * factor),
        min_height=round(params.min_height * factor),
        min_run=int(round(params.min_run * factor)) | 1,
        max_tile_width=round(params.max_tile_width * factor),
        max_thickness=params.max_thickness * factor,
        corner_cap=params.corner_cap * factor,
        side_reach=params.side_reach * factor,
        refine_reach=round(params.refine_reach * factor),
        settle_reach=round(params.settle_reach * factor),
    )


class Frames(Protocol):
    """A meeting's frames in time order, decoded on demand."""

    def __len__(self) -> int: ...

    def __getitem__(self, index: int) -> np.ndarray: ...


@dataclass(frozen=True)
class Ring:
    box: Box
    # What the ring adds to the pixels beside it: its colour's direction.
    rgb: tuple[float, float, float]
    thickness: float
    outer_luma: float
    # The chroma its weakest side adds.
    strength: float = 0.0


@dataclass
class Slot:
    box: Box
    sightings: list[int] = field(default_factory=list)
    rings: list[Ring] = field(default_factory=list)
    reference: float = 0.0
    threshold: float = 0.0
    settled_width: float | None = None
    # For a place on a gallery's lattice: its neighbours' typical lit score.
    reference_hint: float = 0.0
    prominence: float = 1.0

    @property
    def rgb(self) -> np.ndarray:
        if not self.rings:
            return np.zeros(3)
        return np.median(np.array([r.rgb for r in self.rings]), axis=0)

    @property
    def thickness(self) -> float:
        if self.settled_width is not None:
            return self.settled_width
        return float(np.median([r.thickness for r in self.rings]))


@dataclass(frozen=True)
class FrameResult:
    lit: tuple[int, ...]
    layout: int | None


@dataclass
class Layout:
    slots: set[int]
    start: int
    end: int

    def window(self, slots: Sequence[Slot]) -> Box:
        boxes = [slots[s].box for s in self.slots]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )


@dataclass
class MeetingResult:
    colour: tuple[float, float, float]
    calibrated: bool
    slots: list[Slot]
    layouts: list[Layout]
    frames: list[FrameResult]

    def lit_boxes(self, index: int) -> list[Box]:
        return [self.slots[s].box for s in self.frames[index].lit]

    def layout_ids(self) -> dict[int, int]:
        """Each layout's number, the same for layouts that share a slot: the
        gallery after a screen share is the gallery from before it."""
        parent = list(range(len(self.layouts)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        owner: dict[int, int] = {}
        for index, layout in enumerate(self.layouts):
            for slot in layout.slots:
                if slot in owner:
                    parent[find(index)] = find(owner[slot])
                owner.setdefault(slot, index)
        roots = sorted({find(i) for i in range(len(self.layouts))})
        number = {root: n for n, root in enumerate(roots)}
        return {i: number[find(i)] for i in range(len(self.layouts))}


def nearest_platform(colour: Sequence[float]) -> str:
    """The app whose ring colour is nearest in hue, as a check on the tag."""
    return min(PRIORS, key=lambda p: hue_distance(hue(PRIORS[p]), hue(colour)))


def hue(rgb: Sequence[float]) -> float:
    """The angle of a colour's chroma, in degrees.

    It takes a colour or the difference between two, since the grey part of
    either drops out.
    """
    r, g, b = (float(c) for c in rgb)
    return math.degrees(math.atan2(math.sqrt(3) / 2 * (g - b), r - (g + b) / 2)) % 360.0


def chroma_size(rgb: Sequence[float]) -> float:
    vector = np.asarray(rgb, dtype=np.float64)
    return float(np.linalg.norm(vector - vector.mean()))


def hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def overlaps(a: Box, b: Box) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _chroma(image: np.ndarray) -> np.ndarray:
    r, g, b = image[..., 0], image[..., 1], image[..., 2]
    return np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)


def _direction(rgb: Sequence[float]) -> np.ndarray:
    vector = np.asarray(rgb, dtype=np.float32)
    vector = vector - vector.mean()
    norm = float(np.linalg.norm(vector))
    if norm < 1e-6:
        raise ValueError("a ring colour needs some chroma")
    return vector / norm


def project(image: np.ndarray, rgb: Sequence[float]) -> np.ndarray:
    """Each pixel's chroma along the ring colour's direction.

    The direction sums to zero, so the grey part of a pixel drops out.
    """
    u = _direction(rgb)
    return image[..., 0] * u[0] + image[..., 1] * u[1] + image[..., 2] * u[2]


def _luma(image: np.ndarray) -> np.ndarray:
    return image @ np.array([0.299, 0.587, 0.114], dtype=np.float32)


def find_rings(
    image: np.ndarray, colour: Sequence[float] | None, params: Params = DEFAULT
) -> list[Ring]:
    """Thin rounded-rectangle outlines of tile size in one frame.

    Faint rings break at their rounded corners, so the shape is not found as
    one connected outline. A tile's left and right sides are straight runs
    of the same height, which text and most interface lines are not, so
    sides are paired first and the top and bottom are then looked for
    between them.
    """
    pixels = image.astype(np.float32)
    if colour is None:
        signal, threshold = _chroma(pixels), params.chroma_threshold
    else:
        signal, threshold = project(pixels, colour), params.project_threshold
    thin = top_hat(signal, params.tophat)
    mask = _thin_mask(thin, threshold)
    upright = np.ascontiguousarray(mask.T)
    lines = (_bridge(mask), np.ascontiguousarray(_bridge(upright).T))
    sides = _runs(upright, params)
    found: list[Ring] = []
    for left, right in _side_pairs(sides, params):
        # Of the boxes two sides close, the one whose weakest side is
        # strongest: a line inside the tile can close a smaller box too.
        closed = []
        for rough in _close(lines, left, right, params):
            refined = _refine(thin, rough, params)
            if refined is not None and _plausible(refined[0], params):
                ring = _check(pixels, (signal, mask), refined, params)
                if ring is not None:
                    closed.append(ring)
        if closed:
            found.append(max(closed, key=lambda r: r.strength))
    # Where boxes overlap, as when a neighbour's edge stands in for a side,
    # the strongest ring is the tile's.
    rings: list[Ring] = []
    for ring in sorted(found, key=lambda r: -r.strength):
        if not any(iou(ring.box, r.box) > 0.5 for r in rings):
            rings.append(ring)
    return rings


def _corner(width: float, height: float, params: Params, share: float = 0.2) -> int:
    """How much of each end of a side to leave out for the rounded corner."""
    return int(min(params.corner_cap, max(3.0, min(width, height) * share)))


def _refine(signal: np.ndarray, rough: Box, params: Params) -> tuple[Box, float] | None:
    """Put each side on its ridge, the line through the ring's middle.

    The rough box is a few pixels out, more so for thick or blurred rings.
    Returns the box through the four ridges and the ring's width there.
    """
    x0, y0, x1, y1 = (int(round(v)) for v in rough)
    corner = _corner(x1 - x0, y1 - y0, params)
    reach = params.refine_reach
    lines, widths = [], []
    for edge, horizontal in ((y0, True), (y1 - 1, True), (x0, False), (x1 - 1, False)):
        lo = max(0, edge - reach)
        if horizontal:
            profile = signal[lo : edge + reach + 1, x0 + corner : x1 - corner].mean(
                axis=1
            )
        else:
            profile = signal[y0 + corner : y1 - corner, lo : edge + reach + 1].mean(
                axis=0
            )
        found = _ridge_line(profile)
        if found is None:
            return None
        lines.append(lo + found[0])
        widths.append(found[1])
    top, bottom, left, right = lines
    if right - left < 8 or bottom - top < 8:
        return None
    return (left, top, right, bottom), float(np.median(widths))


def _ridge_line(profile: np.ndarray) -> tuple[float, float] | None:
    """The centre and width of the strongest peak across a ring's side."""
    if not profile.size:
        return None
    peak = int(np.argmax(profile))
    height = float(profile[peak])
    if height <= 0:
        return None
    half = height / 2
    low, high = peak, peak
    while low > 0 and profile[low - 1] >= half:
        low -= 1
    while high + 1 < profile.size and profile[high + 1] >= half:
        high += 1
    weights = profile[low : high + 1] - half
    centre = low + float(np.average(np.arange(high - low + 1), weights=weights + 1e-6))
    return centre, float(high - low + 1)


# A straight run of ring pixels: start and end along it, then the first and
# last-plus-one pixel across it.
Run = tuple[int, int, int, int]


def _slide(values: np.ndarray, size: int, axis: int, op: np.ufunc) -> np.ndarray:
    """``op`` over a centred window of odd ``size`` along one axis.

    Built from shifted copies, doubling the window each time, which is many
    times faster here than scipy's one-dimensional filters. Edges repeat the
    border value.
    """
    half = size // 2
    pad = [(0, 0), (0, 0)]
    pad[axis] = (half, half)
    out = np.pad(values, pad, mode="edge")
    span = 1
    while span * 2 <= size:
        out = op(_take(out, 0, -span, axis), _take(out, span, None, axis))
        span *= 2
    length = values.shape[axis]
    return op(
        _take(out, 0, length, axis), _take(out, size - span, size - span + length, axis)
    )


def _take(values: np.ndarray, start: int, stop: int | None, axis: int) -> np.ndarray:
    return values[start:stop] if axis == 0 else values[:, start:stop]


def top_hat(signal: np.ndarray, size: int) -> np.ndarray:
    """What is brighter than its surroundings and narrower than ``size``."""
    opened = _slide(_slide(signal, size, 0, np.minimum), size, 1, np.minimum)
    opened = _slide(_slide(opened, size, 0, np.maximum), size, 1, np.maximum)
    return signal - opened


def _thin_mask(thin: np.ndarray, threshold: float) -> np.ndarray:
    """Pixels on thin bright structures, with hysteresis: pixels over half
    the threshold count when they touch one over it, so a faint side of a
    ring stays joined to a stronger one."""
    weak = thin >= threshold / 2
    labels, count = ndimage.label(weak, structure=np.ones((3, 3), bool))
    keep = np.zeros(count + 1, bool)
    keep[np.unique(labels[thin >= threshold])] = True
    keep[0] = False
    return keep[labels]


def _bridge(mask: np.ndarray) -> np.ndarray:
    """Horizontal lines made whole: a faint line drifts between two rows once
    shrunk, so let it, then close small gaps along it."""
    grown = _slide(mask, 3, 0, np.maximum)
    return _slide(_slide(grown, 5, 1, np.maximum), 5, 1, np.minimum)


def _runs(mask: np.ndarray, params: Params) -> list[Run]:
    """Long horizontal runs; pass the transpose for vertical ones."""
    # Odd window lengths keep each erosion and dilation centred.
    lines = _bridge(mask)
    lines = _slide(
        _slide(lines, params.min_run, 1, np.minimum), params.min_run, 1, np.maximum
    )
    labels, _ = ndimage.label(lines, structure=np.ones((3, 3), bool))
    runs = []
    for index, found in enumerate(ndimage.find_objects(labels), start=1):
        if found is not None:
            runs.extend(_cores(labels[found] == index, mask[found], found, params))
    return runs


def _cores(
    component: np.ndarray,
    original: np.ndarray,
    found: tuple[slice, slice],
    params: Params,
) -> list[Run]:
    """The lines in a shape, without what touches them.

    Meet's disc can touch a ring's top, and a thin stripe in a camera
    picture a few pixels off can merge with a ring's side once the lines
    are grown. Each band of rows that is long in the shape and dense in the
    pixels it was grown from is a line of its own.
    """
    counts = component.sum(axis=1)
    density = (original & component).sum(axis=1) / np.maximum(counts, 1)
    long_enough = (counts >= max(params.min_run, 0.3 * counts.max())) & (density >= 0.4)
    row0, col0 = found[0].start, found[1].start
    runs = []
    for first, last in _bands(long_enough):
        if last - first > params.max_thickness + 4:
            continue
        cols = np.flatnonzero(component[first:last].any(axis=0))
        runs.append(
            (col0 + int(cols[0]), col0 + int(cols[-1]) + 1, row0 + first, row0 + last)
        )
    return runs


def _side_pairs(sides: list[Run], params: Params) -> list[tuple[Run, Run]]:
    """Left and right sides that share a stretch of rows, a tile's width apart.

    Their ends are not trusted: a corner's colour, a name label's edge, or
    the next tile down in the same column can carry a side on.
    """
    ordered = sorted(sides, key=lambda r: r[2])
    pairs = []
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            gap = right[2] - left[3]
            if gap < params.min_width - 8:
                continue
            if gap > params.max_tile_width:
                break
            if min(left[1], right[1]) - max(left[0], right[0]) >= params.min_height:
                pairs.append((left, right))
    return pairs


def _close(
    lines: tuple[np.ndarray, np.ndarray], left: Run, right: Run, params: Params
) -> list[Box]:
    """Tiles between two sides: any top and bottom the ring's line crosses,
    of a tile's shape, with the sides covering the rows between them.

    A side can run on past its tile, into the tiles above and below it in
    the column, and a shirt of the ring's colour can hide part of a bottom,
    so every pair is tried; the caller keeps the strongest ring.
    """
    rows, cols = lines
    x0, x1 = left[2] + 1, right[3] - 1
    reach = int(min(params.side_reach, 0.3 * (x1 - x0))) + int(params.max_thickness) + 2
    corner = _corner(x1 - x0, x1 - x0, params, 0.1)
    y_lo = max(0, min(left[0], right[0]) - reach)
    y_hi = min(rows.shape[0], max(left[1], right[1]) + reach)
    if x1 - x0 - 2 * corner < 4 or y_hi - y_lo < params.min_height:
        return []
    cover = rows[y_lo:y_hi, x0 + corner : x1 - corner].mean(axis=1)
    bands = [(y_lo + a, y_lo + b) for a, b in _bands(cover >= params.line_cover)]
    boxes = []
    for top, _ in bands:
        for _, bottom in bands:
            box = (float(x0), float(top + 1), float(x1), float(bottom - 1))
            if (
                bottom > top
                and _plausible(box, params)
                and _sides_cover(cols, box, (left, right), params)
            ):
                boxes.append(box)
    return boxes


def _bands(flags: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous runs of True: their first index and last-plus-one."""
    padded = np.concatenate([[False], flags, [False]])
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(a), int(b)) for a, b in zip(changes[::2], changes[1::2], strict=True)]


def _plausible(box: Box, params: Params) -> bool:
    width, height = box[2] - box[0], box[3] - box[1]
    if width < params.min_width or height < params.min_height:
        return False
    return params.aspect[0] <= width / height <= params.aspect[1]


def _sides_cover(
    cols: np.ndarray, box: Box, sides: tuple[Run, Run], params: Params
) -> bool:
    x0, y0, x1, y1 = (int(v) for v in box)
    corner = _corner(x1 - x0, y1 - y0, params)
    span = slice(y0 + corner, y1 - corner)
    if span.stop - span.start < 4:
        return False
    for side in sides:
        if cols[span, side[2] : side[3]].any(axis=1).mean() < params.side_cover:
            return False
    return True


def _check(
    pixels: np.ndarray,
    maps: tuple[np.ndarray, np.ndarray],
    refined: tuple[Box, float],
    params: Params,
) -> Ring | None:
    """Keep an outline with an empty middle and one colour all round.

    Each side's colour is what its ridge adds to the plainer of the two
    sides beside it, a few pixels off. Outlines made of text, or of edges in
    a picture, change colour from side to side; a ring does not.
    """
    signal, mask = maps
    box, width = refined
    if width > params.max_thickness:
        return None
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    margin = int(width // 2) + 3
    inner = mask[y0 + margin : y1 - margin, x0 + margin : x1 - margin]
    if inner.size and inner.mean() > params.interior:
        return None
    ridges = _ridge_colours(
        pixels, signal, (x0, y0, x1, y1), (int(max(3, width // 2 + 2)), params)
    )
    if ridges is None:
        return None
    vectors = [
        ridge - min((before, after), key=chroma_size) for ridge, before, after in ridges
    ]
    if min(chroma_size(v) for v in vectors) < params.side_chroma:
        return None
    centre = hue(np.mean([v / chroma_size(v) for v in vectors], axis=0))
    if max(hue_distance(hue(v), centre) for v in vectors) > params.side_hue_spread:
        return None
    height, full_width = mask.shape
    colour = np.mean(vectors, axis=0)
    half = width / 2
    outer = (box[0] - half, box[1] - half, box[2] + half, box[3] + half)
    return Ring(
        box=box,
        rgb=(float(colour[0]), float(colour[1]), float(colour[2])),
        thickness=width,
        outer_luma=_outer_band(pixels, outer, (full_width, height)),
        strength=min(chroma_size(v) for v in vectors),
    )


def _ridge_colours(
    pixels: np.ndarray,
    signal: np.ndarray,
    box: tuple[int, int, int, int],
    where: tuple[int, Params],
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]] | None:
    """Each side's mean colour along its ridge, and a few pixels either side."""
    gap, params = where
    x0, y0, x1, y1 = box
    corner = _corner(x1 - x0, y1 - y0, params)
    along_x = np.arange(x0 + corner, x1 - corner)
    along_y = np.arange(y0 + corner, y1 - corner)
    if len(along_x) < 2 or len(along_y) < 2:
        return None
    vectors = []
    # The top and bottom, then the sides on the transposed frame.
    for image, value, along, edges in (
        (pixels, signal, along_x, (y0, y1 - 1)),
        (pixels.transpose(1, 0, 2), signal.T, along_y, (x0, x1 - 1)),
    ):
        for edge in edges:
            vector = _ridge(image, value, along, (edge, gap))
            if vector is None:
                return None
            vectors.append(vector)
    return vectors


def _ridge(
    pixels: np.ndarray, signal: np.ndarray, along: np.ndarray, where: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    edge, gap = where
    rows = np.arange(max(gap, edge - 3), min(signal.shape[0] - gap, edge + 4))
    if not len(rows):
        return None
    best = rows[np.argmax(signal[rows][:, along], axis=0)]
    ridge = pixels[best, along].mean(axis=0)
    before = pixels[best - gap, along].mean(axis=0)
    after = pixels[best + gap, along].mean(axis=0)
    return ridge, before, after


def _outer_band(pixels: np.ndarray, box: Box, size: tuple[int, int]) -> float:
    """The luminance just outside a ring, on its second-darkest side.

    The apps draw tiles on a dark stage, which shows in the gaps between
    them. An outline on a white page or slide has light all round; one at a
    window's edge has a single dark side.
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    width, height = size
    strips = [
        pixels[max(0, y0 - 6) : max(0, y0 - 1), x0:x1],
        pixels[min(height, y1 + 1) : min(height, y1 + 6), x0:x1],
        pixels[y0:y1, max(0, x0 - 6) : max(0, x0 - 1)],
        pixels[y0:y1, min(width, x1 + 1) : min(width, x1 + 6)],
    ]
    sides = sorted(float(np.median(_luma(s))) for s in strips if s.size)
    return sides[1] if len(sides) > 1 else 0.0


def _cluster(rings: Sequence[Sequence[Ring]], params: Params) -> list[Slot]:
    """Sightings at the same place, frame after frame, become one slot."""
    slots: list[Slot] = []
    for frame, found in enumerate(rings):
        for ring in found:
            best = max(slots, key=lambda s: iou(s.box, ring.box), default=None)
            if best is not None and iou(best.box, ring.box) >= params.match_iou:
                if not best.sightings or best.sightings[-1] != frame:
                    best.sightings.append(frame)
                best.rings.append(ring)
                best.box = _median_box(best.rings)
            else:
                slots.append(Slot(ring.box, [frame], [ring]))
    return slots


def _median_box(rings: Sequence[Ring]) -> Box:
    boxes = np.array([r.box for r in rings[-25:]])
    return tuple(float(v) for v in np.median(boxes, axis=0))  # type: ignore[return-value]


def _valid(
    slots: list[Slot],
    colour: Sequence[float] | None,
    params: Params,
    extent: tuple[int, int],
) -> list[Slot]:
    """Slots that behave as meeting tiles rather than outlines elsewhere.

    ``extent`` is the number of frames and the frame's width.
    """
    frames, width = extent
    seen = [s for s in slots if len(s.sightings) >= params.min_sightings]
    # An outline seen in nearly every frame of the meeting is part of some
    # window's furniture, such as an icon on a button of the ring's colour.
    seen = [s for s in seen if len(s.sightings) < params.furniture * frames]
    if colour is not None:
        seen = [s for s in seen if _hue_matches(s.rgb, colour, params)]
    seen = [
        s
        for s in seen
        if np.median([r.outer_luma for r in s.rings]) <= params.stage_luma
    ]
    seen = [s for s in seen if s.prominence >= params.prominence]
    seen = _no_nesting(seen)
    # Shape first: a small outline that comes and goes elsewhere on screen
    # must not make a tile's long turn look like a slide's outline.
    seen = [
        s
        for s in seen
        if _has_partner(s, seen, params) or _large_tile(s, params, width)
    ]
    seen = [s for s in seen if not _static(s, seen, params)]
    return _apart(seen, params)


def _hue_matches(rgb: Sequence[float], colour: Sequence[float], params: Params) -> bool:
    """Within the tolerance of the ring colour. It is wide: once JPEG has
    quantised its chroma, a thin ring's hue is only known to some 25
    degrees."""
    return hue_distance(hue(rgb), hue(colour)) <= params.hue_tolerance


def _no_nesting(slots: list[Slot]) -> list[Slot]:
    """Of a slot and one inside it, keep the stronger ring.

    Tiles never nest. A box inside a ring, closed by something in the tile,
    shares only two of its sides and scores less; so does a box round two
    neighbouring rings.
    """
    kept = list(slots)
    for slot in sorted(slots, key=lambda s: s.reference):
        if slot in kept and any(
            o is not slot and (_contains(o.box, slot.box) or _contains(slot.box, o.box))
            for o in kept
        ):
            kept.remove(slot)
    return kept


def _large_tile(slot: Slot, params: Params, width: int) -> bool:
    """A tile with no other of its size lit beside it must be a large one,
    as in a gallery of two to four. Outlines in a shared slide and the
    furniture of other windows are smaller."""
    w, h = slot.box[2] - slot.box[0], slot.box[3] - slot.box[1]
    return (
        params.tile_aspect[0] <= w / h <= params.tile_aspect[1]
        and w >= params.lone_width * width
    )


def _contains(outer: Box, inner: Box) -> bool:
    return (
        outer[0] <= inner[0] + 2
        and outer[1] <= inner[1] + 2
        and outer[2] >= inner[2] - 2
        and outer[3] >= inner[3] - 2
    )


def _duty(slot: Slot) -> float:
    """The share of frames, from its first sighting to its last, it is seen in."""
    return len(slot.sightings) / (slot.sightings[-1] - slot.sightings[0] + 1)


def _static(slot: Slot, slots: Sequence[Slot], params: Params) -> bool:
    """Lit throughout while other tiles take turns: an outline in a slide.

    A speaker's one long turn, or a tile Zoom keeps lit through a silence,
    is lit throughout too, but while the others stay dark.
    """
    frames = slot.sightings
    if len(frames) < params.static_frames or _duty(slot) < 0.9:
        return False
    span = frames[-1] - frames[0] + 1
    for other in slots:
        if other is slot:
            continue
        inside = sum(1 for f in other.sightings if frames[0] <= f <= frames[-1])
        if 2 <= inside < span:
            return True
    return False


def _apart(slots: list[Slot], params: Params) -> list[Slot]:
    """Drop slots until none is mostly lit along with others.

    Tiles' rings take turns. The slot lit with others in the largest share
    of its own frames goes first; on a tie, the weaker ring.
    """
    kept = list(slots)
    while True:
        worst: tuple[float, float, Slot] | None = None
        for slot in kept:
            others: set[int] = set()
            for other in kept:
                if other is not slot:
                    others.update(other.sightings)
            together = len(others.intersection(slot.sightings)) / len(slot.sightings)
            key = (together, -slot.reference, slot)
            if together > params.colit_limit and (worst is None or key[:2] > worst[:2]):
                worst = key
        if worst is None:
            return kept
        kept.remove(worst[2])


def _has_partner(slot: Slot, slots: Sequence[Slot], params: Params) -> bool:
    w = slot.box[2] - slot.box[0]
    h = slot.box[3] - slot.box[1]
    for other in slots:
        if other is slot or overlaps(other.box, slot.box):
            continue
        ow, oh = other.box[2] - other.box[0], other.box[3] - other.box[1]
        if (
            abs(ow - w) <= params.size_tolerance * w
            and abs(oh - h) <= params.size_tolerance * h
        ):
            return True
    return False


def calibrate(
    slots: Sequence[Slot], platform: str | None, params: Params = DEFAULT
) -> tuple[float, float, float] | None:
    """The ring colour: the sightings' hue histogram peak nearest the prior."""
    if not slots:
        return None
    weights = np.zeros(36)
    for slot in slots:
        weights[int(hue(slot.rgb) // 10) % 36] += len(slot.sightings)
    smooth = weights + 0.5 * (np.roll(weights, 1) + np.roll(weights, -1))
    centres = np.arange(36) * 10 + 5
    if platform in PRIORS:
        prior = hue(PRIORS[platform])
        smooth = smooth * np.array(
            [hue_distance(c, prior) <= params.hue_window for c in centres]
        )
    if smooth.max() <= 0:
        return None
    peak = float(centres[int(np.argmax(smooth))])
    chosen = [s for s in slots if hue_distance(hue(s.rgb), peak) <= 15]
    if sum(len(s.sightings) for s in chosen) < params.calibration_sightings:
        return None
    rgb = np.average(
        np.array([s.rgb for s in chosen]),
        axis=0,
        weights=[len(s.sightings) for s in chosen],
    )
    return (float(rgb[0]), float(rgb[1]), float(rgb[2]))


def _sample(count: int, limit: int) -> list[int]:
    if count <= limit:
        return list(range(count))
    step = count / limit
    return sorted({int(i * step) for i in range(limit)})


class _Scorer:
    """Ring evidence along a slot's four sides in one frame."""

    def __init__(
        self,
        image: np.ndarray,
        colour: Sequence[float],
        params: Params = DEFAULT,
        slack: int = 2,
    ) -> None:
        # Only what is thin counts, so a neighbour's picture of the ring's
        # colour does not raise the bands beside a side.
        self.signal = top_hat(project(image.astype(np.float32), colour), params.tophat)
        self.height, self.width = self.signal.shape
        self.slack = slack
        self.params = params

    def score(self, box: Box, width: float) -> float:
        """The weakest side's ridge above the higher of the bands beside it.

        The ridge is looked for within ``slack`` pixels of where the slot
        puts it, so a slot a pixel out still reads, and the bands are taken
        beside the ridge found. Standing above both sides, not their mean,
        keeps a hard edge from scoring: JPEG's overshoot along a bright tile
        stands only a little above that tile.
        """
        lx, ty, rx, by = box
        corner = _corner(rx - lx, by - ty, self.params)
        near = math.ceil(max(0.5, width / 2)) + 1
        along_x = (int(lx + corner), int(rx - corner))
        along_y = (int(ty + corner), int(by - corner))
        sides = []
        for line, along, horizontal in (
            (ty, along_x, True),
            (by, along_x, True),
            (lx, along_y, False),
            (rx, along_y, False),
        ):
            value = self._side(int(round(line)), along, horizontal, near)
            if value is None:
                return 0.0
            sides.append(value)
        return min(sides)

    def _side(
        self, line: int, along: tuple[int, int], horizontal: bool, near: int
    ) -> float | None:
        """A side's ridge over the bands beside it; at the frame's edge, over
        the one band that fits."""
        reach = near + 3
        limit = self.height if horizontal else self.width
        start = max(0, line - self.slack - reach)
        stop = min(limit, line + self.slack + reach + 1)
        if along[1] - along[0] < 2 or not start <= line < stop:
            return None
        if horizontal:
            profile = self.signal[start:stop, along[0] : along[1]].mean(axis=1)
        else:
            profile = self.signal[along[0] : along[1], start:stop].mean(axis=0)
        centre = line - start
        low = max(0, centre - self.slack)
        peak_at = low + int(np.argmax(profile[low : centre + self.slack + 1]))
        beside = [
            float(band.mean())
            for band in (
                profile[max(0, peak_at - reach) : max(0, peak_at - near + 1)],
                profile[peak_at + near : peak_at + reach + 1],
            )
            if band.size
        ]
        return float(profile[peak_at]) - max(beside) if beside else None


def _lit_slots(
    scores: np.ndarray, lit: np.ndarray, slots: Sequence[Slot]
) -> tuple[int, ...]:
    """The slots lit in one frame; of two that overlap, the clearer one."""
    found = [
        (float(scores[i]) / max(slots[i].threshold, 1e-6), i)
        for i in np.flatnonzero(lit)
    ]
    chosen: list[int] = []
    for _, index in sorted(found, reverse=True):
        if not any(overlaps(slots[index].box, slots[c].box) for c in chosen):
            chosen.append(int(index))
    return tuple(sorted(chosen))


def _layouts(
    lit: Sequence[tuple[int, ...]], slots: Sequence[Slot]
) -> tuple[list[Layout], list[int | None]]:
    """Split the meeting where a slot lights on top of the current layout's."""
    layouts: list[Layout] = []
    current: Layout | None = None
    confirmed: set[int] = set()
    per_frame: list[int | None] = []
    for frame, found in enumerate(lit):
        for index in found:
            current, confirmed = _place(
                index, frame, (current, confirmed), slots, layouts
            )
        if current is not None:
            current.end = frame
        per_frame.append(len(layouts) - 1 if current is not None else None)
    return layouts, per_frame


def _place(
    index: int,
    frame: int,
    state: tuple[Layout | None, set[int]],
    slots: Sequence[Slot],
    layouts: list[Layout],
) -> tuple[Layout, set[int]]:
    current, confirmed = state
    if current is not None and index in current.slots:
        confirmed.add(index)
        return current, confirmed
    clash = (
        set()
        if current is None
        else {s for s in current.slots if overlaps(slots[s].box, slots[index].box)}
    )
    if current is None or clash & confirmed:
        carried = (
            set() if current is None else {s for s in current.slots if s not in clash}
        )
        current = Layout({index, *carried}, frame, frame)
        layouts.append(current)
        return current, {index}
    current.slots -= clash
    current.slots.add(index)
    confirmed.add(index)
    return current, confirmed


def detect_meeting(
    frames: Frames, platform: str | None, params: Params = DEFAULT
) -> MeetingResult:
    """Lit tiles in every frame of one meeting."""
    if len(frames):
        params = scaled(params, max(frames[0].shape[:2]))
    colour, calibrated = _calibrate_meeting(frames, platform, params)
    rings = [find_rings(frames[i], colour, params) for i in range(len(frames))]
    target = hue(colour)
    candidates = [
        c
        for c in _cluster(rings, params)
        if hue_distance(hue(c.rgb), target) <= params.hue_tolerance
        and np.median([r.outer_luma for r in c.rings]) <= params.stage_luma
    ]
    strict = _sightings(rings, candidates, params)
    _settle(frames, candidates, strict, (colour, params))
    scores = _score_all(frames, candidates, colour, params)
    lit = _read_sightings(candidates, scores, strict, params)
    keep = {
        id(s)
        for s in _valid(candidates, colour, params, (len(frames), frames[0].shape[1]))
    }
    chosen = [i for i, c in enumerate(candidates) if id(c) in keep]
    slots = [candidates[i] for i in chosen]
    scores, lit = scores[:, chosen], lit[:, chosen]
    found = _grid_slots(frames, slots, colour, params)
    if found is not None:
        extra, extra_scores, extra_lit = found
        slots = slots + extra
        scores = np.concatenate([scores, extra_scores], axis=1)
        lit = np.concatenate([lit, extra_lit], axis=1)
    per_frame = [_lit_slots(scores[f], lit[f], slots) for f in range(len(frames))]
    layouts, layout_of = _layouts(per_frame, slots)
    results = [
        FrameResult(found, layout)
        for found, layout in zip(per_frame, layout_of, strict=True)
    ]
    return MeetingResult(colour, calibrated, slots, layouts, results)


def _grid_slots(
    frames: Frames, slots: Sequence[Slot], colour: Sequence[float], params: Params
) -> tuple[list[Slot], np.ndarray, np.ndarray] | None:
    """Tiles whose ring was never found on its own, at the gallery's places.

    Tiles of one size sit on a lattice, so places a known tile's size apart
    are scored too. One becomes a slot when its scores split into lit and
    not lit, at least three times lit, with rings as strong as its
    neighbours' are.
    """
    if not slots or not len(frames):
        return None
    height, width = frames[0].shape[:2]
    places = [
        p
        for p in _lattice(slots, (width, height), params)
        if not any(iou(p.box, s.box) > 0.2 for s in slots)
    ]
    if not places:
        return None
    # A lattice drawn from a few slots can be some pixels out at its far
    # places, so each is first read with room to spare, then settled on the
    # ridge in the frames it was lit, then read again as a slot is.
    loose = _score_all(frames, places, colour, params, slack=params.grid_slack)
    _read_sightings(places, loose, {}, params)
    places = [p for p in places if len(p.sightings) >= params.min_sightings]
    if not places:
        return None
    lit_in: dict[int, set[int]] = {}
    for index, place in enumerate(places):
        for frame in place.sightings[: params.settle_frames]:
            lit_in.setdefault(frame, set()).add(index)
    _settle(frames, places, lit_in, (colour, params))
    scores = _score_all(frames, places, colour, params)
    lit = _read_sightings(places, scores, {}, params)
    keep = []
    for index, place in enumerate(places):
        if len(place.sightings) < params.min_sightings or len(
            place.sightings
        ) >= params.furniture * len(frames):
            continue
        strong = np.median(scores[lit[:, index], index]) if lit[:, index].any() else 0.0
        if strong >= params.grid_strength * place.reference_hint:
            keep.append(index)
    if not keep:
        return None
    chosen = _no_nesting_places([places[i] for i in keep])
    index = [places.index(p) for p in chosen]
    return chosen, scores[:, index], lit[:, index]


def _no_nesting_places(places: list[Slot]) -> list[Slot]:
    kept: list[Slot] = []
    for place in sorted(places, key=lambda p: -len(p.sightings)):
        if not any(overlaps(place.box, k.box) for k in kept):
            kept.append(place)
    return kept


def _lattice(
    slots: Sequence[Slot], size: tuple[int, int], params: Params
) -> list[Slot]:
    """Places on each family of same-sized slots' lattice, inside the frame."""
    places: list[Slot] = []
    for family in _families(slots, params):
        widths = [s.box[2] - s.box[0] for s in family]
        heights = [s.box[3] - s.box[1] for s in family]
        w, h = float(np.median(widths)), float(np.median(heights))
        xs = sorted((s.box[0] + s.box[2]) / 2 for s in family)
        ys = sorted((s.box[1] + s.box[3]) / 2 for s in family)
        pitch_x = _pitch(xs, w)
        pitch_y = _pitch(ys, h)
        if pitch_x is None and pitch_y is None:
            continue
        gap = (pitch_x - w) if pitch_x is not None else (pitch_y - h)  # type: ignore[operator]
        pitch_x = pitch_x if pitch_x is not None else w + gap
        pitch_y = pitch_y if pitch_y is not None else h + gap
        width_ = float(np.median([s.thickness for s in family]))
        strength = float(np.median([s.reference for s in family]))
        for j in range(-params.grid_reach, params.grid_reach + 1):
            cy = ys[0] + j * pitch_y
            for half in range(-2 * params.grid_reach, 2 * params.grid_reach + 1):
                cx = xs[0] + half * pitch_x / 2
                box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                if (
                    box[0] >= 2
                    and box[1] >= 2
                    and box[2] <= size[0] - 2
                    and box[3] <= size[1] - 2
                ):
                    place = Slot(box)
                    place.settled_width = width_
                    place.reference_hint = strength
                    places.append(place)
    return places


def _families(slots: Sequence[Slot], params: Params) -> list[list[Slot]]:
    families: list[list[Slot]] = []
    for slot in sorted(slots, key=lambda s: -(s.box[2] - s.box[0])):
        w, h = slot.box[2] - slot.box[0], slot.box[3] - slot.box[1]
        for family in families:
            fw, fh = (
                family[0].box[2] - family[0].box[0],
                family[0].box[3] - family[0].box[1],
            )
            if (
                abs(fw - w) <= params.size_tolerance * fw
                and abs(fh - h) <= params.size_tolerance * fh
            ):
                family.append(slot)
                break
        else:
            families.append([slot])
    return [f for f in families if len(f) >= 2]


def _pitch(centres: Sequence[float], length: float) -> float | None:
    """The step between neighbouring tiles along one axis.

    Every pair of tiles is some whole number of steps apart; a step is a
    tile's length plus a gap of at most a third of it. Fitting all pairs
    at once keeps the step exact enough to place tiles seven steps away.
    """
    diffs = [
        b - a
        for i, a in enumerate(centres)
        for b in centres[i + 1 :]
        if b - a > 0.5 * length
    ]
    step = length * 1.03
    for _ in range(3):
        fits = [(d, max(1, round(d / step))) for d in diffs]
        fits = [(d, k) for d, k in fits if abs(d / k - step) <= 0.06 * length]
        if not fits:
            return None
        step = sum(d * k for d, k in fits) / sum(k * k for _, k in fits)
    return step if length * 0.98 <= step <= length * 1.35 else None


def _settle(
    frames: Frames,
    slots: Sequence[Slot],
    strict: dict[int, set[int]],
    setting: tuple[Sequence[float], Params],
) -> None:
    """Put each slot's sides on the ridge its rings share.

    A slot's box is the median of its rings' boxes, which a ring or two
    found against the wrong edge can pull a few pixels off. The ridge in the
    side profiles averaged over the frames its ring was found is where the
    ring is.
    """
    colour, params = setting
    wanted: dict[int, list[int]] = {}
    for frame in sorted(strict):
        for index in strict[frame]:
            if len(wanted.setdefault(index, [])) < params.settle_frames:
                wanted[index].append(frame)
    by_frame: dict[int, list[int]] = {}
    for index, chosen in wanted.items():
        for frame in chosen:
            by_frame.setdefault(frame, []).append(index)
    total: dict[int, np.ndarray] = {}
    raw: dict[int, np.ndarray] = {}
    for frame, indices in by_frame.items():
        pixels = frames[frame].astype(np.float32)
        plain = project(pixels, colour)
        thin = top_hat(plain, params.tophat)
        for index in indices:
            total[index] = total.get(index, 0) + _side_profiles(
                thin, slots[index].box, params
            )
            raw[index] = raw.get(index, 0) + _side_profiles(
                plain, slots[index].box, params
            )
    for index, profiles in total.items():
        settled = _from_profiles(profiles, slots[index].box, params.settle_reach)
        if settled is None:
            continue
        slots[index].prominence = _prominence(raw[index], settled[2], settled[1])
        slots[index].box, slots[index].settled_width = settled[0], settled[1]


def _prominence(profiles: np.ndarray, centres: Sequence[float], width: float) -> float:
    """How far each side's ridge stands above the higher of its two sides,
    as a share of its height above the lower; the second-weakest side.

    A ring stands well clear of both. JPEG's overshoot along a bright
    camera tile, or the edge of a filled icon, is a step with a lip.
    """
    near = math.ceil(width / 2) + 2
    shares = []
    for profile, centre in zip(profiles, centres, strict=True):
        c = int(round(centre))
        peak = float(profile[max(0, c - 1) : c + 2].max())
        before = float(profile[max(0, c - near - 3) : max(1, c - near + 1)].mean())
        after = (
            float(profile[c + near : c + near + 4].mean())
            if c + near < len(profile)
            else before
        )
        height = peak - max(0.0, min(before, after))
        shares.append((peak - max(before, after)) / height if height > 0 else 0.0)
    return sorted(shares)[1]


def _side_profiles(signal: np.ndarray, box: Box, params: Params) -> np.ndarray:
    """The mean signal across each side, out to ``settle_reach`` either way."""
    reach = params.settle_reach
    lx, ty, rx, by = box
    corner = _corner(rx - lx, by - ty, params)
    height, width = signal.shape
    out = np.zeros((4, 2 * reach + 1), np.float64)
    sides = (
        (ty, (int(lx + corner), int(rx - corner)), True),
        (by, (int(lx + corner), int(rx - corner)), True),
        (lx, (int(ty + corner), int(by - corner)), False),
        (rx, (int(ty + corner), int(by - corner)), False),
    )
    for k, (line, along, horizontal) in enumerate(sides):
        centre = int(round(line))
        for j, at in enumerate(range(centre - reach, centre + reach + 1)):
            if 0 <= at < (height if horizontal else width) and along[1] > along[0]:
                band = (
                    signal[at, along[0] : along[1]]
                    if horizontal
                    else signal[along[0] : along[1], at]
                )
                out[k, j] = float(band.mean())
    return out


def _from_profiles(
    profiles: np.ndarray, box: Box, reach: int
) -> tuple[Box, float, list[float]] | None:
    """The settled box, the ring's width, and where each side's ridge sits
    in its profile (top, bottom, left, right)."""
    lines, widths, centres = [], [], [0.0] * 4
    for k, line in enumerate(box):
        # The order of ``box`` is left, top, right, bottom; profiles are
        # top, bottom, left, right.
        side = (2, 0, 3, 1)[k]
        found = _ridge_line(profiles[side])
        if found is None:
            return None
        lines.append(int(round(line)) - reach + found[0])
        widths.append(found[1])
        centres[side] = found[0]
    left, top, right, bottom = lines
    if right - left < 8 or bottom - top < 8:
        return None
    return (left, top, right, bottom), float(np.median(widths)), centres


def _calibrate_meeting(
    frames: Frames, platform: str | None, params: Params
) -> tuple[tuple[float, float, float], bool]:
    """The ring colour from rings of any colour, or the prior when too few."""
    sample = _sample(len(frames), params.calibration_frames)
    loose = [find_rings(frames[i], None, params) for i in sample]
    extent = (len(sample), frames[sample[0]].shape[1] if sample else 0)
    colour = calibrate(
        _valid(_cluster(loose, params), None, params, extent), platform, params
    )
    if colour is not None:
        return colour, True
    prior = PRIORS.get(platform or "", PRIORS["teams"])
    return (float(prior[0]), float(prior[1]), float(prior[2])), False


def _score_all(
    frames: Frames,
    slots: Sequence[Slot],
    colour: Sequence[float],
    params: Params = DEFAULT,
    slack: int = 2,
) -> np.ndarray:
    """Every slot's ring score in every frame."""
    scores = np.zeros((len(frames), len(slots)), np.float32)
    if not slots:
        return scores
    for frame in range(len(frames)):
        scorer = _Scorer(frames[frame], colour, params, slack)
        scores[frame] = [scorer.score(s.box, s.thickness) for s in slots]
    return scores


def _read_sightings(
    slots: Sequence[Slot],
    scores: np.ndarray,
    strict: dict[int, set[int]],
    params: Params,
) -> np.ndarray:
    """Which slots are lit in each frame, from each slot's own scores.

    A ring comes and goes, so a slot's scores over the meeting fall into two
    groups, lit and not, far apart. The cut between them reads the slot in
    every frame, so a ring found only now and then still counts each time it
    is lit. A slot whose scores do not split, such as the JPEG overshoot all
    round a bright camera tile, is never lit.
    """
    lit = np.zeros(scores.shape, bool)
    for index, slot in enumerate(slots):
        column = scores[:, index]
        # Below zero means no ring; how far below says nothing more.
        low, high, spread = _two_groups(np.maximum(column, 0.0))
        found = [f for f, indices in strict.items() if index in indices]
        slot.reference = float(np.median(column[found])) if found else high
        apart = high - low >= max(params.lit_separation, params.lit_contrast * spread)
        if not apart or high < params.lit_floor:
            slot.threshold = math.inf
        else:
            slot.threshold = max(params.lit_floor, (low + high) / 2)
        lit[:, index] = column >= slot.threshold
        slot.sightings = [int(f) for f in np.flatnonzero(lit[:, index])]
    return lit


def _two_groups(values: np.ndarray) -> tuple[float, float, float]:
    """Two-means on one slot's scores: the low mean, the high mean, and the
    larger of the two groups' spreads."""
    low, high = float(values.min()), float(values.max())
    for _ in range(20):
        cut = (low + high) / 2
        under, over = values[values < cut], values[values >= cut]
        if not under.size or not over.size:
            break
        low, high = float(under.mean()), float(over.mean())
    cut = (low + high) / 2
    under, over = values[values < cut], values[values >= cut]
    spread = max(
        float(under.std()) if under.size else 0.0,
        float(over.std()) if over.size else 0.0,
    )
    return low, high, spread


def _sightings(
    rings: Sequence[Sequence[Ring]], slots: Sequence[Slot], params: Params
) -> dict[int, set[int]]:
    found: dict[int, set[int]] = {}
    for frame, rings_here in enumerate(rings):
        for ring in rings_here:
            for index, slot in enumerate(slots):
                if iou(slot.box, ring.box) >= params.match_iou:
                    found.setdefault(frame, set()).add(index)
    return found
