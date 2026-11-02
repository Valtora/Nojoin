"""Canonical utterance replacement and compatibility segment writes."""

from collections.abc import Collection

from backend.utils.transcript_change import change_sequence_bump

from .assignment import _resolve_recording_speaker_for_value, _state_for_segment
from .constants import *
from .guards import freeze_live_line, protect_text_replacement, settling_required
from .live_compatibility import capture_segment_sort_key
from .projection_segments import _build_projection_segment
from .public_ids import public_ids_for_write
from .queries import (
    _processing_run_idempotency_key,
    ensure_processing_run,
    list_active_utterances,
)
from .scope import _lock_active_lines
from .segmentation import *
from .speaker import *


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
    keep_segment_ids: bool = False,
) -> list[TranscriptUtterance]:
    transcript = _load_transcript(session, recording_id, lock=True)
    recording = session.get(Recording, recording_id)
    if transcript is None or recording is None:
        return []
    locked_utterances = _lock_active_lines(session, recording_id) if force else []
    # Before any write: a client edit keeping held ids is refused here.
    utterance_ids = public_ids_for_write(
        session,
        recording_id=recording_id,
        segments=segments,
        keep_requested=keep_segment_ids,
    )

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
        source_run_id=processing_run.id if processing_run else None,
    )
    speakers_by_id = {speaker.id: speaker for speaker in recording_speakers}

    previous_utterances = sorted(
        locked_utterances, key=lambda utterance: (utterance.sort_key, utterance.id)
    )
    if force:
        for utterance in previous_utterances:
            old_state = (
                utterance.state.value
                if hasattr(utterance.state, "value")
                else str(utterance.state)
            )
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
        utterance_state = state_override or _state_for_segment(recording, segment)
        manual_text_locked = bool(segment.get("text_manually_edited") is True)
        manual_speaker_locked = bool(segment.get("speaker_manually_edited") is True)
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
            public_id=utterance_ids.assign(segment),
            recording_id=recording_id,
            sort_key=capture_segment_sort_key(
                transcript, index, _segment_to_ms(segment.get("start", 0.0))
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
            processing_run_id=processing_run.id if processing_run else None,
            revision=int(segment.get("revision") or 1),
            overlap_group_id=overlap_groups.get(index, {}).get("group_id"),
            overlap_rank=overlap_groups.get(index, {}).get("rank", 0),
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
        )
        confidence_payload = segment.get("confidence_payload")
        utterance.confidence_payload = {
            **(
                dict(confidence_payload) if isinstance(confidence_payload, dict) else {}
            ),
            **dict(utterance_ids.lineage_for(utterance.public_id) or {}),
        } or None
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
                overlap_labels=_projection_overlap_labels(
                    index, segments, overlap_groups, recording_speakers
                ),
            )
        )

    transcript.segments = projection_segments
    transcript.text = " ".join(
        segment.get("text", "") for segment in projection_segments
    ).strip()
    flag_modified(transcript, "segments")
    session.add(transcript)
    session.execute(change_sequence_bump(recording_id))
    refresh_recording_speaker_usage_state(session, recording_id)

    _append_boundary_revision_events(
        session,
        previous_utterances=previous_utterances,
        new_utterances=utterances,
        processing_run_id=processing_run.id if processing_run else None,
        source=source,
    )

    return utterances


def apply_compatibility_segment_replace(
    session,
    *,
    recording_id: int,
    segments: Sequence[dict[str, Any]],
    text_edit_ids: Collection[str] = (),
) -> list[TranscriptUtterance]:
    """Write a whole-transcript segment list onto the canonical lines.

    A line whose text a person locked keeps that text, so a stale snapshot,
    such as the one a speaker change sends back, never undoes an edit.
    ``text_edit_ids`` names the lines whose incoming text is the person's own
    edit, such as a find and replace (issue #842): their text goes through
    the lock, and they stay locked against later passes.
    """
    transcript = _load_transcript(session, recording_id, lock=True)
    if transcript is None:
        return []

    active_utterances = _lock_active_lines(session, recording_id)
    active_by_public_id = {
        utterance.public_id: utterance for utterance in active_utterances
    }
    incoming_ids = [str(segment.get("id")) for segment in segments if segment.get("id")]

    if (
        active_utterances
        and len(incoming_ids) == len(segments)
        and set(incoming_ids) == set(active_by_public_id)
    ):
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
            keeps_locked_text = (
                old_text_locked and utterance.public_id not in text_edit_ids
            )
            effective_segment = dict(segment)
            effective_segment["text"] = (
                utterance.text
                if keeps_locked_text
                else str(segment.get("text", "") or "")
            )
            effective_segment["text_manually_edited"] = old_text_locked or bool(
                segment.get("text_manually_edited") is True
            )
            effective_segment["text_edit_source"] = (
                utterance.text_last_edit_source
                if keeps_locked_text
                else segment.get("text_edit_source")
            )

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
            effective_segment["speaker_manually_edited"] = old_speaker_locked or bool(
                segment.get("speaker_manually_edited") is True
            )
            effective_segment["speaker_edit_source"] = (
                utterance.speaker_last_edit_source
                if old_speaker_locked
                else segment.get("speaker_edit_source")
            )

            # Rolling lines use capture-clock keys so later windows can append
            # between existing lines. An explicit edit must retain that order.
            effective_sort_key = (
                utterance.sort_key
                if (transcript.settling_state or {}).get("eligible")
                else _sort_key_for_index(index)
            )
            effective_start_ms = _segment_to_ms(segment.get("start", 0.0))
            effective_end_ms = _segment_to_ms(segment.get("end", 0.0))
            effective_text = str(effective_segment.get("text", "") or "")
            effective_recording_speaker_id = (
                recording_speaker.id if recording_speaker else None
            )
            effective_speaker_label = (
                recording_speaker.diarization_label
                if recording_speaker is not None
                else str(effective_segment.get("speaker") or UNKNOWN_SPEAKER)
            )
            if settling_required(transcript):
                if effective_text != utterance.text:
                    effective_segment.update(
                        text_manually_edited=True, text_edit_source="api"
                    )
                if effective_speaker_label != utterance.speaker_label:
                    effective_segment.update(
                        speaker_manually_edited=True,
                        speaker_edit_source="api",
                        speaker_assignment_source=SPEAKER_ASSIGNMENT_SOURCE_MANUAL,
                        speaker_assignment_authority=SPEAKER_ASSIGNMENT_AUTHORITY_MANUAL,
                    )
            effective_manual_text_locked = bool(
                effective_segment.get("text_manually_edited") is True
            )
            effective_manual_speaker_locked = bool(
                effective_segment.get("speaker_manually_edited") is True
            )
            effective_text_edit_source = _segment_edit_source(
                effective_segment,
                "text_edit_source",
                locked=effective_manual_text_locked,
            )
            effective_speaker_edit_source = _segment_edit_source(
                effective_segment,
                "speaker_edit_source",
                locked=effective_manual_speaker_locked,
            )
            effective_state = _state_for_segment(recording, effective_segment)
            effective_speaker_assignment_source = (
                _resolve_segment_speaker_assignment_source(
                    effective_segment,
                    source="compatibility_replace",
                    state=effective_state,
                    manual_speaker_locked=effective_manual_speaker_locked,
                )
            )
            effective_speaker_assignment_authority = (
                _resolve_segment_speaker_assignment_authority(
                    effective_segment,
                    state=effective_state,
                    manual_speaker_locked=effective_manual_speaker_locked,
                )
            )

            changed = any(
                (
                    utterance.sort_key != effective_sort_key,
                    utterance.start_ms != effective_start_ms,
                    utterance.end_ms != effective_end_ms,
                    utterance.text != effective_text,
                    utterance.recording_speaker_id != effective_recording_speaker_id,
                    utterance.speaker_label != effective_speaker_label,
                    bool(utterance.manual_text_locked) != effective_manual_text_locked,
                    bool(utterance.manual_speaker_locked)
                    != effective_manual_speaker_locked,
                    utterance.text_last_edit_source != effective_text_edit_source,
                    utterance.speaker_last_edit_source != effective_speaker_edit_source,
                    utterance.state != effective_state,
                    utterance.overlap_group_id is not None,
                    int(utterance.overlap_rank or 0) != 0,
                    _utterance_speaker_assignment_source(utterance)
                    != effective_speaker_assignment_source,
                    _utterance_speaker_assignment_authority(utterance)
                    != effective_speaker_assignment_authority,
                )
            )

            if changed:
                if settling_required(transcript) and utterance.text != effective_text:
                    protect_text_replacement(utterance, effective_text)
                utterance.sort_key = effective_sort_key
                utterance.start_ms = effective_start_ms
                utterance.end_ms = effective_end_ms
                utterance.text = effective_text
                utterance.recording_speaker_id = effective_recording_speaker_id
                utterance.speaker_label = effective_speaker_label
                utterance.manual_text_locked = effective_manual_text_locked
                utterance.manual_speaker_locked = effective_manual_speaker_locked
                freeze_live_line(utterance)
                utterance.text_last_edit_source = effective_text_edit_source
                utterance.speaker_last_edit_source = effective_speaker_edit_source
                utterance.revision += 1
                utterance.overlap_group_id = None
                utterance.overlap_rank = 0
                utterance.state = effective_state
                utterance.speaker_assignment_source = (
                    effective_speaker_assignment_source
                )
                utterance.speaker_assignment_authority = (
                    effective_speaker_assignment_authority
                )
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
                    event_type=_compatibility_replace_event_type(
                        old_values, new_values
                    ),
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
                    overlap_labels=list(
                        effective_segment.get("overlapping_speakers") or []
                    ),
                )
            )

        transcript.segments = projection_segments
        transcript.text = " ".join(
            segment.get("text", "") for segment in projection_segments
        ).strip()
        flag_modified(transcript, "segments")
        session.add(transcript)
        session.execute(change_sequence_bump(recording_id))
        return utterances

    return replace_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=segments,
        run_kind=None,
        source="compatibility_replace",
        force=True,
        keep_segment_ids=True,
    )


__all__ = [
    "replace_utterances_from_segments",
    "apply_compatibility_segment_replace",
]
