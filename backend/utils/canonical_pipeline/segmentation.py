from .constants import *
from .speaker import *

def refine_recording_utterances_via_segmentation(
    session,
    *,
    recording_id: int,
    audio_path: str,
    device_str: str = "auto",
    hf_token: str | None = None,
    processing_run_id: int | None = None,
    source: str = "segmentation_refinement",
    candidate_predicate: Callable[[TranscriptUtterance], bool] | None = None,
) -> dict[str, Any]:
    from .diarization import _apply_boundary_reconciliation_segments
    from .core import list_active_utterances
    """Phase F finalize-time pass: re-split ambiguous utterances using
    frame-level segmentation derived turns.

    For each candidate active utterance (boundary-flagged by Phase B, or any
    sufficiently long utterance when ``candidate_predicate`` is None), runs
    the pyannote segmentation-3.0 model on the utterance audio span via
    :mod:`backend.processing.segmentation_refinement`, maps the resulting
    local speakers to ``RecordingSpeaker`` rows by embedding cosine
    similarity, and feeds the synthetic turns into the existing splitter
    machinery so word-level reassignment, supersession, and projection
    refresh all follow the canonical path.

    Returns a summary dict suitable for pipeline metrics.
    """

    summary: dict[str, Any] = {
        "candidate_utterance_count": 0,
        "refined_utterance_count": 0,
        "produced_split_count": 0,
        "segmentation_skipped_count": 0,
        "errors": 0,
        "window_result_id": None,
    }

    recording_speakers = _load_recording_speakers(session, recording_id)
    recording_speakers_with_voiceprint = [
        speaker
        for speaker in recording_speakers
        if speaker.embedding and len(speaker.embedding) > 0
    ]
    if len(recording_speakers_with_voiceprint) < 2:
        summary["skipped_reason"] = "insufficient_voiceprints"
        return summary

    recording_speakers_by_id = {
        int(speaker.id): speaker
        for speaker in recording_speakers
        if speaker.id is not None
    }

    active_utterances = list_active_utterances(session, recording_id)

    def _default_candidate(utterance: TranscriptUtterance) -> bool:
        if utterance.manual_text_locked or utterance.manual_speaker_locked:
            return False
        duration_ms = int(utterance.end_ms) - int(utterance.start_ms)
        if duration_ms < 1500:
            return False
        rolling_payload = (utterance.confidence_payload or {}).get(
            "rolling_diarization"
        ) or {}
        if isinstance(rolling_payload, dict) and rolling_payload.get(
            "is_boundary_utterance"
        ):
            return True
        # Long live-emitted utterances without a window-level split are the
        # other high-yield target for refinement.
        if str(utterance.source_kind or "").lower() == "live" and duration_ms >= 4000:
            return True
        return False

    predicate = candidate_predicate or _default_candidate
    candidates = [utterance for utterance in active_utterances if predicate(utterance)]
    summary["candidate_utterance_count"] = len(candidates)
    if not candidates:
        return summary

    # Lazy import keeps pyannote out of import-time graph for callers that
    # don't run finalize (e.g. read-only API paths).
    try:
        from backend.processing.segmentation_refinement import (
            SEGMENTATION_MODEL,
            refine_utterance_via_segmentation,
        )
    except Exception as exc:  # pragma: no cover - import error path  # noqa: BLE001
        logger.warning(
            "Segmentation refinement module unavailable for recording %s: %s",
            recording_id,
            exc,
        )
        summary["skipped_reason"] = "module_unavailable"
        summary["errors"] = 1
        return summary

    # Persist a sentinel DiarizationWindowResult row so the synthetic turns
    # have a stable provenance handle in confidence_payload.rolling_diarization.
    window_result = DiarizationWindowResult(
        recording_id=recording_id,
        processing_run_id=processing_run_id,
        window_index=0,
        window_start_ms=min(int(utterance.start_ms) for utterance in candidates),
        window_end_ms=max(int(utterance.end_ms) for utterance in candidates),
        model_name=SEGMENTATION_MODEL,
        model_version="segmentation-3.0",
        device=device_str,
        config_hash="segmentation_refinement",
        status="completed",
        raw_payload={"source": source},
    )
    session.add(window_result)
    session.flush()
    summary["window_result_id"] = int(window_result.id) if window_result.id else None

    for utterance in candidates:
        try:
            synthetic_turns = refine_utterance_via_segmentation(
                audio_path,
                utterance=utterance,
                recording_speakers=recording_speakers_with_voiceprint,
                device_str=device_str,
                hf_token=hf_token,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Segmentation refinement failed for utterance %s: %s",
                utterance.public_id,
                exc,
                exc_info=True,
            )
            summary["errors"] += 1
            continue

        if not synthetic_turns:
            summary["segmentation_skipped_count"] += 1
            continue

        split_segments = _build_split_replacement_segments_from_diarization(
            utterance,
            turn_rows=synthetic_turns,
            recording_speakers_by_id=recording_speakers_by_id,
            window_result_id=int(window_result.id),
        )
        if not split_segments:
            split_segments = _build_turn_boundary_split_segments_from_diarization(
                utterance,
                turn_rows=synthetic_turns,
                recording_speakers_by_id=recording_speakers_by_id,
                window_result_id=int(window_result.id),
            )
        if not split_segments:
            summary["segmentation_skipped_count"] += 1
            continue

        # Tag the replacement payloads so downstream UI / metrics can tell
        # the segmentation-derived splits apart from rolling-window splits.
        for replacement in split_segments:
            payload = replacement.setdefault("confidence_payload", {})
            rolling_payload = payload.setdefault("rolling_diarization", {})
            rolling_payload["split_source"] = source
            rolling_payload["split_model"] = SEGMENTATION_MODEL

        replacement_utterances = _apply_boundary_reconciliation_segments(
            session,
            recording_id=recording_id,
            source_utterances=[utterance],
            replacement_segments=split_segments,
            processing_run_id=processing_run_id,
            source=source,
        )
        if replacement_utterances:
            summary["refined_utterance_count"] += 1
            summary["produced_split_count"] += len(replacement_utterances)

    return summary



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
        "speaker_assignment_source": _utterance_speaker_assignment_source(utterance),
        "speaker_assignment_authority": _utterance_speaker_assignment_authority(utterance),
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
    replay_policy: SpeakerReplayPolicy | None = None,
) -> dict[int, dict[str, Any]]:
    window_ids_by_speaker_id: dict[int, set[int]] = defaultdict(set)
    overlap_ms_by_speaker_id: dict[int, int] = defaultdict(int)

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
        if overlap_ms < ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS:
            continue
        speaker_id = int(normalized_speaker_id)
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




__all__ = [name for name in globals() if not name.startswith('__')]
