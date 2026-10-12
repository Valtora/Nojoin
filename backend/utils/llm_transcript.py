"""The transcript of one recording, rendered as text for an LLM prompt.

Meeting Chat sends a recording's whole transcript with every turn. This module
is the one place that reads it: the segments come from
``build_transcript_segments_for_read`` (the canonical utterances, or the
``Transcript.segments`` projection for a recording without them), speaker
names from ``build_recording_speaker_map`` as for notes generation, and the
line format from ``format_segments_for_llm``. Lines carry the start time only
(``[MM:SS]``), the form the chat prompt asks the model to cite, because the
transcript is resent with every turn and the end time would add roughly a sixth
to its size. A meeting too long for the provider's window is sent as excerpts
instead, read here from the same lines.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy.orm import Session

from backend.utils.canonical_pipeline import build_transcript_segments_for_read
from backend.utils.canonical_pipeline.constants import (
    UNKNOWN_SPEAKER,
    _load_recording_speakers,
)
from backend.utils.meeting_notes import (
    build_recording_speaker_map,
    format_segments_for_llm,
)


def _segments_the_view_shows(segments: list[dict]) -> list[dict]:
    # TranscriptView hides finalised UNKNOWN (unattributed) lines, such as a
    # removed speaker's, once any line has a known speaker. Sending them would
    # let the model attribute and cite lines the user cannot see.
    if all(segment.get("speaker") == UNKNOWN_SPEAKER for segment in segments):
        return segments
    return [
        segment
        for segment in segments
        if segment.get("speaker") != UNKNOWN_SPEAKER
        or segment.get("provisional") is True
    ]


def render_transcript_for_llm(session: Session, recording_id: int) -> str | None:
    """The recording's transcript as prompt text, or None when it has none."""
    segments = build_transcript_segments_for_read(session, recording_id)
    if not segments:
        return None
    # The loader fetches each speaker's global speaker up front; name
    # resolution reads it, and a lazy load costs a query per speaker.
    speakers = _load_recording_speakers(session, recording_id)
    return format_segments_for_llm(
        _segments_the_view_shows(segments),
        build_recording_speaker_map(speakers),
        with_end=False,
    )


# Written between passages that are not consecutive in the transcript.
EXCERPT_GAP = "[...]"


def _overlapping(segments: list[dict], start: float, end: float) -> list[int]:
    return [
        index
        for index, segment in enumerate(segments)
        if float(segment.get("start", 0)) < end
        and float(segment.get("end", segment.get("start", 0))) > start
    ]


def render_transcript_excerpts_for_llm(
    session: Session,
    recording_id: int,
    ranges: Sequence[tuple[float, float]],
    max_bytes: Optional[int] = None,
) -> str:
    """The lines of the transcript inside ``ranges``, as prompt text.

    Ranges are taken most relevant first, skipping any that would take the
    total past ``max_bytes``, and shown in time order in the same line format
    as the whole transcript, with ``[...]`` wherever lines were left out. The
    lines are read now, so an edit or a rename made since ``ranges`` were
    indexed still shows. Empty when no range holds a line.
    """
    segments = build_transcript_segments_for_read(session, recording_id)
    if not segments or not ranges:
        return ""
    segments = _segments_the_view_shows(segments)
    speaker_map = build_recording_speaker_map(
        _load_recording_speakers(session, recording_id)
    )

    rendered: dict[int, str] = {}

    def line(index: int) -> str:
        if index not in rendered:
            rendered[index] = format_segments_for_llm(
                [segments[index]], speaker_map, with_end=False
            )
        return rendered[index]

    chosen: set[int] = set()
    size = 0
    for start, end in ranges:
        new = [
            index for index in _overlapping(segments, start, end) if index not in chosen
        ]
        cost = sum(len(line(index).encode("utf-8")) + 1 for index in new)
        if max_bytes is not None and size + cost > max_bytes:
            continue
        chosen.update(new)
        size += cost

    if not chosen:
        return ""
    lines: list[str] = []
    previous = -1
    for index in sorted(chosen):
        if index != previous + 1:
            lines.append(EXCERPT_GAP)
        lines.append(line(index))
        previous = index
    if previous != len(segments) - 1:
        lines.append(EXCERPT_GAP)
    return "\n".join(lines)
