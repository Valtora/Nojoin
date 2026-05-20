from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any, Iterable, Sequence
from uuid import uuid4

from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from backend.models.pipeline import (
    DiarizationWindowResult,
    DiarizationWindowTurn,
    ProcessingRun,
    ProcessingRunKind,
    ProcessingRunStatus,
    RecordingSpeakerAlias,
    RecordingSpeakerAliasType,
    SpeakerCorrectionEvent,
    SpeakerCorrectionEventType,
    SpeakerCorrectionScope,
    TranscriptUtterance,
    TranscriptUtteranceEvent,
    TranscriptUtteranceState,
)
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.processing.embedding import merge_embeddings
from backend.utils.speaker_assignment import matches_speaker_name, reconcile_segment_assignment
from backend.utils.time import utc_now


IN_FLIGHT_RECORDING_STATUSES = {
    RecordingStatus.UPLOADING.value,
    RecordingStatus.QUEUED.value,
    RecordingStatus.PROCESSING.value,
}

ACTIVE_UTTERANCE_STATES = {
    TranscriptUtteranceState.PROVISIONAL.value,
    TranscriptUtteranceState.STABLE.value,
    TranscriptUtteranceState.FINALIZED.value,
}

TOMBSTONE_UTTERANCE_STATES = {
    TranscriptUtteranceState.SUPERSEDED.value,
    TranscriptUtteranceState.DELETED.value,
}

UNKNOWN_SPEAKER = "UNKNOWN"
LABEL_PATTERN = re.compile(r"^(LIVE_\d+|SPEAKER_\d+|MANUAL_[A-Za-z0-9]+)$")

ROLLING_DIARIZATION_MANUAL_WEIGHT = 6.0
ROLLING_DIARIZATION_CONTINUITY_WEIGHT = 4.0
ROLLING_DIARIZATION_UTTERANCE_WEIGHT = 2.0
ROLLING_DIARIZATION_EMBEDDING_WEIGHT = 2000.0
ROLLING_DIARIZATION_GLOBAL_WEIGHT = 1500.0
ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS = 250
ROLLING_DIARIZATION_MIN_TURN_MATCH_MARGIN = 150.0
ROLLING_DIARIZATION_CONFIDENCE_FLOOR = 0.55
ROLLING_DIARIZATION_EXISTING_CONFIDENCE_MARGIN = 0.15
ROLLING_DIARIZATION_STABLE_WINDOW_COUNT = 2
ROLLING_DIARIZATION_MERGE_BOUNDARY_GAP_MS = 150
ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL = "provisional"
ROLLING_DIARIZATION_SPEAKER_STATE_STABLE = "stable"
ROLLING_DIARIZATION_SPEAKER_STATE_MANUAL_OVERRIDE = "manual_override"


def recording_ready_for_canonical_backfill(status: RecordingStatus | str | None) -> bool:
    if status is None:
        return False
    status_value = status.value if isinstance(status, RecordingStatus) else str(status)
    return status_value not in IN_FLIGHT_RECORDING_STATUSES


def list_active_utterances(session, recording_id: int) -> list[TranscriptUtterance]:
    statement = (
        select(TranscriptUtterance)
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.state.in_(ACTIVE_UTTERANCE_STATES))
        .order_by(TranscriptUtterance.sort_key, TranscriptUtterance.id)
    )
    return list(session.execute(statement).scalars().all())


def list_utterances_for_processing_run(session, processing_run_id: int) -> list[TranscriptUtterance]:
    statement = (
        select(TranscriptUtterance)
        .where(TranscriptUtterance.processing_run_id == processing_run_id)
        .where(TranscriptUtterance.state.in_(ACTIVE_UTTERANCE_STATES))
        .order_by(TranscriptUtterance.sort_key, TranscriptUtterance.id)
    )
    return list(session.execute(statement).scalars().all())


def get_canonical_transcript_revision(session, recording_id: int) -> int:
    statement = select(func.max(TranscriptUtteranceEvent.id)).where(
        TranscriptUtteranceEvent.recording_id == recording_id
    )
    revision = session.execute(statement).scalar_one_or_none()
    return int(revision or 0)


def _append_utterance_event(
    session,
    *,
    utterance: TranscriptUtterance,
    event_type: str,
    source: str,
    old_values: dict[str, Any] | None,
    new_values: dict[str, Any] | None,
    resulting_revision: int,
    processing_run_id: int | None = None,
    actor_user_id: int | None = None,
) -> TranscriptUtteranceEvent:
    event = TranscriptUtteranceEvent(
        recording_id=utterance.recording_id,
        utterance_id=utterance.id,
        processing_run_id=processing_run_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        source=source,
        old_values=old_values,
        new_values=new_values,
        resulting_revision=resulting_revision,
    )
    session.add(event)
    session.flush()
    utterance.last_utterance_event_id = event.id
    session.add(utterance)
    return event


def _record_manual_lock_events(
    session,
    *,
    utterance: TranscriptUtterance,
    old_text_locked: bool,
    old_speaker_locked: bool,
    source: str,
    actor_user_id: int | None = None,
    processing_run_id: int | None = None,
) -> None:
    if not old_text_locked and utterance.manual_text_locked:
        _append_utterance_event(
            session,
            utterance=utterance,
            event_type="manual_lock_text",
            source=source,
            old_values={"manual_text_locked": False},
            new_values={"manual_text_locked": True},
            resulting_revision=utterance.revision,
            processing_run_id=processing_run_id,
            actor_user_id=actor_user_id,
        )
    if not old_speaker_locked and utterance.manual_speaker_locked:
        _append_utterance_event(
            session,
            utterance=utterance,
            event_type="manual_lock_speaker",
            source=source,
            old_values={"manual_speaker_locked": False},
            new_values={"manual_speaker_locked": True},
            resulting_revision=utterance.revision,
            processing_run_id=processing_run_id,
            actor_user_id=actor_user_id,
        )


def _creation_event_type(
    *,
    source: str,
    state: TranscriptUtteranceState | str | None,
) -> str:
    if source == "backfill":
        return "backfilled"
    state_value = state.value if hasattr(state, "value") else str(state or "")
    if source == "finalize" or state_value == TranscriptUtteranceState.FINALIZED.value:
        return "finalize"
    return "created"


def _compatibility_replace_event_type(
    old_values: dict[str, Any],
    new_values: dict[str, Any],
) -> str:
    timing_changed = (
        old_values.get("start_ms") != new_values.get("start_ms")
        or old_values.get("end_ms") != new_values.get("end_ms")
    )
    text_changed = old_values.get("text") != new_values.get("text")
    speaker_changed = old_values.get("speaker") != new_values.get("speaker")

    if timing_changed and not text_changed and not speaker_changed:
        return "update_timing"
    if text_changed and not timing_changed and not speaker_changed:
        return "update_text"
    if speaker_changed and not timing_changed and not text_changed:
        return "update_speaker"
    return "compatibility_replace"


def serialize_canonical_delta(
    session,
    recording_id: int,
    *,
    after_revision: int | None = None,
) -> tuple[int, list[dict[str, Any]], list[str]]:
    revision = get_canonical_transcript_revision(session, recording_id)
    if after_revision is None or after_revision <= 0:
        return revision, serialize_canonical_utterances(session, recording_id), []

    event_rows = session.execute(
        select(
            TranscriptUtteranceEvent.event_type,
            TranscriptUtterance.public_id,
            TranscriptUtterance.state,
        )
        .join(
            TranscriptUtterance,
            TranscriptUtterance.id == TranscriptUtteranceEvent.utterance_id,
        )
        .where(TranscriptUtteranceEvent.recording_id == recording_id)
        .where(TranscriptUtteranceEvent.id > after_revision)
        .order_by(TranscriptUtteranceEvent.id)
    ).all()

    changed_public_ids: set[str] = set()
    tombstones: list[str] = []
    tombstone_ids: set[str] = set()

    for event_type, public_id, state in event_rows:
        if not public_id:
            continue
        state_value = state.value if hasattr(state, "value") else str(state)
        if event_type in {"supersede", "delete"} or state_value in TOMBSTONE_UTTERANCE_STATES:
            changed_public_ids.discard(public_id)
            if public_id not in tombstone_ids:
                tombstone_ids.add(public_id)
                tombstones.append(public_id)
            continue
        if state_value in ACTIVE_UTTERANCE_STATES:
            changed_public_ids.add(public_id)

    return (
        revision,
        serialize_canonical_utterances(
            session,
            recording_id,
            only_public_ids=changed_public_ids,
        ),
        tombstones,
    )


def ensure_processing_run(
    session,
    *,
    recording_id: int,
    run_kind: ProcessingRunKind,
    status: ProcessingRunStatus = ProcessingRunStatus.COMPLETED,
    trigger_source: str = "system",
    reused_live_asr: bool = False,
    config_hash: str | None = None,
    transcription_backend: str | None = None,
    diarization_backend: str | None = None,
    model_metadata: dict[str, Any] | None = None,
    span_start_ms: int | None = None,
    span_end_ms: int | None = None,
    idempotency_key: str | None = None,
) -> ProcessingRun:
    if idempotency_key:
        existing_run = session.execute(
            select(ProcessingRun)
            .where(ProcessingRun.recording_id == recording_id)
            .where(ProcessingRun.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing_run is not None:
            return existing_run

    processing_run = ProcessingRun(
        recording_id=recording_id,
        run_kind=run_kind,
        trigger_source=trigger_source,
        status=status,
        reused_live_asr=reused_live_asr,
        config_hash=config_hash,
        transcription_backend=transcription_backend,
        diarization_backend=diarization_backend,
        model_metadata=model_metadata,
        span_start_ms=span_start_ms,
        span_end_ms=span_end_ms,
        idempotency_key=idempotency_key,
        started_at=utc_now(),
        completed_at=utc_now() if status == ProcessingRunStatus.COMPLETED else None,
    )
    session.add(processing_run)
    session.flush()
    return processing_run


def _processing_run_idempotency_key(
    *,
    run_kind: ProcessingRunKind,
    source: str,
    segments: Sequence[dict[str, Any]],
    state_override: TranscriptUtteranceState | None,
    reused_live_asr: bool,
) -> str:
    state_value = state_override.value if hasattr(state_override, "value") else str(state_override or "")
    signature = {
        "run_kind": run_kind.value,
        "source": source,
        "state": state_value,
        "reused_live_asr": bool(reused_live_asr),
        "segments": [
            {
                "start_ms": _segment_to_ms(segment.get("start", 0.0)),
                "end_ms": _segment_to_ms(segment.get("end", 0.0)),
                "speaker": str(segment.get("speaker") or UNKNOWN_SPEAKER),
                "text": str(segment.get("text", "") or ""),
                "segment_source": str(segment.get("segment_source") or source),
            }
            for segment in segments
        ],
    }
    digest = hashlib.sha256(
        json.dumps(signature, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return f"{run_kind.value}:{digest}"


def ensure_recording_speaker_aliases_for_speaker(
    session,
    recording_speaker: RecordingSpeaker,
    *,
    source_run_id: int | None = None,
) -> None:
    existing_rows = session.execute(
        select(RecordingSpeakerAlias).where(
            RecordingSpeakerAlias.recording_speaker_id == recording_speaker.id
        )
    ).scalars().all()
    existing = {
        (
            row.alias_type.value if hasattr(row.alias_type, "value") else str(row.alias_type),
            row.alias_value,
        )
        for row in existing_rows
    }

    candidates: list[tuple[RecordingSpeakerAliasType, str]] = []
    if recording_speaker.diarization_label:
        candidates.append((_alias_type_for_value(recording_speaker.diarization_label), recording_speaker.diarization_label))
    if recording_speaker.local_name:
        candidates.append((RecordingSpeakerAliasType.DISPLAY_NAME, recording_speaker.local_name))
    if recording_speaker.name and recording_speaker.name != recording_speaker.local_name:
        candidates.append((RecordingSpeakerAliasType.DISPLAY_NAME, recording_speaker.name))
    global_speaker = getattr(recording_speaker, "global_speaker", None)
    if global_speaker is None and recording_speaker.global_speaker_id:
        global_speaker = session.get(GlobalSpeaker, recording_speaker.global_speaker_id)
    if global_speaker is not None and getattr(global_speaker, "name", None):
        candidates.append((RecordingSpeakerAliasType.GLOBAL_NAME, global_speaker.name))

    for alias_type, alias_value in candidates:
        key = (alias_type.value, alias_value)
        if key in existing:
            continue
        _ensure_recording_speaker_alias(
            session,
            recording_speaker_id=recording_speaker.id,
            alias_type=alias_type,
            alias_value=alias_value,
            source_run_id=source_run_id,
            active=True,
        )


def _ensure_recording_speaker_alias(
    session,
    *,
    recording_speaker_id: int,
    alias_type: RecordingSpeakerAliasType,
    alias_value: str,
    source_run_id: int | None = None,
    active: bool = True,
    valid_from_ms: int | None = None,
    valid_to_ms: int | None = None,
    confidence: float | None = None,
) -> RecordingSpeakerAlias:
    existing_rows = session.execute(
        select(RecordingSpeakerAlias)
        .where(RecordingSpeakerAlias.recording_speaker_id == recording_speaker_id)
        .where(RecordingSpeakerAlias.alias_type == alias_type)
        .where(RecordingSpeakerAlias.alias_value == alias_value)
    ).scalars().all()

    for row in existing_rows:
        if (
            bool(row.active) == bool(active)
            and row.valid_from_ms == valid_from_ms
            and row.valid_to_ms == valid_to_ms
        ):
            if source_run_id is not None and row.source_run_id is None:
                row.source_run_id = source_run_id
                session.add(row)
            if confidence is not None and row.confidence is None:
                row.confidence = confidence
                session.add(row)
            return row

    alias_row = RecordingSpeakerAlias(
        recording_speaker_id=recording_speaker_id,
        alias_type=alias_type,
        alias_value=alias_value,
        source_run_id=source_run_id,
        active=active,
        valid_from_ms=valid_from_ms,
        valid_to_ms=valid_to_ms,
        confidence=confidence,
    )
    session.add(alias_row)
    return alias_row


def ensure_recording_speaker_aliases(
    session,
    recording_id: int,
    *,
    source_run_id: int | None = None,
) -> list[RecordingSpeaker]:
    speakers = _load_recording_speakers(session, recording_id)
    for speaker in speakers:
        speaker.speaker_status = "merged" if speaker.merged_into_id else "active"
        session.add(speaker)
        ensure_recording_speaker_aliases_for_speaker(
            session,
            speaker,
            source_run_id=source_run_id,
        )
    return speakers


def merge_recording_speaker_aliases(
    session,
    *,
    source_speaker: RecordingSpeaker,
    target_speaker: RecordingSpeaker,
    source_run_id: int | None = None,
) -> None:
    ensure_recording_speaker_aliases_for_speaker(
        session,
        source_speaker,
        source_run_id=source_run_id,
    )
    ensure_recording_speaker_aliases_for_speaker(
        session,
        target_speaker,
        source_run_id=source_run_id,
    )

    source_alias_rows = session.execute(
        select(RecordingSpeakerAlias).where(
            RecordingSpeakerAlias.recording_speaker_id == source_speaker.id
        )
    ).scalars().all()
    target_alias_rows = session.execute(
        select(RecordingSpeakerAlias).where(
            RecordingSpeakerAlias.recording_speaker_id == target_speaker.id
        )
    ).scalars().all()
    existing_target_keys = {
        (
            row.alias_type.value if hasattr(row.alias_type, "value") else str(row.alias_type),
            row.alias_value,
        )
        for row in target_alias_rows
    }

    for alias_row in source_alias_rows:
        alias_type_value = (
            alias_row.alias_type.value
            if hasattr(alias_row.alias_type, "value")
            else str(alias_row.alias_type)
        )
        alias_key = (alias_type_value, alias_row.alias_value)
        if alias_key in existing_target_keys:
            continue
        _ensure_recording_speaker_alias(
            session,
            recording_speaker_id=target_speaker.id,
            alias_type=alias_row.alias_type,
            alias_value=alias_row.alias_value,
            source_run_id=alias_row.source_run_id or source_run_id,
            active=bool(alias_row.active),
            valid_from_ms=alias_row.valid_from_ms,
            valid_to_ms=alias_row.valid_to_ms,
            confidence=alias_row.confidence,
        )
        existing_target_keys.add(alias_key)


def _live_alias_values_for_speaker(session, recording_speaker: RecordingSpeaker) -> set[str]:
    values: set[str] = set()
    if recording_speaker.diarization_label and recording_speaker.diarization_label.startswith("LIVE_"):
        values.add(recording_speaker.diarization_label)

    alias_rows = session.execute(
        select(RecordingSpeakerAlias.alias_value)
        .where(RecordingSpeakerAlias.recording_speaker_id == recording_speaker.id)
        .where(RecordingSpeakerAlias.alias_type == RecordingSpeakerAliasType.LIVE_LABEL)
        .where(RecordingSpeakerAlias.active.is_(True))
    ).all()
    values.update(str(alias_value) for (alias_value,) in alias_rows if alias_value)
    return values


def _preserve_live_label_continuity(
    session,
    *,
    source_speaker: RecordingSpeaker | None,
    target_speaker: RecordingSpeaker,
    scope: SpeakerCorrectionScope,
    anchor_start_ms: int,
) -> None:
    if source_speaker is None or source_speaker.id == target_speaker.id:
        return
    if scope in {SpeakerCorrectionScope.UTTERANCE_ONLY, SpeakerCorrectionScope.MERGE_INTO_SPEAKER}:
        return

    valid_from_ms = anchor_start_ms if scope == SpeakerCorrectionScope.FROM_THIS_UTTERANCE_FORWARD else None
    for alias_value in _live_alias_values_for_speaker(session, source_speaker):
        _ensure_recording_speaker_alias(
            session,
            recording_speaker_id=target_speaker.id,
            alias_type=RecordingSpeakerAliasType.LIVE_LABEL,
            alias_value=alias_value,
            source_run_id=target_speaker.processing_run_id or source_speaker.processing_run_id,
            active=True,
            valid_from_ms=valid_from_ms,
        )


def _resolve_active_recording_speaker(
    session,
    recording_speaker: RecordingSpeaker,
) -> RecordingSpeaker:
    current = recording_speaker
    visited: set[int] = set()
    while current.merged_into_id and current.id not in visited:
        visited.add(current.id)
        merged_target = session.get(RecordingSpeaker, current.merged_into_id)
        if merged_target is None:
            break
        current = merged_target
    return current


def _apply_source_run_provenance(
    session,
    recording_speaker: RecordingSpeaker,
    source_run_id: int | None,
) -> None:
    if source_run_id is None or recording_speaker.processing_run_id is not None:
        return
    recording_speaker.processing_run_id = source_run_id
    session.add(recording_speaker)


def _find_matching_recording_speaker(
    session,
    *,
    recording_id: int,
    recording_speakers: list[RecordingSpeaker],
    value: str,
    source_run_id: int | None,
    segment_start_ms: int | None = None,
) -> RecordingSpeaker | None:
    speaker_ids = [speaker.id for speaker in recording_speakers]
    if speaker_ids and segment_start_ms is not None:
        alias_rows = session.execute(
            select(RecordingSpeakerAlias)
            .where(RecordingSpeakerAlias.recording_speaker_id.in_(speaker_ids))
            .where(RecordingSpeakerAlias.active.is_(True))
            .where(RecordingSpeakerAlias.alias_value == value)
            .where(
                or_(
                    RecordingSpeakerAlias.valid_from_ms.is_(None),
                    RecordingSpeakerAlias.valid_from_ms <= segment_start_ms,
                )
            )
            .where(
                or_(
                    RecordingSpeakerAlias.valid_to_ms.is_(None),
                    RecordingSpeakerAlias.valid_to_ms > segment_start_ms,
                )
            )
            .order_by(func.coalesce(RecordingSpeakerAlias.valid_from_ms, -1).desc(), RecordingSpeakerAlias.id.desc())
        ).scalars().all()
        speakers_by_id = {speaker.id: speaker for speaker in recording_speakers}
        for alias_row in alias_rows:
            alias_speaker = speakers_by_id.get(alias_row.recording_speaker_id)
            if alias_speaker is None:
                alias_speaker = session.get(RecordingSpeaker, alias_row.recording_speaker_id)
            if alias_speaker is None or alias_speaker.recording_id != recording_id:
                continue
            resolved = _resolve_active_recording_speaker(session, alias_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved

    for recording_speaker in recording_speakers:
        if recording_speaker.diarization_label == value:
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved

    for recording_speaker in recording_speakers:
        if matches_speaker_name(recording_speaker.local_name, value):
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved
        if matches_speaker_name(recording_speaker.name, value):
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved
        global_speaker = getattr(recording_speaker, "global_speaker", None)
        if global_speaker and matches_speaker_name(global_speaker.name, value):
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved

    if not speaker_ids:
        return None

    alias_rows = session.execute(
        select(RecordingSpeakerAlias)
        .where(RecordingSpeakerAlias.recording_speaker_id.in_(speaker_ids))
        .where(RecordingSpeakerAlias.active.is_(True))
        .where(RecordingSpeakerAlias.alias_value == value)
        .order_by(RecordingSpeakerAlias.id.desc())
    ).scalars().all()
    speakers_by_id = {speaker.id: speaker for speaker in recording_speakers}
    for alias_row in alias_rows:
        alias_speaker = speakers_by_id.get(alias_row.recording_speaker_id)
        if alias_speaker is None:
            alias_speaker = session.get(RecordingSpeaker, alias_row.recording_speaker_id)
        if alias_speaker is None or alias_speaker.recording_id != recording_id:
            continue
        resolved = _resolve_active_recording_speaker(session, alias_speaker)
        _apply_source_run_provenance(session, resolved, source_run_id)
        return resolved

    return None


def _recording_speaker_display_name(session, recording_speaker: RecordingSpeaker) -> str:
    if recording_speaker.local_name:
        return recording_speaker.local_name
    global_speaker = getattr(recording_speaker, "global_speaker", None)
    if global_speaker is None and recording_speaker.global_speaker_id:
        global_speaker = session.get(GlobalSpeaker, recording_speaker.global_speaker_id)
    if global_speaker is not None and getattr(global_speaker, "name", None):
        return global_speaker.name
    if recording_speaker.name:
        return recording_speaker.name
    return recording_speaker.diarization_label or UNKNOWN_SPEAKER


def _utterance_ranges_overlap(
    first: TranscriptUtterance,
    second: TranscriptUtterance,
) -> bool:
    if first.start_ms == second.start_ms and first.end_ms == second.end_ms:
        return True
    return first.start_ms < second.end_ms and second.start_ms < first.end_ms


def _append_boundary_revision_events(
    session,
    *,
    previous_utterances: Sequence[TranscriptUtterance],
    new_utterances: Sequence[TranscriptUtterance],
    processing_run_id: int | None,
    source: str,
) -> None:
    if not previous_utterances or not new_utterances:
        return

    overlapping_new_by_old: dict[int, list[TranscriptUtterance]] = defaultdict(list)
    overlapping_old_by_new: dict[int, list[TranscriptUtterance]] = defaultdict(list)
    for previous_utterance in previous_utterances:
        for new_utterance in new_utterances:
            if not _utterance_ranges_overlap(previous_utterance, new_utterance):
                continue
            overlapping_new_by_old[previous_utterance.id].append(new_utterance)
            overlapping_old_by_new[new_utterance.id].append(previous_utterance)

    for new_utterance in new_utterances:
        source_utterances = overlapping_old_by_new.get(new_utterance.id, [])
        if len(source_utterances) > 1:
            _append_utterance_event(
                session,
                utterance=new_utterance,
                processing_run_id=processing_run_id,
                event_type="merge",
                source=source,
                old_values={
                    "source_public_ids": [utterance.public_id for utterance in source_utterances],
                },
                new_values={
                    "start_ms": new_utterance.start_ms,
                    "end_ms": new_utterance.end_ms,
                    "text": new_utterance.text,
                    "speaker": new_utterance.speaker_label,
                },
                resulting_revision=new_utterance.revision,
            )
            continue

        if len(source_utterances) != 1:
            continue

        source_utterance = source_utterances[0]
        if len(overlapping_new_by_old.get(source_utterance.id, [])) <= 1:
            continue

        _append_utterance_event(
            session,
            utterance=new_utterance,
            processing_run_id=processing_run_id,
            event_type="split",
            source=source,
            old_values={"source_public_id": source_utterance.public_id},
            new_values={
                "start_ms": new_utterance.start_ms,
                "end_ms": new_utterance.end_ms,
                "text": new_utterance.text,
                "speaker": new_utterance.speaker_label,
            },
            resulting_revision=new_utterance.revision,
        )


def _append_speaker_correction_event(
    session,
    *,
    recording_id: int,
    actor_user_id: int | None,
    event_type: SpeakerCorrectionEventType,
    scope: SpeakerCorrectionScope,
    utterance_id: int | None = None,
    source_recording_speaker_id: int | None = None,
    target_recording_speaker_id: int | None = None,
    target_global_speaker_id: int | None = None,
    effective_from_ms: int | None = None,
    payload: dict[str, Any] | None = None,
    update_source_provenance: bool = False,
) -> SpeakerCorrectionEvent:
    correction_event = SpeakerCorrectionEvent(
        recording_id=recording_id,
        actor_user_id=actor_user_id,
        utterance_id=utterance_id,
        source_recording_speaker_id=source_recording_speaker_id,
        target_recording_speaker_id=target_recording_speaker_id,
        target_global_speaker_id=target_global_speaker_id,
        event_type=event_type,
        scope=scope,
        effective_from_ms=effective_from_ms,
        payload=payload,
    )
    session.add(correction_event)
    session.flush()

    if target_recording_speaker_id is not None:
        target_speaker = session.get(RecordingSpeaker, target_recording_speaker_id)
        if target_speaker is not None:
            ensure_recording_speaker_aliases_for_speaker(session, target_speaker)
            target_speaker.last_speaker_correction_event_id = correction_event.id
            session.add(target_speaker)

    if (
        update_source_provenance
        and source_recording_speaker_id is not None
        and source_recording_speaker_id != target_recording_speaker_id
    ):
        source_speaker = session.get(RecordingSpeaker, source_recording_speaker_id)
        if source_speaker is not None:
            source_speaker.last_speaker_correction_event_id = correction_event.id
            session.add(source_speaker)

    return correction_event


def record_recording_speaker_corrections(
    session,
    *,
    recording_id: int,
    target_recording_speaker_ids: Sequence[int],
    actor_user_id: int | None,
    event_type: SpeakerCorrectionEventType,
    scope: SpeakerCorrectionScope = SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
    target_global_speaker_id: int | None = None,
    payload: dict[str, Any] | None = None,
    payload_by_speaker_id: dict[int, dict[str, Any]] | None = None,
) -> list[SpeakerCorrectionEvent]:
    correction_events: list[SpeakerCorrectionEvent] = []
    replay_historical_windows = event_type in {
        SpeakerCorrectionEventType.LINK_GLOBAL_SPEAKER,
        SpeakerCorrectionEventType.PROMOTE_GLOBAL_SPEAKER,
        SpeakerCorrectionEventType.MERGE_SPEAKERS,
    }
    for speaker_id in target_recording_speaker_ids:
        recording_speaker = session.get(RecordingSpeaker, speaker_id)
        if recording_speaker is None:
            continue

        event_payload = dict(payload or {})
        if payload_by_speaker_id:
            event_payload.update(payload_by_speaker_id.get(speaker_id, {}))
        event_payload.setdefault("diarization_label", recording_speaker.diarization_label)
        event_payload.setdefault("target_public_id", recording_speaker.public_id)
        event_payload.setdefault(
            "speaker_name",
            _recording_speaker_display_name(session, recording_speaker),
        )

        correction_events.append(
            _append_speaker_correction_event(
                session,
                recording_id=recording_id,
                actor_user_id=actor_user_id,
                event_type=event_type,
                scope=scope,
                target_recording_speaker_id=recording_speaker.id,
                target_global_speaker_id=(
                    target_global_speaker_id
                    if target_global_speaker_id is not None
                    else recording_speaker.global_speaker_id
                ),
                effective_from_ms=recording_speaker.first_seen_ms,
                payload=event_payload or None,
            )
        )

    if replay_historical_windows and correction_events:
        replay_effective_from_ms = min(
            int(correction_event.effective_from_ms or 0)
            for correction_event in correction_events
        )
        _reconcile_completed_windows_from_effective_point(
            session,
            recording_id=recording_id,
            effective_from_ms=replay_effective_from_ms,
            source="speaker_identity_replay",
        )

    return correction_events


def _reconcile_completed_windows_from_effective_point(
    session,
    *,
    recording_id: int,
    effective_from_ms: int | None,
    source: str,
    processing_run_id: int | None = None,
) -> dict[str, int]:
    if effective_from_ms is None:
        return {
            "window_count": 0,
            "matched_turn_count": 0,
            "updated_utterance_count": 0,
            "preserved_manual_lock_count": 0,
        }

    completed_window_rows = list(
        session.execute(
            select(DiarizationWindowResult)
            .where(DiarizationWindowResult.recording_id == recording_id)
            .where(DiarizationWindowResult.status == "completed")
            .where(DiarizationWindowResult.window_end_ms > int(effective_from_ms))
            .order_by(DiarizationWindowResult.window_start_ms, DiarizationWindowResult.id)
        ).scalars().all()
    )
    if not completed_window_rows:
        return {
            "window_count": 0,
            "matched_turn_count": 0,
            "updated_utterance_count": 0,
            "preserved_manual_lock_count": 0,
        }

    summary = {
        "window_count": 0,
        "matched_turn_count": 0,
        "updated_utterance_count": 0,
        "preserved_manual_lock_count": 0,
    }
    for window_row in completed_window_rows:
        if window_row.id is None:
            continue
        replay_summary = reconcile_diarization_window_result(
            session,
            recording_id=recording_id,
            window_result_id=int(window_row.id),
            processing_run_id=processing_run_id,
            source=source,
        )
        summary["window_count"] += 1
        summary["matched_turn_count"] += int(replay_summary.get("matched_turn_count", 0))
        summary["updated_utterance_count"] += int(replay_summary.get("updated_utterance_count", 0))
        summary["preserved_manual_lock_count"] += int(
            replay_summary.get("preserved_manual_lock_count", 0)
        )
    return summary


def ensure_canonical_backfill(
    session,
    recording_id: int,
    *,
    force: bool = False,
) -> list[TranscriptUtterance]:
    recording = session.get(Recording, recording_id)
    transcript = _load_transcript(session, recording_id)
    if recording is None or transcript is None:
        return []

    existing = list_active_utterances(session, recording_id)
    if existing and not force:
        return existing

    if not force and not recording_ready_for_canonical_backfill(recording.status):
        return []

    if not transcript.segments:
        return []

    return replace_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[dict(segment) for segment in transcript.segments],
        run_kind=ProcessingRunKind.BACKFILL,
        source="backfill",
        force=True,
    )


def replace_utterances_from_segments(
    session,
    *,
    recording_id: int,
    segments: Sequence[dict[str, Any]],
    run_kind: ProcessingRunKind | None,
    source: str,
    force: bool,
    state_override: TranscriptUtteranceState | None = None,
    reused_live_asr: bool = False,
    trigger_source: str = "system",
    idempotency_key: str | None = None,
) -> list[TranscriptUtterance]:
    transcript = _load_transcript(session, recording_id)
    recording = session.get(Recording, recording_id)
    if transcript is None or recording is None:
        return []

    processing_run = None
    if run_kind is not None:
        run_idempotency_key = idempotency_key or _processing_run_idempotency_key(
            run_kind=run_kind,
            source=source,
            segments=segments,
            state_override=state_override,
            reused_live_asr=reused_live_asr,
        )
        existing_processing_run = session.execute(
            select(ProcessingRun)
            .where(ProcessingRun.recording_id == recording_id)
            .where(ProcessingRun.idempotency_key == run_idempotency_key)
        ).scalar_one_or_none()
        if existing_processing_run is not None:
            return list_active_utterances(session, recording_id)
        processing_run = ensure_processing_run(
            session,
            recording_id=recording_id,
            run_kind=run_kind,
            trigger_source=trigger_source,
            reused_live_asr=reused_live_asr,
            span_start_ms=_segment_to_ms(min((segment.get("start", 0.0) for segment in segments), default=0.0)),
            span_end_ms=_segment_to_ms(max((segment.get("end", 0.0) for segment in segments), default=0.0)),
            idempotency_key=run_idempotency_key,
        )

    recording_speakers = ensure_recording_speaker_aliases(
        session,
        recording_id,
        source_run_id=processing_run.id if processing_run else None,
    )
    speakers_by_id = {speaker.id: speaker for speaker in recording_speakers}

    previous_utterances = list_active_utterances(session, recording_id) if force else []
    if force:
        for utterance in previous_utterances:
            old_state = utterance.state.value if hasattr(utterance.state, "value") else str(utterance.state)
            utterance.state = TranscriptUtteranceState.SUPERSEDED
            session.add(utterance)
            session.flush()
            _append_utterance_event(
                session,
                utterance=utterance,
                processing_run_id=processing_run.id if processing_run else None,
                event_type="supersede",
                source=source,
                old_values={"state": old_state},
                new_values={"state": TranscriptUtteranceState.SUPERSEDED.value},
                resulting_revision=utterance.revision,
            )

    overlap_groups = _build_overlap_groups(segments)
    utterances: list[TranscriptUtterance] = []
    projection_segments: list[dict[str, Any]] = []

    for index, segment in enumerate(segments):
        recording_speaker = _resolve_recording_speaker_for_value(
            session,
            recording_id=recording_id,
            recording=recording,
            speaker_value=str(segment.get("speaker") or UNKNOWN_SPEAKER),
            recording_speakers=recording_speakers,
            source_run_id=processing_run.id if processing_run else None,
            source_segment=segment,
        )
        if recording_speaker is not None:
            speakers_by_id[recording_speaker.id] = recording_speaker
            _touch_recording_speaker_bounds(recording_speaker, segment)
            session.add(recording_speaker)

        utterance = TranscriptUtterance(
            public_id=str(segment.get("id") or uuid4()),
            recording_id=recording_id,
            sort_key=_sort_key_for_index(index),
            start_ms=_segment_to_ms(segment.get("start", 0.0)),
            end_ms=_segment_to_ms(segment.get("end", 0.0)),
            text=str(segment.get("text", "") or ""),
            speaker_label=(recording_speaker.diarization_label if recording_speaker else str(segment.get("speaker") or UNKNOWN_SPEAKER)),
            recording_speaker_id=recording_speaker.id if recording_speaker else None,
            state=state_override or _state_for_segment(recording, segment),
            source_kind=str(segment.get("segment_source") or source),
            processing_run_id=processing_run.id if processing_run else None,
            revision=int(segment.get("revision") or 1),
            overlap_group_id=overlap_groups.get(index, {}).get("group_id"),
            overlap_rank=overlap_groups.get(index, {}).get("rank", 0),
            manual_text_locked=bool(segment.get("text_manually_edited") is True),
            manual_speaker_locked=bool(segment.get("speaker_manually_edited") is True),
            text_confidence=_to_optional_float(segment.get("text_confidence")),
            speaker_confidence=_to_optional_float(segment.get("speaker_confidence")),
        )
        session.add(utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=utterance,
            processing_run_id=processing_run.id if processing_run else None,
            event_type=_creation_event_type(source=source, state=utterance.state),
            source=source,
            old_values=None,
            new_values={
                "start_ms": utterance.start_ms,
                "end_ms": utterance.end_ms,
                "text": utterance.text,
                "speaker": utterance.speaker_label,
            },
            resulting_revision=utterance.revision,
        )
        _record_manual_lock_events(
            session,
            utterance=utterance,
            old_text_locked=False,
            old_speaker_locked=False,
            source=source,
            processing_run_id=processing_run.id if processing_run else None,
        )
        utterances.append(utterance)

        projection_segments.append(
            _build_projection_segment(
                utterance,
                source_segment=segment,
                recording_speaker=recording_speaker,
                overlap_labels=_projection_overlap_labels(index, segments, overlap_groups, recording_speakers),
            )
        )

    transcript.segments = projection_segments
    transcript.text = " ".join(segment.get("text", "") for segment in projection_segments).strip()
    flag_modified(transcript, "segments")
    session.add(transcript)

    _append_boundary_revision_events(
        session,
        previous_utterances=previous_utterances,
        new_utterances=utterances,
        processing_run_id=processing_run.id if processing_run else None,
        source=source,
    )

    return utterances


def finalize_utterances_from_segments(
    session,
    *,
    recording_id: int,
    segments: Sequence[dict[str, Any]],
    reused_live_asr: bool = False,
    trigger_source: str = "system",
    idempotency_key: str | None = None,
) -> list[TranscriptUtterance]:
    transcript = _load_transcript(session, recording_id)
    recording = session.get(Recording, recording_id)
    if transcript is None or recording is None:
        return []

    run_idempotency_key = idempotency_key or _processing_run_idempotency_key(
        run_kind=ProcessingRunKind.FINALIZE,
        source="finalize",
        segments=segments,
        state_override=TranscriptUtteranceState.FINALIZED,
        reused_live_asr=reused_live_asr,
    )
    existing_processing_run = session.execute(
        select(ProcessingRun)
        .where(ProcessingRun.recording_id == recording_id)
        .where(ProcessingRun.idempotency_key == run_idempotency_key)
    ).scalar_one_or_none()
    if existing_processing_run is not None:
        return list_active_utterances(session, recording_id)

    processing_run = ensure_processing_run(
        session,
        recording_id=recording_id,
        run_kind=ProcessingRunKind.FINALIZE,
        trigger_source=trigger_source,
        reused_live_asr=reused_live_asr,
        span_start_ms=_segment_to_ms(min((segment.get("start", 0.0) for segment in segments), default=0.0)),
        span_end_ms=_segment_to_ms(max((segment.get("end", 0.0) for segment in segments), default=0.0)),
        idempotency_key=run_idempotency_key,
    )

    recording_speakers = ensure_recording_speaker_aliases(
        session,
        recording_id,
        source_run_id=processing_run.id,
    )
    active_utterances = list_active_utterances(session, recording_id)
    exact_matches: dict[tuple[int, int], list[TranscriptUtterance]] = defaultdict(list)
    for utterance in active_utterances:
        exact_matches[(utterance.start_ms, utterance.end_ms)].append(utterance)

    overlap_groups = _build_overlap_groups(segments)
    projection_segments: list[dict[str, Any]] = []
    utterances: list[TranscriptUtterance] = []
    new_boundary_utterances: list[TranscriptUtterance] = []
    matched_utterance_ids: set[int] = set()

    for index, segment in enumerate(segments):
        start_ms = _segment_to_ms(segment.get("start", 0.0))
        end_ms = _segment_to_ms(segment.get("end", 0.0))
        boundary_key = (start_ms, end_ms)
        matched_utterance = None
        while exact_matches.get(boundary_key):
            candidate = exact_matches[boundary_key].pop(0)
            if candidate.id not in matched_utterance_ids:
                matched_utterance = candidate
                break

        resolved_speaker = None
        if matched_utterance is None or not matched_utterance.manual_speaker_locked:
            resolved_speaker = _resolve_recording_speaker_for_value(
                session,
                recording_id=recording_id,
                recording=recording,
                speaker_value=str(segment.get("speaker") or UNKNOWN_SPEAKER),
                recording_speakers=recording_speakers,
                source_run_id=processing_run.id,
                source_segment=segment,
            )
            if resolved_speaker is not None:
                _touch_recording_speaker_bounds(resolved_speaker, segment)
                session.add(resolved_speaker)

        if matched_utterance is not None:
            matched_utterance_ids.add(matched_utterance.id)
            old_values = {
                "start_ms": matched_utterance.start_ms,
                "end_ms": matched_utterance.end_ms,
                "text": matched_utterance.text,
                "speaker": matched_utterance.speaker_label,
                "state": matched_utterance.state.value if hasattr(matched_utterance.state, "value") else str(matched_utterance.state),
                "revision": matched_utterance.revision,
            }
            old_text_locked = bool(matched_utterance.manual_text_locked)
            old_speaker_locked = bool(matched_utterance.manual_speaker_locked)

            effective_segment = dict(segment)
            effective_segment["segment_source"] = matched_utterance.source_kind or str(segment.get("segment_source") or "finalize")
            effective_segment["text"] = matched_utterance.text if old_text_locked else str(segment.get("text", "") or "")
            effective_segment["text_manually_edited"] = old_text_locked or bool(segment.get("text_manually_edited") is True)

            if old_speaker_locked:
                current_speaker = (
                    session.get(RecordingSpeaker, matched_utterance.recording_speaker_id)
                    if matched_utterance.recording_speaker_id is not None
                    else None
                )
                effective_segment["speaker"] = (
                    current_speaker.diarization_label
                    if current_speaker is not None
                    else str(matched_utterance.speaker_label or UNKNOWN_SPEAKER)
                )
                recording_speaker = current_speaker
            else:
                recording_speaker = resolved_speaker
                effective_segment["speaker"] = (
                    recording_speaker.diarization_label
                    if recording_speaker is not None
                    else str(segment.get("speaker") or UNKNOWN_SPEAKER)
                )
            effective_segment["speaker_manually_edited"] = old_speaker_locked or bool(segment.get("speaker_manually_edited") is True)

            effective_text = str(effective_segment.get("text", "") or "")
            effective_speaker_label = (
                recording_speaker.diarization_label
                if recording_speaker is not None
                else str(effective_segment.get("speaker") or UNKNOWN_SPEAKER)
            )
            effective_recording_speaker_id = recording_speaker.id if recording_speaker is not None else None
            effective_manual_text_locked = bool(effective_segment.get("text_manually_edited") is True)
            effective_manual_speaker_locked = bool(effective_segment.get("speaker_manually_edited") is True)
            effective_text_confidence = _to_optional_float(segment.get("text_confidence"))
            if effective_text_confidence is None:
                effective_text_confidence = matched_utterance.text_confidence
            effective_speaker_confidence = _to_optional_float(segment.get("speaker_confidence"))
            if effective_speaker_confidence is None:
                effective_speaker_confidence = matched_utterance.speaker_confidence

            changed = any(
                (
                    matched_utterance.sort_key != _sort_key_for_index(index),
                    matched_utterance.text != effective_text,
                    matched_utterance.speaker_label != effective_speaker_label,
                    matched_utterance.recording_speaker_id != effective_recording_speaker_id,
                    bool(matched_utterance.manual_text_locked) != effective_manual_text_locked,
                    bool(matched_utterance.manual_speaker_locked) != effective_manual_speaker_locked,
                    matched_utterance.state != TranscriptUtteranceState.FINALIZED,
                    matched_utterance.processing_run_id != processing_run.id,
                    matched_utterance.overlap_group_id != overlap_groups.get(index, {}).get("group_id"),
                    int(matched_utterance.overlap_rank or 0) != overlap_groups.get(index, {}).get("rank", 0),
                    matched_utterance.text_confidence != effective_text_confidence,
                    matched_utterance.speaker_confidence != effective_speaker_confidence,
                )
            )

            matched_utterance.sort_key = _sort_key_for_index(index)
            matched_utterance.text = effective_text
            matched_utterance.speaker_label = effective_speaker_label
            matched_utterance.recording_speaker_id = effective_recording_speaker_id
            matched_utterance.manual_text_locked = effective_manual_text_locked
            matched_utterance.manual_speaker_locked = effective_manual_speaker_locked
            matched_utterance.text_confidence = effective_text_confidence
            matched_utterance.speaker_confidence = effective_speaker_confidence
            matched_utterance.state = TranscriptUtteranceState.FINALIZED
            matched_utterance.processing_run_id = processing_run.id
            matched_utterance.overlap_group_id = overlap_groups.get(index, {}).get("group_id")
            matched_utterance.overlap_rank = overlap_groups.get(index, {}).get("rank", 0)
            if changed:
                matched_utterance.revision += 1
                session.add(matched_utterance)
                session.flush()
                _append_utterance_event(
                    session,
                    utterance=matched_utterance,
                    processing_run_id=processing_run.id,
                    event_type="finalize",
                    source="finalize",
                    old_values=old_values,
                    new_values={
                        "start_ms": matched_utterance.start_ms,
                        "end_ms": matched_utterance.end_ms,
                        "text": matched_utterance.text,
                        "speaker": matched_utterance.speaker_label,
                        "state": TranscriptUtteranceState.FINALIZED.value,
                    },
                    resulting_revision=matched_utterance.revision,
                )
                _record_manual_lock_events(
                    session,
                    utterance=matched_utterance,
                    old_text_locked=old_text_locked,
                    old_speaker_locked=old_speaker_locked,
                    source="finalize",
                    processing_run_id=processing_run.id,
                )
            utterances.append(matched_utterance)
            effective_segment["revision"] = matched_utterance.revision
            projection_segments.append(
                _build_projection_segment(
                    matched_utterance,
                    source_segment=effective_segment,
                    recording_speaker=recording_speaker,
                    overlap_labels=_projection_overlap_labels(index, segments, overlap_groups, recording_speakers),
                )
            )
            continue

        recording_speaker = resolved_speaker
        utterance = TranscriptUtterance(
            public_id=str(segment.get("id") or uuid4()),
            recording_id=recording_id,
            sort_key=_sort_key_for_index(index),
            start_ms=start_ms,
            end_ms=end_ms,
            text=str(segment.get("text", "") or ""),
            speaker_label=(recording_speaker.diarization_label if recording_speaker else str(segment.get("speaker") or UNKNOWN_SPEAKER)),
            recording_speaker_id=recording_speaker.id if recording_speaker else None,
            state=TranscriptUtteranceState.FINALIZED,
            source_kind=str(segment.get("segment_source") or "finalize"),
            processing_run_id=processing_run.id,
            revision=int(segment.get("revision") or 1),
            overlap_group_id=overlap_groups.get(index, {}).get("group_id"),
            overlap_rank=overlap_groups.get(index, {}).get("rank", 0),
            manual_text_locked=bool(segment.get("text_manually_edited") is True),
            manual_speaker_locked=bool(segment.get("speaker_manually_edited") is True),
            text_confidence=_to_optional_float(segment.get("text_confidence")),
            speaker_confidence=_to_optional_float(segment.get("speaker_confidence")),
        )
        session.add(utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=utterance,
            processing_run_id=processing_run.id,
            event_type="finalize",
            source="finalize",
            old_values=None,
            new_values={
                "start_ms": utterance.start_ms,
                "end_ms": utterance.end_ms,
                "text": utterance.text,
                "speaker": utterance.speaker_label,
                "state": TranscriptUtteranceState.FINALIZED.value,
            },
            resulting_revision=utterance.revision,
        )
        _record_manual_lock_events(
            session,
            utterance=utterance,
            old_text_locked=False,
            old_speaker_locked=False,
            source="finalize",
            processing_run_id=processing_run.id,
        )
        utterances.append(utterance)
        new_boundary_utterances.append(utterance)
        projection_segments.append(
            _build_projection_segment(
                utterance,
                source_segment=segment,
                recording_speaker=recording_speaker,
                overlap_labels=_projection_overlap_labels(index, segments, overlap_groups, recording_speakers),
            )
        )

    previous_boundary_utterances: list[TranscriptUtterance] = []
    for utterance in active_utterances:
        if utterance.id in matched_utterance_ids:
            continue
        previous_boundary_utterances.append(utterance)
        old_state = utterance.state.value if hasattr(utterance.state, "value") else str(utterance.state)
        utterance.state = TranscriptUtteranceState.SUPERSEDED
        session.add(utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=utterance,
            processing_run_id=processing_run.id,
            event_type="supersede",
            source="finalize",
            old_values={"state": old_state},
            new_values={"state": TranscriptUtteranceState.SUPERSEDED.value},
            resulting_revision=utterance.revision,
        )

    transcript.segments = projection_segments
    transcript.text = " ".join(segment.get("text", "") for segment in projection_segments).strip()
    flag_modified(transcript, "segments")
    session.add(transcript)

    _append_boundary_revision_events(
        session,
        previous_utterances=previous_boundary_utterances,
        new_utterances=new_boundary_utterances,
        processing_run_id=processing_run.id,
        source="finalize",
    )

    return utterances


def append_utterances_from_segments(
    session,
    *,
    recording_id: int,
    segments: Sequence[dict[str, Any]],
    run_kind: ProcessingRunKind,
    source: str,
    state_override: TranscriptUtteranceState | None = None,
    trigger_source: str = "system",
    idempotency_key: str | None = None,
    config_hash: str | None = None,
    transcription_backend: str | None = None,
    model_metadata: dict[str, Any] | None = None,
    span_start_ms: int | None = None,
    span_end_ms: int | None = None,
    reused_live_asr: bool = False,
) -> list[TranscriptUtterance]:
    transcript = _load_transcript(session, recording_id)
    recording = session.get(Recording, recording_id)
    if transcript is None or recording is None or not segments:
        return []

    run_idempotency_key = idempotency_key or _processing_run_idempotency_key(
        run_kind=run_kind,
        source=source,
        segments=segments,
        state_override=state_override,
        reused_live_asr=reused_live_asr,
    )
    existing_processing_run = session.execute(
        select(ProcessingRun)
        .where(ProcessingRun.recording_id == recording_id)
        .where(ProcessingRun.idempotency_key == run_idempotency_key)
    ).scalar_one_or_none()
    if existing_processing_run is not None:
        return list_utterances_for_processing_run(session, existing_processing_run.id)

    processing_run = ensure_processing_run(
        session,
        recording_id=recording_id,
        run_kind=run_kind,
        trigger_source=trigger_source,
        reused_live_asr=reused_live_asr,
        config_hash=config_hash,
        transcription_backend=transcription_backend,
        model_metadata=model_metadata,
        span_start_ms=(
            span_start_ms
            if span_start_ms is not None
            else _segment_to_ms(min((segment.get("start", 0.0) for segment in segments), default=0.0))
        ),
        span_end_ms=(
            span_end_ms
            if span_end_ms is not None
            else _segment_to_ms(max((segment.get("end", 0.0) for segment in segments), default=0.0))
        ),
        idempotency_key=run_idempotency_key,
    )

    recording_speakers = ensure_recording_speaker_aliases(
        session,
        recording_id,
        source_run_id=processing_run.id,
    )
    next_sort_index = len(list_active_utterances(session, recording_id))
    overlap_groups = _build_overlap_groups(segments)
    utterances: list[TranscriptUtterance] = []

    for offset, segment in enumerate(segments):
        recording_speaker = _resolve_recording_speaker_for_value(
            session,
            recording_id=recording_id,
            recording=recording,
            speaker_value=str(segment.get("speaker") or UNKNOWN_SPEAKER),
            recording_speakers=recording_speakers,
            source_run_id=processing_run.id,
            source_segment=segment,
        )
        if recording_speaker is not None:
            _touch_recording_speaker_bounds(recording_speaker, segment)
            session.add(recording_speaker)

        utterance = TranscriptUtterance(
            public_id=str(segment.get("id") or uuid4()),
            recording_id=recording_id,
            sort_key=_sort_key_for_index(next_sort_index + offset),
            start_ms=_segment_to_ms(segment.get("start", 0.0)),
            end_ms=_segment_to_ms(segment.get("end", 0.0)),
            text=str(segment.get("text", "") or ""),
            speaker_label=(recording_speaker.diarization_label if recording_speaker else str(segment.get("speaker") or UNKNOWN_SPEAKER)),
            recording_speaker_id=recording_speaker.id if recording_speaker else None,
            state=state_override or _state_for_segment(recording, segment),
            source_kind=str(segment.get("segment_source") or source),
            processing_run_id=processing_run.id,
            revision=int(segment.get("revision") or 1),
            overlap_group_id=overlap_groups.get(offset, {}).get("group_id"),
            overlap_rank=overlap_groups.get(offset, {}).get("rank", 0),
            manual_text_locked=bool(segment.get("text_manually_edited") is True),
            manual_speaker_locked=bool(segment.get("speaker_manually_edited") is True),
            text_confidence=_to_optional_float(segment.get("text_confidence")),
            speaker_confidence=_to_optional_float(segment.get("speaker_confidence")),
            confidence_payload=(dict(segment.get("confidence_payload")) if isinstance(segment.get("confidence_payload"), dict) else None),
        )
        session.add(utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=utterance,
            processing_run_id=processing_run.id,
            event_type=_creation_event_type(source=source, state=utterance.state),
            source=source,
            old_values=None,
            new_values={
                "start_ms": utterance.start_ms,
                "end_ms": utterance.end_ms,
                "text": utterance.text,
                "speaker": utterance.speaker_label,
            },
            resulting_revision=utterance.revision,
        )
        _record_manual_lock_events(
            session,
            utterance=utterance,
            old_text_locked=False,
            old_speaker_locked=False,
            source=source,
            processing_run_id=processing_run.id,
        )
        utterances.append(utterance)

    refresh_transcript_projection_from_canonical(session, recording_id)
    return utterances


def update_utterance_text(
    session,
    *,
    recording_id: int,
    utterance_public_id: str,
    text: str,
    actor_user_id: int | None = None,
    expected_revision: int | None = None,
    source: str = "api",
) -> TranscriptUtterance:
    utterance = _get_utterance(session, recording_id, utterance_public_id)
    if utterance is None:
        raise LookupError("Utterance not found")
    if expected_revision is not None and utterance.revision != expected_revision:
        raise RuntimeError("Utterance revision conflict")

    old_values = {"text": utterance.text, "revision": utterance.revision}
    old_text_locked = bool(utterance.manual_text_locked)
    utterance.text = text
    utterance.manual_text_locked = True
    utterance.revision += 1
    session.add(utterance)
    session.flush()

    _append_utterance_event(
        session,
        utterance=utterance,
        actor_user_id=actor_user_id,
        event_type="update_text",
        source=source,
        old_values=old_values,
        new_values={"text": utterance.text},
        resulting_revision=utterance.revision,
    )
    _record_manual_lock_events(
        session,
        utterance=utterance,
        old_text_locked=old_text_locked,
        old_speaker_locked=bool(utterance.manual_speaker_locked),
        source=source,
        actor_user_id=actor_user_id,
    )

    transcript = _load_transcript(session, recording_id)
    if transcript is not None:
        _update_projection_segment_by_public_id(
            transcript,
            utterance.public_id,
            {
                "text": utterance.text,
                "text_manually_edited": True,
                "revision": utterance.revision,
                "state": utterance.state.value,
                "updated_at": utterance.updated_at.isoformat(),
            },
        )
        transcript.text = " ".join(
            str(segment.get("text", "") or "") for segment in (transcript.segments or [])
        ).strip()
        session.add(transcript)

    return utterance


def update_utterance_speaker(
    session,
    *,
    recording_id: int,
    utterance_public_id: str,
    new_speaker_name: str,
    global_speaker_id: int | None = None,
    diarization_label: str | None = None,
    scope: SpeakerCorrectionScope = SpeakerCorrectionScope.UTTERANCE_ONLY,
    actor_user_id: int | None = None,
    expected_revision: int | None = None,
    source: str = "api",
) -> tuple[TranscriptUtterance, RecordingSpeaker]:
    utterance = _get_utterance(session, recording_id, utterance_public_id)
    if utterance is None:
        raise LookupError("Utterance not found")
    if expected_revision is not None and utterance.revision != expected_revision:
        raise RuntimeError("Utterance revision conflict")

    transcript = _load_transcript(session, recording_id)
    recording = session.get(Recording, recording_id)
    if transcript is None or recording is None:
        raise LookupError("Transcript not found")

    target_speaker = resolve_assignment_target(
        session,
        recording_id=recording_id,
        recording=recording,
        new_speaker_name=new_speaker_name,
        global_speaker_id=global_speaker_id,
        diarization_label=diarization_label,
    )

    current_key = utterance.recording_speaker_id or utterance.speaker_label
    target_key = target_speaker.id
    source_recording_speaker_id = utterance.recording_speaker_id
    source_speaker = session.get(RecordingSpeaker, source_recording_speaker_id) if source_recording_speaker_id else None
    target_utterances = _select_utterances_for_scope(
        session,
        recording_id=recording_id,
        anchor_utterance=utterance,
        scope=scope,
        current_key=current_key,
    )

    updated_segments = [dict(segment) for segment in (transcript.segments or [])]
    index_by_public_id = {
        str(segment.get("id")): index
        for index, segment in enumerate(updated_segments)
        if segment.get("id")
    }

    for target_utterance in target_utterances:
        old_label = target_utterance.speaker_label or UNKNOWN_SPEAKER
        old_speaker_locked = bool(target_utterance.manual_speaker_locked)
        old_values = {
            "speaker_label": old_label,
            "recording_speaker_id": target_utterance.recording_speaker_id,
            "revision": target_utterance.revision,
        }
        target_utterance.recording_speaker_id = target_key
        target_utterance.speaker_label = target_speaker.diarization_label
        target_utterance.manual_speaker_locked = True
        target_utterance.revision += 1
        session.add(target_utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=target_utterance,
            actor_user_id=actor_user_id,
            event_type="update_speaker",
            source=source,
            old_values=old_values,
            new_values={
                "speaker_label": target_speaker.diarization_label,
                "recording_speaker_id": target_key,
            },
            resulting_revision=target_utterance.revision,
        )
        _record_manual_lock_events(
            session,
            utterance=target_utterance,
            old_text_locked=bool(target_utterance.manual_text_locked),
            old_speaker_locked=old_speaker_locked,
            source=source,
            actor_user_id=actor_user_id,
        )

        projection_index = index_by_public_id.get(target_utterance.public_id)
        if projection_index is not None:
            reconcile_segment_assignment(
                updated_segments,
                projection_index,
                old_label,
                target_speaker.diarization_label,
            )
            updated_segments[projection_index]["speaker_manually_edited"] = True
            updated_segments[projection_index]["recording_speaker_id"] = target_key
            updated_segments[projection_index]["revision"] = target_utterance.revision
            updated_segments[projection_index]["state"] = target_utterance.state.value
            updated_segments[projection_index]["updated_at"] = target_utterance.updated_at.isoformat()

    _preserve_live_label_continuity(
        session,
        source_speaker=source_speaker,
        target_speaker=target_speaker,
        scope=scope,
        anchor_start_ms=utterance.start_ms,
    )

    if scope == SpeakerCorrectionScope.MERGE_INTO_SPEAKER and source_recording_speaker_id and source_recording_speaker_id != target_key:
        if source_speaker is not None:
            source_speaker.merged_into_id = target_key
            source_speaker.speaker_status = "merged"
            session.add(source_speaker)
            merge_recording_speaker_aliases(
                session,
                source_speaker=source_speaker,
                target_speaker=target_speaker,
            )

    _append_speaker_correction_event(
        session,
        recording_id=recording_id,
        actor_user_id=actor_user_id,
        utterance_id=utterance.id,
        source_recording_speaker_id=source_recording_speaker_id,
        target_recording_speaker_id=target_key,
        target_global_speaker_id=target_speaker.global_speaker_id,
        event_type=_event_type_for_scope(scope),
        scope=scope,
        effective_from_ms=utterance.start_ms,
        payload={
            "new_speaker_name": new_speaker_name,
            "diarization_label": diarization_label,
            "target_public_id": target_speaker.public_id,
        },
        update_source_provenance=(
            scope == SpeakerCorrectionScope.MERGE_INTO_SPEAKER
            and source_recording_speaker_id is not None
            and source_recording_speaker_id != target_key
        ),
    )

    transcript.segments = updated_segments
    flag_modified(transcript, "segments")
    session.add(transcript)

    if scope != SpeakerCorrectionScope.UTTERANCE_ONLY:
        replay_effective_from_ms = min(
            int(target_utterance.start_ms)
            for target_utterance in target_utterances
        ) if target_utterances else int(utterance.start_ms)
        _reconcile_completed_windows_from_effective_point(
            session,
            recording_id=recording_id,
            effective_from_ms=replay_effective_from_ms,
            source="speaker_correction_replay",
        )

    return utterance, target_speaker


def update_recording_speaker_identity(
    session,
    *,
    recording_id: int,
    diarization_label: str,
    new_speaker_name: str,
    target_global_speaker_id: int | None = None,
    actor_user_id: int | None = None,
    merge_global_embedding_alpha: float | None = None,
    event_type: SpeakerCorrectionEventType | None = None,
    source: str = "api",
) -> list[RecordingSpeaker]:
    recording = session.get(Recording, recording_id)
    if recording is None:
        raise LookupError("Recording not found")

    if recording_ready_for_canonical_backfill(recording.status):
        ensure_canonical_backfill(session, recording_id)

    recording_speakers = ensure_recording_speaker_aliases(session, recording_id)
    matching_speakers = [
        recording_speaker
        for recording_speaker in recording_speakers
        if recording_speaker.diarization_label == diarization_label and not recording_speaker.merged_into_id
    ]
    if not matching_speakers:
        raise LookupError(f"Speaker '{diarization_label}' not found in recording")

    old_display_names = {
        recording_speaker.id: _recording_speaker_display_name(session, recording_speaker)
        for recording_speaker in matching_speakers
    }

    target_global_speaker = None
    if target_global_speaker_id is not None:
        target_global_speaker = session.execute(
            select(GlobalSpeaker)
            .where(GlobalSpeaker.id == target_global_speaker_id)
            .where(GlobalSpeaker.user_id == recording.user_id)
        ).scalar_one_or_none()
        if target_global_speaker is None:
            raise LookupError("Global speaker not found")

    for recording_speaker in matching_speakers:
        if target_global_speaker is not None:
            recording_speaker.global_speaker_id = target_global_speaker.id
            recording_speaker.global_speaker = target_global_speaker
            recording_speaker.local_name = None
            if merge_global_embedding_alpha is not None and recording_speaker.embedding:
                if target_global_speaker.embedding:
                    target_global_speaker.embedding = merge_embeddings(
                        target_global_speaker.embedding,
                        recording_speaker.embedding,
                        alpha=merge_global_embedding_alpha,
                    )
                else:
                    target_global_speaker.embedding = list(recording_speaker.embedding)
                session.add(target_global_speaker)
        else:
            recording_speaker.global_speaker_id = None
            recording_speaker.global_speaker = None
            recording_speaker.local_name = new_speaker_name

        recording_speaker.name = None
        recording_speaker.identity_confidence = 1.0
        recording_speaker.identity_locked = True
        ensure_recording_speaker_aliases_for_speaker(session, recording_speaker)
        session.add(recording_speaker)

    effective_event_type = event_type or (
        SpeakerCorrectionEventType.LINK_GLOBAL_SPEAKER
        if target_global_speaker is not None
        else SpeakerCorrectionEventType.RENAME
    )
    record_recording_speaker_corrections(
        session,
        recording_id=recording_id,
        target_recording_speaker_ids=[recording_speaker.id for recording_speaker in matching_speakers],
        actor_user_id=actor_user_id,
        event_type=effective_event_type,
        scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
        target_global_speaker_id=(target_global_speaker.id if target_global_speaker is not None else None),
        payload_by_speaker_id={
            recording_speaker.id: {
                "old_name": old_display_names.get(recording_speaker.id),
                "new_name": (
                    target_global_speaker.name if target_global_speaker is not None else new_speaker_name
                ),
                "matched_global_speaker": target_global_speaker is not None,
                "source": source,
            }
            for recording_speaker in matching_speakers
        },
    )

    if list_active_utterances(session, recording_id):
        refresh_transcript_projection_from_canonical(session, recording_id)

    return matching_speakers


def merge_recording_speakers_by_label(
    session,
    *,
    recording_id: int,
    source_diarization_label: str,
    target_diarization_label: str,
    actor_user_id: int | None = None,
    source: str = "api",
) -> tuple[RecordingSpeaker, RecordingSpeaker]:
    recording = session.get(Recording, recording_id)
    if recording is None:
        raise LookupError("Recording not found")

    if source_diarization_label == target_diarization_label:
        raise ValueError("Cannot merge speaker into itself")

    if recording_ready_for_canonical_backfill(recording.status):
        ensure_canonical_backfill(session, recording_id)

    recording_speakers = ensure_recording_speaker_aliases(session, recording_id)
    source_matches = [
        recording_speaker
        for recording_speaker in recording_speakers
        if recording_speaker.diarization_label == source_diarization_label
    ]
    target_matches = [
        recording_speaker
        for recording_speaker in recording_speakers
        if recording_speaker.diarization_label == target_diarization_label
    ]

    source_speaker = (
        _resolve_active_recording_speaker(session, source_matches[0]) if source_matches else None
    )
    target_speaker = (
        _resolve_active_recording_speaker(session, target_matches[0]) if target_matches else None
    )

    if source_speaker is None:
        raise LookupError(f"Source speaker '{source_diarization_label}' not found")
    if target_speaker is None:
        raise LookupError(f"Target speaker '{target_diarization_label}' not found")
    if source_speaker.id == target_speaker.id:
        raise ValueError("Cannot merge speaker into itself")

    if source_speaker.embedding:
        if target_speaker.embedding:
            target_speaker.embedding = merge_embeddings(
                target_speaker.embedding,
                source_speaker.embedding,
                alpha=0.5,
            )
        else:
            target_speaker.embedding = list(source_speaker.embedding)
        session.add(target_speaker)

    source_utterances = [
        utterance
        for utterance in list_active_utterances(session, recording_id)
        if (utterance.recording_speaker_id or utterance.speaker_label) == source_speaker.id
    ]
    if not source_utterances:
        source_utterances = [
            utterance
            for utterance in list_active_utterances(session, recording_id)
            if (utterance.recording_speaker_id or utterance.speaker_label)
            == source_speaker.diarization_label
        ]

    if source_utterances:
        update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id=source_utterances[0].public_id,
            new_speaker_name=_recording_speaker_display_name(session, target_speaker),
            diarization_label=target_speaker.diarization_label,
            scope=SpeakerCorrectionScope.MERGE_INTO_SPEAKER,
            actor_user_id=actor_user_id,
            source=source,
        )
    else:
        source_speaker.merged_into_id = target_speaker.id
        source_speaker.speaker_status = "merged"
        merge_recording_speaker_aliases(
            session,
            source_speaker=source_speaker,
            target_speaker=target_speaker,
        )
        _append_speaker_correction_event(
            session,
            recording_id=recording_id,
            actor_user_id=actor_user_id,
            source_recording_speaker_id=source_speaker.id,
            target_recording_speaker_id=target_speaker.id,
            target_global_speaker_id=target_speaker.global_speaker_id,
            event_type=SpeakerCorrectionEventType.MERGE_SPEAKERS,
            scope=SpeakerCorrectionScope.MERGE_INTO_SPEAKER,
            effective_from_ms=source_speaker.first_seen_ms,
            payload={
                "new_speaker_name": _recording_speaker_display_name(session, target_speaker),
                "diarization_label": target_speaker.diarization_label,
                "target_public_id": target_speaker.public_id,
            },
            update_source_provenance=True,
        )

    source_speaker.embedding = None
    session.add(source_speaker)

    if list_active_utterances(session, recording_id):
        refresh_transcript_projection_from_canonical(session, recording_id)

    return source_speaker, target_speaker


def apply_compatibility_segment_replace(
    session,
    *,
    recording_id: int,
    segments: Sequence[dict[str, Any]],
) -> list[TranscriptUtterance]:
    transcript = _load_transcript(session, recording_id)
    if transcript is None:
        return []

    active_utterances = list_active_utterances(session, recording_id)
    active_by_public_id = {utterance.public_id: utterance for utterance in active_utterances}
    incoming_ids = [str(segment.get("id")) for segment in segments if segment.get("id")]

    if active_utterances and len(incoming_ids) == len(segments) and set(incoming_ids) == set(active_by_public_id):
        recording = session.get(Recording, recording_id)
        recording_speakers = ensure_recording_speaker_aliases(session, recording_id)
        utterances: list[TranscriptUtterance] = []
        projection_segments: list[dict[str, Any]] = []
        for index, segment in enumerate(segments):
            utterance = active_by_public_id[str(segment.get("id"))]
            old_values = {
                "start_ms": utterance.start_ms,
                "end_ms": utterance.end_ms,
                "text": utterance.text,
                "speaker": utterance.speaker_label,
                "revision": utterance.revision,
            }
            old_text_locked = bool(utterance.manual_text_locked)
            old_speaker_locked = bool(utterance.manual_speaker_locked)
            effective_segment = dict(segment)
            effective_segment["text"] = utterance.text if old_text_locked else str(segment.get("text", "") or "")
            effective_segment["text_manually_edited"] = old_text_locked or bool(segment.get("text_manually_edited") is True)

            if old_speaker_locked:
                recording_speaker = (
                    session.get(RecordingSpeaker, utterance.recording_speaker_id)
                    if utterance.recording_speaker_id is not None
                    else None
                )
                effective_segment["speaker"] = (
                    recording_speaker.diarization_label
                    if recording_speaker is not None
                    else str(utterance.speaker_label or UNKNOWN_SPEAKER)
                )
            else:
                recording_speaker = _resolve_recording_speaker_for_value(
                    session,
                    recording_id=recording_id,
                    recording=recording,
                    speaker_value=str(segment.get("speaker") or UNKNOWN_SPEAKER),
                    recording_speakers=recording_speakers,
                    source_run_id=None,
                    source_segment=segment,
                )
                effective_segment["speaker"] = (
                    recording_speaker.diarization_label
                    if recording_speaker is not None
                    else str(segment.get("speaker") or UNKNOWN_SPEAKER)
                )
            effective_segment["speaker_manually_edited"] = (
                old_speaker_locked or bool(segment.get("speaker_manually_edited") is True)
            )

            effective_sort_key = _sort_key_for_index(index)
            effective_start_ms = _segment_to_ms(segment.get("start", 0.0))
            effective_end_ms = _segment_to_ms(segment.get("end", 0.0))
            effective_text = str(effective_segment.get("text", "") or "")
            effective_recording_speaker_id = recording_speaker.id if recording_speaker else None
            effective_speaker_label = (
                recording_speaker.diarization_label
                if recording_speaker is not None
                else str(effective_segment.get("speaker") or UNKNOWN_SPEAKER)
            )
            effective_manual_text_locked = bool(effective_segment.get("text_manually_edited") is True)
            effective_manual_speaker_locked = bool(effective_segment.get("speaker_manually_edited") is True)
            effective_state = _state_for_segment(recording, effective_segment)

            changed = any(
                (
                    utterance.sort_key != effective_sort_key,
                    utterance.start_ms != effective_start_ms,
                    utterance.end_ms != effective_end_ms,
                    utterance.text != effective_text,
                    utterance.recording_speaker_id != effective_recording_speaker_id,
                    utterance.speaker_label != effective_speaker_label,
                    bool(utterance.manual_text_locked) != effective_manual_text_locked,
                    bool(utterance.manual_speaker_locked) != effective_manual_speaker_locked,
                    utterance.state != effective_state,
                    utterance.overlap_group_id is not None,
                    int(utterance.overlap_rank or 0) != 0,
                )
            )

            if changed:
                utterance.sort_key = effective_sort_key
                utterance.start_ms = effective_start_ms
                utterance.end_ms = effective_end_ms
                utterance.text = effective_text
                utterance.recording_speaker_id = effective_recording_speaker_id
                utterance.speaker_label = effective_speaker_label
                utterance.manual_text_locked = effective_manual_text_locked
                utterance.manual_speaker_locked = effective_manual_speaker_locked
                utterance.revision += 1
                utterance.overlap_group_id = None
                utterance.overlap_rank = 0
                utterance.state = effective_state
                session.add(utterance)
                session.flush()
                new_values = {
                    "start_ms": utterance.start_ms,
                    "end_ms": utterance.end_ms,
                    "text": utterance.text,
                    "speaker": utterance.speaker_label,
                }
                _append_utterance_event(
                    session,
                    utterance=utterance,
                    event_type=_compatibility_replace_event_type(old_values, new_values),
                    source="api",
                    old_values=old_values,
                    new_values=new_values,
                    resulting_revision=utterance.revision,
                )
                _record_manual_lock_events(
                    session,
                    utterance=utterance,
                    old_text_locked=old_text_locked,
                    old_speaker_locked=old_speaker_locked,
                    source="api",
                )
            utterances.append(utterance)
            effective_segment["revision"] = utterance.revision
            projection_segments.append(
                _build_projection_segment(
                    utterance,
                    source_segment=effective_segment,
                    recording_speaker=recording_speaker,
                    overlap_labels=list(effective_segment.get("overlapping_speakers") or []),
                )
            )

        transcript.segments = projection_segments
        transcript.text = " ".join(segment.get("text", "") for segment in projection_segments).strip()
        flag_modified(transcript, "segments")
        session.add(transcript)
        return utterances

    return replace_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=segments,
        run_kind=None,
        source="compatibility_replace",
        force=True,
    )


def serialize_canonical_utterances(
    session,
    recording_id: int,
    only_public_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    transcript = _load_transcript(session, recording_id)
    recording_speakers = _load_recording_speakers(session, recording_id)
    recording_speakers_by_id = {
        int(speaker.id): speaker for speaker in recording_speakers if speaker.id is not None
    }
    projection_by_id = {
        str(segment.get("id")): segment
        for segment in (transcript.segments or [])
        if isinstance(segment, dict) and segment.get("id")
    } if transcript else {}
    payloads: list[dict[str, Any]] = []
    for utterance in list_active_utterances(session, recording_id):
        if only_public_ids is not None and utterance.public_id not in only_public_ids:
            continue
        projection = projection_by_id.get(utterance.public_id, {})
        payloads.append(
            {
                "id": utterance.public_id,
                "start": utterance.start_ms / 1000.0,
                "end": utterance.end_ms / 1000.0,
                "start_ms": utterance.start_ms,
                "end_ms": utterance.end_ms,
                "text": utterance.text,
                "speaker": utterance.speaker_label or projection.get("speaker") or UNKNOWN_SPEAKER,
                "recording_speaker_id": utterance.recording_speaker_id,
                "state": utterance.state.value if hasattr(utterance.state, "value") else str(utterance.state),
                "revision": utterance.revision,
                "segment_source": utterance.source_kind,
                "provisional": (utterance.state.value if hasattr(utterance.state, "value") else str(utterance.state)) == TranscriptUtteranceState.PROVISIONAL.value,
                "speaker_manually_edited": utterance.manual_speaker_locked,
                "text_manually_edited": utterance.manual_text_locked,
                "speaker_state": _speaker_state_for_utterance(utterance, projection=projection),
                "speaker_confidence": utterance.speaker_confidence,
                "text_confidence": utterance.text_confidence,
                "updated_at": utterance.updated_at.isoformat(),
                "overlapping_speakers": _rolling_overlap_labels_for_utterance(
                    utterance,
                    projection=projection,
                    recording_speakers_by_id=recording_speakers_by_id,
                ),
            }
        )
    return payloads


def _normalize_transcript_segments(raw_segments: Any) -> list[dict[str, Any]]:
    if isinstance(raw_segments, str):
        try:
            raw_segments = json.loads(raw_segments)
        except json.JSONDecodeError:
            return []

    if not isinstance(raw_segments, list):
        return []

    normalized: list[dict[str, Any]] = []
    for segment in raw_segments:
        if isinstance(segment, dict):
            normalized.append(dict(segment))
    return normalized


def build_transcript_segments_for_read(
    session,
    recording_id: int,
    *,
    transcript: Transcript | None = None,
) -> list[dict[str, Any]]:
    transcript = transcript if transcript is not None else _load_transcript(session, recording_id)
    fallback_segments = _normalize_transcript_segments(
        getattr(transcript, "segments", None) if transcript is not None else None
    )

    if transcript is None or not hasattr(session, "execute"):
        return fallback_segments

    try:
        canonical_segments = serialize_canonical_utterances(session, recording_id)
    except Exception:
        return fallback_segments

    if canonical_segments:
        return [dict(segment) for segment in canonical_segments]
    return fallback_segments


def build_transcript_text_for_read(
    session,
    recording_id: int,
    *,
    transcript: Transcript | None = None,
    segments: Sequence[dict[str, Any]] | None = None,
) -> str:
    effective_segments = [dict(segment) for segment in segments] if segments is not None else build_transcript_segments_for_read(
        session,
        recording_id,
        transcript=transcript,
    )
    if effective_segments:
        return " ".join(str(segment.get("text", "") or "") for segment in effective_segments).strip()

    transcript = transcript if transcript is not None else _load_transcript(session, recording_id)
    return str(getattr(transcript, "text", "") or "") if transcript is not None else ""


def build_reusable_live_segments(session, recording_id: int) -> list[dict[str, Any]]:
    reusable_segments: list[dict[str, Any]] = []
    for payload in serialize_canonical_utterances(session, recording_id):
        source_kind = str(payload.get("segment_source") or "")
        if source_kind not in {"live", "catch_up"} and payload.get("provisional") is not True:
            continue
        reusable_segments.append(dict(payload))
    return reusable_segments


def refresh_transcript_projection_from_canonical(
    session,
    recording_id: int,
) -> list[dict[str, Any]]:
    transcript = _load_transcript(session, recording_id)
    if transcript is None:
        return []

    projection_segments = serialize_canonical_utterances(session, recording_id)
    transcript.segments = projection_segments
    transcript.text = " ".join(
        str(segment.get("text", "") or "")
        for segment in projection_segments
    ).strip()
    flag_modified(transcript, "segments")
    session.add(transcript)
    return projection_segments


def reconcile_diarization_window_result(
    session,
    *,
    recording_id: int,
    window_result_id: int,
    processing_run_id: int | None = None,
    source: str = "rolling_diarization",
) -> dict[str, int]:
    window_result = session.get(DiarizationWindowResult, window_result_id)
    if window_result is None or window_result.recording_id != recording_id:
        return {"matched_turn_count": 0, "updated_utterance_count": 0, "preserved_manual_lock_count": 0}

    turn_rows = list(
        session.execute(
            select(DiarizationWindowTurn)
            .where(DiarizationWindowTurn.window_result_id == window_result.id)
            .order_by(DiarizationWindowTurn.start_ms, DiarizationWindowTurn.end_ms, DiarizationWindowTurn.id)
        ).scalars().all()
    )
    if not turn_rows:
        return {"matched_turn_count": 0, "updated_utterance_count": 0, "preserved_manual_lock_count": 0}

    recording_speakers = _load_recording_speakers(session, recording_id)
    recording_speakers_by_id = {speaker.id: speaker for speaker in recording_speakers if speaker.id is not None}
    overlapping_utterances = list(
        session.execute(
            select(TranscriptUtterance)
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(TranscriptUtterance.state.in_(ACTIVE_UTTERANCE_STATES))
            .where(TranscriptUtterance.start_ms < int(window_result.window_end_ms))
            .where(TranscriptUtterance.end_ms > int(window_result.window_start_ms))
            .order_by(TranscriptUtterance.sort_key, TranscriptUtterance.id)
        ).scalars().all()
    )
    previous_turn_rows = list(
        session.execute(
            select(DiarizationWindowTurn)
            .join(
                DiarizationWindowResult,
                DiarizationWindowResult.id == DiarizationWindowTurn.window_result_id,
            )
            .where(DiarizationWindowResult.recording_id == recording_id)
            .where(DiarizationWindowResult.id != window_result.id)
            .where(DiarizationWindowResult.status == "completed")
            .where(DiarizationWindowTurn.matched_recording_speaker_id.is_not(None))
            .where(DiarizationWindowTurn.end_ms > int(window_result.window_start_ms))
            .where(DiarizationWindowTurn.start_ms < int(window_result.window_end_ms))
        ).scalars().all()
    )

    raw_payload = dict(window_result.raw_payload or {})
    speaker_metadata_by_key = {
        str(local_speaker_key): dict(metadata or {})
        for local_speaker_key, metadata in (raw_payload.get("speaker_metadata") or {}).items()
    }

    turns_by_local_speaker: dict[str, list[DiarizationWindowTurn]] = defaultdict(list)
    for turn_row in turn_rows:
        turns_by_local_speaker[str(turn_row.local_speaker_key)].append(turn_row)

    matched_turn_count = 0
    for local_speaker_key, local_turn_rows in turns_by_local_speaker.items():
        matched_speaker, turn_confidence, evidence_payload = _match_window_local_speaker(
            session,
            local_turn_rows=local_turn_rows,
            speaker_metadata=speaker_metadata_by_key.get(local_speaker_key, {}),
            overlapping_utterances=overlapping_utterances,
            previous_turn_rows=previous_turn_rows,
            recording_speakers_by_id=recording_speakers_by_id,
        )

        metadata_payload = dict(speaker_metadata_by_key.get(local_speaker_key, {}))
        metadata_payload.update(
            {
                "matched_recording_speaker_id": int(matched_speaker.id) if matched_speaker is not None else None,
                "match_confidence": round(float(turn_confidence), 4),
                "provisional": matched_speaker is None,
                "evidence": evidence_payload,
            }
        )
        speaker_metadata_by_key[local_speaker_key] = metadata_payload

        for turn_row in local_turn_rows:
            turn_metadata = dict(turn_row.metadata_payload or {})
            turn_metadata["match"] = metadata_payload
            turn_row.metadata_payload = turn_metadata
            turn_row.confidence = round(float(turn_confidence), 4)
            turn_row.matched_recording_speaker_id = matched_speaker.id if matched_speaker is not None else None
            session.add(turn_row)

        if matched_speaker is not None:
            matched_turn_count += len(local_turn_rows)
            matched_speaker.last_diarization_window_result_id = window_result.id
            session.add(matched_speaker)

    raw_payload["speaker_metadata"] = speaker_metadata_by_key
    window_result.raw_payload = raw_payload
    flag_modified(window_result, "raw_payload")
    session.add(window_result)

    support_turn_rows = [*previous_turn_rows, *turn_rows]
    updated_utterance_count = 0
    preserved_manual_lock_count = 0
    projection_dirty = False
    merge_source_utterance_ids: set[int] = set()

    merge_plans = _build_merge_replacement_plans_from_diarization(
        overlapping_utterances,
        turn_rows=turn_rows,
        recording_speakers_by_id=recording_speakers_by_id,
        window_result_id=int(window_result.id),
    )
    for merge_plan in merge_plans:
        replacement_utterances = _apply_boundary_reconciliation_segments(
            session,
            recording_id=recording_id,
            source_utterances=merge_plan["source_utterances"],
            replacement_segments=merge_plan["replacement_segments"],
            processing_run_id=processing_run_id,
            source=source,
        )
        if not replacement_utterances:
            continue
        updated_utterance_count += len(replacement_utterances)
        merge_source_utterance_ids.update(
            int(source_utterance.id)
            for source_utterance in merge_plan["source_utterances"]
            if source_utterance.id is not None
        )

    for utterance in overlapping_utterances:
        if utterance.id is not None and int(utterance.id) in merge_source_utterance_ids:
            continue
        support_summary = _summarize_utterance_turn_support(
            utterance,
            turn_rows=support_turn_rows,
        )
        existing_payload = dict(utterance.confidence_payload or {})
        existing_rolling_payload = dict(existing_payload.get("rolling_diarization") or {})
        split_replacement_segments = _build_split_replacement_segments_from_diarization(
            utterance,
            turn_rows=turn_rows,
            recording_speakers_by_id=recording_speakers_by_id,
            window_result_id=int(window_result.id),
        )
        if split_replacement_segments:
            replacement_utterances = _apply_boundary_reconciliation_segments(
                session,
                recording_id=recording_id,
                source_utterances=[utterance],
                replacement_segments=split_replacement_segments,
                processing_run_id=processing_run_id,
                source=source,
            )
            if replacement_utterances:
                updated_utterance_count += len(replacement_utterances)
            continue

        candidate_speaker, candidate_confidence, candidate_payload = _match_utterance_from_diarization_turns(
            utterance,
            turn_rows=turn_rows,
            recording_speakers_by_id=recording_speakers_by_id,
        )
        current_speaker_id = utterance.recording_speaker_id
        existing_speaker_confidence = _to_optional_float(utterance.speaker_confidence) or 0.0

        def overlap_payload_for(applied_speaker_id: int | None) -> dict[str, Any]:
            return _build_utterance_overlap_projection_payload(
                utterance,
                turn_rows=turn_rows,
                recording_speakers_by_id=recording_speakers_by_id,
                primary_speaker_id=applied_speaker_id,
            )

        if candidate_speaker is None:
            overlap_payload = overlap_payload_for(int(current_speaker_id) if current_speaker_id is not None else None)
            if not _rolling_overlap_payload_changed(existing_rolling_payload, overlap_payload):
                continue
            rolling_payload = dict(existing_rolling_payload)
            rolling_payload["window_result_id"] = int(window_result.id)
            _merge_overlap_payload_into_rolling(rolling_payload, overlap_payload)
            existing_payload["rolling_diarization"] = rolling_payload
            utterance.confidence_payload = existing_payload
            utterance.last_diarization_window_result_id = window_result.id
            session.add(utterance)
            projection_dirty = True
            continue

        rolling_payload = dict(candidate_payload)
        rolling_payload.update(
            {
                "window_result_id": int(window_result.id),
                "matched_recording_speaker_id": int(candidate_speaker.id),
                "confidence": round(float(candidate_confidence), 4),
            }
        )

        candidate_state_payload = _build_utterance_speaker_state_payload(
            utterance,
            speaker_id=int(candidate_speaker.id),
            confidence=candidate_confidence,
            support_summary=support_summary,
        )

        current_state_payload = None
        if current_speaker_id == candidate_speaker.id:
            current_state_payload = dict(candidate_state_payload)
        elif current_speaker_id is not None:
            current_state_payload = _build_utterance_speaker_state_payload(
                utterance,
                speaker_id=int(current_speaker_id),
                confidence=existing_speaker_confidence,
                support_summary=support_summary,
                manual_override=utterance.manual_speaker_locked,
            )

        if utterance.manual_speaker_locked:
            preserved_manual_lock_count += 1
            rolling_payload.update(
                current_state_payload
                or _build_utterance_speaker_state_payload(
                    utterance,
                    speaker_id=(int(current_speaker_id) if current_speaker_id is not None else int(candidate_speaker.id)),
                    confidence=existing_speaker_confidence or candidate_confidence,
                    support_summary=support_summary,
                    manual_override=True,
                )
            )
            rolling_payload["applied_recording_speaker_id"] = (
                int(current_speaker_id) if current_speaker_id is not None else None
            )
            rolling_payload["candidate_recording_speaker_id"] = int(candidate_speaker.id)
            _merge_overlap_payload_into_rolling(
                rolling_payload,
                overlap_payload_for(int(current_speaker_id) if current_speaker_id is not None else None),
            )
            existing_payload["rolling_diarization"] = rolling_payload
            utterance.confidence_payload = existing_payload
            utterance.last_diarization_window_result_id = window_result.id
            session.add(utterance)
            projection_dirty = True
            continue

        if current_speaker_id == candidate_speaker.id:
            rolling_payload.update(candidate_state_payload)
            rolling_payload["applied_recording_speaker_id"] = int(candidate_speaker.id)
            _merge_overlap_payload_into_rolling(
                rolling_payload,
                overlap_payload_for(int(candidate_speaker.id)),
            )
            utterance.speaker_confidence = max(existing_speaker_confidence, candidate_confidence)
            utterance.last_diarization_window_result_id = window_result.id
            existing_payload["rolling_diarization"] = rolling_payload
            utterance.confidence_payload = existing_payload
            session.add(utterance)
            projection_dirty = True
            continue

        if (
            current_speaker_id is not None
            and current_state_payload is not None
            and current_state_payload.get("speaker_state") == ROLLING_DIARIZATION_SPEAKER_STATE_STABLE
            and int(candidate_state_payload.get("supporting_window_count", 0)) < ROLLING_DIARIZATION_STABLE_WINDOW_COUNT
        ):
            rolling_payload.update(current_state_payload)
            rolling_payload["applied_recording_speaker_id"] = int(current_speaker_id)
            rolling_payload["candidate_recording_speaker_id"] = int(candidate_speaker.id)
            rolling_payload["candidate_confidence"] = round(float(candidate_confidence), 4)
            rolling_payload["candidate_supporting_window_count"] = int(
                candidate_state_payload.get("supporting_window_count", 0)
            )
            rolling_payload["candidate_rejected"] = True
            rolling_payload["rejection_reason"] = "stable_speaker_requires_repeated_evidence"
            _merge_overlap_payload_into_rolling(
                rolling_payload,
                overlap_payload_for(int(current_speaker_id)),
            )
            existing_payload["rolling_diarization"] = rolling_payload
            utterance.confidence_payload = existing_payload
            utterance.last_diarization_window_result_id = window_result.id
            session.add(utterance)
            projection_dirty = True
            continue

        if existing_speaker_confidence >= (candidate_confidence + ROLLING_DIARIZATION_EXISTING_CONFIDENCE_MARGIN):
            overlap_payload = overlap_payload_for(int(current_speaker_id) if current_speaker_id is not None else None)
            if not _rolling_overlap_payload_changed(existing_rolling_payload, overlap_payload):
                continue
            if current_state_payload is not None:
                rolling_payload.update(current_state_payload)
            rolling_payload["applied_recording_speaker_id"] = (
                int(current_speaker_id) if current_speaker_id is not None else None
            )
            rolling_payload["candidate_recording_speaker_id"] = int(candidate_speaker.id)
            rolling_payload["candidate_confidence"] = round(float(candidate_confidence), 4)
            rolling_payload["candidate_rejected"] = True
            rolling_payload["rejection_reason"] = "existing_speaker_confidence_higher"
            _merge_overlap_payload_into_rolling(rolling_payload, overlap_payload)
            existing_payload["rolling_diarization"] = rolling_payload
            utterance.confidence_payload = existing_payload
            utterance.last_diarization_window_result_id = window_result.id
            session.add(utterance)
            projection_dirty = True
            continue

        old_values = {
            "speaker_label": utterance.speaker_label,
            "recording_speaker_id": utterance.recording_speaker_id,
            "speaker_confidence": utterance.speaker_confidence,
            "revision": utterance.revision,
        }
        utterance.speaker_label = candidate_speaker.diarization_label
        utterance.recording_speaker_id = candidate_speaker.id
        utterance.speaker_confidence = candidate_confidence
        utterance.last_diarization_window_result_id = window_result.id
        rolling_payload.update(candidate_state_payload)
        rolling_payload["applied_recording_speaker_id"] = int(candidate_speaker.id)
        _merge_overlap_payload_into_rolling(
            rolling_payload,
            overlap_payload_for(int(candidate_speaker.id)),
        )
        existing_payload["rolling_diarization"] = rolling_payload
        utterance.confidence_payload = existing_payload
        utterance.revision += 1
        session.add(utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=utterance,
            processing_run_id=processing_run_id,
            event_type="update_speaker",
            source=source,
            old_values=old_values,
            new_values={
                "speaker_label": utterance.speaker_label,
                "recording_speaker_id": utterance.recording_speaker_id,
                "speaker_confidence": utterance.speaker_confidence,
                "last_diarization_window_result_id": utterance.last_diarization_window_result_id,
            },
            resulting_revision=utterance.revision,
        )
        updated_utterance_count += 1
        projection_dirty = True

    if projection_dirty:
        refresh_transcript_projection_from_canonical(session, recording_id)

    return {
        "matched_turn_count": matched_turn_count,
        "updated_utterance_count": updated_utterance_count,
        "preserved_manual_lock_count": preserved_manual_lock_count,
    }


def _match_window_local_speaker(
    session,
    *,
    local_turn_rows: Sequence[DiarizationWindowTurn],
    speaker_metadata: dict[str, Any],
    overlapping_utterances: Sequence[TranscriptUtterance],
    previous_turn_rows: Sequence[DiarizationWindowTurn],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
) -> tuple[RecordingSpeaker | None, float, dict[str, Any]]:
    evidence_by_speaker_id: dict[int, float] = defaultdict(float)
    detail_by_speaker_id: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "manual_overlap_ms": 0,
            "utterance_overlap_ms": 0,
            "continuity_overlap_ms": 0,
            "embedding_score": None,
            "global_score": None,
        }
    )

    for turn_row in local_turn_rows:
        for utterance in overlapping_utterances:
            if utterance.recording_speaker_id is None:
                continue
            overlap_ms = _range_overlap_ms(
                turn_row.start_ms,
                turn_row.end_ms,
                utterance.start_ms,
                utterance.end_ms,
            )
            if overlap_ms <= 0:
                continue

            resolved_speaker = recording_speakers_by_id.get(utterance.recording_speaker_id)
            if resolved_speaker is None:
                continue
            resolved_speaker = _resolve_active_recording_speaker(session, resolved_speaker)

            if utterance.manual_speaker_locked:
                evidence_by_speaker_id[resolved_speaker.id] += overlap_ms * ROLLING_DIARIZATION_MANUAL_WEIGHT
                detail_by_speaker_id[resolved_speaker.id]["manual_overlap_ms"] += overlap_ms
            else:
                evidence_by_speaker_id[resolved_speaker.id] += overlap_ms * ROLLING_DIARIZATION_UTTERANCE_WEIGHT
                detail_by_speaker_id[resolved_speaker.id]["utterance_overlap_ms"] += overlap_ms

        for previous_turn_row in previous_turn_rows:
            if previous_turn_row.matched_recording_speaker_id is None:
                continue
            overlap_ms = _range_overlap_ms(
                turn_row.start_ms,
                turn_row.end_ms,
                previous_turn_row.start_ms,
                previous_turn_row.end_ms,
            )
            if overlap_ms <= 0:
                continue
            evidence_by_speaker_id[int(previous_turn_row.matched_recording_speaker_id)] += (
                overlap_ms * ROLLING_DIARIZATION_CONTINUITY_WEIGHT
            )
            detail_by_speaker_id[int(previous_turn_row.matched_recording_speaker_id)][
                "continuity_overlap_ms"
            ] += overlap_ms

    best_recording_speaker_id = speaker_metadata.get("best_recording_speaker_id")
    if best_recording_speaker_id is not None:
        try:
            best_recording_speaker_id = int(best_recording_speaker_id)
        except (TypeError, ValueError):
            best_recording_speaker_id = None
    best_recording_speaker_score = _to_optional_float(
        speaker_metadata.get("best_recording_speaker_score")
    )
    if (
        best_recording_speaker_id is not None
        and best_recording_speaker_id in recording_speakers_by_id
        and best_recording_speaker_score is not None
    ):
        evidence_by_speaker_id[int(best_recording_speaker_id)] += (
            best_recording_speaker_score * ROLLING_DIARIZATION_EMBEDDING_WEIGHT
        )
        detail_by_speaker_id[int(best_recording_speaker_id)]["embedding_score"] = best_recording_speaker_score

    best_global_speaker_id = speaker_metadata.get("best_global_speaker_id")
    if best_global_speaker_id is not None:
        try:
            best_global_speaker_id = int(best_global_speaker_id)
        except (TypeError, ValueError):
            best_global_speaker_id = None
    best_global_speaker_score = _to_optional_float(
        speaker_metadata.get("best_global_speaker_score")
    )
    if best_global_speaker_id is not None and best_global_speaker_score is not None:
        for recording_speaker in recording_speakers_by_id.values():
            if recording_speaker.global_speaker_id != int(best_global_speaker_id):
                continue
            resolved_speaker = _resolve_active_recording_speaker(session, recording_speaker)
            evidence_by_speaker_id[int(resolved_speaker.id)] += (
                best_global_speaker_score * ROLLING_DIARIZATION_GLOBAL_WEIGHT
            )
            detail_by_speaker_id[int(resolved_speaker.id)]["global_score"] = best_global_speaker_score
            break

    if not evidence_by_speaker_id:
        return None, 0.0, {"provisional": True, "reason": "no_evidence"}

    ranked_candidates = sorted(
        evidence_by_speaker_id.items(),
        key=lambda item: (float(item[1]), int(item[0])),
        reverse=True,
    )
    top_speaker_id, top_score = ranked_candidates[0]
    second_score = ranked_candidates[1][1] if len(ranked_candidates) > 1 else 0.0
    total_score = sum(evidence_by_speaker_id.values()) or top_score
    confidence = round(float(top_score) / float(total_score or 1.0), 4)
    evidence_detail = dict(detail_by_speaker_id[top_speaker_id])
    evidence_detail["margin_score"] = round(float(top_score - second_score), 3)
    evidence_detail["weighted_score"] = round(float(top_score), 3)

    matched = False
    if evidence_detail["manual_overlap_ms"] >= ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS:
        matched = True
    elif evidence_detail["continuity_overlap_ms"] >= ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS:
        matched = True
    elif (
        best_recording_speaker_id == top_speaker_id
        and best_recording_speaker_score is not None
        and best_recording_speaker_score >= 0.75
    ):
        matched = True
    elif (
        evidence_detail["utterance_overlap_ms"] >= ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS
        and confidence >= ROLLING_DIARIZATION_CONFIDENCE_FLOOR
        and (top_score - second_score) >= ROLLING_DIARIZATION_MIN_TURN_MATCH_MARGIN
    ):
        matched = True
    elif confidence >= 0.8 and (top_score - second_score) >= ROLLING_DIARIZATION_MIN_TURN_MATCH_MARGIN:
        matched = True

    if not matched:
        evidence_detail["provisional"] = True
        return None, confidence, evidence_detail

    matched_speaker = recording_speakers_by_id.get(top_speaker_id)
    if matched_speaker is None:
        evidence_detail["provisional"] = True
        return None, confidence, evidence_detail

    evidence_detail["provisional"] = False
    return _resolve_active_recording_speaker(session, matched_speaker), confidence, evidence_detail


def _match_utterance_from_diarization_turns(
    utterance: TranscriptUtterance,
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
) -> tuple[RecordingSpeaker | None, float, dict[str, Any]]:
    overlap_by_speaker_id: dict[int, int] = defaultdict(int)
    for turn_row in turn_rows:
        if turn_row.matched_recording_speaker_id is None:
            continue
        overlap_ms = _range_overlap_ms(
            utterance.start_ms,
            utterance.end_ms,
            turn_row.start_ms,
            turn_row.end_ms,
        )
        if overlap_ms <= 0:
            continue
        overlap_by_speaker_id[int(turn_row.matched_recording_speaker_id)] += overlap_ms

    if not overlap_by_speaker_id:
        return None, 0.0, {}

    ranked = sorted(
        overlap_by_speaker_id.items(),
        key=lambda item: (int(item[1]), int(item[0])),
        reverse=True,
    )
    top_speaker_id, top_overlap_ms = ranked[0]
    second_overlap_ms = ranked[1][1] if len(ranked) > 1 else 0
    total_overlap_ms = sum(overlap_by_speaker_id.values()) or top_overlap_ms
    confidence = round(float(top_overlap_ms) / float(total_overlap_ms or 1.0), 4)

    if top_overlap_ms < ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS:
        return None, confidence, {
            "top_overlap_ms": int(top_overlap_ms),
            "second_overlap_ms": int(second_overlap_ms),
            "total_overlap_ms": int(total_overlap_ms),
            "provisional": True,
        }

    if len(ranked) > 1 and confidence < ROLLING_DIARIZATION_CONFIDENCE_FLOOR:
        return None, confidence, {
            "top_overlap_ms": int(top_overlap_ms),
            "second_overlap_ms": int(second_overlap_ms),
            "total_overlap_ms": int(total_overlap_ms),
            "provisional": True,
        }

    matched_speaker = recording_speakers_by_id.get(top_speaker_id)
    if matched_speaker is None:
        return None, confidence, {
            "top_overlap_ms": int(top_overlap_ms),
            "second_overlap_ms": int(second_overlap_ms),
            "total_overlap_ms": int(total_overlap_ms),
            "provisional": True,
        }

    return matched_speaker, confidence, {
        "top_overlap_ms": int(top_overlap_ms),
        "second_overlap_ms": int(second_overlap_ms),
        "total_overlap_ms": int(total_overlap_ms),
        "provisional": False,
    }


def _build_utterance_overlap_projection_payload(
    utterance: TranscriptUtterance,
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
    primary_speaker_id: int | None,
) -> dict[str, Any]:
    overlap_by_speaker_id: dict[int, int] = defaultdict(int)
    for turn_row in turn_rows:
        if turn_row.matched_recording_speaker_id is None:
            continue
        overlap_ms = _range_overlap_ms(
            utterance.start_ms,
            utterance.end_ms,
            turn_row.start_ms,
            turn_row.end_ms,
        )
        if overlap_ms <= 0:
            continue
        overlap_by_speaker_id[int(turn_row.matched_recording_speaker_id)] += overlap_ms

    ranked_speakers = sorted(
        overlap_by_speaker_id.items(),
        key=lambda item: (int(item[1]), int(item[0])),
        reverse=True,
    )
    overlapping_recording_speaker_ids: list[int] = []
    overlapping_speakers: list[str] = []

    for speaker_id, overlap_ms in ranked_speakers:
        if primary_speaker_id is not None and int(speaker_id) == int(primary_speaker_id):
            continue
        if int(overlap_ms) < ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS:
            continue
        recording_speaker = recording_speakers_by_id.get(int(speaker_id))
        if recording_speaker is None:
            continue
        overlapping_recording_speaker_ids.append(int(speaker_id))
        label = str(recording_speaker.diarization_label or "").strip()
        if not label or label == (utterance.speaker_label or UNKNOWN_SPEAKER):
            continue
        if label not in overlapping_speakers:
            overlapping_speakers.append(label)

    return {
        "overlapping_recording_speaker_ids": overlapping_recording_speaker_ids,
        "overlapping_speakers": overlapping_speakers,
    }


def _merge_overlap_payload_into_rolling(
    rolling_payload: dict[str, Any],
    overlap_payload: dict[str, Any],
) -> None:
    rolling_payload["overlapping_recording_speaker_ids"] = list(
        overlap_payload.get("overlapping_recording_speaker_ids") or []
    )
    rolling_payload["overlapping_speakers"] = list(
        overlap_payload.get("overlapping_speakers") or []
    )


def _rolling_overlap_payload_changed(
    existing_rolling_payload: dict[str, Any],
    overlap_payload: dict[str, Any],
) -> bool:
    return (
        list(existing_rolling_payload.get("overlapping_recording_speaker_ids") or [])
        != list(overlap_payload.get("overlapping_recording_speaker_ids") or [])
        or list(existing_rolling_payload.get("overlapping_speakers") or [])
        != list(overlap_payload.get("overlapping_speakers") or [])
    )


def _build_split_replacement_segments_from_diarization(
    utterance: TranscriptUtterance,
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
    window_result_id: int,
) -> list[dict[str, Any]]:
    if utterance.manual_text_locked or utterance.manual_speaker_locked:
        return []

    utterance_words = _load_utterance_asr_words(utterance)
    if len(utterance_words) < 2:
        return []

    speaker_groups: list[dict[str, Any]] = []
    for word_payload in utterance_words:
        speaker_id = _match_word_to_recording_speaker_id(
            word_payload,
            turn_rows=turn_rows,
        )
        if speaker_id is None or speaker_id not in recording_speakers_by_id:
            return []
        if speaker_groups and int(speaker_groups[-1]["speaker_id"]) == int(speaker_id):
            speaker_groups[-1]["words"].append(word_payload)
            continue
        speaker_groups.append(
            {
                "speaker_id": int(speaker_id),
                "words": [word_payload],
            }
        )

    if len(speaker_groups) < 2:
        return []
    if len({int(group["speaker_id"]) for group in speaker_groups}) < 2:
        return []

    base_payload = dict(utterance.confidence_payload or {})
    base_rolling_payload = dict(base_payload.get("rolling_diarization") or {})
    replacement_segments: list[dict[str, Any]] = []

    for group in speaker_groups:
        words = list(group["words"])
        group_text = _join_asr_words(words)
        if not group_text:
            return []

        group_start_ms = max(int(utterance.start_ms), int(words[0]["start_ms"]))
        group_end_ms = min(int(utterance.end_ms), int(words[-1]["end_ms"]))
        if group_end_ms <= group_start_ms:
            return []

        recording_speaker = recording_speakers_by_id[int(group["speaker_id"])]
        rolling_payload = dict(base_rolling_payload)
        rolling_payload.update(
            {
                "window_result_id": int(window_result_id),
                "matched_recording_speaker_id": int(recording_speaker.id),
                "split_from_public_id": utterance.public_id,
            }
        )
        replacement_payload = dict(base_payload)
        replacement_payload["rolling_diarization"] = rolling_payload
        replacement_payload["asr_segments"] = [
            {
                "start_ms": int(group_start_ms),
                "end_ms": int(group_end_ms),
                "text": group_text,
                "words": [dict(word_payload) for word_payload in words],
            }
        ]
        replacement_payload["asr_word_timestamps_available"] = True

        replacement_segments.append(
            {
                "id": str(uuid4()),
                "start": group_start_ms / 1000.0,
                "end": group_end_ms / 1000.0,
                "text": group_text,
                "speaker": recording_speaker.diarization_label,
                "recording_speaker_id": int(recording_speaker.id),
                "segment_source": utterance.source_kind,
                "provisional": utterance.state == TranscriptUtteranceState.PROVISIONAL,
                "state": utterance.state.value if hasattr(utterance.state, "value") else str(utterance.state),
                "speaker_manually_edited": False,
                "text_manually_edited": False,
                "speaker_confidence": utterance.speaker_confidence,
                "text_confidence": utterance.text_confidence,
                "confidence_payload": replacement_payload,
                "last_diarization_window_result_id": int(window_result_id),
            }
        )

    return replacement_segments


def _build_merge_replacement_plans_from_diarization(
    overlapping_utterances: Sequence[TranscriptUtterance],
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
    window_result_id: int,
) -> list[dict[str, Any]]:
    split_candidate_ids = {
        int(utterance.id)
        for utterance in overlapping_utterances
        if utterance.id is not None
        and _build_split_replacement_segments_from_diarization(
            utterance,
            turn_rows=turn_rows,
            recording_speakers_by_id=recording_speakers_by_id,
            window_result_id=window_result_id,
        )
    }

    merge_plans: list[dict[str, Any]] = []
    current_group: list[TranscriptUtterance] = []
    current_confidences: list[float] = []
    current_speaker: RecordingSpeaker | None = None

    def flush_group() -> None:
        nonlocal current_group, current_confidences, current_speaker
        if current_speaker is not None and len(current_group) > 1:
            replacement_segments = _build_merge_replacement_segments_from_diarization(
                source_utterances=current_group,
                recording_speaker=current_speaker,
                speaker_confidences=current_confidences,
                window_result_id=window_result_id,
            )
            if replacement_segments:
                merge_plans.append(
                    {
                        "source_utterances": list(current_group),
                        "replacement_segments": replacement_segments,
                    }
                )
        current_group = []
        current_confidences = []
        current_speaker = None

    for utterance in overlapping_utterances:
        if (
            utterance.id is not None
            and int(utterance.id) in split_candidate_ids
        ) or utterance.manual_text_locked or utterance.manual_speaker_locked:
            flush_group()
            continue

        candidate_speaker, candidate_confidence, _candidate_payload = _match_utterance_from_diarization_turns(
            utterance,
            turn_rows=turn_rows,
            recording_speakers_by_id=recording_speakers_by_id,
        )
        if candidate_speaker is None:
            flush_group()
            continue

        if not current_group or current_speaker is None:
            current_group = [utterance]
            current_confidences = [candidate_confidence]
            current_speaker = candidate_speaker
            continue

        if int(candidate_speaker.id) != int(current_speaker.id):
            flush_group()
            current_group = [utterance]
            current_confidences = [candidate_confidence]
            current_speaker = candidate_speaker
            continue

        if not _boundary_supported_by_same_speaker_turns(
            current_group[-1],
            utterance,
            speaker_id=int(candidate_speaker.id),
            turn_rows=turn_rows,
        ):
            flush_group()
            current_group = [utterance]
            current_confidences = [candidate_confidence]
            current_speaker = candidate_speaker
            continue

        current_group.append(utterance)
        current_confidences.append(candidate_confidence)

    flush_group()
    return merge_plans


def _boundary_supported_by_same_speaker_turns(
    left_utterance: TranscriptUtterance,
    right_utterance: TranscriptUtterance,
    *,
    speaker_id: int,
    turn_rows: Sequence[DiarizationWindowTurn],
) -> bool:
    interval_start_ms = max(
        int(left_utterance.start_ms),
        int(left_utterance.end_ms) - ROLLING_DIARIZATION_MERGE_BOUNDARY_GAP_MS,
    )
    interval_end_ms = min(
        int(right_utterance.end_ms),
        int(right_utterance.start_ms) + ROLLING_DIARIZATION_MERGE_BOUNDARY_GAP_MS,
    )
    if interval_end_ms <= interval_start_ms:
        return False

    supporting_overlap_ms = 0
    conflicting_overlap_ms = 0
    for turn_row in turn_rows:
        if turn_row.matched_recording_speaker_id is None:
            continue
        overlap_ms = _range_overlap_ms(
            interval_start_ms,
            interval_end_ms,
            turn_row.start_ms,
            turn_row.end_ms,
        )
        if overlap_ms <= 0:
            continue
        if int(turn_row.matched_recording_speaker_id) == int(speaker_id):
            supporting_overlap_ms += overlap_ms
        else:
            conflicting_overlap_ms += overlap_ms

    required_overlap_ms = interval_end_ms - interval_start_ms
    return supporting_overlap_ms >= required_overlap_ms and conflicting_overlap_ms == 0


def _build_merge_replacement_segments_from_diarization(
    *,
    source_utterances: Sequence[TranscriptUtterance],
    recording_speaker: RecordingSpeaker,
    speaker_confidences: Sequence[float],
    window_result_id: int,
) -> list[dict[str, Any]]:
    if len(source_utterances) < 2:
        return []

    merged_text = " ".join(
        str(utterance.text or "").strip()
        for utterance in source_utterances
        if str(utterance.text or "").strip()
    ).strip()
    if not merged_text:
        return []

    first_utterance = source_utterances[0]
    last_utterance = source_utterances[-1]
    speaker_confidence = None
    if speaker_confidences:
        speaker_confidence = round(
            sum(float(confidence) for confidence in speaker_confidences) / float(len(speaker_confidences)),
            4,
        )

    replacement_payload = _merge_boundary_confidence_payload(
        source_utterances,
        matched_recording_speaker_id=int(recording_speaker.id),
        window_result_id=window_result_id,
    )

    return [
        {
            "id": str(uuid4()),
            "start": int(first_utterance.start_ms) / 1000.0,
            "end": int(last_utterance.end_ms) / 1000.0,
            "text": merged_text,
            "speaker": recording_speaker.diarization_label,
            "recording_speaker_id": int(recording_speaker.id),
            "segment_source": first_utterance.source_kind,
            "provisional": _merged_boundary_state_value(source_utterances) == TranscriptUtteranceState.PROVISIONAL.value,
            "state": _merged_boundary_state_value(source_utterances),
            "speaker_manually_edited": False,
            "text_manually_edited": False,
            "speaker_confidence": speaker_confidence,
            "text_confidence": min(
                (
                    float(utterance.text_confidence)
                    for utterance in source_utterances
                    if _to_optional_float(utterance.text_confidence) is not None
                ),
                default=None,
            ),
            "confidence_payload": replacement_payload,
            "last_diarization_window_result_id": int(window_result_id),
        }
    ]


def _merged_boundary_state_value(source_utterances: Sequence[TranscriptUtterance]) -> str:
    state_values = {
        utterance.state.value if hasattr(utterance.state, "value") else str(utterance.state)
        for utterance in source_utterances
    }
    if state_values == {TranscriptUtteranceState.FINALIZED.value}:
        return TranscriptUtteranceState.FINALIZED.value
    if TranscriptUtteranceState.PROVISIONAL.value in state_values:
        return TranscriptUtteranceState.PROVISIONAL.value
    return TranscriptUtteranceState.STABLE.value


def _merge_boundary_confidence_payload(
    source_utterances: Sequence[TranscriptUtterance],
    *,
    matched_recording_speaker_id: int,
    window_result_id: int,
) -> dict[str, Any]:
    merged_asr_segments: list[dict[str, Any]] = []
    has_word_timestamps = False
    speaker_states = {
        _speaker_state_for_utterance(utterance)
        for utterance in source_utterances
    }

    for utterance in source_utterances:
        payload = dict(utterance.confidence_payload or {})
        if payload.get("asr_word_timestamps_available"):
            has_word_timestamps = True
        for asr_segment in payload.get("asr_segments") or []:
            merged_asr_segments.append(_clone_asr_segment_payload(asr_segment))

    merged_asr_segments.sort(
        key=lambda payload: (
            int(payload.get("start_ms", 0)),
            int(payload.get("end_ms", 0)),
            str(payload.get("text", "")),
        )
    )

    speaker_state = (
        ROLLING_DIARIZATION_SPEAKER_STATE_STABLE
        if speaker_states == {ROLLING_DIARIZATION_SPEAKER_STATE_STABLE}
        else ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL
    )

    payload: dict[str, Any] = {
        "utterance_start_ms": int(source_utterances[0].start_ms),
        "utterance_end_ms": int(source_utterances[-1].end_ms),
        "rolling_diarization": {
            "window_result_id": int(window_result_id),
            "matched_recording_speaker_id": int(matched_recording_speaker_id),
            "merged_from_public_ids": [utterance.public_id for utterance in source_utterances],
            "speaker_state": speaker_state,
        },
    }
    if merged_asr_segments:
        payload["asr_segments"] = merged_asr_segments
    if has_word_timestamps or merged_asr_segments:
        payload["asr_word_timestamps_available"] = bool(has_word_timestamps)
    return payload


def _clone_asr_segment_payload(asr_segment: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "start_ms": int(_to_optional_int(asr_segment.get("start_ms")) or 0),
        "end_ms": int(_to_optional_int(asr_segment.get("end_ms")) or 0),
        "text": str(asr_segment.get("text", "") or ""),
    }
    words = []
    for word_payload in asr_segment.get("words") or []:
        word_text = str(word_payload.get("word") or "").strip()
        if not word_text:
            continue
        words.append(
            {
                "start_ms": int(_to_optional_int(word_payload.get("start_ms")) or 0),
                "end_ms": int(_to_optional_int(word_payload.get("end_ms")) or 0),
                "word": word_text,
            }
        )
    if words:
        payload["words"] = words
    return payload


def _apply_boundary_reconciliation_segments(
    session,
    *,
    recording_id: int,
    source_utterances: Sequence[TranscriptUtterance],
    replacement_segments: Sequence[dict[str, Any]],
    processing_run_id: int | None,
    source: str,
) -> list[TranscriptUtterance]:
    transcript = _load_transcript(session, recording_id)
    recording = session.get(Recording, recording_id)
    if transcript is None or recording is None or not source_utterances or not replacement_segments:
        return []

    active_utterances = list_active_utterances(session, recording_id)
    source_utterance_ids = {int(utterance.id) for utterance in source_utterances if utterance.id is not None}
    if not source_utterance_ids:
        return []

    recording_speakers = ensure_recording_speaker_aliases(
        session,
        recording_id,
        source_run_id=processing_run_id,
    )
    recording_speakers_by_id = {
        int(speaker.id): speaker for speaker in recording_speakers if speaker.id is not None
    }

    source_inserted = False
    resulting_segments: list[dict[str, Any]] = []
    ordered_active_utterances = sorted(
        active_utterances,
        key=lambda utterance: (utterance.sort_key, int(utterance.id or 0)),
    )
    for active_utterance in ordered_active_utterances:
        if int(active_utterance.id or 0) in source_utterance_ids:
            if not source_inserted:
                resulting_segments.extend(dict(segment) for segment in replacement_segments)
                source_inserted = True
            continue
        resulting_segments.append(_segment_payload_from_utterance(active_utterance))

    if not source_inserted:
        return []

    overlap_groups = _build_overlap_groups(resulting_segments)
    replacement_utterances: list[TranscriptUtterance] = []
    projection_segments: list[dict[str, Any]] = []
    remaining_existing_utterances = {
        utterance.public_id: utterance
        for utterance in ordered_active_utterances
        if int(utterance.id or 0) not in source_utterance_ids
    }

    for source_utterance in source_utterances:
        old_state = source_utterance.state.value if hasattr(source_utterance.state, "value") else str(source_utterance.state)
        source_utterance.state = TranscriptUtteranceState.SUPERSEDED
        session.add(source_utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=source_utterance,
            processing_run_id=processing_run_id,
            event_type="supersede",
            source=source,
            old_values={"state": old_state},
            new_values={"state": TranscriptUtteranceState.SUPERSEDED.value},
            resulting_revision=source_utterance.revision,
        )

    for index, segment in enumerate(resulting_segments):
        overlap_group = overlap_groups.get(index, {})
        existing_utterance = remaining_existing_utterances.get(str(segment.get("id") or ""))
        if existing_utterance is not None:
            existing_utterance.sort_key = _sort_key_for_index(index)
            existing_utterance.overlap_group_id = overlap_group.get("group_id")
            existing_utterance.overlap_rank = overlap_group.get("rank", 0)
            session.add(existing_utterance)
            recording_speaker = (
                recording_speakers_by_id.get(int(existing_utterance.recording_speaker_id))
                if existing_utterance.recording_speaker_id is not None
                else None
            )
            projection_segments.append(
                _build_projection_segment(
                    existing_utterance,
                    source_segment=segment,
                    recording_speaker=recording_speaker,
                    overlap_labels=_projection_overlap_labels(
                        index,
                        resulting_segments,
                        overlap_groups,
                        recording_speakers,
                    ),
                )
            )
            continue

        recording_speaker = None
        recording_speaker_id = segment.get("recording_speaker_id")
        if recording_speaker_id is not None:
            recording_speaker = recording_speakers_by_id.get(int(recording_speaker_id))

        utterance = TranscriptUtterance(
            public_id=str(segment.get("id") or uuid4()),
            recording_id=recording_id,
            sort_key=_sort_key_for_index(index),
            start_ms=_segment_to_ms(segment.get("start", 0.0)),
            end_ms=_segment_to_ms(segment.get("end", 0.0)),
            text=str(segment.get("text", "") or ""),
            speaker_label=(
                recording_speaker.diarization_label
                if recording_speaker is not None
                else str(segment.get("speaker") or UNKNOWN_SPEAKER)
            ),
            recording_speaker_id=(recording_speaker.id if recording_speaker is not None else None),
            state=_state_for_segment(recording, segment),
            source_kind=str(segment.get("segment_source") or source),
            processing_run_id=processing_run_id,
            revision=int(segment.get("revision") or 1),
            overlap_group_id=overlap_group.get("group_id"),
            overlap_rank=overlap_group.get("rank", 0),
            manual_text_locked=bool(segment.get("text_manually_edited") is True),
            manual_speaker_locked=bool(segment.get("speaker_manually_edited") is True),
            text_confidence=_to_optional_float(segment.get("text_confidence")),
            speaker_confidence=_to_optional_float(segment.get("speaker_confidence")),
            confidence_payload=(dict(segment.get("confidence_payload")) if isinstance(segment.get("confidence_payload"), dict) else None),
            last_diarization_window_result_id=segment.get("last_diarization_window_result_id"),
        )
        session.add(utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=utterance,
            processing_run_id=processing_run_id,
            event_type=_creation_event_type(source=source, state=utterance.state),
            source=source,
            old_values=None,
            new_values={
                "start_ms": utterance.start_ms,
                "end_ms": utterance.end_ms,
                "text": utterance.text,
                "speaker": utterance.speaker_label,
            },
            resulting_revision=utterance.revision,
        )
        replacement_utterances.append(utterance)
        projection_segments.append(
            _build_projection_segment(
                utterance,
                source_segment=segment,
                recording_speaker=recording_speaker,
                overlap_labels=_projection_overlap_labels(
                    index,
                    resulting_segments,
                    overlap_groups,
                    recording_speakers,
                ),
            )
        )

    transcript.segments = projection_segments
    transcript.text = " ".join(segment.get("text", "") for segment in projection_segments).strip()
    flag_modified(transcript, "segments")
    session.add(transcript)

    _append_boundary_revision_events(
        session,
        previous_utterances=source_utterances,
        new_utterances=replacement_utterances,
        processing_run_id=processing_run_id,
        source=source,
    )

    return replacement_utterances


def _segment_payload_from_utterance(utterance: TranscriptUtterance) -> dict[str, Any]:
    return {
        "id": utterance.public_id,
        "start": utterance.start_ms / 1000.0,
        "end": utterance.end_ms / 1000.0,
        "text": utterance.text,
        "speaker": utterance.speaker_label or UNKNOWN_SPEAKER,
        "segment_source": utterance.source_kind,
        "speaker_manually_edited": bool(utterance.manual_speaker_locked),
        "text_manually_edited": bool(utterance.manual_text_locked),
        "revision": int(utterance.revision),
        "recording_speaker_id": utterance.recording_speaker_id,
        "state": utterance.state.value if hasattr(utterance.state, "value") else str(utterance.state),
        "speaker_confidence": utterance.speaker_confidence,
        "text_confidence": utterance.text_confidence,
        "overlapping_speakers": _rolling_overlap_labels_for_utterance(utterance),
        "confidence_payload": dict(utterance.confidence_payload or {}),
    }


def _rolling_overlap_labels_for_utterance(
    utterance: TranscriptUtterance,
    *,
    projection: dict[str, Any] | None = None,
    recording_speakers_by_id: dict[int, RecordingSpeaker] | None = None,
) -> list[str]:
    projection = projection or {}
    confidence_payload = dict(utterance.confidence_payload or {})
    rolling_payload = dict(confidence_payload.get("rolling_diarization") or {})

    if (
        "overlapping_recording_speaker_ids" in rolling_payload
        or "overlapping_speakers" in rolling_payload
    ):
        labels: list[str] = []
        for speaker_id in rolling_payload.get("overlapping_recording_speaker_ids") or []:
            try:
                speaker_id_value = int(speaker_id)
            except (TypeError, ValueError):
                continue
            speaker = (
                recording_speakers_by_id.get(speaker_id_value)
                if recording_speakers_by_id is not None
                else None
            )
            label = str(speaker.diarization_label or "").strip() if speaker is not None else ""
            if label and label != (utterance.speaker_label or UNKNOWN_SPEAKER) and label not in labels:
                labels.append(label)
        for label in rolling_payload.get("overlapping_speakers") or []:
            label_value = str(label or "").strip()
            if not label_value or label_value == (utterance.speaker_label or UNKNOWN_SPEAKER):
                continue
            if label_value not in labels:
                labels.append(label_value)
        return labels

    return list(projection.get("overlapping_speakers") or [])


def _load_utterance_asr_words(utterance: TranscriptUtterance) -> list[dict[str, Any]]:
    confidence_payload = dict(utterance.confidence_payload or {})
    words: list[dict[str, Any]] = []

    for segment in confidence_payload.get("asr_segments") or []:
        for word_payload in segment.get("words") or []:
            word_text = str(word_payload.get("word") or "").strip()
            if not word_text:
                continue
            start_ms = _to_optional_int(word_payload.get("start_ms"))
            end_ms = _to_optional_int(word_payload.get("end_ms"))
            if start_ms is None or end_ms is None or end_ms <= start_ms:
                continue
            words.append(
                {
                    "start_ms": int(start_ms),
                    "end_ms": int(end_ms),
                    "word": word_text,
                }
            )

    words.sort(key=lambda payload: (int(payload["start_ms"]), int(payload["end_ms"])))
    return words


def _match_word_to_recording_speaker_id(
    word_payload: dict[str, Any],
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
) -> int | None:
    best_speaker_id = None
    best_overlap_ms = 0
    midpoint_ms = (int(word_payload["start_ms"]) + int(word_payload["end_ms"])) // 2

    for turn_row in turn_rows:
        if turn_row.matched_recording_speaker_id is None:
            continue
        overlap_ms = _range_overlap_ms(
            int(word_payload["start_ms"]),
            int(word_payload["end_ms"]),
            turn_row.start_ms,
            turn_row.end_ms,
        )
        if overlap_ms > best_overlap_ms:
            best_overlap_ms = overlap_ms
            best_speaker_id = int(turn_row.matched_recording_speaker_id)
            continue
        if overlap_ms == 0 and best_speaker_id is None and turn_row.start_ms <= midpoint_ms < turn_row.end_ms:
            best_speaker_id = int(turn_row.matched_recording_speaker_id)

    return best_speaker_id


def _join_asr_words(words: Sequence[dict[str, Any]]) -> str:
    return " ".join(
        str(word_payload.get("word") or "").strip()
        for word_payload in words
        if str(word_payload.get("word") or "").strip()
    ).strip()


def _summarize_utterance_turn_support(
    utterance: TranscriptUtterance,
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
) -> dict[int, dict[str, Any]]:
    window_ids_by_speaker_id: dict[int, set[int]] = defaultdict(set)
    overlap_ms_by_speaker_id: dict[int, int] = defaultdict(int)

    for turn_row in turn_rows:
        if turn_row.matched_recording_speaker_id is None:
            continue
        overlap_ms = _range_overlap_ms(
            utterance.start_ms,
            utterance.end_ms,
            turn_row.start_ms,
            turn_row.end_ms,
        )
        if overlap_ms < ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS:
            continue
        speaker_id = int(turn_row.matched_recording_speaker_id)
        window_ids_by_speaker_id[speaker_id].add(int(turn_row.window_result_id))
        overlap_ms_by_speaker_id[speaker_id] += int(overlap_ms)

    return {
        speaker_id: {
            "window_ids": set(window_ids),
            "supporting_window_count": len(window_ids),
            "supporting_overlap_ms": int(overlap_ms_by_speaker_id[speaker_id]),
        }
        for speaker_id, window_ids in window_ids_by_speaker_id.items()
    }


def _build_utterance_speaker_state_payload(
    utterance: TranscriptUtterance,
    *,
    speaker_id: int,
    confidence: float,
    support_summary: dict[int, dict[str, Any]],
    manual_override: bool = False,
) -> dict[str, Any]:
    speaker_support = dict(support_summary.get(int(speaker_id), {}))
    supporting_window_count = int(speaker_support.get("supporting_window_count", 0))
    supporting_overlap_ms = int(speaker_support.get("supporting_overlap_ms", 0))

    conflicting_window_ids: set[int] = set()
    conflicting_overlap_ms = 0
    for candidate_speaker_id, candidate_support in support_summary.items():
        if int(candidate_speaker_id) == int(speaker_id):
            continue
        conflicting_window_ids.update(candidate_support.get("window_ids", set()))
        conflicting_overlap_ms += int(candidate_support.get("supporting_overlap_ms", 0))

    state_value = utterance.state.value if hasattr(utterance.state, "value") else str(utterance.state)
    if manual_override:
        speaker_state = ROLLING_DIARIZATION_SPEAKER_STATE_MANUAL_OVERRIDE
    elif state_value == TranscriptUtteranceState.FINALIZED.value:
        speaker_state = ROLLING_DIARIZATION_SPEAKER_STATE_STABLE
    elif (
        utterance.source_kind not in {"live", "catch_up"}
        and state_value != TranscriptUtteranceState.PROVISIONAL.value
    ):
        speaker_state = ROLLING_DIARIZATION_SPEAKER_STATE_STABLE
    elif (
        supporting_window_count >= ROLLING_DIARIZATION_STABLE_WINDOW_COUNT
        and float(confidence) >= ROLLING_DIARIZATION_CONFIDENCE_FLOOR
    ):
        speaker_state = ROLLING_DIARIZATION_SPEAKER_STATE_STABLE
    else:
        speaker_state = ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL

    return {
        "speaker_state": speaker_state,
        "supporting_window_count": supporting_window_count,
        "conflicting_window_count": len(conflicting_window_ids),
        "supporting_overlap_ms": supporting_overlap_ms,
        "conflicting_overlap_ms": int(conflicting_overlap_ms),
    }


def _speaker_state_for_utterance(
    utterance: TranscriptUtterance,
    *,
    projection: dict[str, Any] | None = None,
) -> str:
    if utterance.manual_speaker_locked:
        return ROLLING_DIARIZATION_SPEAKER_STATE_MANUAL_OVERRIDE

    confidence_payload = dict(utterance.confidence_payload or {})
    rolling_payload = dict(confidence_payload.get("rolling_diarization") or {})
    if rolling_payload.get("speaker_state"):
        return str(rolling_payload["speaker_state"])

    if projection and projection.get("speaker_state"):
        return str(projection["speaker_state"])

    state_value = utterance.state.value if hasattr(utterance.state, "value") else str(utterance.state)
    if state_value == TranscriptUtteranceState.FINALIZED.value:
        return ROLLING_DIARIZATION_SPEAKER_STATE_STABLE
    if utterance.source_kind in {"live", "catch_up"} or state_value == TranscriptUtteranceState.PROVISIONAL.value:
        return ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL
    return ROLLING_DIARIZATION_SPEAKER_STATE_STABLE


def _range_overlap_ms(
    start_a: int,
    end_a: int,
    start_b: int,
    end_b: int,
) -> int:
    return max(0, min(int(end_a), int(end_b)) - max(int(start_a), int(start_b)))


def build_transient_utterance_payloads_from_segments(transcript: Transcript | None) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for index, segment in enumerate((transcript.segments or []) if transcript else []):
        payloads.append(
            {
                "id": segment.get("id") or f"legacy-{index}",
                "start": float(segment.get("start", 0.0)),
                "end": float(segment.get("end", 0.0)),
                "start_ms": _segment_to_ms(segment.get("start", 0.0)),
                "end_ms": _segment_to_ms(segment.get("end", 0.0)),
                "text": str(segment.get("text", "") or ""),
                "speaker": str(segment.get("speaker") or UNKNOWN_SPEAKER),
                "recording_speaker_id": segment.get("recording_speaker_id"),
                "state": str(segment.get("state") or (TranscriptUtteranceState.PROVISIONAL.value if segment.get("provisional") else TranscriptUtteranceState.STABLE.value)),
                "revision": int(segment.get("revision") or 1),
                "segment_source": segment.get("segment_source") or "legacy",
                "provisional": bool(segment.get("provisional") is True),
                "speaker_manually_edited": bool(segment.get("speaker_manually_edited") is True),
                "text_manually_edited": bool(segment.get("text_manually_edited") is True),
                "speaker_state": segment.get("speaker_state"),
                "speaker_confidence": _to_optional_float(segment.get("speaker_confidence")),
                "text_confidence": _to_optional_float(segment.get("text_confidence")),
                "updated_at": segment.get("updated_at"),
                "overlapping_speakers": list(segment.get("overlapping_speakers") or []),
            }
        )
    return payloads


def resolve_assignment_target(
    session,
    *,
    recording_id: int,
    recording: Recording,
    new_speaker_name: str,
    global_speaker_id: int | None,
    diarization_label: str | None,
) -> RecordingSpeaker:
    recording_speakers = ensure_recording_speaker_aliases(session, recording_id)

    if diarization_label:
        recording_speaker = _find_matching_recording_speaker(
            session,
            recording_id=recording_id,
            recording_speakers=recording_speakers,
            value=diarization_label.strip(),
            source_run_id=None,
        )
        if recording_speaker is not None:
            return recording_speaker
        raise LookupError("Speaker not found in recording")

    if global_speaker_id is not None:
        global_speaker = session.execute(
            select(GlobalSpeaker)
            .where(GlobalSpeaker.id == global_speaker_id)
            .where(GlobalSpeaker.user_id == recording.user_id)
        ).scalar_one_or_none()
        if global_speaker is None:
            raise LookupError("Global speaker not found")

        for recording_speaker in recording_speakers:
            if recording_speaker.global_speaker_id == global_speaker.id:
                return recording_speaker

        label = f"MANUAL_{uuid4().hex[:8]}"
        recording_speaker = RecordingSpeaker(
            recording_id=recording_id,
            diarization_label=label,
            global_speaker_id=global_speaker.id,
            name=None,
            speaker_kind="manual",
        )
        session.add(recording_speaker)
        session.flush()
        recording_speaker.global_speaker = global_speaker
        ensure_recording_speaker_aliases_for_speaker(session, recording_speaker)
        return recording_speaker

    recording_speaker = _find_matching_recording_speaker(
        session,
        recording_id=recording_id,
        recording_speakers=recording_speakers,
        value=new_speaker_name.strip(),
        source_run_id=None,
    )
    if recording_speaker is not None:
        return recording_speaker

    label = f"MANUAL_{uuid4().hex[:8]}"
    recording_speaker = RecordingSpeaker(
        recording_id=recording_id,
        diarization_label=label,
        local_name=new_speaker_name,
        name=None,
        speaker_kind="manual",
    )
    session.add(recording_speaker)
    session.flush()
    ensure_recording_speaker_aliases_for_speaker(session, recording_speaker)
    return recording_speaker


def _load_transcript(session, recording_id: int) -> Transcript | None:
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    return session.execute(statement).scalar_one_or_none()


def _load_recording_speakers(session, recording_id: int) -> list[RecordingSpeaker]:
    statement = (
        select(RecordingSpeaker)
        .where(RecordingSpeaker.recording_id == recording_id)
        .options(selectinload(RecordingSpeaker.global_speaker))
    )
    return list(session.execute(statement).scalars().all())


def _resolve_recording_speaker_for_value(
    session,
    *,
    recording_id: int,
    recording: Recording | None,
    speaker_value: str,
    recording_speakers: list[RecordingSpeaker],
    source_run_id: int | None,
    source_segment: dict[str, Any],
) -> RecordingSpeaker | None:
    cleaned_value = speaker_value.strip()
    if not cleaned_value or cleaned_value == UNKNOWN_SPEAKER:
        return None

    existing_speaker = _find_matching_recording_speaker(
        session,
        recording_id=recording_id,
        recording_speakers=recording_speakers,
        value=cleaned_value,
        source_run_id=source_run_id,
        segment_start_ms=_segment_to_ms(source_segment.get("start", 0.0)),
    )
    if existing_speaker is not None:
        return existing_speaker

    label = cleaned_value if LABEL_PATTERN.match(cleaned_value) else f"MANUAL_{uuid4().hex[:8]}"
    recording_speaker = RecordingSpeaker(
        recording_id=recording_id,
        diarization_label=label,
        local_name=None if LABEL_PATTERN.match(cleaned_value) else cleaned_value,
        name=None,
        speaker_kind=_speaker_kind_for_label(label, source_segment),
        speaker_status="active",
        processing_run_id=source_run_id,
    )
    session.add(recording_speaker)
    session.flush()
    recording_speakers.append(recording_speaker)
    ensure_recording_speaker_aliases_for_speaker(
        session,
        recording_speaker,
        source_run_id=source_run_id,
    )
    return recording_speaker


def _touch_recording_speaker_bounds(recording_speaker: RecordingSpeaker, segment: dict[str, Any]) -> None:
    start_ms = _segment_to_ms(segment.get("start", 0.0))
    end_ms = _segment_to_ms(segment.get("end", 0.0))
    if recording_speaker.first_seen_ms is None or start_ms < recording_speaker.first_seen_ms:
        recording_speaker.first_seen_ms = start_ms
    if recording_speaker.last_seen_ms is None or end_ms > recording_speaker.last_seen_ms:
        recording_speaker.last_seen_ms = end_ms


def _state_for_segment(recording: Recording, segment: dict[str, Any]) -> TranscriptUtteranceState:
    if segment.get("provisional") is True:
        return TranscriptUtteranceState.PROVISIONAL
    status_value = recording.status.value if isinstance(recording.status, RecordingStatus) else str(recording.status)
    if status_value == RecordingStatus.PROCESSED.value:
        return TranscriptUtteranceState.FINALIZED
    return TranscriptUtteranceState.STABLE


def _projection_overlap_labels(
    index: int,
    segments: Sequence[dict[str, Any]],
    overlap_groups: dict[int, dict[str, Any]],
    recording_speakers: Iterable[RecordingSpeaker],
) -> list[str]:
    source_segment = segments[index]
    existing = list(source_segment.get("overlapping_speakers") or [])
    if existing:
        return existing

    group = overlap_groups.get(index)
    if not group:
        return []
    labels: list[str] = []
    for other_index in group["members"]:
        if other_index == index:
            continue
        label = str(segments[other_index].get("speaker") or UNKNOWN_SPEAKER)
        if label not in labels and label != source_segment.get("speaker"):
            labels.append(label)
    return labels


def _build_projection_segment(
    utterance: TranscriptUtterance,
    *,
    source_segment: dict[str, Any],
    recording_speaker: RecordingSpeaker | None,
    overlap_labels: list[str],
) -> dict[str, Any]:
    segment = dict(source_segment)
    segment.update(
        {
            "id": utterance.public_id,
            "start": utterance.start_ms / 1000.0,
            "end": utterance.end_ms / 1000.0,
            "text": utterance.text,
            "speaker": recording_speaker.diarization_label if recording_speaker else utterance.speaker_label or UNKNOWN_SPEAKER,
            "overlapping_speakers": overlap_labels,
            "provisional": utterance.state == TranscriptUtteranceState.PROVISIONAL,
            "segment_source": source_segment.get("segment_source") or utterance.source_kind,
            "speaker_manually_edited": utterance.manual_speaker_locked,
            "text_manually_edited": utterance.manual_text_locked,
            "speaker_state": _speaker_state_for_utterance(utterance, projection=source_segment),
            "revision": utterance.revision,
            "recording_speaker_id": utterance.recording_speaker_id,
            "state": utterance.state.value if hasattr(utterance.state, "value") else str(utterance.state),
            "speaker_confidence": utterance.speaker_confidence,
            "text_confidence": utterance.text_confidence,
            "updated_at": utterance.updated_at.isoformat(),
        }
    )
    return segment


def _build_overlap_groups(segments: Sequence[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    adjacency: dict[int, set[int]] = defaultdict(set)
    for index, segment in enumerate(segments):
        for other_index in range(index + 1, len(segments)):
            other_segment = segments[other_index]
            if _segments_overlap(segment, other_segment):
                adjacency[index].add(other_index)
                adjacency[other_index].add(index)

    groups: dict[int, dict[str, Any]] = {}
    visited: set[int] = set()
    for index in range(len(segments)):
        if index in visited or not adjacency.get(index):
            continue
        stack = [index]
        members: list[int] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            members.append(current)
            stack.extend(adjacency.get(current, []))
        members.sort(key=lambda item: (float(segments[item].get("start", 0.0)), item))
        group_id = str(uuid4())
        for rank, member in enumerate(members):
            groups[member] = {"group_id": group_id, "rank": rank, "members": members}
    return groups


def _segments_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_start = float(first.get("start", 0.0))
    first_end = float(first.get("end", 0.0))
    second_start = float(second.get("start", 0.0))
    second_end = float(second.get("end", 0.0))
    return first_start < second_end and second_start < first_end


def _sort_key_for_index(index: int) -> str:
    return f"{index:012d}"


def _segment_to_ms(value: Any) -> int:
    return int(round(float(value or 0.0) * 1000.0))


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _alias_type_for_value(value: str) -> RecordingSpeakerAliasType:
    if value.startswith("LIVE_"):
        return RecordingSpeakerAliasType.LIVE_LABEL
    if value.startswith("MANUAL_"):
        return RecordingSpeakerAliasType.MANUAL_LABEL
    if value.startswith("SPEAKER_"):
        return RecordingSpeakerAliasType.DIARIZATION_LABEL
    return RecordingSpeakerAliasType.IMPORT_LABEL


def _speaker_kind_for_label(label: str, source_segment: dict[str, Any]) -> str:
    if label.startswith("LIVE_"):
        return "live"
    if label.startswith("MANUAL_"):
        return "manual"
    if str(source_segment.get("segment_source") or "") == "import":
        return "imported"
    return "automated"


def _get_utterance(session, recording_id: int, utterance_public_id: str) -> TranscriptUtterance | None:
    statement = (
        select(TranscriptUtterance)
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.public_id == utterance_public_id)
    )
    return session.execute(statement).scalar_one_or_none()


def _update_projection_segment_by_public_id(
    transcript: Transcript,
    utterance_public_id: str,
    updates: dict[str, Any],
) -> None:
    updated_segments = [dict(segment) for segment in (transcript.segments or [])]
    for index, segment in enumerate(updated_segments):
        if str(segment.get("id")) != utterance_public_id:
            continue
        updated_segments[index].update(updates)
        transcript.segments = updated_segments
        flag_modified(transcript, "segments")
        return


def _select_utterances_for_scope(
    session,
    *,
    recording_id: int,
    anchor_utterance: TranscriptUtterance,
    scope: SpeakerCorrectionScope,
    current_key: int | str | None,
) -> list[TranscriptUtterance]:
    active_utterances = list_active_utterances(session, recording_id)
    if scope == SpeakerCorrectionScope.UTTERANCE_ONLY:
        return [anchor_utterance]

    if scope == SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING:
        return [
            utterance
            for utterance in active_utterances
            if (utterance.recording_speaker_id or utterance.speaker_label) == current_key
        ]

    if scope == SpeakerCorrectionScope.FROM_THIS_UTTERANCE_FORWARD:
        return [
            utterance
            for utterance in active_utterances
            if (utterance.recording_speaker_id or utterance.speaker_label) == current_key
            and utterance.start_ms >= anchor_utterance.start_ms
        ]

    if scope == SpeakerCorrectionScope.MERGE_INTO_SPEAKER:
        return [
            utterance
            for utterance in active_utterances
            if (utterance.recording_speaker_id or utterance.speaker_label) == current_key
        ]

    return [anchor_utterance]


def _event_type_for_scope(scope: SpeakerCorrectionScope) -> SpeakerCorrectionEventType:
    if scope == SpeakerCorrectionScope.MERGE_INTO_SPEAKER:
        return SpeakerCorrectionEventType.MERGE_SPEAKERS
    if scope == SpeakerCorrectionScope.FROM_THIS_UTTERANCE_FORWARD:
        return SpeakerCorrectionEventType.ASSIGN_FROM_NOW_ON
    if scope == SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING:
        return SpeakerCorrectionEventType.ASSIGN_RECORDING_SPEAKER
    return SpeakerCorrectionEventType.ASSIGN_UTTERANCE