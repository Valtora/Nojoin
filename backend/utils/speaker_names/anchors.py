"""The turn a name is given for.

A turn is a run of consecutive lines with one speaker, with gaps under 1.5 s.
The lines are any timed rows keyed by speaker: canonical utterances keyed by
their recording speaker, or final segments keyed by diarisation label.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Sequence
from typing import Any

TURN_GAP_MS = 1500

Line = tuple[Hashable, int, int]
Span = tuple[int, int]


def turns_of(lines: Iterable[Line], *, gap_ms: int = TURN_GAP_MS) -> list[Line]:
    """Merge time-ordered lines into turns.

    A turn ends at another speaker's line, or at a gap of ``gap_ms`` or more.
    """
    turns: list[Line] = []
    for key, start, end in sorted(lines, key=lambda line: (line[1], line[2])):
        if end <= start:
            continue
        if turns:
            last_key, last_start, last_end = turns[-1]
            if last_key == key and start - last_end < gap_ms:
                turns[-1] = (key, last_start, max(last_end, end))
                continue
        turns.append((key, start, end))
    return turns


def _turns_for(lines: Iterable[Line], key: Hashable) -> list[Span]:
    return [(start, end) for k, start, end in turns_of(lines) if k == key]


def longest_turn(lines: Iterable[Line], key: Hashable) -> Span | None:
    """The speaker's longest turn; the earliest of equals."""
    turns = _turns_for(lines, key)
    if not turns:
        return None
    return max(turns, key=lambda span: (span[1] - span[0], -span[0]))


def first_turn(lines: Iterable[Line], key: Hashable) -> Span | None:
    turns = _turns_for(lines, key)
    return turns[0] if turns else None


def turn_nearest(
    lines: Iterable[Line], key: Hashable, start_ms: int, end_ms: int
) -> Span | None:
    """The speaker's turn holding or closest to a span, else their longest.

    An LLM's evidence line is often someone else addressing the speaker, so
    the name belongs to the speaker's turn next to it, not to the line itself.
    """
    line_list = list(lines)
    turns = _turns_for(line_list, key)
    if not turns:
        return None

    def distance(span: Span) -> int:
        return max(0, span[0] - end_ms, start_ms - span[1])

    return min(turns, key=lambda span: (distance(span), span[0]))


def lines_from_final_segments(final_segments: Sequence[dict[str, Any]]) -> list[Line]:
    """Final segments as lines keyed by their primary diarisation label.

    Unattributed speech (``UNKNOWN``, empty) is no speaker's, so it is left out.
    """
    lines: list[Line] = []
    for segment in final_segments:
        label = str(segment.get("speaker") or "").strip()
        if not label or label.upper() == "UNKNOWN":
            continue
        start = int(round(float(segment.get("start") or 0.0) * 1000.0))
        end = int(round(float(segment.get("end") or 0.0) * 1000.0))
        if end > start:
            lines.append((label, start, end))
    return lines
