"""Bind manual live-label selections to the final voice holding their speech."""

from __future__ import annotations

from typing import Any

from sqlmodel import col, select

from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.utils.canonical_pipeline.constants import ACTIVE_UTTERANCE_STATES
from backend.utils.live_speaker_labels import live_speaker_display_name
from backend.utils.speaker_names.anchors import lines_from_final_segments
from backend.utils.speaker_names.live import live_label_spans
from backend.utils.speaker_names.resolve import held_label_for_spans


def carry_live_speaker_assignments(
    session: Any, recording: Recording, final_segments: list[dict[str, Any]]
) -> None:
    """Resolve identities after final merges, before manual-lock inheritance.

    Reference speech excludes manually assigned lines, so a long corrected
    line cannot vote for its own target. Names use these same reference spans.
    An ambiguous or absent holder leaves the manual target as it stands.
    """
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording.id)
    ).first()
    # Live line corrections open only after settled publication. Ordinary
    # naming does not depend on the canonical live-line assignment path.
    if (getattr(transcript, "settling_state", None) or {}).get("settled") is not True:
        return
    recording_id = recording.id
    rows = (
        session.execute(
            select(TranscriptUtterance)
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(col(TranscriptUtterance.state).in_(ACTIVE_UTTERANCE_STATES))
            .where(col(TranscriptUtterance.manual_speaker_locked).is_(True))
        )
        .scalars()
        .all()
    )
    selected = [row for row in rows if live_speaker_display_name(row.speaker_label)]
    references = live_label_spans(
        session, recording_id, {str(row.speaker_label) for row in selected}
    )
    lines = lines_from_final_segments(final_segments)
    targets: dict[str, RecordingSpeaker] = {}
    for label, spans in references.items():
        holder = held_label_for_spans(spans, lines)
        if holder is None:
            continue
        speaker = session.execute(
            select(RecordingSpeaker)
            .where(RecordingSpeaker.recording_id == recording_id)
            .where(RecordingSpeaker.diarization_label == holder)
            .where(RecordingSpeaker.merged_into_id.is_(None))
        ).scalar_one_or_none()
        if speaker is not None:
            targets[label] = speaker
    for row in selected:
        target = targets.get(str(row.speaker_label))
        if target is not None:
            row.speaker_label = target.diarization_label
            row.recording_speaker_id = target.id
            session.add(row)
    session.flush()
