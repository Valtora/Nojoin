"""Causal native activity overlap, indexed by immutable shadow turn units."""

from __future__ import annotations

import numpy as np

from .engine import LiveLabelEngine


def _union_pieces(
    shadow: LiveLabelEngine, interval: tuple[float, float]
) -> list[tuple[int, float, float]]:
    start_s, end_s = interval
    spans = sorted(
        (p.unit, max(start_s, p.start), min(end_s, p.end))
        for p in shadow.pieces
        if p.end > start_s and p.start < end_s
    )
    merged: list[tuple[int, float, float]] = []
    for unit, start, end in spans:
        if merged and merged[-1][0] == unit and merged[-1][2] >= start:
            previous = merged[-1]
            merged[-1] = (unit, previous[1], max(previous[2], end))
        else:
            merged.append((unit, start, end))
    return merged


def activity_integral(
    probabilities: np.ndarray, first_frame: int, positions_s: np.ndarray
) -> np.ndarray:
    """Integrate study-precision activity up to fractional 10 ms positions."""
    activity = (probabilities.astype(np.float16) > 0.5).astype(np.float64)
    prefix = np.vstack((np.zeros((1, 8)), activity.cumsum(axis=0)))
    positions = np.clip(positions_s * 100 - first_frame, 0, len(activity))
    whole = np.floor(positions).astype(np.int64)
    fraction = positions - whole
    values = prefix[whole].copy()
    inside = whole < len(activity)
    values[inside] += activity[whole[inside]] * fraction[inside, None]
    return values / 100


def add_overlap(
    shadow: LiveLabelEngine,
    probabilities: np.ndarray,
    first_frame: int,
    evidence: np.ndarray,
    interval: tuple[float, float],
) -> np.ndarray:
    """Credit only this common emitted interval, using pieces' current unit IDs."""
    if len(evidence) < shadow.units.count:
        evidence = np.pad(evidence, ((0, shadow.units.count - len(evidence)), (0, 0)))
    start_s, end_s = interval
    pieces = _union_pieces(shadow, interval)
    if not pieces or end_s <= start_s or not len(probabilities):
        return evidence
    starts = np.array([p[1] for p in pieces])
    ends = np.array([p[2] for p in pieces])
    votes = activity_integral(probabilities, first_frame, ends) - activity_integral(
        probabilities, first_frame, starts
    )
    np.add.at(evidence, [p[0] for p in pieces], votes)
    return evidence
