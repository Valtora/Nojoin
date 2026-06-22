from .constants import *
from .segmentation import *
from .speaker import *


def reconcile_completed_diarization_windows(
    session,
    *,
    recording_id: int,
    effective_from_ms: int | None = 0,
    source: str = "finalize_window_replay",
    processing_run_id: int | None = None,
) -> dict[str, int]:
    from .core import _reconcile_completed_windows_from_effective_point

    return _reconcile_completed_windows_from_effective_point(
        session,
        recording_id=recording_id,
        effective_from_ms=effective_from_ms,
        source=source,
        processing_run_id=processing_run_id,
    )


def reconcile_diarization_window_result(
    session,
    *,
    recording_id: int,
    window_result_id: int,
    processing_run_id: int | None = None,
    source: str = "rolling_diarization",
    effective_from_ms: int | None = None,
    allow_speaker_reassignment: bool = True,
    replay_policy: SpeakerReplayPolicy | None = None,
) -> dict[str, int]:
    from .core import (
        list_active_utterances,
        refresh_transcript_projection_from_canonical,
    )

    window_result = session.get(DiarizationWindowResult, window_result_id)
    if window_result is None or window_result.recording_id != recording_id:
        return {
            "matched_turn_count": 0,
            "updated_utterance_count": 0,
            "preserved_manual_lock_count": 0,
        }

    turn_rows = list(
        session.execute(
            select(DiarizationWindowTurn)
            .where(DiarizationWindowTurn.window_result_id == window_result.id)
            .order_by(
                DiarizationWindowTurn.start_ms,
                DiarizationWindowTurn.end_ms,
                DiarizationWindowTurn.id,
            )
        )
        .scalars()
        .all()
    )
    if not turn_rows:
        return {
            "matched_turn_count": 0,
            "updated_utterance_count": 0,
            "preserved_manual_lock_count": 0,
        }

    effective_policy = _effective_speaker_replay_policy(
        replay_policy,
        allow_speaker_reassignment=allow_speaker_reassignment,
    )
    transcript = _load_transcript(session, recording_id)
    projection_by_public_id = (
        {
            str(segment.get("id")): segment
            for segment in (transcript.segments or [])
            if isinstance(segment, dict) and segment.get("id")
        }
        if transcript is not None
        else {}
    )
    recording_speakers = _load_recording_speakers(session, recording_id)
    recording_speakers_by_id = {
        speaker.id: speaker for speaker in recording_speakers if speaker.id is not None
    }
    overlap_start_ms = int(window_result.window_start_ms)
    if effective_from_ms is not None:
        overlap_start_ms = max(overlap_start_ms, int(effective_from_ms))
    overlapping_utterances = list(
        session.execute(
            select(TranscriptUtterance)
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(TranscriptUtterance.state.in_(ACTIVE_UTTERANCE_STATES))
            .where(TranscriptUtterance.start_ms < int(window_result.window_end_ms))
            .where(TranscriptUtterance.end_ms > overlap_start_ms)
            .order_by(TranscriptUtterance.sort_key, TranscriptUtterance.id)
        )
        .scalars()
        .all()
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
        )
        .scalars()
        .all()
    )

    raw_payload = dict(window_result.raw_payload or {})
    speaker_metadata_by_key = {
        str(local_speaker_key): dict(metadata or {})
        for local_speaker_key, metadata in (
            raw_payload.get("speaker_metadata") or {}
        ).items()
    }

    turns_by_local_speaker: dict[str, list[DiarizationWindowTurn]] = defaultdict(list)
    for turn_row in turn_rows:
        turns_by_local_speaker[str(turn_row.local_speaker_key)].append(turn_row)

    local_speaker_matches: dict[str, dict[str, Any]] = {}
    for local_speaker_key, local_turn_rows in turns_by_local_speaker.items():
        matched_speaker, turn_confidence, evidence_payload = (
            _match_window_local_speaker(
                session,
                local_turn_rows=local_turn_rows,
                speaker_metadata=speaker_metadata_by_key.get(local_speaker_key, {}),
                overlapping_utterances=overlapping_utterances,
                previous_turn_rows=previous_turn_rows,
                recording_speakers_by_id=recording_speakers_by_id,
            )
        )
        local_speaker_matches[local_speaker_key] = {
            "matched_speaker": matched_speaker,
            "confidence": turn_confidence,
            "evidence": evidence_payload,
        }

    _enforce_distinct_window_local_speaker_matches(
        session,
        recording_id=recording_id,
        processing_run_id=processing_run_id,
        local_speaker_matches=local_speaker_matches,
        turns_by_local_speaker=turns_by_local_speaker,
        previous_turn_rows=previous_turn_rows,
        speaker_metadata_by_key=speaker_metadata_by_key,
        recording_speakers_by_id=recording_speakers_by_id,
        replay_policy=effective_policy,
    )

    matched_turn_count = 0
    for local_speaker_key, local_turn_rows in turns_by_local_speaker.items():
        match_payload = local_speaker_matches.get(local_speaker_key, {})
        matched_speaker = _normalized_identity_replay_candidate(
            session,
            replay_policy=effective_policy,
            matched_speaker=match_payload.get("matched_speaker"),
        )
        turn_confidence = float(match_payload.get("confidence") or 0.0)
        evidence_payload = dict(match_payload.get("evidence") or {})
        if (
            matched_speaker is not None
            and match_payload.get("matched_speaker") is not None
            and getattr(match_payload.get("matched_speaker"), "id", None)
            != getattr(matched_speaker, "id", None)
        ):
            evidence_payload["identity_replay_normalized_from_recording_speaker_id"] = (
                int(match_payload["matched_speaker"].id)
            )
            evidence_payload["identity_replay_anchor_recording_speaker_id"] = int(
                matched_speaker.id
            )

        metadata_payload = dict(speaker_metadata_by_key.get(local_speaker_key, {}))
        metadata_payload.update(
            {
                "matched_recording_speaker_id": int(matched_speaker.id)
                if matched_speaker is not None
                else None,
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
            turn_row.matched_recording_speaker_id = (
                matched_speaker.id if matched_speaker is not None else None
            )
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

    merge_plans = []
    if not _is_identity_replay_policy(effective_policy):
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
        projection = projection_by_public_id.get(utterance.public_id, {})
        support_summary = _summarize_utterance_turn_support(
            utterance,
            turn_rows=support_turn_rows,
            replay_policy=effective_policy,
        )
        existing_payload = dict(utterance.confidence_payload or {})
        existing_rolling_payload = dict(
            existing_payload.get("rolling_diarization") or {}
        )
        current_overlap_labels = _rolling_overlap_labels_for_utterance(
            utterance,
            projection=projection,
            recording_speakers_by_id=recording_speakers_by_id,
        )
        has_existing_overlap = bool(
            current_overlap_labels
            or list(projection.get("overlapping_speakers") or [])
            or list(
                existing_rolling_payload.get("overlapping_recording_speaker_ids") or []
            )
            or list(existing_rolling_payload.get("overlapping_speakers") or [])
            or utterance.overlap_group_id
        )
        if not _is_identity_replay_policy(effective_policy):
            split_replacement_segments = (
                _build_split_replacement_segments_from_diarization(
                    utterance,
                    turn_rows=turn_rows,
                    recording_speakers_by_id=recording_speakers_by_id,
                    window_result_id=int(window_result.id),
                )
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

            turn_boundary_replacement_segments = (
                _build_turn_boundary_split_segments_from_diarization(
                    utterance,
                    turn_rows=turn_rows,
                    recording_speakers_by_id=recording_speakers_by_id,
                    window_result_id=int(window_result.id),
                )
            )
            if turn_boundary_replacement_segments:
                replacement_utterances = _apply_boundary_reconciliation_segments(
                    session,
                    recording_id=recording_id,
                    source_utterances=[utterance],
                    replacement_segments=turn_boundary_replacement_segments,
                    processing_run_id=processing_run_id,
                    source=source,
                )
                if replacement_utterances:
                    updated_utterance_count += len(replacement_utterances)
                continue

        candidate_speaker, candidate_confidence, candidate_payload = (
            _match_utterance_from_diarization_turns(
                utterance,
                turn_rows=turn_rows,
                recording_speakers_by_id=recording_speakers_by_id,
                replay_policy=effective_policy,
            )
        )
        current_speaker_id = utterance.recording_speaker_id
        identity_anchor_speaker_id = _identity_replay_anchor_recording_speaker_id(
            effective_policy
        )
        current_is_identity_cluster_member = _identity_replay_scope_contains(
            effective_policy,
            current_speaker_id,
        )
        existing_speaker_confidence = (
            _to_optional_float(utterance.speaker_confidence) or 0.0
        )

        def overlap_payload_for(applied_speaker_id: int | None) -> dict[str, Any]:
            return _build_utterance_overlap_projection_payload(
                utterance,
                turn_rows=turn_rows,
                recording_speakers_by_id=recording_speakers_by_id,
                primary_speaker_id=_replay_normalized_recording_speaker_id(
                    effective_policy,
                    applied_speaker_id,
                ),
                replay_policy=effective_policy,
            )

        if candidate_speaker is None:
            overlap_payload = overlap_payload_for(
                int(current_speaker_id) if current_speaker_id is not None else None
            )
            if not _rolling_overlap_payload_changed(
                existing_rolling_payload, overlap_payload
            ):
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
        cluster_normalization = (
            _is_identity_replay_policy(effective_policy)
            and identity_anchor_speaker_id is not None
            and current_speaker_id is not None
            and current_is_identity_cluster_member
            and int(candidate_speaker.id) == int(identity_anchor_speaker_id)
            and int(current_speaker_id) != int(candidate_speaker.id)
        )
        if cluster_normalization:
            current_state_payload = dict(candidate_state_payload)

        def reject_candidate(
            rejection_reason: str,
            *,
            metric_reason: str | None = None,
        ) -> None:
            if current_state_payload is not None:
                rolling_payload.update(current_state_payload)
            rolling_payload["applied_recording_speaker_id"] = (
                int(current_speaker_id) if current_speaker_id is not None else None
            )
            rolling_payload["candidate_recording_speaker_id"] = int(
                candidate_speaker.id
            )
            rolling_payload["candidate_confidence"] = round(
                float(candidate_confidence), 4
            )
            rolling_payload["candidate_rejected"] = True
            rolling_payload["rejection_reason"] = rejection_reason
            _merge_overlap_payload_into_rolling(
                rolling_payload,
                overlap_payload_for(
                    int(current_speaker_id) if current_speaker_id is not None else None
                ),
            )
            existing_payload["rolling_diarization"] = rolling_payload
            utterance.confidence_payload = existing_payload
            utterance.last_diarization_window_result_id = window_result.id
            session.add(utterance)
            if metric_reason is not None:
                _record_guarded_replay_rejection(
                    recording_id=recording_id,
                    window_result_id=int(window_result.id),
                    replay_policy=effective_policy,
                    reason=metric_reason,
                    utterance=utterance,
                    candidate_speaker_id=int(candidate_speaker.id),
                    current_speaker_id=(
                        int(current_speaker_id)
                        if current_speaker_id is not None
                        else None
                    ),
                )

        if utterance.manual_speaker_locked:
            preserved_manual_lock_count += 1
            rolling_payload.update(
                current_state_payload
                or _build_utterance_speaker_state_payload(
                    utterance,
                    speaker_id=(
                        int(current_speaker_id)
                        if current_speaker_id is not None
                        else int(candidate_speaker.id)
                    ),
                    confidence=existing_speaker_confidence or candidate_confidence,
                    support_summary=support_summary,
                    manual_override=True,
                )
            )
            rolling_payload["applied_recording_speaker_id"] = (
                int(current_speaker_id) if current_speaker_id is not None else None
            )
            rolling_payload["candidate_recording_speaker_id"] = int(
                candidate_speaker.id
            )
            _merge_overlap_payload_into_rolling(
                rolling_payload,
                overlap_payload_for(
                    int(current_speaker_id) if current_speaker_id is not None else None
                ),
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
            utterance.speaker_confidence = max(
                existing_speaker_confidence, candidate_confidence
            )
            utterance.last_diarization_window_result_id = window_result.id
            existing_payload["rolling_diarization"] = rolling_payload
            utterance.confidence_payload = existing_payload
            _set_utterance_speaker_assignment_provenance(
                utterance,
                source=source,
                authority=_max_speaker_assignment_authority(
                    _utterance_speaker_assignment_authority(
                        utterance, projection=projection
                    ),
                    _derive_default_speaker_assignment_authority(
                        state=utterance.state,
                        manual_speaker_locked=bool(utterance.manual_speaker_locked),
                    ),
                ),
            )
            session.add(utterance)
            projection_dirty = True
            continue

        if not effective_policy.allow_speaker_reassignment:
            reject_candidate("boundary_only_live_tail_reconciliation")
            projection_dirty = True
            continue

        if (
            _is_identity_replay_policy(effective_policy)
            and current_speaker_id is not None
            and int(current_speaker_id) != int(candidate_speaker.id)
        ):
            utterance_state_value = (
                utterance.state.value
                if hasattr(utterance.state, "value")
                else str(utterance.state)
            )
            if (
                effective_policy.preserve_finalized_overlap_primary
                and utterance_state_value == TranscriptUtteranceState.FINALIZED.value
                and has_existing_overlap
            ):
                reject_candidate(
                    "overlap_primary_preserved",
                    metric_reason="overlap_primary_preserved",
                )
                projection_dirty = True
                continue
            if (
                effective_policy.preserve_finalized_boundary_primary
                and utterance_state_value == TranscriptUtteranceState.FINALIZED.value
                and (
                    candidate_payload.get("is_boundary_utterance")
                    or existing_rolling_payload.get("is_boundary_utterance")
                )
            ):
                reject_candidate(
                    "boundary_primary_preserved",
                    metric_reason="boundary_primary_preserved",
                )
                projection_dirty = True
                continue

        if (
            _is_identity_replay_policy(effective_policy)
            and identity_anchor_speaker_id is not None
            and int(candidate_speaker.id) != int(identity_anchor_speaker_id)
        ):
            candidate_status = str(
                getattr(candidate_speaker, "speaker_status", "") or ""
            )
            if (
                not effective_policy.allow_inactive_candidate_reactivation
                and candidate_status not in {"", "active"}
            ):
                reject_candidate(
                    "inactive_candidate_blocked",
                    metric_reason="inactive_candidate_blocked",
                )
                projection_dirty = True
                continue
            reject_candidate(
                "out_of_cluster_candidate",
                metric_reason="out_of_cluster_candidate",
            )
            projection_dirty = True
            continue

        if (
            not cluster_normalization
            and current_speaker_id is not None
            and current_state_payload is not None
            and current_state_payload.get("speaker_state")
            == ROLLING_DIARIZATION_SPEAKER_STATE_STABLE
            and int(candidate_state_payload.get("supporting_window_count", 0))
            < ROLLING_DIARIZATION_STABLE_WINDOW_COUNT
        ):
            rolling_payload["candidate_supporting_window_count"] = int(
                candidate_state_payload.get("supporting_window_count", 0)
            )
            reject_candidate("stable_speaker_requires_repeated_evidence")
            projection_dirty = True
            continue

        if not cluster_normalization and existing_speaker_confidence >= (
            candidate_confidence + ROLLING_DIARIZATION_EXISTING_CONFIDENCE_MARGIN
        ):
            overlap_payload = overlap_payload_for(
                int(current_speaker_id) if current_speaker_id is not None else None
            )
            if not _rolling_overlap_payload_changed(
                existing_rolling_payload, overlap_payload
            ):
                continue
            reject_candidate("existing_speaker_confidence_higher")
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
        _set_utterance_speaker_assignment_provenance(
            utterance,
            source=source,
            authority=_max_speaker_assignment_authority(
                _utterance_speaker_assignment_authority(
                    utterance, projection=projection
                ),
                _derive_default_speaker_assignment_authority(
                    state=utterance.state,
                    manual_speaker_locked=bool(utterance.manual_speaker_locked),
                ),
            ),
        )
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


def _enforce_distinct_window_local_speaker_matches(
    session,
    *,
    recording_id: int,
    processing_run_id: int | None,
    local_speaker_matches: dict[str, dict[str, Any]],
    turns_by_local_speaker: dict[str, list[DiarizationWindowTurn]],
    previous_turn_rows: Sequence[DiarizationWindowTurn],
    speaker_metadata_by_key: dict[str, dict[str, Any]],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
    replay_policy: SpeakerReplayPolicy | None = None,
) -> None:
    matched_local_keys_by_speaker_id: dict[int, list[str]] = defaultdict(list)
    for local_speaker_key, match_payload in local_speaker_matches.items():
        matched_speaker = match_payload.get("matched_speaker")
        if matched_speaker is None or getattr(matched_speaker, "id", None) is None:
            continue
        matched_local_keys_by_speaker_id[int(matched_speaker.id)].append(
            local_speaker_key
        )

    for (
        contested_speaker_id,
        local_speaker_keys,
    ) in matched_local_keys_by_speaker_id.items():
        if len(local_speaker_keys) < 2:
            continue

        primary_local_speaker_key = max(
            local_speaker_keys,
            key=lambda local_speaker_key: _window_local_speaker_contested_claim_score(
                local_speaker_key,
                contested_speaker_id=contested_speaker_id,
                speaker_metadata_by_key=speaker_metadata_by_key,
                turns_by_local_speaker=turns_by_local_speaker,
            ),
        )

        for local_speaker_key in local_speaker_keys:
            if local_speaker_key == primary_local_speaker_key:
                continue

            alternate_speaker, alternate_confidence, alternate_evidence = (
                _select_distinct_alternate_window_speaker(
                    session,
                    local_turn_rows=turns_by_local_speaker.get(local_speaker_key, []),
                    previous_turn_rows=previous_turn_rows,
                    speaker_metadata=speaker_metadata_by_key.get(local_speaker_key, {}),
                    recording_speakers_by_id=recording_speakers_by_id,
                    excluded_recording_speaker_ids={contested_speaker_id},
                )
            )

            if (
                alternate_speaker is None
                and _local_turn_total_duration_ms(
                    turns_by_local_speaker.get(local_speaker_key, [])
                )
                >= ROLLING_DIARIZATION_DISTINCT_LOCAL_SPEAKER_MIN_DURATION_MS
            ):
                if (
                    replay_policy is not None
                    and not replay_policy.allow_new_recording_speakers
                ):
                    alternate_evidence = {
                        "reason": "distinct_local_speaker_new_recording_speaker_blocked",
                        "provisional": True,
                    }
                else:
                    alternate_speaker = _create_rolling_diarization_recording_speaker(
                        session,
                        recording_id=recording_id,
                        processing_run_id=processing_run_id,
                        local_turn_rows=turns_by_local_speaker.get(
                            local_speaker_key, []
                        ),
                    )
                    recording_speakers_by_id[int(alternate_speaker.id)] = (
                        alternate_speaker
                    )
                    alternate_confidence = ROLLING_DIARIZATION_CONFIDENCE_FLOOR
                    alternate_evidence = {
                        "reason": "distinct_local_speaker_new_recording_speaker",
                        "provisional": False,
                    }

            existing_evidence = dict(
                local_speaker_matches[local_speaker_key].get("evidence") or {}
            )
            if alternate_speaker is None:
                existing_evidence.update(
                    {
                        "provisional": True,
                        "reason": "distinct_local_speaker_conflict_unmatched",
                        "original_recording_speaker_id": contested_speaker_id,
                        "primary_local_speaker_key": primary_local_speaker_key,
                    }
                )
                local_speaker_matches[local_speaker_key].update(
                    {
                        "matched_speaker": None,
                        "confidence": min(
                            float(
                                local_speaker_matches[local_speaker_key].get(
                                    "confidence"
                                )
                                or 0.0
                            ),
                            ROLLING_DIARIZATION_CONFIDENCE_FLOOR,
                        ),
                        "evidence": existing_evidence,
                    }
                )
                continue

            existing_evidence.update(alternate_evidence)
            existing_evidence.update(
                {
                    "provisional": False,
                    "original_recording_speaker_id": contested_speaker_id,
                    "primary_local_speaker_key": primary_local_speaker_key,
                    "conflict_resolution": "distinct_local_speaker_alternate",
                }
            )
            local_speaker_matches[local_speaker_key].update(
                {
                    "matched_speaker": alternate_speaker,
                    "confidence": max(
                        float(alternate_confidence),
                        ROLLING_DIARIZATION_CONFIDENCE_FLOOR,
                    ),
                    "evidence": existing_evidence,
                }
            )

    assigned_recording_speaker_ids = {
        int(match_payload["matched_speaker"].id)
        for match_payload in local_speaker_matches.values()
        if match_payload.get("matched_speaker") is not None
        and getattr(match_payload["matched_speaker"], "id", None) is not None
    }
    for local_speaker_key, match_payload in local_speaker_matches.items():
        if match_payload.get("matched_speaker") is not None:
            continue
        local_turn_rows = turns_by_local_speaker.get(local_speaker_key, [])
        alternate_speaker, alternate_confidence, alternate_evidence = (
            _select_distinct_alternate_window_speaker(
                session,
                local_turn_rows=local_turn_rows,
                previous_turn_rows=previous_turn_rows,
                speaker_metadata=speaker_metadata_by_key.get(local_speaker_key, {}),
                recording_speakers_by_id=recording_speakers_by_id,
                excluded_recording_speaker_ids=assigned_recording_speaker_ids,
            )
        )
        if alternate_speaker is None:
            if (
                len(turns_by_local_speaker) < 2
                or _local_turn_total_duration_ms(local_turn_rows)
                < ROLLING_DIARIZATION_DISTINCT_LOCAL_SPEAKER_MIN_DURATION_MS
            ):
                continue
            if (
                replay_policy is not None
                and not replay_policy.allow_new_recording_speakers
            ):
                alternate_evidence = {
                    "reason": "distinct_unmatched_local_speaker_new_recording_speaker_blocked",
                    "provisional": True,
                }
            else:
                alternate_speaker = _create_rolling_diarization_recording_speaker(
                    session,
                    recording_id=recording_id,
                    processing_run_id=processing_run_id,
                    local_turn_rows=local_turn_rows,
                )
                recording_speakers_by_id[int(alternate_speaker.id)] = alternate_speaker
                alternate_confidence = ROLLING_DIARIZATION_CONFIDENCE_FLOOR
                alternate_evidence = {
                    "reason": "distinct_unmatched_local_speaker_new_recording_speaker",
                    "provisional": False,
                }
        existing_evidence = dict(match_payload.get("evidence") or {})
        existing_evidence.update(alternate_evidence)
        if alternate_speaker is None:
            existing_evidence.update(
                {
                    "provisional": True,
                    "conflict_resolution": "distinct_unmatched_local_speaker_unmatched",
                }
            )
            match_payload.update(
                {
                    "matched_speaker": None,
                    "confidence": min(
                        float(match_payload.get("confidence") or 0.0),
                        ROLLING_DIARIZATION_CONFIDENCE_FLOOR,
                    ),
                    "evidence": existing_evidence,
                }
            )
            continue
        existing_evidence.update(
            {
                "provisional": False,
                "conflict_resolution": "distinct_unmatched_local_speaker_alternate",
            }
        )
        match_payload.update(
            {
                "matched_speaker": alternate_speaker,
                "confidence": max(
                    float(alternate_confidence),
                    ROLLING_DIARIZATION_CONFIDENCE_FLOOR,
                ),
                "evidence": existing_evidence,
            }
        )
        assigned_recording_speaker_ids.add(int(alternate_speaker.id))


def _window_local_speaker_contested_claim_score(
    local_speaker_key: str,
    *,
    contested_speaker_id: int,
    speaker_metadata_by_key: dict[str, dict[str, Any]],
    turns_by_local_speaker: dict[str, list[DiarizationWindowTurn]],
) -> float:
    metadata = dict(speaker_metadata_by_key.get(local_speaker_key, {}) or {})
    score = float(
        _local_turn_total_duration_ms(turns_by_local_speaker.get(local_speaker_key, []))
    )
    best_recording_speaker_id = _to_optional_int(
        metadata.get("best_recording_speaker_id")
    )
    best_recording_speaker_score = _to_optional_float(
        metadata.get("best_recording_speaker_score")
    )
    if (
        best_recording_speaker_id == int(contested_speaker_id)
        and best_recording_speaker_score is not None
    ):
        score += float(best_recording_speaker_score) * 100_000.0
    return score


def _select_distinct_alternate_window_speaker(
    session,
    *,
    local_turn_rows: Sequence[DiarizationWindowTurn],
    previous_turn_rows: Sequence[DiarizationWindowTurn],
    speaker_metadata: dict[str, Any],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
    excluded_recording_speaker_ids: set[int],
) -> tuple[RecordingSpeaker | None, float, dict[str, Any]]:
    best_recording_speaker_id = _to_optional_int(
        speaker_metadata.get("best_recording_speaker_id")
    )
    best_recording_speaker_score = _to_optional_float(
        speaker_metadata.get("best_recording_speaker_score")
    )
    if (
        best_recording_speaker_id is not None
        and best_recording_speaker_id not in excluded_recording_speaker_ids
        and best_recording_speaker_id in recording_speakers_by_id
        and best_recording_speaker_score is not None
        and best_recording_speaker_score
        >= ROLLING_DIARIZATION_DISTINCT_LOCAL_SPEAKER_EMBEDDING_THRESHOLD
    ):
        return (
            _resolve_active_recording_speaker(
                session,
                recording_speakers_by_id[int(best_recording_speaker_id)],
            ),
            float(best_recording_speaker_score),
            {
                "reason": "distinct_local_speaker_embedding_match",
                "alternate_recording_speaker_score": round(
                    float(best_recording_speaker_score), 4
                ),
            },
        )

    previous_overlap_by_speaker_id: dict[int, int] = defaultdict(int)
    for local_turn_row in local_turn_rows:
        for previous_turn_row in previous_turn_rows:
            previous_speaker_id = previous_turn_row.matched_recording_speaker_id
            if (
                previous_speaker_id is None
                or int(previous_speaker_id) in excluded_recording_speaker_ids
            ):
                continue
            overlap_ms = _range_overlap_ms(
                local_turn_row.start_ms,
                local_turn_row.end_ms,
                previous_turn_row.start_ms,
                previous_turn_row.end_ms,
            )
            if overlap_ms <= 0:
                continue
            previous_overlap_by_speaker_id[int(previous_speaker_id)] += int(overlap_ms)

    if previous_overlap_by_speaker_id:
        ranked_previous = sorted(
            previous_overlap_by_speaker_id.items(),
            key=lambda item: (int(item[1]), int(item[0])),
            reverse=True,
        )
        top_speaker_id, top_overlap_ms = ranked_previous[0]
        second_overlap_ms = ranked_previous[1][1] if len(ranked_previous) > 1 else 0
        if top_overlap_ms >= ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS:
            total_overlap_ms = (
                sum(previous_overlap_by_speaker_id.values()) or top_overlap_ms
            )
            confidence = round(
                float(top_overlap_ms) / float(total_overlap_ms or 1.0), 4
            )
            recording_speaker = recording_speakers_by_id.get(int(top_speaker_id))
            if recording_speaker is not None:
                return (
                    _resolve_active_recording_speaker(session, recording_speaker),
                    confidence,
                    {
                        "reason": "distinct_local_speaker_previous_window_match",
                        "previous_window_overlap_ms": int(top_overlap_ms),
                        "previous_window_second_overlap_ms": int(second_overlap_ms),
                    },
                )

    return None, 0.0, {"reason": "distinct_local_speaker_no_alternate"}


def _local_turn_total_duration_ms(turn_rows: Sequence[DiarizationWindowTurn]) -> int:
    return sum(
        max(0, int(turn_row.end_ms) - int(turn_row.start_ms)) for turn_row in turn_rows
    )


def _next_live_recording_speaker_index(
    recording_speakers_by_id: dict[int, RecordingSpeaker],
) -> int:
    indexes: list[int] = []
    for recording_speaker in recording_speakers_by_id.values():
        label = str(recording_speaker.diarization_label or "")
        match = re.match(r"^LIVE_(\d+)$", label)
        if not match:
            continue
        indexes.append(int(match.group(1)))
    return (max(indexes) + 1) if indexes else 1


def _create_rolling_diarization_recording_speaker(
    session,
    *,
    recording_id: int,
    processing_run_id: int | None,
    local_turn_rows: Sequence[DiarizationWindowTurn],
) -> RecordingSpeaker:
    recording_speakers = _load_recording_speakers(session, recording_id)
    recording_speakers_by_id = {
        int(speaker.id): speaker
        for speaker in recording_speakers
        if speaker.id is not None
    }
    next_index = _next_live_recording_speaker_index(recording_speakers_by_id)
    start_ms = min(
        (int(turn_row.start_ms) for turn_row in local_turn_rows), default=None
    )
    end_ms = max((int(turn_row.end_ms) for turn_row in local_turn_rows), default=None)
    recording_speaker = RecordingSpeaker(
        recording_id=recording_id,
        diarization_label=f"LIVE_{next_index:02d}",
        name=f"Speaker {next_index}",
        speaker_kind="live",
        speaker_status="active",
        processing_run_id=processing_run_id,
        first_seen_ms=start_ms,
        last_seen_ms=end_ms,
        identity_confidence=ROLLING_DIARIZATION_CONFIDENCE_FLOOR,
    )
    session.add(recording_speaker)
    session.flush()
    ensure_recording_speaker_aliases_for_speaker(
        session,
        recording_speaker,
        source_run_id=processing_run_id,
    )
    return recording_speaker


def _match_window_local_speaker_impl(
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

            resolved_speaker = recording_speakers_by_id.get(
                utterance.recording_speaker_id
            )
            if resolved_speaker is None:
                continue
            resolved_speaker = _resolve_active_recording_speaker(
                session, resolved_speaker
            )

            if utterance.manual_speaker_locked:
                evidence_by_speaker_id[resolved_speaker.id] += (
                    overlap_ms * ROLLING_DIARIZATION_MANUAL_WEIGHT
                )
                detail_by_speaker_id[resolved_speaker.id]["manual_overlap_ms"] += (
                    overlap_ms
                )
            else:
                evidence_by_speaker_id[resolved_speaker.id] += (
                    overlap_ms * ROLLING_DIARIZATION_UTTERANCE_WEIGHT
                )
                detail_by_speaker_id[resolved_speaker.id]["utterance_overlap_ms"] += (
                    overlap_ms
                )

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
            evidence_by_speaker_id[
                int(previous_turn_row.matched_recording_speaker_id)
            ] += overlap_ms * ROLLING_DIARIZATION_CONTINUITY_WEIGHT
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
        detail_by_speaker_id[int(best_recording_speaker_id)]["embedding_score"] = (
            best_recording_speaker_score
        )

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
            resolved_speaker = _resolve_active_recording_speaker(
                session, recording_speaker
            )
            evidence_by_speaker_id[int(resolved_speaker.id)] += (
                best_global_speaker_score * ROLLING_DIARIZATION_GLOBAL_WEIGHT
            )
            detail_by_speaker_id[int(resolved_speaker.id)]["global_score"] = (
                best_global_speaker_score
            )
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
    if (
        evidence_detail["manual_overlap_ms"]
        >= ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS
    ):
        matched = True
    elif (
        evidence_detail["continuity_overlap_ms"]
        >= ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS
    ):
        matched = True
    elif (
        best_recording_speaker_id == top_speaker_id
        and best_recording_speaker_score is not None
        and best_recording_speaker_score >= 0.75
    ):
        matched = True
    elif (
        evidence_detail["utterance_overlap_ms"]
        >= ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS
        and confidence >= ROLLING_DIARIZATION_CONFIDENCE_FLOOR
        and (top_score - second_score) >= ROLLING_DIARIZATION_MIN_TURN_MATCH_MARGIN
    ):
        matched = True
    elif (
        confidence >= 0.8
        and (top_score - second_score) >= ROLLING_DIARIZATION_MIN_TURN_MATCH_MARGIN
    ):
        matched = True

    if not matched:
        evidence_detail["provisional"] = True
        return None, confidence, evidence_detail

    matched_speaker = recording_speakers_by_id.get(top_speaker_id)
    if matched_speaker is None:
        evidence_detail["provisional"] = True
        return None, confidence, evidence_detail

    evidence_detail["provisional"] = False
    return (
        _resolve_active_recording_speaker(session, matched_speaker),
        confidence,
        evidence_detail,
    )


def _match_utterance_from_diarization_turns(
    utterance: TranscriptUtterance,
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
    replay_policy: SpeakerReplayPolicy | None = None,
) -> tuple[RecordingSpeaker | None, float, dict[str, Any]]:
    overlap_by_speaker_id: dict[int, int] = defaultdict(int)
    for turn_row in turn_rows:
        if turn_row.matched_recording_speaker_id is None:
            continue
        normalized_speaker_id = _replay_normalized_recording_speaker_id(
            replay_policy,
            int(turn_row.matched_recording_speaker_id),
        )
        if normalized_speaker_id is None:
            continue
        overlap_ms = _range_overlap_ms(
            utterance.start_ms,
            utterance.end_ms,
            turn_row.start_ms,
            turn_row.end_ms,
        )
        if overlap_ms <= 0:
            continue
        overlap_by_speaker_id[int(normalized_speaker_id)] += overlap_ms

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

    utterance_duration_ms = max(1, int(utterance.end_ms) - int(utterance.start_ms))
    overlap_ratio_by_speaker_id = {
        int(speaker_id): round(float(overlap_ms) / float(utterance_duration_ms), 4)
        for speaker_id, overlap_ms in overlap_by_speaker_id.items()
    }
    top_ratio = overlap_ratio_by_speaker_id.get(int(top_speaker_id), 0.0)
    second_ratio = (
        overlap_ratio_by_speaker_id.get(int(ranked[1][0]), 0.0)
        if len(ranked) > 1
        else 0.0
    )
    is_boundary_utterance = (
        len(ranked) > 1
        and top_ratio >= ROLLING_DIARIZATION_BOUNDARY_AMBIGUITY_RATIO
        and second_ratio >= ROLLING_DIARIZATION_BOUNDARY_AMBIGUITY_RATIO
    )
    overlapping_recording_speaker_ids = [
        int(speaker_id)
        for speaker_id, _ in ranked
        if int(speaker_id) != int(top_speaker_id)
        and overlap_ratio_by_speaker_id.get(int(speaker_id), 0.0)
        >= ROLLING_DIARIZATION_BOUNDARY_AMBIGUITY_RATIO
    ]

    base_evidence: dict[str, Any] = {
        "top_overlap_ms": int(top_overlap_ms),
        "second_overlap_ms": int(second_overlap_ms),
        "total_overlap_ms": int(total_overlap_ms),
        "overlap_ratio_by_speaker_id": overlap_ratio_by_speaker_id,
        "is_boundary_utterance": bool(is_boundary_utterance),
        "boundary_overlapping_recording_speaker_ids": overlapping_recording_speaker_ids,
    }

    if top_overlap_ms < ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS:
        return None, confidence, {**base_evidence, "provisional": True}

    if len(ranked) > 1 and confidence < ROLLING_DIARIZATION_CONFIDENCE_FLOOR:
        return None, confidence, {**base_evidence, "provisional": True}

    matched_speaker = recording_speakers_by_id.get(top_speaker_id)
    if matched_speaker is None:
        return None, confidence, {**base_evidence, "provisional": True}

    effective_confidence = confidence
    if is_boundary_utterance:
        effective_confidence = round(
            confidence * ROLLING_DIARIZATION_BOUNDARY_CONFIDENCE_DAMPENER,
            4,
        )
        base_evidence["boundary_dampened"] = True
        base_evidence["raw_confidence"] = confidence

    return (
        matched_speaker,
        effective_confidence,
        {**base_evidence, "provisional": False},
    )


def _build_utterance_overlap_projection_payload(
    utterance: TranscriptUtterance,
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
    primary_speaker_id: int | None,
    replay_policy: SpeakerReplayPolicy | None = None,
) -> dict[str, Any]:
    overlap_by_speaker_id: dict[int, int] = defaultdict(int)
    for turn_row in turn_rows:
        if turn_row.matched_recording_speaker_id is None:
            continue
        normalized_speaker_id = _replay_normalized_recording_speaker_id(
            replay_policy,
            int(turn_row.matched_recording_speaker_id),
        )
        if normalized_speaker_id is None:
            continue
        overlap_ms = _range_overlap_ms(
            utterance.start_ms,
            utterance.end_ms,
            turn_row.start_ms,
            turn_row.end_ms,
        )
        if overlap_ms <= 0:
            continue
        overlap_by_speaker_id[int(normalized_speaker_id)] += overlap_ms

    ranked_speakers = sorted(
        overlap_by_speaker_id.items(),
        key=lambda item: (int(item[1]), int(item[0])),
        reverse=True,
    )
    overlapping_recording_speaker_ids: list[int] = []
    overlapping_speakers: list[str] = []

    for speaker_id, overlap_ms in ranked_speakers:
        if primary_speaker_id is not None and int(speaker_id) == int(
            primary_speaker_id
        ):
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
    return list(
        existing_rolling_payload.get("overlapping_recording_speaker_ids") or []
    ) != list(overlap_payload.get("overlapping_recording_speaker_ids") or []) or list(
        existing_rolling_payload.get("overlapping_speakers") or []
    ) != list(overlap_payload.get("overlapping_speakers") or [])


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

    # First pass: best-effort speaker per word from overlapping turns. Words
    # that have no overlapping matched turn (typical at silence-aligned
    # speaker boundaries) are left as None and filled below from the nearest
    # mapped neighbour so a single unmapped word does not defeat the split.
    per_word_speaker_ids: list[int | None] = []
    for word_payload in utterance_words:
        speaker_id = _match_word_to_recording_speaker_id(
            word_payload,
            turn_rows=turn_rows,
        )
        if speaker_id is not None and speaker_id not in recording_speakers_by_id:
            speaker_id = None
        per_word_speaker_ids.append(speaker_id)

    if all(speaker_id is None for speaker_id in per_word_speaker_ids):
        return []

    # Forward-fill then backward-fill so every word inherits the nearest
    # mapped neighbour's speaker.
    last_known: int | None = None
    for index, speaker_id in enumerate(per_word_speaker_ids):
        if speaker_id is None:
            per_word_speaker_ids[index] = last_known
        else:
            last_known = speaker_id
    last_known = None
    for index in range(len(per_word_speaker_ids) - 1, -1, -1):
        if per_word_speaker_ids[index] is None:
            per_word_speaker_ids[index] = last_known
        else:
            last_known = per_word_speaker_ids[index]

    if any(speaker_id is None for speaker_id in per_word_speaker_ids):
        return []

    speaker_groups: list[dict[str, Any]] = []
    for word_payload, speaker_id in zip(utterance_words, per_word_speaker_ids):
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
                "state": utterance.state.value
                if hasattr(utterance.state, "value")
                else str(utterance.state),
                "speaker_manually_edited": False,
                "text_manually_edited": False,
                "speaker_confidence": utterance.speaker_confidence,
                "text_confidence": utterance.text_confidence,
                "confidence_payload": replacement_payload,
                "last_diarization_window_result_id": int(window_result_id),
            }
        )

    return replacement_segments


def _build_turn_boundary_split_segments_from_diarization(
    utterance: TranscriptUtterance,
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
    window_result_id: int,
) -> list[dict[str, Any]]:
    """Split a transition utterance at diarization boundaries when the
    word-level splitter cannot run (e.g. ASR returned no word timestamps).

    Triggers when two or more matched recording speakers each cover a
    meaningful, contiguous slice of the utterance. Text is distributed
    proportionally by word count (whitespace tokens) along the boundary.
    """

    if utterance.manual_text_locked or utterance.manual_speaker_locked:
        return []

    utterance_start_ms = int(utterance.start_ms)
    utterance_end_ms = int(utterance.end_ms)
    utterance_duration_ms = utterance_end_ms - utterance_start_ms
    if utterance_duration_ms <= 0:
        return []

    # Collect overlapping turns clipped to the utterance and grouped into
    # consecutive same-speaker runs, in time order.
    clipped_turns: list[tuple[int, int, int]] = []
    for turn_row in turn_rows:
        if turn_row.matched_recording_speaker_id is None:
            continue
        speaker_id = int(turn_row.matched_recording_speaker_id)
        if speaker_id not in recording_speakers_by_id:
            continue
        start_ms = max(utterance_start_ms, int(turn_row.start_ms))
        end_ms = min(utterance_end_ms, int(turn_row.end_ms))
        if end_ms <= start_ms:
            continue
        clipped_turns.append((start_ms, end_ms, speaker_id))

    if len(clipped_turns) < 2:
        return []

    clipped_turns.sort(key=lambda item: (item[0], item[1]))

    runs: list[dict[str, int]] = []
    for start_ms, end_ms, speaker_id in clipped_turns:
        if (
            runs
            and int(runs[-1]["speaker_id"]) == speaker_id
            and start_ms <= int(runs[-1]["end_ms"])
        ):
            runs[-1]["end_ms"] = max(int(runs[-1]["end_ms"]), end_ms)
            continue
        runs.append({"start_ms": start_ms, "end_ms": end_ms, "speaker_id": speaker_id})

    if len(runs) < 2:
        return []

    distinct_speaker_ids = {int(run["speaker_id"]) for run in runs}
    if len(distinct_speaker_ids) < 2:
        return []

    overlap_by_speaker_id: dict[int, int] = defaultdict(int)
    for run in runs:
        overlap_by_speaker_id[int(run["speaker_id"])] += int(run["end_ms"]) - int(
            run["start_ms"]
        )

    min_overlap_ms = max(
        ROLLING_DIARIZATION_BOUNDARY_SPLIT_MIN_OVERLAP_MS,
        int(
            round(ROLLING_DIARIZATION_BOUNDARY_SPLIT_MIN_RATIO * utterance_duration_ms)
        ),
    )
    qualifying_speaker_ids = {
        speaker_id
        for speaker_id, overlap_ms in overlap_by_speaker_id.items()
        if overlap_ms >= min_overlap_ms
    }
    if len(qualifying_speaker_ids) < 2:
        return []

    # Merge any small runs whose speaker did not qualify into the previous
    # qualifying run so we keep contiguous, meaningful boundaries.
    coalesced_runs: list[dict[str, int]] = []
    for run in runs:
        if int(run["speaker_id"]) in qualifying_speaker_ids:
            if coalesced_runs and int(coalesced_runs[-1]["speaker_id"]) == int(
                run["speaker_id"]
            ):
                coalesced_runs[-1]["end_ms"] = max(
                    int(coalesced_runs[-1]["end_ms"]), int(run["end_ms"])
                )
                continue
            coalesced_runs.append(dict(run))
        else:
            if not coalesced_runs:
                continue
            coalesced_runs[-1]["end_ms"] = max(
                int(coalesced_runs[-1]["end_ms"]), int(run["end_ms"])
            )

    if len({int(run["speaker_id"]) for run in coalesced_runs}) < 2:
        return []

    # Close gaps so the union covers the full utterance: snap each boundary
    # to the midpoint of any silence gap between consecutive runs.
    for index in range(len(coalesced_runs) - 1):
        left = coalesced_runs[index]
        right = coalesced_runs[index + 1]
        if int(right["start_ms"]) > int(left["end_ms"]):
            midpoint = (int(left["end_ms"]) + int(right["start_ms"])) // 2
            left["end_ms"] = midpoint
            right["start_ms"] = midpoint
        elif int(right["start_ms"]) < int(left["end_ms"]):
            # Overlapping runs: split at midpoint of the overlap.
            midpoint = (int(left["end_ms"]) + int(right["start_ms"])) // 2
            left["end_ms"] = midpoint
            right["start_ms"] = midpoint
    coalesced_runs[0]["start_ms"] = utterance_start_ms
    coalesced_runs[-1]["end_ms"] = utterance_end_ms

    # Distribute text across the runs. Prefer word timestamps when available
    # so the split aligns with actual spoken words; otherwise fall back to a
    # whitespace-token proportional split.
    utterance_words = _load_utterance_asr_words(utterance)
    text_by_run: list[str] = []
    if utterance_words:
        text_by_run = _split_text_by_word_timestamps(coalesced_runs, utterance_words)
    if not text_by_run or any(not chunk.strip() for chunk in text_by_run):
        text_by_run = _split_text_proportionally(
            str(utterance.text or ""), coalesced_runs
        )
    if not text_by_run or len(text_by_run) != len(coalesced_runs):
        return []
    if any(not chunk.strip() for chunk in text_by_run):
        return []

    base_payload = dict(utterance.confidence_payload or {})
    base_rolling_payload = dict(base_payload.get("rolling_diarization") or {})
    replacement_segments: list[dict[str, Any]] = []

    for run, run_text in zip(coalesced_runs, text_by_run):
        speaker_id = int(run["speaker_id"])
        recording_speaker = recording_speakers_by_id.get(speaker_id)
        if recording_speaker is None:
            return []
        run_start_ms = int(run["start_ms"])
        run_end_ms = int(run["end_ms"])
        if run_end_ms <= run_start_ms:
            return []

        rolling_payload = dict(base_rolling_payload)
        rolling_payload.update(
            {
                "window_result_id": int(window_result_id),
                "matched_recording_speaker_id": int(recording_speaker.id),
                "split_from_public_id": utterance.public_id,
                "split_strategy": "diarization_turn_boundary",
            }
        )
        replacement_payload = dict(base_payload)
        replacement_payload["rolling_diarization"] = rolling_payload
        replacement_payload["asr_segments"] = [
            {
                "start_ms": run_start_ms,
                "end_ms": run_end_ms,
                "text": run_text.strip(),
            }
        ]
        replacement_payload["asr_word_timestamps_available"] = False

        replacement_segments.append(
            {
                "id": str(uuid4()),
                "start": run_start_ms / 1000.0,
                "end": run_end_ms / 1000.0,
                "text": run_text.strip(),
                "speaker": recording_speaker.diarization_label,
                "recording_speaker_id": int(recording_speaker.id),
                "segment_source": utterance.source_kind,
                "provisional": utterance.state == TranscriptUtteranceState.PROVISIONAL,
                "state": utterance.state.value
                if hasattr(utterance.state, "value")
                else str(utterance.state),
                "speaker_manually_edited": False,
                "text_manually_edited": False,
                "speaker_confidence": utterance.speaker_confidence,
                "text_confidence": utterance.text_confidence,
                "confidence_payload": replacement_payload,
                "last_diarization_window_result_id": int(window_result_id),
            }
        )

    return replacement_segments


def _split_text_by_word_timestamps(
    runs: Sequence[dict[str, int]],
    utterance_words: Sequence[dict[str, Any]],
) -> list[str]:
    grouped: list[list[str]] = [[] for _ in runs]
    for word_payload in utterance_words:
        word_start_ms = int(word_payload["start_ms"])
        word_end_ms = int(word_payload["end_ms"])
        word_text = str(word_payload.get("word") or "").strip()
        if not word_text:
            continue
        best_index = 0
        best_overlap = -1
        for index, run in enumerate(runs):
            overlap_ms = _range_overlap_ms(
                word_start_ms,
                word_end_ms,
                int(run["start_ms"]),
                int(run["end_ms"]),
            )
            if overlap_ms > best_overlap:
                best_overlap = overlap_ms
                best_index = index
        grouped[best_index].append(word_text)
    return [" ".join(words) for words in grouped]


def _split_text_proportionally(
    text: str,
    runs: Sequence[dict[str, int]],
) -> list[str]:
    tokens = (text or "").split()
    if not tokens:
        return []
    total_duration_ms = sum(int(run["end_ms"]) - int(run["start_ms"]) for run in runs)
    if total_duration_ms <= 0:
        return []
    total_tokens = len(tokens)
    chunks: list[str] = []
    cursor = 0
    for index, run in enumerate(runs):
        if index == len(runs) - 1:
            chunk_tokens = tokens[cursor:]
        else:
            run_duration_ms = int(run["end_ms"]) - int(run["start_ms"])
            take = max(
                1,
                int(round(total_tokens * (run_duration_ms / total_duration_ms))),
            )
            take = min(take, total_tokens - cursor - (len(runs) - index - 1))
            take = max(1, take)
            chunk_tokens = tokens[cursor : cursor + take]
            cursor += take
        chunks.append(" ".join(chunk_tokens))
    if any(not chunk.strip() for chunk in chunks):
        return []
    return chunks


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
        and (
            _build_split_replacement_segments_from_diarization(
                utterance,
                turn_rows=turn_rows,
                recording_speakers_by_id=recording_speakers_by_id,
                window_result_id=window_result_id,
            )
            or _build_turn_boundary_split_segments_from_diarization(
                utterance,
                turn_rows=turn_rows,
                recording_speakers_by_id=recording_speakers_by_id,
                window_result_id=window_result_id,
            )
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
            (utterance.id is not None and int(utterance.id) in split_candidate_ids)
            or utterance.manual_text_locked
            or utterance.manual_speaker_locked
        ):
            flush_group()
            continue

        candidate_speaker, candidate_confidence, _candidate_payload = (
            _match_utterance_from_diarization_turns(
                utterance,
                turn_rows=turn_rows,
                recording_speakers_by_id=recording_speakers_by_id,
            )
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
            sum(float(confidence) for confidence in speaker_confidences)
            / float(len(speaker_confidences)),
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
            "provisional": _merged_boundary_state_value(source_utterances)
            == TranscriptUtteranceState.PROVISIONAL.value,
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


def _merged_boundary_state_value(
    source_utterances: Sequence[TranscriptUtterance],
) -> str:
    state_values = {
        utterance.state.value
        if hasattr(utterance.state, "value")
        else str(utterance.state)
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
        _speaker_state_for_utterance(utterance) for utterance in source_utterances
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
            "merged_from_public_ids": [
                utterance.public_id for utterance in source_utterances
            ],
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
    from .core import (
        _build_projection_segment,
        _projection_overlap_labels,
        _state_for_segment,
        list_active_utterances,
    )

    transcript = _load_transcript(session, recording_id)
    recording = session.get(Recording, recording_id)
    if (
        transcript is None
        or recording is None
        or not source_utterances
        or not replacement_segments
    ):
        return []

    active_utterances = list_active_utterances(session, recording_id)
    source_utterance_ids = {
        int(utterance.id) for utterance in source_utterances if utterance.id is not None
    }
    if not source_utterance_ids:
        return []

    recording_speakers = ensure_recording_speaker_aliases(
        session,
        recording_id,
        source_run_id=processing_run_id,
    )
    recording_speakers_by_id = {
        int(speaker.id): speaker
        for speaker in recording_speakers
        if speaker.id is not None
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
                resulting_segments.extend(
                    dict(segment) for segment in replacement_segments
                )
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
        old_state = (
            source_utterance.state.value
            if hasattr(source_utterance.state, "value")
            else str(source_utterance.state)
        )
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
        existing_utterance = remaining_existing_utterances.get(
            str(segment.get("id") or "")
        )
        if existing_utterance is not None:
            existing_utterance.sort_key = _sort_key_for_index(index)
            existing_utterance.overlap_group_id = overlap_group.get("group_id")
            existing_utterance.overlap_rank = overlap_group.get("rank", 0)
            session.add(existing_utterance)
            recording_speaker = (
                recording_speakers_by_id.get(
                    int(existing_utterance.recording_speaker_id)
                )
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
            recording_speaker_id=(
                recording_speaker.id if recording_speaker is not None else None
            ),
            state=_state_for_segment(recording, segment),
            source_kind=str(segment.get("segment_source") or source),
            processing_run_id=processing_run_id,
            revision=int(segment.get("revision") or 1),
            overlap_group_id=overlap_group.get("group_id"),
            overlap_rank=overlap_group.get("rank", 0),
            manual_text_locked=bool(segment.get("text_manually_edited") is True),
            manual_speaker_locked=bool(segment.get("speaker_manually_edited") is True),
            speaker_assignment_source=_resolve_segment_speaker_assignment_source(
                segment,
                source=source,
                state=_state_for_segment(recording, segment),
                manual_speaker_locked=bool(
                    segment.get("speaker_manually_edited") is True
                ),
            ),
            speaker_assignment_authority=_resolve_segment_speaker_assignment_authority(
                segment,
                state=_state_for_segment(recording, segment),
                manual_speaker_locked=bool(
                    segment.get("speaker_manually_edited") is True
                ),
            ),
            text_confidence=_to_optional_float(segment.get("text_confidence")),
            speaker_confidence=_to_optional_float(segment.get("speaker_confidence")),
            confidence_payload=(
                dict(segment.get("confidence_payload"))
                if isinstance(segment.get("confidence_payload"), dict)
                else None
            ),
            last_diarization_window_result_id=segment.get(
                "last_diarization_window_result_id"
            ),
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
    transcript.text = " ".join(
        segment.get("text", "") for segment in projection_segments
    ).strip()
    flag_modified(transcript, "segments")
    session.add(transcript)
    refresh_recording_speaker_usage_state(session, recording_id)

    _append_boundary_revision_events(
        session,
        previous_utterances=source_utterances,
        new_utterances=replacement_utterances,
        processing_run_id=processing_run_id,
        source=source,
    )

    return replacement_utterances


def _match_window_local_speaker(*args, **kwargs):
    import sys

    cp = sys.modules.get("backend.utils.canonical_pipeline")
    if cp and hasattr(cp, "_match_window_local_speaker"):
        actual = cp._match_window_local_speaker
        if actual.__code__ is not _match_window_local_speaker.__code__:
            return actual(*args, **kwargs)
    return _match_window_local_speaker_impl(*args, **kwargs)


__all__ = [name for name in globals() if not name.startswith("__")]
