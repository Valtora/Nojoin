"""Live speaker labels on provisional live utterances.

The live lane commits its text first. This module then writes the speaker
labels in a second transaction: ``speaker_label`` becomes ``LIVE_PROVISIONAL_<n>`` (or back
to ``UNKNOWN``), the label's lock goes in ``confidence_payload`` under
``LIVE_SPEAKER_LABEL_PAYLOAD_KEY``, and each change appends an utterance event,
so the ``after_revision`` read returns the line again.

The transaction locks the transcript first, so its copy of the projection is
current and no other writer of it lands in between. Only provisional live
utterances without a manual speaker are touched, read ``FOR UPDATE SKIP
LOCKED``: a line a person is editing is left for the next run, so the label
step never waits on an edit while holding the transcript, and the edit, which
locks its line before checking its revision, sees the label. A skipped line
counts as open, so the settled prefix stops at it. The projection's speaker
fields are rebuilt from every live provisional row, settled or not, so a
projection written from an older copy is put right on the next run. No
``RecordingSpeaker`` row is created:
a live label is not a speaker of the recording, and the final pass never reads
one. Lines that ended before ``settled_ms`` already have their final label and
are not read again.

The settled cursor assumes lines commit in the order they end, which the live
lane does: a line committed later with an earlier end would never be read. A
segment PUT that moves an open line's end to or before the cursor leaves that
line with its unlocked label until finalize replaces it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import col, select

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.transcript import Transcript

from .constants import (
    LIVE_SPEAKER_LABEL_PAYLOAD_KEY,
    UNKNOWN_SPEAKER,
    _append_utterance_event,
    _load_transcript,
)
from .segmentation import _speaker_state_for_utterance

LIVE_SPEAKER_LABEL_EVENT = "live_speaker_label"


@dataclass(frozen=True)
class LiveLabelDecision:
    """The label a line should show now, and whether it can still change."""

    speaker: str
    locked: bool
    share: float | None


@dataclass(frozen=True)
class LiveLabelWrite:
    """What a write changed, and how far the settled prefix of lines reaches.

    ``open_from_s`` is the start of the earliest line whose label can still
    change, or None when every line read has its final label.
    """

    changed: int
    settled_ms: int
    open_from_s: float | None


# (start_ms, end_ms) of a line -> its label now.
DecideFn = Callable[[int, int], LiveLabelDecision]


def _stored_label(utterance: TranscriptUtterance) -> dict[str, Any] | None:
    stored = (utterance.confidence_payload or {}).get(LIVE_SPEAKER_LABEL_PAYLOAD_KEY)
    return stored if isinstance(stored, dict) else None


def _needs_write(
    utterance: TranscriptUtterance,
    stored: dict[str, Any] | None,
    decision: LiveLabelDecision,
) -> bool:
    current = utterance.speaker_label or UNKNOWN_SPEAKER
    if stored is None:
        # Never labelled: an unlabelled line stays as the lane wrote it.
        return decision.speaker != current
    return decision.speaker != current or bool(stored.get("locked")) != decision.locked


def _write_label(
    session, utterance: TranscriptUtterance, decision: LiveLabelDecision
) -> None:
    old_values = {
        "speaker_label": utterance.speaker_label,
        "revision": utterance.revision,
    }
    payload = dict(utterance.confidence_payload or {})
    payload[LIVE_SPEAKER_LABEL_PAYLOAD_KEY] = {
        "locked": decision.locked,
        "share": decision.share,
    }
    utterance.speaker_label = decision.speaker
    utterance.confidence_payload = payload
    utterance.revision += 1
    session.add(utterance)
    session.flush()
    _append_utterance_event(
        session,
        utterance=utterance,
        event_type=LIVE_SPEAKER_LABEL_EVENT,
        source="live",
        old_values=old_values,
        new_values={"speaker_label": decision.speaker, "locked": decision.locked},
        resulting_revision=utterance.revision,
    )


def _projected_speaker(utterance: TranscriptUtterance) -> dict[str, Any]:
    return {
        "speaker": utterance.speaker_label,
        "speaker_state": _speaker_state_for_utterance(utterance),
        "revision": utterance.revision,
    }


def _live_rows(session, recording_id: int) -> list[TranscriptUtterance]:
    """Every live provisional row, settled or not."""
    return list(
        session.execute(
            select(TranscriptUtterance)
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(TranscriptUtterance.state == TranscriptUtteranceState.PROVISIONAL)
            .where(TranscriptUtterance.source_kind == "live")
        )
        .scalars()
        .all()
    )


def _refresh_projection(
    session, transcript: Transcript, rows: list[TranscriptUtterance]
) -> None:
    """Write the rows' speaker fields into the projection, if any differ."""
    by_id = {utterance.public_id: utterance for utterance in rows}
    segments = [dict(segment) for segment in (transcript.segments or [])]
    stale = False
    for segment in segments:
        utterance = by_id.get(str(segment.get("id")))
        if utterance is None:
            continue
        fields = _projected_speaker(utterance)
        if any(segment.get(key) != value for key, value in fields.items()):
            segment.update(fields)
            stale = True
    if not stale:
        return
    transcript.segments = segments
    flag_modified(transcript, "segments")
    session.add(transcript)


def _unsettled(statement, recording_id: int, settled_ms: int):
    """Live lines that may still need a label, in the order they ended."""
    return (
        statement.where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.state == TranscriptUtteranceState.PROVISIONAL)
        .where(TranscriptUtterance.source_kind == "live")
        .where(col(TranscriptUtterance.manual_speaker_locked).is_(False))
        .where(TranscriptUtterance.end_ms > settled_ms)
        .order_by(col(TranscriptUtterance.end_ms), col(TranscriptUtterance.id))
    )


def _label_line(
    session, utterance: TranscriptUtterance, decide: DecideFn
) -> tuple[bool, bool]:
    """Write the line's label if it changed: (label is final, label written)."""
    stored = _stored_label(utterance)
    if stored is not None and stored.get("locked") is True:
        return True, False
    decision = decide(utterance.start_ms, utterance.end_ms)
    written = _needs_write(utterance, stored, decision)
    if written:
        _write_label(session, utterance, decision)
    return decision.locked, written


def apply_live_speaker_labels(
    session, *, recording_id: int, settled_ms: int, decide: DecideFn
) -> LiveLabelWrite:
    """Bring every unsettled live line's label up to date. The caller commits."""
    transcript = _load_transcript(session, recording_id, lock=True)
    held = {
        utterance.id: utterance
        for utterance in session.execute(
            _unsettled(
                select(TranscriptUtterance), recording_id, settled_ms
            ).with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    }
    # Read after the lock, so it holds every locked line; a line missing from
    # the locked ones is held by someone else.
    candidates = session.execute(
        _unsettled(
            select(
                TranscriptUtterance.id,
                TranscriptUtterance.start_ms,
                TranscriptUtterance.end_ms,
            ),
            recording_id,
            settled_ms,
        )
    ).all()
    changed = 0
    settled = settled_ms
    open_from_s: float | None = None
    for row_id, start_ms, end_ms in candidates:
        utterance = held.get(row_id)
        if utterance is None:
            # Skipped, so still open: the settled prefix stops here, and the
            # next run reads the line again.
            final = False
        else:
            final, written = _label_line(session, utterance, decide)
            changed += written
        if not final:
            start_s = start_ms / 1000.0
            open_from_s = start_s if open_from_s is None else min(open_from_s, start_s)
        elif open_from_s is None:
            settled = max(settled, end_ms)
    if transcript is not None:
        # Every row, not only the lines read here: an edit holding an older copy
        # of the projection may have written it back over settled lines.
        _refresh_projection(session, transcript, _live_rows(session, recording_id))
    return LiveLabelWrite(changed=changed, settled_ms=settled, open_from_s=open_from_s)
