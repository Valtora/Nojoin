"""Advance transcript hints once per transaction publishing public facts.

The flush boundary is shared by REST, MCP, workers and compatibility writers.
It also covers status and name-only writes, which have no utterance event. The
counter is ordinary transactional data: neither a delivery cursor nor an audit
event. Publication entry points acquire the Transcript fence before line locks.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import event, inspect, select, update
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.orm.attributes import set_committed_value

from backend.models.base import BaseDBModel
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceEvent
from backend.models.speaker import GlobalSpeaker, GlobalSpeakerRead, RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.transcript import Transcript
from backend.services.transcript_events_publish import publish_transcript_changes

_PUBLICATION_SCOPES = "transcript_publication_scopes"
_LINE_FIELDS = (
    "text",
    "speaker_label",
    "recording_speaker_id",
    "start_ms",
    "end_ms",
    "sort_key",
    "state",
    "revision",
    "manual_text_locked",
    "manual_speaker_locked",
)
_SPEAKER_FIELDS = (
    "name",
    "local_name",
    "global_speaker_id",
    "diarization_label",
    "merged_into_id",
    "name_source",
    "identity_locked",
    "identity_confidence",
    "speaker_status",
    "color",
    "speaker_kind",
    "first_seen_ms",
    "last_seen_ms",
    "name_last_edit_source",
)
# The nested public speaker response serialises GlobalSpeakerRead. Watch its
# mutable stored columns, including CRM, person colour and voiceprint lock.
# Stable ids/timestamps are not independent facts; computed availability is
# compared separately, and tags are not mapped person columns.
_PERSON_FIELDS = tuple(
    field
    for field in GlobalSpeakerRead.model_fields
    if field not in BaseDBModel.model_fields
    and field in inspect(GlobalSpeaker).column_attrs
)


def _changed(row: Any, fields: Iterable[str]) -> bool:
    return any(inspect(row).attrs[field].history.has_changes() for field in fields)


def _speaker_changed(session: Session, row: Any, fields: Iterable[str]) -> bool:
    """Compare expired assignments without refreshing pending speaker edits."""
    changed = [field for field in fields if _changed(row, (field,))]
    if not changed:
        return False
    stored = (
        session.connection()
        .execute(
            select(*(getattr(type(row), field) for field in changed)).where(
                type(row).id == row.id
            )
        )
        .one_or_none()
    )
    return stored is None or any(
        value != getattr(row, field)
        for field, value in zip(changed, stored, strict=True)
    )


def _voiceprint_changed(session: Session, row: Any) -> bool:
    """Publish availability changes, keeping private vector updates invisible."""
    history = inspect(row).attrs.embedding.history
    if not history.has_changes():
        return False
    previous = (
        session.connection()
        .execute(select(type(row).embedding).where(type(row).id == row.id))
        .scalar_one_or_none()
    )
    before = previous is not None and len(previous) > 0
    current = row.embedding
    after = current is not None and len(current) > 0
    return before != after


def _public_segments(segments: Any) -> Any:
    """Exclude private inference evidence from the compatibility projection."""
    if not isinstance(segments, list):
        return segments
    return [
        {
            key: value
            for key, value in segment.items()
            if key not in {"confidence_payload", "updated_at"}
        }
        if isinstance(segment, dict)
        else segment
        for segment in segments
    ]


def _transcript_changed(session: Session, row: Transcript) -> bool:
    fields = ("text", "segments", "transcript_status")
    if row.transcript_status == "error":
        fields += ("error_message",)
    changed = [field for field in fields if _changed(row, (field,))]
    if not changed:
        return False
    # flag_modified is used by projection writers even when nothing differs.
    # Compare scalar columns without refreshing away pending ORM changes.
    stored = (
        session.connection()
        .execute(
            select(*(getattr(Transcript, field) for field in changed)).where(
                Transcript.id == row.id
            )
        )
        .one_or_none()
    )
    if stored is None:
        return True
    return any(
        (_public_segments(value) != _public_segments(getattr(row, field)))
        if field == "segments"
        else value != getattr(row, field)
        for field, value in zip(changed, stored, strict=True)
    )


def _publishes_row(session: Session, row: Any) -> bool:
    fresh = row in session.new or row in session.deleted
    if isinstance(row, TranscriptUtterance):
        return fresh or _changed(row, _LINE_FIELDS)
    if isinstance(row, RecordingSpeaker):
        return (
            fresh
            or _speaker_changed(session, row, _SPEAKER_FIELDS)
            or _voiceprint_changed(session, row)
        )
    if isinstance(row, TranscriptUtteranceEvent):
        return row in session.new
    return isinstance(row, SpeakerNameEvent) and fresh and row.surface == "live"


def _candidate_speakers(session: Session, row: Any) -> list[Any]:
    """Identify possible speaker changes without reading their stored values."""
    if isinstance(row, GlobalSpeaker) and row.id is None:
        return []
    fields = (
        (*_PERSON_FIELDS, "embedding")
        if isinstance(row, GlobalSpeaker)
        else (*_SPEAKER_FIELDS, "embedding")
    )
    fresh = row in session.new or row in session.deleted
    return [row] if fresh or _changed(row, fields) else []


def _affected_recordings(session: Session) -> set[int]:
    recording_ids: set[int] = set()
    people: list[GlobalSpeaker] = []
    speakers: list[RecordingSpeaker] = []
    transcripts: list[Transcript] = []
    published = _published_in_scope(session)
    for row in [*session.new, *session.dirty, *session.deleted]:
        if getattr(row, "recording_id", None) in published:
            continue
        fresh = row in session.new or row in session.deleted
        if isinstance(row, Transcript):
            if not fresh:
                if _changed(
                    row, ("text", "segments", "transcript_status", "error_message")
                ):
                    transcripts.append(row)
                continue
            publishes = True
        elif isinstance(row, GlobalSpeaker):
            people.extend(_candidate_speakers(session, row))
            continue
        elif isinstance(row, RecordingSpeaker):
            speakers.extend(_candidate_speakers(session, row))
            continue
        else:
            publishes = _publishes_row(session, row)
        if publishes and row.recording_id is not None:
            recording_ids.add(row.recording_id)
    linked = _person_recordings(session, people)
    recording_ids.difference_update(published)
    candidates = recording_ids | {row.recording_id for row in [*transcripts, *speakers]}
    candidates.update(recording_id for ids in linked.values() for recording_id in ids)
    # A dirty expired assignment can appear unchanged until a concurrent writer
    # commits. Fence candidates before deciding whether the stored fact differs.
    lock_transcript_publications(session, candidates - published)
    recording_ids.update(
        row.recording_id for row in speakers if _publishes_row(session, row)
    )
    recording_ids.update(
        recording_id
        for person in people
        if (
            person in session.deleted
            or _speaker_changed(session, person, _PERSON_FIELDS)
            or _voiceprint_changed(session, person)
        )
        for recording_id in linked.get(person.id, set())
    )
    recording_ids.update(
        row.recording_id for row in transcripts if _transcript_changed(session, row)
    )
    return recording_ids


def _person_recordings(
    session: Session, people: list[GlobalSpeaker]
) -> dict[int, set[int]]:
    linked: dict[int, set[int]] = {}
    if people:
        rows = session.connection().execute(
            select(
                RecordingSpeaker.global_speaker_id, RecordingSpeaker.recording_id
            ).where(RecordingSpeaker.global_speaker_id.in_([row.id for row in people]))
        )
        for person_id, recording_id in rows:
            linked.setdefault(person_id, set()).add(recording_id)
    return linked


def _published_in_scope(session: Session) -> set[int]:
    scopes = session.info.get(_PUBLICATION_SCOPES, {})
    scope = session.get_nested_transaction() or session.get_transaction()
    published: set[int] = set()
    while scope is not None:
        published.update(scopes.get(scope, set()))
        scope = scope.parent
    return published


def lock_transcript_publications(
    session: Session, recording_ids: Iterable[int]
) -> None:
    """Fence a multi-recording name operation in one deterministic order.

    Scalar reads preserve callers' pending ORM fields. Call before the first
    mutation when one operation touches several recordings, such as People merge.
    """
    ids = sorted(set(recording_ids))
    if ids:
        session.connection().execute(
            select(Transcript.id)
            .where(Transcript.recording_id.in_(ids))
            .order_by(Transcript.recording_id)
            .with_for_update()
        ).all()


def bump_transcript_change(session: Session, recording_id: int) -> None:
    """Publish one sequence change, including across flushes and savepoints.

    A committed savepoint merges its publication set into its parent. A rolled
    back savepoint discards it, just as PostgreSQL discards its counter update.
    The next write can then increment the current database value under the fence.
    """
    scope = session.get_nested_transaction() or session.get_transaction()
    if scope is None:
        raise RuntimeError("Transcript publication requires a transaction")
    scopes: dict[SessionTransaction, dict[int, int]] = session.info.setdefault(
        _PUBLICATION_SCOPES, {}
    )
    ancestor: SessionTransaction | None = scope
    while ancestor is not None:
        if recording_id in scopes.get(ancestor, set()):
            return
        ancestor = ancestor.parent

    pending = next(
        (
            row
            for row in session.new
            if isinstance(row, Transcript) and row.recording_id == recording_id
        ),
        None,
    )
    if pending is not None:
        pending.change_sequence = 1
        sequence = 1
    else:
        connection = session.connection()
        stored = connection.execute(
            select(Transcript.id, Transcript.change_sequence)
            .where(Transcript.recording_id == recording_id)
            .with_for_update()
        ).one_or_none()
        if stored is None:
            return
        sequence = stored.change_sequence + 1
        connection.execute(
            update(Transcript)
            .where(Transcript.id == stored.id)
            .values(change_sequence=sequence)
        )
        for row in session.identity_map.values():
            if isinstance(row, Transcript) and row.id == stored.id:
                set_committed_value(row, "change_sequence", sequence)
    scopes.setdefault(scope, {})[recording_id] = sequence


@event.listens_for(Session, "before_flush")
def _publish_flush(session: Session, flush_context: Any, instances: Any) -> None:
    if session.info.get("restoring_transcripts"):
        return
    for recording_id in sorted(_affected_recordings(session)):
        bump_transcript_change(session, recording_id)


@event.listens_for(Session, "after_commit")
def _merge_publications(session: Session) -> None:
    scope = session.get_nested_transaction()
    scopes = session.info.get(_PUBLICATION_SCOPES, {})
    if scope is not None and scope.parent is not None:
        scopes.setdefault(scope.parent, {}).update(scopes.pop(scope, {}))
    elif scope is None:
        # The transaction is committed: querying here is forbidden. Capture
        # sequences at the flush boundary and send only primitive values.
        changes = scopes.get(session.get_transaction(), {})
        if changes:
            publish_transcript_changes(dict(changes))


@event.listens_for(Session, "after_rollback")
def _discard_publications(session: Session) -> None:
    scope = session.get_nested_transaction() or session.get_transaction()
    discarded = session.info.get(_PUBLICATION_SCOPES, {}).pop(scope, set())
    for row in session.identity_map.values():
        if (
            isinstance(row, Transcript)
            and inspect(row).dict.get("recording_id") in discarded
        ):
            # The counter is updated through scalar SQL so it cannot overwrite
            # pending projection fields. ORM savepoint tracking does not see
            # that update on a name-only publication; expire its cached copy.
            session.expire(row, ["change_sequence"])


@event.listens_for(Session, "after_transaction_end")
def _forget_publications(session: Session, transaction: SessionTransaction) -> None:
    scopes = session.info.get(_PUBLICATION_SCOPES, {})
    scopes.pop(transaction, None)
    if transaction.parent is None:
        session.info.pop(_PUBLICATION_SCOPES, None)
