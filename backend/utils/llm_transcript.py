"""The transcript of one recording, rendered as text for an LLM prompt.

Meeting Chat sends a recording's whole transcript with every turn. This module
is the one place that reads it: the segments come from
``build_transcript_segments_for_read`` (the canonical utterances, or the
``Transcript.segments`` projection for a recording without them), speaker
names from the recording's speakers, and the line format from
``format_segments_for_llm``. Lines carry the start time only (``[MM:SS]``), the
form the chat prompt asks the model to cite, because the transcript is resent
with every turn and the end time would add roughly a sixth to its size.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session, selectinload
from sqlmodel import select

from backend.models.speaker import RecordingSpeaker
from backend.utils.canonical_pipeline import build_transcript_segments_for_read
from backend.utils.meeting_notes import (
    build_recording_speaker_map,
    format_segments_for_llm,
)


def load_recording_speakers_for_llm(
    session: Session, recording_id: int
) -> list[RecordingSpeaker]:
    """A recording's speakers with their global speakers, in a fixed order.

    The global speaker is loaded up front because name resolution reads it for
    every linked speaker; lazy loading it costs one query per speaker.
    """
    statement = (
        select(RecordingSpeaker)
        .where(RecordingSpeaker.recording_id == recording_id)
        .options(selectinload(RecordingSpeaker.global_speaker))
        .order_by(RecordingSpeaker.id)
    )
    return list(session.execute(statement).scalars().all())


def render_transcript_for_llm(session: Session, recording_id: int) -> Optional[str]:
    """The recording's transcript as prompt text, or None when it has none."""
    segments = build_transcript_segments_for_read(session, recording_id)
    if not segments:
        return None
    speakers = load_recording_speakers_for_llm(session, recording_id)
    return format_segments_for_llm(
        segments, build_recording_speaker_map(speakers), with_end=False
    )
