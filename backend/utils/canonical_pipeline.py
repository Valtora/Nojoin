from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any, Iterable, Sequence
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from backend.models.pipeline import (
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
        session.add(
            RecordingSpeakerAlias(
                recording_speaker_id=recording_speaker.id,
                alias_type=alias_type,
                alias_value=alias_value,
                source_run_id=source_run_id,
                active=True,
            )
        )


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
        session.add(
            RecordingSpeakerAlias(
                recording_speaker_id=target_speaker.id,
                alias_type=alias_row.alias_type,
                alias_value=alias_row.alias_value,
                source_run_id=alias_row.source_run_id or source_run_id,
                active=bool(alias_row.active),
                valid_from_ms=alias_row.valid_from_ms,
                valid_to_ms=alias_row.valid_to_ms,
                confidence=alias_row.confidence,
            )
        )
        existing_target_keys.add(alias_key)


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
) -> RecordingSpeaker | None:
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

    speaker_ids = [speaker.id for speaker in recording_speakers]
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

    return correction_events


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

    if scope == SpeakerCorrectionScope.MERGE_INTO_SPEAKER and source_recording_speaker_id and source_recording_speaker_id != target_key:
        source_speaker = session.get(RecordingSpeaker, source_recording_speaker_id)
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

    return utterance, target_speaker


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
            recording_speaker = _resolve_recording_speaker_for_value(
                session,
                recording_id=recording_id,
                recording=recording,
                speaker_value=str(segment.get("speaker") or UNKNOWN_SPEAKER),
                recording_speakers=recording_speakers,
                source_run_id=None,
                source_segment=segment,
            )
            old_values = {
                "start_ms": utterance.start_ms,
                "end_ms": utterance.end_ms,
                "text": utterance.text,
                "speaker": utterance.speaker_label,
                "revision": utterance.revision,
            }
            old_text_locked = bool(utterance.manual_text_locked)
            old_speaker_locked = bool(utterance.manual_speaker_locked)
            utterance.sort_key = _sort_key_for_index(index)
            utterance.start_ms = _segment_to_ms(segment.get("start", 0.0))
            utterance.end_ms = _segment_to_ms(segment.get("end", 0.0))
            utterance.text = str(segment.get("text", "") or "")
            utterance.recording_speaker_id = recording_speaker.id if recording_speaker else None
            utterance.speaker_label = recording_speaker.diarization_label if recording_speaker else str(segment.get("speaker") or UNKNOWN_SPEAKER)
            utterance.manual_text_locked = bool(segment.get("text_manually_edited") is True)
            utterance.manual_speaker_locked = bool(segment.get("speaker_manually_edited") is True)
            utterance.revision += 1
            utterance.overlap_group_id = None
            utterance.overlap_rank = 0
            utterance.state = _state_for_segment(recording, segment)
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
            projection_segments.append(
                _build_projection_segment(
                    utterance,
                    source_segment=segment,
                    recording_speaker=recording_speaker,
                    overlap_labels=list(segment.get("overlapping_speakers") or []),
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
                "speaker_confidence": utterance.speaker_confidence,
                "text_confidence": utterance.text_confidence,
                "updated_at": utterance.updated_at.isoformat(),
                "overlapping_speakers": list(projection.get("overlapping_speakers") or []),
            }
        )
    return payloads


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