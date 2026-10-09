"""The transcript of one recording, rendered as text for an LLM prompt.

Meeting Chat sends a recording's whole transcript with every turn. This module
is the one place that reads it: the segments come from
``build_transcript_segments_for_read`` (the canonical utterances, or the
``Transcript.segments`` projection for a recording without them), speaker
names are resolved the way the transcript view resolves them, and the line
format comes from ``format_segments_for_llm``. Lines carry the start time only
(``[MM:SS]``), the form the chat prompt asks the model to cite, because the
transcript is resent with every turn and the end time would add roughly a sixth
to its size.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from sqlalchemy.orm import Session, selectinload
from sqlmodel import select

from backend.models.speaker import RecordingSpeaker
from backend.utils.canonical_pipeline import (
    build_transcript_segments_for_read,
    filter_recording_speakers_for_public_read,
)
from backend.utils.meeting_notes import (
    format_segments_for_llm,
    resolve_recording_speaker_name,
)

# Generic names the view also accepts for a label: live labels count from
# "Speaker 0", diarisation labels from "Speaker 1".
_GENERIC_LABEL_ALIASES = (
    (re.compile(r"^LIVE_(\d+)$"), 0),
    (re.compile(r"^SPEAKER_(\d+)$"), 1),
)


def _generic_aliases(label: Optional[str]) -> list[str]:
    for pattern, offset in _GENERIC_LABEL_ALIASES:
        match = pattern.match(label or "")
        if match:
            return [f"Speaker {int(match.group(1)) + offset}"]
    return []


def build_view_speaker_map(speakers: Iterable[RecordingSpeaker]) -> dict[str, str]:
    """Map every name a segment may carry for a speaker to the name shown.

    Mirrors ``buildRecordingSpeakerDisplayMap`` in
    ``frontend/src/lib/recordingSpeakerUtils.ts``: a segment may name its
    speaker by diarisation label, by a legacy or local name, by the linked
    global speaker's name, or by a generic "Speaker N", and all of them resolve
    to the speaker's current name. A later speaker wins a shared alias, as in
    the view.
    """
    speaker_map: dict[str, str] = {}
    for speaker in speakers:
        display_name = resolve_recording_speaker_name(speaker)
        if not display_name:
            continue
        global_speaker = speaker.global_speaker
        aliases = [
            speaker.diarization_label,
            speaker.name,
            speaker.local_name,
            global_speaker.name if global_speaker is not None else None,
            *_generic_aliases(speaker.diarization_label),
        ]
        for alias in aliases:
            if alias:
                speaker_map[alias] = display_name
    return speaker_map


def _load_recording_speakers(
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
    """The recording's transcript as prompt text, or None when it has none.

    Speakers are the ones the transcript view lists: merged speakers are left
    out, and so are speakers without an active utterance when the recording
    has canonical utterances.
    """
    segments = build_transcript_segments_for_read(session, recording_id)
    if not segments:
        return None
    speakers = filter_recording_speakers_for_public_read(
        session, recording_id, _load_recording_speakers(session, recording_id)
    )
    return format_segments_for_llm(
        segments, build_view_speaker_map(speakers), with_end=False
    )
