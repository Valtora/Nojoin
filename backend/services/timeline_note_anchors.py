"""Check source selections and drag destinations without restricting typed notes."""

from sqlalchemy.orm import Session

from backend.models.timeline_note import TimelineNoteKind
from backend.models.timeline_note_public import TimelineNoteCreate, TimelineNoteUpdate
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.canonical_pipeline.guards import (
    LineActionConflict,
    require_action_lines,
    require_source_line,
    settling_required,
)
from backend.utils.canonical_pipeline.scope import _lock_active_lines


def guard_note_anchor(
    session: Session,
    recording_id: int,
    payload: TimelineNoteCreate | TimelineNoteUpdate,
) -> None:
    """Take Transcript first, then resolve every active line of a source scope."""
    selection = (
        isinstance(payload, TimelineNoteCreate)
        and payload.kind == TimelineNoteKind.SELECTION
    )
    target_id = payload.target_utterance_id
    moving = isinstance(payload, TimelineNoteUpdate) and payload.anchor_ms is not None
    if not (selection or target_id or moving):
        return
    transcript = _load_transcript(session, recording_id, lock=True)
    if not settling_required(transcript):
        return
    rows = _lock_active_lines(session, recording_id)
    if target_id:
        row = require_source_line(
            session, transcript, target_id, payload.target_utterance_revision
        )
        if (
            row is None
            or payload.anchor_ms is None
            or not row.start_ms <= payload.anchor_ms <= row.end_ms
        ):
            raise LineActionConflict(
                "line_stale",
                "This note's destination changed. Reload it and try again.",
            )
        return
    if selection:
        selected = [
            row
            for row in rows
            if row.start_ms < payload.end_ms and row.end_ms > payload.start_ms
        ]
        for reference in payload.source_utterances or []:
            row = require_source_line(
                session, transcript, reference.id, reference.revision
            )
            if row not in selected:
                raise LineActionConflict(
                    "line_stale", "The selected lines changed. Select the text again."
                )
    else:
        selected = [
            row for row in rows if row.start_ms <= payload.anchor_ms < row.end_ms
        ]
    if not selected:
        raise LineActionConflict(
            "line_stale", "The source line changed. Reload it and try again."
        )
    require_action_lines(transcript, selected)
