"""Canonical append, edit, and read operations with pipeline compatibility exports."""

import logging

from backend.models.live_settling_public import public_settling_fields

from .assignment import *
from .constants import *
from .finalization import finalize_utterances_from_segments
from .guards import (
    protect_text_replacement,
    require_action_lines,
    settling_required,
)
from .live_compatibility import capture_segment_sort_key
from .persistence import (
    apply_compatibility_segment_replace,
    replace_utterances_from_segments,
)
from .projection_segments import *
from .public_ids import UtterancePublicIds, public_ids_for_write
from .queries import *
from .scope import _lock_anchor, _select_utterances_for_scope
from .segmentation import *
from .speaker import *
from .speaker_matching import _find_matching_recording_speaker  # noqa: F401
from .startup import *
from .unmerge import *

# Its own logger, not the one the star imports bring in, so a record names
# this module.
logger = logging.getLogger(__name__)


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
    reused_live_asr: bool = False,
) -> list[TranscriptUtterance]:
    transcript = _load_transcript(session, recording_id, lock=True)
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
        span_start_ms=_segment_to_ms(
            min((segment.get("start", 0.0) for segment in segments), default=0.0)
        ),
        span_end_ms=_segment_to_ms(
            max((segment.get("end", 0.0) for segment in segments), default=0.0)
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
        utterance_state = state_override or _state_for_segment(recording, segment)
        manual_text_locked = bool(segment.get("text_manually_edited") is True)
        manual_speaker_locked = bool(segment.get("speaker_manually_edited") is True)
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
            sort_key=capture_segment_sort_key(
                transcript,
                next_sort_index + offset,
                _segment_to_ms(segment.get("start", 0.0)),
            ),
            start_ms=_segment_to_ms(segment.get("start", 0.0)),
            end_ms=_segment_to_ms(segment.get("end", 0.0)),
            text=str(segment.get("text", "") or ""),
            speaker_label=(
                recording_speaker.diarization_label
                if recording_speaker
                else str(segment.get("speaker") or UNKNOWN_SPEAKER)
            ),
            recording_speaker_id=recording_speaker.id if recording_speaker else None,
            diarization_label=_segment_diarization_label(segment),
            state=utterance_state,
            source_kind=str(segment.get("segment_source") or source),
            processing_run_id=processing_run.id,
            revision=int(segment.get("revision") or 1),
            overlap_group_id=overlap_groups.get(offset, {}).get("group_id"),
            overlap_rank=overlap_groups.get(offset, {}).get("rank", 0),
            manual_text_locked=manual_text_locked,
            manual_speaker_locked=manual_speaker_locked,
            text_last_edit_source=_segment_edit_source(
                segment, "text_edit_source", locked=manual_text_locked
            ),
            speaker_last_edit_source=_segment_edit_source(
                segment, "speaker_edit_source", locked=manual_speaker_locked
            ),
            speaker_assignment_source=_resolve_segment_speaker_assignment_source(
                segment,
                source=source,
                state=utterance_state,
                manual_speaker_locked=manual_speaker_locked,
            ),
            speaker_assignment_authority=_resolve_segment_speaker_assignment_authority(
                segment,
                state=utterance_state,
                manual_speaker_locked=manual_speaker_locked,
            ),
            text_confidence=_to_optional_float(segment.get("text_confidence")),
            speaker_confidence=_to_optional_float(segment.get("speaker_confidence")),
            confidence_payload=(
                dict(segment.get("confidence_payload"))
                if isinstance(segment.get("confidence_payload"), dict)
                else None
            ),
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
    transcript = _load_transcript(session, recording_id, lock=True)
    utterance = _get_utterance(session, recording_id, utterance_public_id, lock=True)
    if utterance is None:
        raise LookupError("Utterance not found")
    if expected_revision is not None and utterance.revision != expected_revision:
        raise RuntimeError("Utterance revision conflict")
    require_action_lines(transcript, [utterance])

    old_values = {"text": utterance.text, "revision": utterance.revision}
    old_text_locked = bool(utterance.manual_text_locked)
    if settling_required(transcript):
        protect_text_replacement(utterance, text)
    utterance.text = text
    utterance.manual_text_locked = True
    utterance.text_last_edit_source = source
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

    if transcript is not None:
        _update_projection_segment_by_public_id(
            transcript,
            utterance.public_id,
            {
                "text": utterance.text,
                "text_manually_edited": True,
                "text_edit_source": source,
                "revision": utterance.revision,
                "state": utterance.state.value,
                "updated_at": utterance.updated_at.isoformat(),
                **(
                    {"confidence_payload": utterance.confidence_payload}
                    if settling_required(transcript)
                    else {}
                ),
            },
        )
        transcript.text = " ".join(
            str(segment.get("text", "") or "")
            for segment in (transcript.segments or [])
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
    utterance = _lock_anchor(session, recording_id, utterance_public_id, scope)
    if utterance is None:
        raise LookupError("Utterance not found")
    if expected_revision is not None and utterance.revision != expected_revision:
        raise RuntimeError("Utterance revision conflict")

    transcript = _load_transcript(session, recording_id)
    recording = session.get(Recording, recording_id)
    if transcript is None or recording is None:
        raise LookupError("Transcript not found")

    source_recording_speaker_id = utterance.recording_speaker_id
    source_speaker = (
        session.get(RecordingSpeaker, source_recording_speaker_id)
        if source_recording_speaker_id
        else None
    )

    current_key = utterance.recording_speaker_id or utterance.speaker_label
    target_utterances = _select_utterances_for_scope(
        session,
        recording_id=recording_id,
        anchor_utterance=utterance,
        scope=scope,
        current_key=current_key,
    )
    require_action_lines(transcript, [utterance, *target_utterances])

    target_speaker = resolve_assignment_target(
        session,
        recording_id=recording_id,
        recording=recording,
        new_speaker_name=new_speaker_name,
        global_speaker_id=global_speaker_id,
        diarization_label=diarization_label,
        source_speaker=source_speaker,
        scope=scope,
    )

    target_key = target_speaker.id

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
        target_utterance.speaker_last_edit_source = source
        target_utterance.speaker_assignment_source = SPEAKER_ASSIGNMENT_SOURCE_MANUAL
        target_utterance.speaker_assignment_authority = (
            SPEAKER_ASSIGNMENT_AUTHORITY_MANUAL
        )
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
            updated_segments[projection_index]["speaker_edit_source"] = source
            updated_segments[projection_index]["recording_speaker_id"] = target_key
            updated_segments[projection_index]["revision"] = target_utterance.revision
            updated_segments[projection_index]["state"] = target_utterance.state.value
            updated_segments[projection_index]["updated_at"] = (
                target_utterance.updated_at.isoformat()
            )
            updated_segments[projection_index]["speaker_assignment_source"] = (
                target_utterance.speaker_assignment_source
            )
            updated_segments[projection_index]["speaker_assignment_authority"] = (
                target_utterance.speaker_assignment_authority
            )

    _preserve_speaker_label_continuity(
        session,
        source_speaker=source_speaker,
        target_speaker=target_speaker,
        scope=scope,
        anchor_start_ms=utterance.start_ms,
    )

    if (
        scope == SpeakerCorrectionScope.MERGE_INTO_SPEAKER
        and source_recording_speaker_id
        and source_recording_speaker_id != target_key
    ):
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
    refresh_recording_speaker_usage_state(session, recording_id)

    return utterance, target_speaker


def serialize_canonical_utterances(
    session,
    recording_id: int,
    only_public_ids: set[str] | None = None,
    include_confidence_payload: bool = False,
    resolve_merged_overlaps: bool = True,
) -> list[dict[str, Any]]:
    transcript = _load_transcript(session, recording_id)
    recording_speakers = _load_recording_speakers(session, recording_id)
    recording_speakers_by_id = {
        int(speaker.id): speaker
        for speaker in recording_speakers
        if speaker.id is not None
    }
    active_labels = (
        _active_speaker_labels(recording_speakers_by_id)
        if resolve_merged_overlaps
        else None
    )
    projection_by_id = (
        {
            str(segment.get("id")): segment
            for segment in (transcript.segments or [])
            if isinstance(segment, dict) and segment.get("id")
        }
        if transcript
        else {}
    )
    payloads: list[dict[str, Any]] = []
    for utterance in list_active_utterances(session, recording_id):
        if only_public_ids is not None and utterance.public_id not in only_public_ids:
            continue
        projection = projection_by_id.get(utterance.public_id, {})
        state_value = str(getattr(utterance.state, "value", utterance.state))
        payload = {
            "id": utterance.public_id,
            "start": utterance.start_ms / 1000.0,
            "end": utterance.end_ms / 1000.0,
            "start_ms": utterance.start_ms,
            "end_ms": utterance.end_ms,
            "text": utterance.text,
            "speaker": utterance.speaker_label
            or projection.get("speaker")
            or UNKNOWN_SPEAKER,
            "recording_speaker_id": utterance.recording_speaker_id,
            "diarization_label": utterance.diarization_label,
            "state": state_value,
            "revision": utterance.revision,
            "segment_source": utterance.source_kind,
            "provisional": state_value == TranscriptUtteranceState.PROVISIONAL.value,
            "speaker_manually_edited": utterance.manual_speaker_locked,
            "text_manually_edited": utterance.manual_text_locked,
            "text_edit_source": utterance.text_last_edit_source,
            "speaker_edit_source": utterance.speaker_last_edit_source,
            "speaker_state": _speaker_state_for_utterance(
                utterance, projection=projection
            ),
            "speaker_confidence": utterance.speaker_confidence,
            # Audio provenance for this utterance, never speaker identity.
            "source_channel": source_channel_from_confidence_payload(
                utterance.confidence_payload
            ),
            "text_confidence": utterance.text_confidence,
            "speaker_assignment_source": _utterance_speaker_assignment_source(
                utterance,
                projection=projection,
            ),
            "speaker_assignment_authority": _utterance_speaker_assignment_authority(
                utterance,
                projection=projection,
            ),
            "updated_at": utterance.updated_at.isoformat(),
            "overlapping_speakers": _rolling_overlap_labels_for_utterance(
                utterance,
                projection=projection,
                recording_speakers_by_id=recording_speakers_by_id,
                active_labels=active_labels,
            ),
        }
        alignment_error = (utterance.confidence_payload or {}).get("alignment_error")
        payload.update(
            public_settling_fields(
                utterance.confidence_payload,
                final_utterance_id=utterance.public_id
                if settling_required(transcript)
                and utterance.state == TranscriptUtteranceState.FINALIZED
                else None,
                final_text=utterance.text,
            )
        )
        if alignment_error:
            payload["alignment_error"] = alignment_error
        if include_confidence_payload:
            payload["confidence_payload"] = dict(utterance.confidence_payload or {})
        payloads.append(payload)
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
    transcript = (
        transcript
        if transcript is not None
        else _load_transcript(session, recording_id)
    )
    fallback_segments = _normalize_transcript_segments(
        getattr(transcript, "segments", None) if transcript is not None else None
    )

    if transcript is None or not hasattr(session, "execute"):
        return fallback_segments

    # The savepoint keeps a database error inside the read: on Postgres a failed
    # statement aborts the whole transaction, so without it the caller's next
    # query would raise InFailedSqlTransaction instead of using the fallback.
    try:
        with session.begin_nested():
            canonical_segments = serialize_canonical_utterances(session, recording_id)
    except Exception:
        logger.exception(
            "Canonical transcript read failed for recording %s; using the projection",
            recording_id,
        )
        return fallback_segments

    if canonical_segments or _settling_read_is_authoritative(transcript):
        return [dict(segment) for segment in canonical_segments]
    return fallback_segments


def _settling_read_is_authoritative(transcript: Transcript | None) -> bool:
    """An empty marked live projection means no current words, not old JSON."""
    state = getattr(transcript, "settling_state", None) or {}
    return bool(
        state.get("eligible") and state.get("policy", {}).get("enabled") is True
    )


def build_transcript_text_for_read(
    session,
    recording_id: int,
    *,
    transcript: Transcript | None = None,
    segments: Sequence[dict[str, Any]] | None = None,
) -> str:
    effective_segments = (
        [dict(segment) for segment in segments]
        if segments is not None
        else build_transcript_segments_for_read(
            session,
            recording_id,
            transcript=transcript,
        )
    )
    if effective_segments:
        return " ".join(
            str(segment.get("text", "") or "") for segment in effective_segments
        ).strip()

    transcript = (
        transcript
        if transcript is not None
        else _load_transcript(session, recording_id)
    )
    if _settling_read_is_authoritative(transcript):
        return ""
    return str(getattr(transcript, "text", "") or "") if transcript is not None else ""


def refresh_transcript_projection_from_canonical(
    session,
    recording_id: int,
) -> list[dict[str, Any]]:
    transcript = _load_transcript(session, recording_id, lock=True)
    if transcript is None:
        return []

    # Unresolved, so an unmerge finds the overlap labels as diarised.
    projection_segments = serialize_canonical_utterances(
        session,
        recording_id,
        include_confidence_payload=True,
        resolve_merged_overlaps=False,
    )
    transcript.segments = projection_segments
    transcript.text = " ".join(
        str(segment.get("text", "") or "") for segment in projection_segments
    ).strip()
    flag_modified(transcript, "segments")
    session.add(transcript)
    refresh_recording_speaker_usage_state(session, recording_id)
    return projection_segments


__all__ = [name for name in globals() if not name.startswith("__")]
