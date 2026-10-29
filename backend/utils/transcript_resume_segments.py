"""Partition final transcript evidence at durable capture resume cuts."""

from collections import defaultdict
from collections.abc import Sequence
from itertools import groupby
from typing import Any

from backend.utils.partial_word_segments import timed_word_groups
from backend.utils.word_text import reconstruct_text_from_words
from backend.utils.word_times import falls_short


def _interval(start: float, boundaries_s: list[float]) -> int:
    """Treat floating timestamp noise at a cut as the same native instant."""
    return sum(not falls_short(start, point) for point in boundaries_s)


def _word_parts(
    segment: dict[str, Any], boundaries_s: list[float]
) -> list[tuple[int, dict[str, Any]]]:
    anchors = timed_word_groups(segment.get("words") or [])
    if not anchors or segment.get("alignment_error"):
        # Without native times there is no honest way to distribute this text.
        return [(_interval(segment["start"], boundaries_s), segment)]
    assigned: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
    for anchor, words in anchors:
        interval = _interval(anchor["start"], boundaries_s)
        if assigned and (
            anchor.get("join_left")
            or (
                anchor.get("source_id")
                and anchor.get("source_id") == assigned[-1][1].get("source_id")
                and not str(anchor.get("word") or "")[:1].isspace()
            )
        ):
            interval = assigned[-1][0]
        assigned.append((interval, anchor, words))
    if len({item[0] for item in assigned}) == 1:
        return [(assigned[0][0], segment)]
    parts = []
    for interval, run in groupby(assigned, key=lambda item: item[0]):
        items = list(run)
        words = [word for _, _, group in items for word in group]
        part = {
            **segment,
            "start": items[0][1]["start"],
            "end": items[-1][1]["end"],
            "text": reconstruct_text_from_words(words),
            "words": words,
        }
        # An engine/source identifier must not become two utterance IDs.
        part.pop("id", None)
        parts.append((interval, part))
    return parts


def resume_segment_groups(
    segments: list[dict[str, Any]], boundaries_ms: Sequence[int]
) -> list[list[dict[str, Any]]]:
    """Consolidate each capture interval separately so even short folds stop."""
    if not boundaries_ms:
        return [segments]
    boundaries_s = [point / 1000 for point in sorted(set(boundaries_ms))]
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        for interval, part in _word_parts(segment, boundaries_s):
            groups[interval].append(part)
    return [groups[interval] for interval in sorted(groups)]
