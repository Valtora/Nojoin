"""Pure studied native/community interval union, with independent label namespaces."""

from __future__ import annotations

from collections import defaultdict
from typing import TypeAlias

import numpy as np
from pyannote.core import Annotation, Segment

from backend.processing.nemotron_types import NemotronStateError, validate_probabilities

Row: TypeAlias = tuple[float, float, str]
Span: TypeAlias = tuple[float, float]


def _merge(spans: list[Span]) -> list[Span]:
    merged: list[Span] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _normalise(rows: list[Row]) -> list[Row]:
    groups: dict[str, list[Span]] = defaultdict(list)
    for start, end, label in rows:
        groups[label].append((start, end))
    return sorted(
        (start, end, label)
        for label, spans in groups.items()
        for start, end in _merge(spans)
    )


def _rows(annotation: Annotation) -> list[Row]:
    if not isinstance(annotation, Annotation):
        raise NemotronStateError("Diarization union needs Annotation inputs")
    rows = []
    for segment, _, label in annotation.itertracks(yield_label=True):
        start, end = segment.start, segment.end
        if (
            not isinstance(label, str)
            or not np.isfinite([start, end]).all()
            or start < 0
            or end <= start
        ):
            raise NemotronStateError("Diarization union contains malformed intervals")
        rows.append((float(start), float(end), label))
    return _normalise(rows)


def _annotation(rows: list[Row], *, uri: str | None) -> Annotation:
    annotation = Annotation(uri=uri)
    for start, end, label in _normalise(rows):
        # Distinct tracks preserve different speakers with identical endpoints.
        annotation[Segment(start, end), label] = label
    return annotation


def native_annotation(
    probabilities: np.ndarray, *, uri: str | None = None
) -> Annotation:
    """Convert all fp32 strict >0.5 runs to 10 ms intervals, without filtering."""
    frames = len(probabilities) if isinstance(probabilities, np.ndarray) else 0
    validate_probabilities(probabilities, frames)
    rows: list[Row] = []
    for channel in range(8):
        active = (probabilities[:, channel] > 0.5).astype(np.int8)
        changes = np.diff(np.r_[0, active, 0])
        starts, ends = np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)
        rows.extend(
            (float(start * 0.01), float(end * 0.01), f"L{channel}")
            for start, end in zip(starts, ends, strict=True)
        )
    return _annotation(rows, uri=uri)


def _mapping(community: list[Row], native: list[Row]) -> dict[str, str]:
    labels = sorted({label for _, _, label in native})
    starts = np.asarray([start for start, _, _ in native])
    ends = np.asarray([end for _, end, _ in native])
    indices = np.asarray([labels.index(label) for _, _, label in native], dtype=int)
    totals: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(len(labels)))
    for start, end, label in community:
        overlap = np.maximum(0, np.minimum(ends, end) - np.maximum(starts, start))
        totals[label] += np.bincount(indices, weights=overlap, minlength=len(labels))
    return {
        label: f"N:{labels[int(weights.argmax())]}"
        if weights.sum() > 0
        else f"C:{label}"
        for label, weights in totals.items()
    }


def _subtract(start: float, end: float, occupied: list[Span]) -> list[Span]:
    fragments: list[Span] = []
    cursor = start
    for left, right in occupied:
        if right <= cursor:
            continue
        if left >= end:
            break
        if left > cursor:
            fragments.append((cursor, left))
        cursor = max(cursor, right)
        if cursor >= end:
            break
    if cursor < end:
        fragments.append((cursor, end))
    return fragments


def union_diarization(community: Annotation, native: Annotation) -> Annotation:
    """Keep native speech and add mapped community only outside all native speech.

    Per-label normalization prevents duplicate source tracks voting twice. Exact
    overlap ties select the sorted native label. Zero-overlap clusters stay fresh.
    """
    community_rows, native_rows = _rows(community), _rows(native)
    mapping = _mapping(community_rows, native_rows)
    occupied = _merge([(start, end) for start, end, _ in native_rows])
    rows = [(start, end, f"N:{label}") for start, end, label in native_rows]
    rows.extend(
        (left, right, mapping[label])
        for start, end, label in community_rows
        for left, right in _subtract(start, end, occupied)
    )
    return _annotation(rows, uri=community.uri)
