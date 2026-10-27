"""Natural live line boundaries, independent of decode cadence and word locks."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.processing.live_limits import DEFAULT_MAX_SEGMENT_S, LIVE_MIN_SILENCE_MS

from .live_labels import LiveLabelDecision


@dataclass
class Partition:
    """A contiguous speaker run and the evidence stabilising its right edge."""

    words: list[dict[str, Any]]
    speaker: str
    speaker_locked: bool
    line_locked: bool = False
    closed: bool = False
    locked_speaker_words: list[str] = field(default_factory=list)
    closure_reason: str | None = None
    boundary_locked: bool = False
    closed_at_ms: int | None = None


@dataclass(frozen=True)
class LineBoundaries:
    """Legacy limits and durable confirmed ends held under the Transcript fence."""

    max_segment_ms: int = round(DEFAULT_MAX_SEGMENT_S * 1000)
    closed_ends: Mapping[str, int | None] = field(default_factory=dict)
    silence_start_ms: int | None = None
    closed_at_ms: Sequence[int] = ()


def word_extent(word: dict[str, Any]) -> tuple[int, int] | None:
    """Use native times or actual capture bounds without timing an unaligned atom."""
    start, end = word.get("start_ms"), word.get("end_ms")
    if start is not None and end is not None:
        return int(start), int(end)
    observed_end = word.get("observed_audio_end_ms")
    if observed_end is None:
        return None
    observed_start = word.get(
        "observed_audio_start_ms",
        word.get("first_observed_audio_end_ms", observed_end),
    )
    return int(observed_start), int(observed_end)


def _extents(partition: Partition) -> list[tuple[int, int]]:
    return [
        extent for word in partition.words if (extent := word_extent(word)) is not None
    ]


def _boundary_reason(
    partition: Partition,
    word: dict[str, Any],
    decision: LiveLabelDecision,
    barriers: Sequence[int],
    boundaries: LineBoundaries,
) -> str | None:
    """Distinguish durable closed ends from provisional speaker/timing evidence."""
    if partition.words[-1].get("id") in boundaries.closed_ends:
        partition.closed_at_ms = boundaries.closed_ends[partition.words[-1]["id"]]
        return "closure"
    extents, current = _extents(partition), word_extent(word)
    if extents and current is not None:
        start, end = current
        for point in boundaries.closed_at_ms:
            if extents[-1][0] < point <= start:
                partition.closed_at_ms = point
                return "closure"
        if start - extents[-1][1] >= LIVE_MIN_SILENCE_MS:
            return "pause"
        if any(extents[-1][0] < barrier <= start for barrier in barriers):
            return "closure"
        if (
            boundaries.max_segment_ms > 0
            and end - extents[0][0] > boundaries.max_segment_ms
        ):
            return "length"
    if partition.speaker != decision.speaker:
        return "speaker"
    return None


def _close_partition(
    partition: Partition, boundaries: LineBoundaries, *, closing: bool, trailing: bool
) -> None:
    """A tentative next speaker must settle before it can freeze its neighbour."""
    extents = _extents(partition)
    retained_end = (
        next(
            (
                point
                for point in boundaries.closed_at_ms
                if extents and extents[-1][0] < point
            ),
            None,
        )
        if trailing
        else None
    )
    terminal_id = partition.words[-1].get("id")
    if terminal_id in boundaries.closed_ends:
        partition.closure_reason, partition.boundary_locked = "closure", True
        partition.closed_at_ms = boundaries.closed_ends[terminal_id]
    elif closing:
        partition.closure_reason, partition.boundary_locked = "closure", True
    elif retained_end is not None:
        partition.closure_reason, partition.boundary_locked = "closure", True
        partition.closed_at_ms = retained_end
    elif extents:
        start, end = min(item[0] for item in extents), max(item[1] for item in extents)
        if (
            trailing
            and boundaries.silence_start_ms is not None
            and end <= boundaries.silence_start_ms
        ):
            partition.closure_reason, partition.boundary_locked = "pause", True
            partition.closed_at_ms = boundaries.silence_start_ms
        elif boundaries.max_segment_ms > 0 and end - start >= boundaries.max_segment_ms:
            partition.closure_reason, partition.boundary_locked = "length", True
    if partition.boundary_locked and partition.closed_at_ms is None and extents:
        partition.closed_at_ms = max(item[1] for item in extents)
    partition.closed = partition.closure_reason is not None
    partition.line_locked = (
        partition.closed
        and partition.boundary_locked
        and all(word.get("locked") for word in partition.words)
        and partition.speaker_locked
    )


def partition_words(
    words: list[dict[str, Any]],
    decisions: list[LiveLabelDecision],
    *,
    closing: bool = False,
    barriers: Sequence[int] = (),
    boundaries: LineBoundaries = LineBoundaries(),
) -> list[Partition]:
    """Project revisable speaker runs; only established boundaries can lock lines."""
    partitions: list[Partition] = []
    for word, decision in zip(words, decisions, strict=True):
        reason = (
            _boundary_reason(partitions[-1], word, decision, barriers, boundaries)
            if partitions
            else None
        )
        if not partitions or reason is not None:
            if partitions:
                previous = partitions[-1]
                previous.closure_reason = reason
                previous.boundary_locked = reason == "closure" or bool(
                    word.get("locked") and (reason != "speaker" or decision.locked)
                )
            partitions.append(Partition([], decision.speaker, True))
        partition = partitions[-1]
        partition.words.append(word)
        partition.speaker_locked = partition.speaker_locked and decision.locked
        if word.get("locked") and decision.locked:
            partition.locked_speaker_words.append(word["id"])
    for partition in partitions:
        _close_partition(
            partition, boundaries, closing=closing, trailing=partition is partitions[-1]
        )
    return partitions
