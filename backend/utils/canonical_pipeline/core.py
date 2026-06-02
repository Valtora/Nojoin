from .constants import *
from .speaker import *
from .diarization import *
from .segmentation import *
from .startup import *

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



def _reconcile_completed_windows_from_effective_point(
    session,
    *,
    recording_id: int,
    effective_from_ms: int | None,
    source: str,
    processing_run_id: int | None = None,
    replay_policy: SpeakerReplayPolicy | None = None,
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
            effective_from_ms=effective_from_ms,
            replay_policy=replay_policy,
        )
        summary["window_count"] += 1
        summary["matched_turn_count"] += int(replay_summary.get("matched_turn_count", 0))
        summary["updated_utterance_count"] += int(replay_summary.get("updated_utterance_count", 0))
        summary["preserved_manual_lock_count"] += int(
            replay_summary.get("preserved_manual_lock_count", 0)
        )
    return summary



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
            public_id=str(segment.get("id") or uuid4()),
            recording_id=recording_id,
            sort_key=_sort_key_for_index(index),
            start_ms=_segment_to_ms(segment.get("start", 0.0)),
            end_ms=_segment_to_ms(segment.get("end", 0.0)),
            text=str(segment.get("text", "") or ""),
            speaker_label=(recording_speaker.diarization_label if recording_speaker else str(segment.get("speaker") or UNKNOWN_SPEAKER)),
            recording_speaker_id=recording_speaker.id if recording_speaker else None,
            state=utterance_state,
            source_kind=str(segment.get("segment_source") or source),
            processing_run_id=processing_run.id if processing_run else None,
            revision=int(segment.get("revision") or 1),
            overlap_group_id=overlap_groups.get(index, {}).get("group_id"),
            overlap_rank=overlap_groups.get(index, {}).get("rank", 0),
            manual_text_locked=manual_text_locked,
            manual_speaker_locked=manual_speaker_locked,
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
    refresh_recording_speaker_usage_state(session, recording_id)

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
    reserved_public_ids: set[str] = {
        str(public_id)
        for public_id in session.execute(
            select(TranscriptUtterance.public_id).where(
                TranscriptUtterance.recording_id == recording_id
            )
        ).scalars().all()
        if str(public_id or "").strip()
    }

    def reserve_finalize_public_id(segment_payload: dict[str, Any]) -> str:
        requested_public_id = str(segment_payload.get("id") or "").strip()
        if requested_public_id and requested_public_id not in reserved_public_ids:
            reserved_public_ids.add(requested_public_id)
            return requested_public_id

        if requested_public_id:
            confidence_payload = dict(segment_payload.get("confidence_payload") or {})
            source_public_ids = [
                str(public_id or "").strip()
                for public_id in confidence_payload.get("source_public_ids")
                or segment_payload.get("source_public_ids")
                or []
                if str(public_id or "").strip()
            ]
            if requested_public_id not in source_public_ids:
                source_public_ids.append(requested_public_id)
            if source_public_ids:
                confidence_payload["source_public_ids"] = source_public_ids
                segment_payload["confidence_payload"] = confidence_payload

        public_id = str(uuid4())
        while public_id in reserved_public_ids:
            public_id = str(uuid4())
        reserved_public_ids.add(public_id)
        return public_id

    def inherit_manual_speaker_for_range(
        *,
        start_ms: int,
        end_ms: int,
    ) -> dict[str, Any] | None:
        overlap_by_speaker_id: dict[int, int] = defaultdict(int)
        source_public_ids_by_speaker_id: dict[int, list[str]] = defaultdict(list)
        confidence_by_speaker_id: dict[int, float] = defaultdict(float)

        for source_utterance in active_utterances:
            if source_utterance.id in matched_utterance_ids:
                continue
            if not source_utterance.manual_speaker_locked or source_utterance.recording_speaker_id is None:
                continue
            overlap_ms = _range_overlap_ms(
                start_ms,
                end_ms,
                source_utterance.start_ms,
                source_utterance.end_ms,
            )
            if overlap_ms <= 0:
                continue
            speaker_id = int(source_utterance.recording_speaker_id)
            overlap_by_speaker_id[speaker_id] += int(overlap_ms)
            source_public_ids_by_speaker_id[speaker_id].append(source_utterance.public_id)
            if source_utterance.speaker_confidence is not None:
                confidence_by_speaker_id[speaker_id] = max(
                    confidence_by_speaker_id[speaker_id],
                    float(source_utterance.speaker_confidence),
                )

        if not overlap_by_speaker_id:
            return None

        ranked = sorted(
            overlap_by_speaker_id.items(),
            key=lambda item: (-item[1], item[0]),
        )
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return None

        speaker_id, overlap_ms = ranked[0]
        speaker = session.get(RecordingSpeaker, speaker_id)
        if speaker is None:
            return None

        return {
            "speaker": speaker,
            "overlap_ms": int(overlap_ms),
            "source_public_ids": source_public_ids_by_speaker_id[speaker_id],
            "speaker_confidence": confidence_by_speaker_id.get(speaker_id) or None,
        }

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

        manual_speaker_inheritance = None
        if matched_utterance is None:
            manual_speaker_inheritance = inherit_manual_speaker_for_range(
                start_ms=start_ms,
                end_ms=end_ms,
            )

        resolved_speaker = None
        if manual_speaker_inheritance is None and (matched_utterance is None or not matched_utterance.manual_speaker_locked):
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
            effective_speaker_assignment_source = _resolve_segment_speaker_assignment_source(
                effective_segment,
                source="finalize",
                state=TranscriptUtteranceState.FINALIZED,
                manual_speaker_locked=effective_manual_speaker_locked,
            )
            effective_speaker_assignment_authority = _resolve_segment_speaker_assignment_authority(
                effective_segment,
                state=TranscriptUtteranceState.FINALIZED,
                manual_speaker_locked=effective_manual_speaker_locked,
            )

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
                    _utterance_speaker_assignment_source(matched_utterance) != effective_speaker_assignment_source,
                    _utterance_speaker_assignment_authority(matched_utterance) != effective_speaker_assignment_authority,
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
            matched_utterance.speaker_assignment_source = effective_speaker_assignment_source
            matched_utterance.speaker_assignment_authority = effective_speaker_assignment_authority
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

        effective_segment = dict(segment)
        if manual_speaker_inheritance is not None:
            recording_speaker = manual_speaker_inheritance["speaker"]
            effective_segment["speaker"] = recording_speaker.diarization_label
            effective_segment["recording_speaker_id"] = int(recording_speaker.id)
            effective_segment["speaker_manually_edited"] = True
            if effective_segment.get("speaker_confidence") is None:
                effective_segment["speaker_confidence"] = manual_speaker_inheritance.get("speaker_confidence")
            confidence_payload = dict(effective_segment.get("confidence_payload") or {})
            confidence_payload["inherited_manual_speaker"] = {
                "recording_speaker_id": int(recording_speaker.id),
                "source_public_ids": list(manual_speaker_inheritance["source_public_ids"]),
                "overlap_ms": int(manual_speaker_inheritance["overlap_ms"]),
            }
            effective_segment["confidence_payload"] = confidence_payload
            _touch_recording_speaker_bounds(recording_speaker, effective_segment)
            session.add(recording_speaker)
        else:
            recording_speaker = resolved_speaker
        utterance = TranscriptUtterance(
            public_id=reserve_finalize_public_id(effective_segment),
            recording_id=recording_id,
            sort_key=_sort_key_for_index(index),
            start_ms=start_ms,
            end_ms=end_ms,
            text=str(effective_segment.get("text", "") or ""),
            speaker_label=(recording_speaker.diarization_label if recording_speaker else str(effective_segment.get("speaker") or UNKNOWN_SPEAKER)),
            recording_speaker_id=recording_speaker.id if recording_speaker else None,
            state=TranscriptUtteranceState.FINALIZED,
            source_kind=str(effective_segment.get("segment_source") or "finalize"),
            processing_run_id=processing_run.id,
            revision=int(effective_segment.get("revision") or 1),
            overlap_group_id=overlap_groups.get(index, {}).get("group_id"),
            overlap_rank=overlap_groups.get(index, {}).get("rank", 0),
            manual_text_locked=bool(effective_segment.get("text_manually_edited") is True),
            manual_speaker_locked=bool(effective_segment.get("speaker_manually_edited") is True),
            speaker_assignment_source=_resolve_segment_speaker_assignment_source(
                effective_segment,
                source="finalize",
                state=TranscriptUtteranceState.FINALIZED,
                manual_speaker_locked=bool(effective_segment.get("speaker_manually_edited") is True),
            ),
            speaker_assignment_authority=_resolve_segment_speaker_assignment_authority(
                effective_segment,
                state=TranscriptUtteranceState.FINALIZED,
                manual_speaker_locked=bool(effective_segment.get("speaker_manually_edited") is True),
            ),
            text_confidence=_to_optional_float(effective_segment.get("text_confidence")),
            speaker_confidence=_to_optional_float(effective_segment.get("speaker_confidence")),
            confidence_payload=(dict(effective_segment.get("confidence_payload")) if isinstance(effective_segment.get("confidence_payload"), dict) else None),
        )
        session.add(utterance)
        session.flush()
        new_values = {
            "start_ms": utterance.start_ms,
            "end_ms": utterance.end_ms,
            "text": utterance.text,
            "speaker": utterance.speaker_label,
            "state": TranscriptUtteranceState.FINALIZED.value,
        }
        if manual_speaker_inheritance is not None:
            new_values["inherited_manual_speaker_from_public_ids"] = list(
                manual_speaker_inheritance["source_public_ids"]
            )
        _append_utterance_event(
            session,
            utterance=utterance,
            processing_run_id=processing_run.id,
            event_type="finalize",
            source="finalize",
            old_values=None,
            new_values=new_values,
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
                source_segment=effective_segment,
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

    refresh_recording_speaker_usage_state(session, recording_id)

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
            sort_key=_sort_key_for_index(next_sort_index + offset),
            start_ms=_segment_to_ms(segment.get("start", 0.0)),
            end_ms=_segment_to_ms(segment.get("end", 0.0)),
            text=str(segment.get("text", "") or ""),
            speaker_label=(recording_speaker.diarization_label if recording_speaker else str(segment.get("speaker") or UNKNOWN_SPEAKER)),
            recording_speaker_id=recording_speaker.id if recording_speaker else None,
            state=utterance_state,
            source_kind=str(segment.get("segment_source") or source),
            processing_run_id=processing_run.id,
            revision=int(segment.get("revision") or 1),
            overlap_group_id=overlap_groups.get(offset, {}).get("group_id"),
            overlap_rank=overlap_groups.get(offset, {}).get("rank", 0),
            manual_text_locked=manual_text_locked,
            manual_speaker_locked=manual_speaker_locked,
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

    source_recording_speaker_id = utterance.recording_speaker_id
    source_speaker = session.get(RecordingSpeaker, source_recording_speaker_id) if source_recording_speaker_id else None

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

    current_key = utterance.recording_speaker_id or utterance.speaker_label
    target_key = target_speaker.id
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
        target_utterance.speaker_assignment_source = SPEAKER_ASSIGNMENT_SOURCE_MANUAL
        target_utterance.speaker_assignment_authority = SPEAKER_ASSIGNMENT_AUTHORITY_MANUAL
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
    refresh_recording_speaker_usage_state(session, recording_id)

    if scope != SpeakerCorrectionScope.UTTERANCE_ONLY:
        replay_effective_from_ms = min(
            int(target_utterance.start_ms)
            for target_utterance in target_utterances
        ) if target_utterances else int(utterance.start_ms)
        replay_event_type = _event_type_for_scope(scope)
        replay_policy = _speaker_replay_policy_for_correction_event(
            session,
            recording_id=recording_id,
            event_type=replay_event_type,
            target_recording_speaker_id=target_key,
        )
        _reconcile_completed_windows_from_effective_point(
            session,
            recording_id=recording_id,
            effective_from_ms=replay_effective_from_ms,
            source=_replay_source_for_policy(replay_policy),
            replay_policy=replay_policy,
        )

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
            effective_speaker_assignment_source = _resolve_segment_speaker_assignment_source(
                effective_segment,
                source="compatibility_replace",
                state=effective_state,
                manual_speaker_locked=effective_manual_speaker_locked,
            )
            effective_speaker_assignment_authority = _resolve_segment_speaker_assignment_authority(
                effective_segment,
                state=effective_state,
                manual_speaker_locked=effective_manual_speaker_locked,
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
                    bool(utterance.manual_speaker_locked) != effective_manual_speaker_locked,
                    utterance.state != effective_state,
                    utterance.overlap_group_id is not None,
                    int(utterance.overlap_rank or 0) != 0,
                    _utterance_speaker_assignment_source(utterance) != effective_speaker_assignment_source,
                    _utterance_speaker_assignment_authority(utterance) != effective_speaker_assignment_authority,
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
                utterance.speaker_assignment_source = effective_speaker_assignment_source
                utterance.speaker_assignment_authority = effective_speaker_assignment_authority
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
    include_confidence_payload: bool = False,
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
        payload = {
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
            ),
        }
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
    transcript = transcript if transcript is not None else _load_transcript(session, recording_id)
    fallback_segments = _normalize_transcript_segments(
        getattr(transcript, "segments", None) if transcript is not None else None
    )

    if transcript is None or not hasattr(session, "execute"):
        return fallback_segments

    try:
        canonical_segments = serialize_canonical_utterances(session, recording_id)
    except Exception:  # noqa: BLE001
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
    for payload in serialize_canonical_utterances(
        session,
        recording_id,
        include_confidence_payload=True,
    ):
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
    refresh_recording_speaker_usage_state(session, recording_id)
    return projection_segments



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
                "speaker_assignment_source": _normalize_speaker_assignment_source(
                    segment.get("speaker_assignment_source")
                    or _derive_default_speaker_assignment_source(
                        source=str(segment.get("segment_source") or "legacy"),
                        source_kind=str(segment.get("segment_source") or "legacy"),
                        state=str(
                            segment.get("state")
                            or (
                                TranscriptUtteranceState.PROVISIONAL.value
                                if segment.get("provisional")
                                else TranscriptUtteranceState.STABLE.value
                            )
                        ),
                        manual_speaker_locked=bool(segment.get("speaker_manually_edited") is True),
                    )
                ),
                "speaker_assignment_authority": _normalize_speaker_assignment_authority(
                    segment.get("speaker_assignment_authority")
                    or _derive_default_speaker_assignment_authority(
                        state=str(
                            segment.get("state")
                            or (
                                TranscriptUtteranceState.PROVISIONAL.value
                                if segment.get("provisional")
                                else TranscriptUtteranceState.STABLE.value
                            )
                        ),
                        manual_speaker_locked=bool(segment.get("speaker_manually_edited") is True),
                    )
                ),
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
    source_speaker: RecordingSpeaker | None = None,
    scope: SpeakerCorrectionScope = SpeakerCorrectionScope.UTTERANCE_ONLY,
) -> RecordingSpeaker:
    cleaned_name = new_speaker_name.strip()
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
        value=cleaned_name,
        source_run_id=None,
    )
    if recording_speaker is not None:
        return recording_speaker

    if (
        source_speaker is not None
        and source_speaker.merged_into_id is None
        and scope in {
            SpeakerCorrectionScope.FROM_THIS_UTTERANCE_FORWARD,
            SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
        }
        and str(source_speaker.diarization_label or "").startswith("LIVE_")
    ):
        source_speaker.global_speaker_id = None
        source_speaker.global_speaker = None
        source_speaker.local_name = cleaned_name
        source_speaker.name = None
        source_speaker.identity_confidence = 1.0
        source_speaker.identity_locked = True
        ensure_recording_speaker_aliases_for_speaker(session, source_speaker)
        session.add(source_speaker)
        return source_speaker

    label = f"MANUAL_{uuid4().hex[:8]}"
    recording_speaker = RecordingSpeaker(
        recording_id=recording_id,
        diarization_label=label,
        local_name=cleaned_name,
        name=None,
        speaker_kind="manual",
    )
    session.add(recording_speaker)
    session.flush()
    ensure_recording_speaker_aliases_for_speaker(session, recording_speaker)
    return recording_speaker



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
            "speaker_assignment_source": _resolve_segment_speaker_assignment_source(
                segment,
                source=str(source_segment.get("segment_source") or utterance.source_kind),
                state=utterance.state,
                manual_speaker_locked=bool(utterance.manual_speaker_locked),
            ),
            "speaker_assignment_authority": _resolve_segment_speaker_assignment_authority(
                segment,
                state=utterance.state,
                manual_speaker_locked=bool(utterance.manual_speaker_locked),
            ),
            "updated_at": utterance.updated_at.isoformat(),
        }
    )
    return segment



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




__all__ = [name for name in globals() if not name.startswith('__')]
