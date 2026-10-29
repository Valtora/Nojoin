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

The user's timeline notes ride along on lines of their own, placed by
``backend.utils.timeline_notes`` on the whole transcript before any line is
hidden, so a note after a hidden line stays after the visible line before it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from backend.prompts import render_prompt
from backend.services.timeline_note_service import load_recording_timeline_notes
from backend.utils.canonical_pipeline import build_transcript_segments_for_read
from backend.utils.canonical_pipeline.constants import (
    UNKNOWN_SPEAKER,
    _load_recording_speakers,
)
from backend.utils.meeting_notes import (
    build_recording_speaker_map,
    format_segments_for_llm,
)
from backend.utils.speaker_names.live import with_live_speaker_names
from backend.utils.timeline_notes import iter_timeline, render_note_line

# Opens a transcript that carries notes, so the model reads them as the user's.
NOTES_LEGEND = render_prompt("shared.notes_legend")


def _view_hides_unknown(segments: list[dict]) -> bool:
    # TranscriptView hides finalised UNKNOWN (unattributed) lines, such as a
    # removed speaker's, once any line has a known speaker. Sending them would
    # let the model attribute and cite lines the user cannot see.
    return not all(segment.get("speaker") == UNKNOWN_SPEAKER for segment in segments)


def _view_shows(segment: dict, hide_unknown: bool) -> bool:
    return (
        not hide_unknown
        or segment.get("speaker") != UNKNOWN_SPEAKER
        or segment.get("provisional") is True
    )


@dataclass
class _ChatTimeline:
    """The visible lines, each with the notes that follow it, in order."""

    # Head notes, and body notes before the first visible line.
    opening: list[str] = field(default_factory=list)
    # One block per visible line: the line, then the body notes after it.
    blocks: list[list[str]] = field(default_factory=list)
    # The visible segments, index for index with ``blocks``.
    segments: list[dict] = field(default_factory=list)
    tail: list[str] = field(default_factory=list)
    has_notes: bool = False


def _chat_timeline(
    session: Session, recording_id: int, segments: list[dict], *, live: bool = False
) -> _ChatTimeline:
    # The loader fetches each speaker's global speaker up front; name
    # resolution reads it, and a lazy load costs a query per speaker.
    speaker_map = with_live_speaker_names(
        session,
        recording_id,
        build_recording_speaker_map(_load_recording_speakers(session, recording_id)),
        **(
            {
                "labels": {
                    str(label)
                    for segment in segments
                    for label in [
                        segment.get("speaker"),
                        *(segment.get("overlapping_speakers") or []),
                    ]
                    if label
                }
            }
            if live
            else {}
        ),
    )
    notes = load_recording_timeline_notes(session, recording_id)
    hide_unknown = _view_hides_unknown(segments)
    timeline = _ChatTimeline(has_notes=bool(notes.notes))
    for item in iter_timeline(len(segments), notes.place(segments)):
        if isinstance(item, int):
            segment = segments[item]
            if _view_shows(segment, hide_unknown):
                timeline.segments.append(segment)
                timeline.blocks.append(
                    [format_segments_for_llm([segment], speaker_map, with_end=False)]
                )
            continue
        line = render_note_line(item.note, notes.author_names)
        if item.slot == "tail":
            timeline.tail.append(line)
        elif item.slot == "body" and timeline.blocks:
            timeline.blocks[-1].append(line)
        else:
            timeline.opening.append(line)
    return timeline


def render_transcript_for_llm(
    session: Session, recording_id: int, *, live: bool = False
) -> str | None:
    """The recording's transcript as prompt text, or None when it has none."""
    segments = build_transcript_segments_for_read(session, recording_id)
    if not segments and not live:
        return None
    timeline = _chat_timeline(session, recording_id, segments, live=live)
    lines = [NOTES_LEGEND] if timeline.has_notes else []
    lines.extend(timeline.opening)
    for block in timeline.blocks:
        lines.extend(block)
    lines.extend(timeline.tail)
    return "\n".join(lines)


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
    as the whole transcript, with ``[...]`` wherever lines were left out. A
    line brings the notes written after it, and the notes about the whole
    meeting open and close the excerpts. The lines are read now, so an edit
    or a rename made since ``ranges`` were indexed still shows. Empty when no
    range holds a line.
    """
    segments = build_transcript_segments_for_read(session, recording_id)
    if not segments or not ranges:
        return ""
    timeline = _chat_timeline(session, recording_id, segments)
    visible = timeline.segments

    def block(index: int) -> str:
        return "\n".join(timeline.blocks[index])

    # Notes about the whole meeting come with every excerpt, as they come with
    # the whole transcript, unless they alone would not fit.
    opening, tail = timeline.opening, timeline.tail
    size = sum(len(line.encode("utf-8")) + 1 for line in (*opening, *tail))
    if max_bytes is not None and size > max_bytes:
        opening, tail, size = [], [], 0
    chosen: set[int] = set()
    for start, end in ranges:
        new = [
            index for index in _overlapping(visible, start, end) if index not in chosen
        ]
        cost = sum(len(block(index).encode("utf-8")) + 1 for index in new)
        if max_bytes is not None and size + cost > max_bytes:
            continue
        chosen.update(new)
        size += cost

    if not chosen:
        return ""
    carries_notes = bool(opening or tail) or any(
        len(timeline.blocks[index]) > 1 for index in chosen
    )
    lines: list[str] = [NOTES_LEGEND] if carries_notes else []
    lines.extend(opening)
    previous = -1
    for index in sorted(chosen):
        if index != previous + 1:
            lines.append(EXCERPT_GAP)
        lines.append(block(index))
        previous = index
    if previous != len(visible) - 1:
        lines.append(EXCERPT_GAP)
    lines.extend(tail)
    return "\n".join(lines)
