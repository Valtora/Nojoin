"""Lossless original-text spans for native forced-alignment tokens."""

from dataclasses import dataclass
from math import isfinite
from typing import Any
from unicodedata import category


@dataclass(frozen=True)
class AlignedWord:
    """One original text span with observed millisecond bounds, or null timings."""

    text: str
    source_start: int
    source_end: int
    start_ms: int | None
    end_ms: int | None


@dataclass(frozen=True)
class AlignmentSuccess:
    """An alignment covering every lexical character of the original text."""

    text: str
    words: tuple[AlignedWord, ...]


@dataclass(frozen=True)
class AlignmentFailure:
    """A failed alignment, distinct from successfully recognised silence."""

    text: str
    reason: str


def _kept(char: str) -> bool:
    """The pinned processor retains letters, numbers and ASCII apostrophes."""
    return char == "'" or category(char).startswith(("L", "N"))


def _timing(
    entry: dict[str, Any], duration_s: float, previous: tuple[float, float]
) -> tuple[float, float]:
    start, end = entry.get("start_time"), entry.get("end_time")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in (start, end)
    ):
        raise ValueError("Alignment returned non-numeric word timings.")
    start, end = float(start), float(end)
    if (
        not all(isfinite(value) for value in (start, end))
        or not 0 <= start < end <= duration_s
    ):
        raise ValueError("Alignment returned invalid word bounds.")
    if start < previous[0] or end < previous[1]:
        raise ValueError("Alignment returned words out of time order.")
    return start, end


def retained_timings(
    entries: list[dict[str, Any]], duration_s: float
) -> list[tuple[float, float] | None]:
    """Keep each observed span that stays in text order, leaving other words untimed.

    Both boundaries must be nondecreasing relative to the last kept word.
    Rejected predictions never become anchors for subsequent words.
    """
    timings: list[tuple[float, float] | None] = []
    previous = (0.0, 0.0)
    for entry in entries:
        try:
            timing = _timing(entry, duration_s, previous)
        except ValueError:
            timing = None
        if timing is not None:
            previous = timing
        timings.append(timing)
    if not any(timing is not None for timing in timings):
        raise ValueError("Alignment returned no usable word timings.")
    return timings


def _boundaries(text: str, cores: list[tuple[int, int]]) -> list[int]:
    """Partition punctuation and separators without changing a character."""
    boundaries = [0]
    for previous, current in zip(cores, cores[1:]):
        gap_start, next_start = previous[1], current[0]
        boundary = next_start
        for index in range(gap_start, next_start):
            if text[index].isspace():
                boundary = index
                break
        boundaries.append(boundary)
    return [*boundaries, len(text)]


def map_alignment_spans(
    text: str, entries: list[dict[str, Any]], duration_s: float, offset_ms: int = 0
) -> AlignmentSuccess:
    """Map all native tokens onto source spans, retaining valid timings only.

    Punctuation is absent from the native tokens. Matching the complete lexical
    character stream in order handles repeated words and CJK tokenisation without
    searching for an arbitrary later occurrence or inventing missing timings.
    """
    positions = [index for index, char in enumerate(text) if _kept(char)]
    lexical = "".join(text[index] for index in positions)
    if not lexical or not entries:
        raise ValueError("Alignment did not return timed lexical words.")
    cursor = 0
    cores: list[tuple[int, int]] = []
    timings = retained_timings(entries, duration_s)
    for entry in entries:
        token = "".join(char for char in str(entry.get("text") or "") if _kept(char))
        stop = cursor + len(token)
        if not token or lexical[cursor:stop] != token:
            raise ValueError(
                "Alignment tokens do not cover the original wording in order."
            )
        first, last = positions[cursor], positions[stop - 1]
        if any(char.isspace() for char in text[first : last + 1]):
            raise ValueError("Alignment merged separate original words.")
        cores.append((first, last + 1))
        cursor = stop
    if cursor != len(lexical):
        raise ValueError("Alignment dropped an original word.")
    boundaries = _boundaries(text, cores)
    words = tuple(
        AlignedWord(
            text=text[begin:end],
            source_start=begin,
            source_end=end,
            start_ms=offset_ms + round(timing[0] * 1000) if timing else None,
            end_ms=offset_ms + round(timing[1] * 1000) if timing else None,
        )
        for begin, end, timing in zip(boundaries, boundaries[1:], timings)
    )
    return AlignmentSuccess(text=text, words=words)
