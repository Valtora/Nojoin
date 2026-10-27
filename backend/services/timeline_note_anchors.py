"""Check source selections and drag destinations without restricting typed notes."""

from sqlalchemy.orm import Session

from backend.models.pipeline import TranscriptUtterance
from backend.models.timeline_note import TimelineNoteKind
from backend.models.timeline_note_public import (
    SourceUtteranceReference,
    TimelineNoteCreate,
    TimelineNoteUpdate,
)
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.canonical_pipeline.guards import (
    LineActionConflict,
    line_effectively_locked,
    settling_required,
)
from backend.utils.canonical_pipeline.live_settling_projection import row_words
from backend.utils.canonical_pipeline.scope import _lock_active_lines


def _source_row(
    rows: list[TranscriptUtterance], reference: SourceUtteranceReference
) -> TranscriptUtterance:
    """A current active source; stable selected words survive unrelated revisions."""
    row = next((item for item in rows if item.public_id == reference.id), None)
    if (
        row is None
        or (row.revision != reference.revision and not reference.word_ids)
        or row.revision < reference.revision
    ):
        raise LineActionConflict(
            "line_stale", "The selected lines changed. Select the text again."
        )
    return row


def _require_selected_words(
    row: TranscriptUtterance,
    payload: TimelineNoteCreate,
    reference: SourceUtteranceReference | None,
) -> None:
    """Inspect exact selected identities, with conservative timing for older clients."""
    assert payload.start_ms is not None and payload.end_ms is not None
    words = row_words(row)
    if reference is not None and reference.word_ids:
        by_id = {word.get("id"): word for word in words}
        if any(word_id not in by_id for word_id in reference.word_ids):
            raise LineActionConflict(
                "line_stale", "The selected words changed. Select the text again."
            )
        selected = [by_id[word_id] for word_id in reference.word_ids]
    else:
        metadata = (row.confidence_payload or {}).get("live_text_settling")
        if not isinstance(metadata, dict) and line_effectively_locked(row):
            return
        selected = [
            word
            for word in words
            if word.get("start_ms") is None
            or word.get("end_ms") is None
            or (word["start_ms"] < payload.end_ms and word["end_ms"] > payload.start_ms)
            or (
                word["start_ms"] == word["end_ms"]
                and payload.start_ms <= word["start_ms"] < payload.end_ms
            )
        ]
        if not selected and line_effectively_locked(row):
            return
    if not selected or any(word.get("locked") is not True for word in selected):
        raise LineActionConflict(
            "line_settling",
            "Some selected words are still settling. Try again once they lock.",
        )


def _guard_selection_sources(
    rows: list[TranscriptUtterance], payload: TimelineNoteCreate
) -> None:
    """Validate the complete word scope before the note is inserted."""
    assert payload.start_ms is not None and payload.end_ms is not None
    if payload.source_utterances and all(
        reference.word_ids for reference in payload.source_utterances
    ):
        # Word identities, including terminal zero-duration punctuation, are
        # authoritative even when their note span touches the line's end.
        for reference in payload.source_utterances:
            _require_selected_words(_source_row(rows, reference), payload, reference)
        return
    selected = [
        row
        for row in rows
        if row.start_ms < payload.end_ms and row.end_ms > payload.start_ms
    ]
    if not selected:
        raise LineActionConflict(
            "line_stale", "The source line changed. Reload it and try again."
        )
    if payload.source_utterances:
        for reference in payload.source_utterances:
            row = _source_row(rows, reference)
            if row not in selected:
                raise LineActionConflict(
                    "line_stale", "The selected lines changed. Select the text again."
                )
            _require_selected_words(row, payload, reference)
    if not payload.source_utterances or any(
        not reference.word_ids for reference in payload.source_utterances
    ):
        for row in selected:
            _require_selected_words(row, payload, None)


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
    if not (selection or target_id):
        return
    transcript = _load_transcript(session, recording_id, lock=True)
    if not settling_required(transcript):
        return
    rows = _lock_active_lines(session, recording_id)
    if target_id:
        assert payload.target_utterance_revision is not None
        row = _source_row(
            rows,
            SourceUtteranceReference(
                id=target_id, revision=payload.target_utterance_revision
            ),
        )
        if (
            payload.anchor_ms is None
            or not row.start_ms <= payload.anchor_ms <= row.end_ms
        ):
            raise LineActionConflict(
                "line_stale",
                "This note's destination changed. Reload it and try again.",
            )
        return
    assert isinstance(payload, TimelineNoteCreate)
    _guard_selection_sources(rows, payload)
