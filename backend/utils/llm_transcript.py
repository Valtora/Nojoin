"""The transcript of one recording, rendered as text for an LLM prompt.

Meeting Chat sends a recording's whole transcript with every turn. This module
is the one place that reads it: the segments come from
``build_transcript_segments_for_read`` (the canonical utterances, or the
``Transcript.segments`` projection for a recording without them), speaker
names from ``build_recording_speaker_map`` as for notes generation, and the
line format from ``format_segments_for_llm``. Lines carry the start time only
(``[MM:SS]``), the form the chat prompt asks the model to cite, because the
transcript is resent with every turn and the end time would add roughly a sixth
to its size.
"""

from __future__ import annotations

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
