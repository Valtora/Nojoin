"""Frames and transcript lines on one clock, and each platform's highlight lag.

A frame's time is its audio chunk's ``absolute_start_ms`` plus its own
``offset_ms``, the clock the transcript's lines use. The screen is sampled
once per second of audio and a frame is kept only when the screen changed,
so a frame's state holds until the next one when nothing was lost between
them: the next frame's ``lost_before`` is 0. Otherwise, or when the field is
missing, the span is unobserved after one tick, and no highlight is carried
across it.

Everything is then laid on a 100 ms grid. The lag is the shift that best
lines the lit tile up with the reviewed speaker, searched from -1 s to +3 s
on the calibration meetings only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

BIN_MS = 100
# A frame stands for its own tick of audio, and for the ticks after it only
# when none of them was lost.
TICK_MS = 1_000
LAG_GRID_MS = tuple(range(-1_000, 3_001, 100))

NONE = -1
SEVERAL = -2
UNKNOWN = -3


@dataclass(frozen=True)
class Utterance:
    start_ms: int
    end_ms: int
    diarization: int | None
    pipeline: int | None
    reviewed: int | None
    overlap_rank: int = 0


@dataclass(frozen=True)
class Highlight:
    """What the detector read, frame by frame, on the transcript clock."""

    times_ms: np.ndarray
    lit: tuple[tuple[int, ...], ...]
    visible: np.ndarray
    # Each tile's layout, so a speaker can be followed within one layout.
    layout_of: Mapping[int, int]
    # Until when each frame's state is known: the next frame's time when
    # nothing was lost before it, else one tick on.
    held_until_ms: np.ndarray | None = None

    @classmethod
    def empty(cls) -> Highlight:
        return cls(
            np.zeros(0, np.int64), (), np.zeros(0, bool), {}, np.zeros(0, np.int64)
        )

    def until(self) -> np.ndarray:
        if self.held_until_ms is not None:
            return self.held_until_ms
        return self.times_ms + TICK_MS


@dataclass(frozen=True)
class Bins:
    """One meeting on the 100 ms grid. Speakers are indexed from 0."""

    reviewed: np.ndarray
    pipeline: np.ndarray
    tile: np.ndarray
    visible: np.ndarray
    speech: np.ndarray
    layout_of: Mapping[int, int]
    # Each speaker key's index, for reading an utterance against the grid.
    reviewed_index: Mapping[int, int]
    pipeline_index: Mapping[int, int]


def frame_times(
    index: Iterable[Mapping[str, object]], chunk_starts: Mapping[int, int]
) -> list[int | None]:
    """Each indexed frame's time, or None when its chunk is unknown."""
    times: list[int | None] = []
    for row in index:
        start = chunk_starts.get(int(row["sequence"]))  # type: ignore[call-overload]
        times.append(None if start is None else start + int(row["offset_ms"]))  # type: ignore[call-overload]
    return times


def held_until(
    times_ms: Sequence[int], lost_before: Sequence[int | None]
) -> np.ndarray:
    """Until when each frame's state is known, frames in time order.

    ``lost_before`` belongs to the frame it is on and describes the gap
    before it. A missing value counts as a loss, so frames from before the
    field existed never carry a highlight across a gap.
    """
    times = np.asarray(times_ms, np.int64)
    until = times + TICK_MS
    for k in range(len(times) - 1):
        if lost_before[k + 1] == 0:
            until[k] = max(until[k], times[k + 1])
    return until


def tiles_at(
    highlight: Highlight, times_ms: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The single lit tile at each time (or NONE, SEVERAL, UNKNOWN), and visibility."""
    tile = np.full(times_ms.shape, UNKNOWN, np.int64)
    visible = np.zeros(times_ms.shape, bool)
    if not len(highlight.times_ms):
        return tile, visible
    frame = np.searchsorted(highlight.times_ms, times_ms, side="right") - 1
    known = frame >= 0
    known[known] = times_ms[known] < highlight.until()[frame[known]]
    per_frame = np.array(
        [
            lit[0] if len(lit) == 1 else (NONE if not lit else SEVERAL)
            for lit in highlight.lit
        ],
        np.int64,
    )
    tile[known] = per_frame[frame[known]]
    visible[known] = highlight.visible[frame[known]]
    return tile, visible


def speaker_index(utterances: Sequence[Utterance], field: str) -> dict[int, int]:
    keys = sorted(
        {getattr(u, field) for u in utterances if getattr(u, field) is not None}
    )
    return {key: i for i, key in enumerate(keys)}


def bin_span(utterance: Utterance) -> slice:
    return slice(utterance.start_ms // BIN_MS, -(-utterance.end_ms // BIN_MS))


def _speaker_bins(
    utterances: Sequence[Utterance], field: str, length: int
) -> np.ndarray:
    """Who holds each bin: an index from 0, NONE, or SEVERAL in overlap."""
    index = speaker_index(utterances, field)
    out = np.full(length, NONE, np.int64)
    for u in utterances:
        key = getattr(u, field)
        if key is None or u.end_ms <= u.start_ms:
            continue
        span = out[bin_span(u)]
        mine = index[key]
        clash = (span != NONE) & (span != mine)
        span[span == NONE] = mine
        span[clash] = SEVERAL
    return out


def meeting_bins(
    utterances: Sequence[Utterance], highlight: Highlight, lag_ms: int
) -> Bins:
    """The meeting on the grid, the highlight shifted back by the lag."""
    end = max((u.end_ms for u in utterances), default=0)
    length = -(-end // BIN_MS) + 1
    reviewed = _speaker_bins(utterances, "reviewed", length)
    pipeline = _speaker_bins(utterances, "pipeline", length)
    centres = np.arange(length, dtype=np.int64) * BIN_MS + BIN_MS // 2
    tile, visible = tiles_at(highlight, centres + lag_ms)
    speech = reviewed != NONE
    return Bins(
        reviewed,
        pipeline,
        tile,
        visible,
        speech,
        highlight.layout_of,
        speaker_index(utterances, "reviewed"),
        speaker_index(utterances, "pipeline"),
    )


def majority(
    rows: np.ndarray, cols: np.ndarray
) -> tuple[dict[int, int], dict[int, int]]:
    """For each row value, the column value it meets most, and how often."""
    best: dict[int, int] = {}
    count: dict[int, int] = {}
    if not len(rows):
        return best, count
    pairs, counts = np.unique(np.stack([rows, cols]), axis=1, return_counts=True)
    for (row, col), n in zip(pairs.T, counts, strict=True):
        if n > count.get(int(row), 0):
            best[int(row)], count[int(row)] = int(col), int(n)
    return best, count


def agreement(bins: Bins) -> tuple[int, int]:
    """Speech bins whose lit tile names the reviewed speaker, and all speech bins.

    Each tile stands for the reviewed speaker it is lit for most. A bin with
    no tile, several, or none visible counts against.
    """
    single = bins.reviewed >= 0
    usable = single & bins.visible & (bins.tile >= 0)
    _, counts = majority(bins.tile[usable], bins.reviewed[usable])
    return sum(counts.values()), int(single.sum())


def fit_lag(
    meetings: Sequence[tuple[Sequence[Utterance], Highlight]],
) -> tuple[int, float]:
    """The lag on the grid that maximises agreement over these meetings.

    Ties go to the lag nearest zero. Returns the lag and its agreement.
    """
    best: tuple[float, int, int] | None = None
    for lag in LAG_GRID_MS:
        right = total = 0
        for utterances, highlight in meetings:
            r, t = agreement(meeting_bins(utterances, highlight, lag))
            right, total = right + r, total + t
        score = right / total if total else 0.0
        key = (score, -abs(lag), lag)
        if best is None or key > best:
            best = key
    if best is None:
        return 0, 0.0
    return best[2], best[0]
