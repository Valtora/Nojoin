"""Catch-up diarization stage: rebuild diarization for live-capture windows.

Extracted from backend.worker.tasks.pipeline as a pure decomposition. The shared
surface comes from .constants, so the constants.py shim wrappers keep resolving
the ``*_impl`` functions here via the ``backend.worker.tasks`` package namespace
with no call-site changes. Three low-level helpers stay in pipeline.py because the
core final-pipeline path also uses them; they are imported explicitly (pipeline
has no import-time dependency on this module, so there is no cycle).
"""

import logging

from backend.worker.tasks.constants import *  # noqa: F401,F403 -- shared task surface
from backend.worker.tasks.pipeline import (
    _final_asr_config_hash,
    _is_unresolved_speaker_label,
    _segment_requires_final_diarization_check,
)

logger = logging.getLogger(__name__)


def _collect_low_confidence_diarization_spans(
    live_segments_for_reuse: Sequence[dict],
) -> list[dict[str, int]]:
    spans: list[dict[str, int]] = []
    for segment in live_segments_for_reuse:
        if not _segment_requires_final_diarization_check(segment):
            continue

        start_ms = max(
            0,
            int(round(float(segment.get("start", 0.0)) * 1000.0))
            - FINAL_DIARIZATION_SPAN_PADDING_MS,
        )
        end_ms = max(
            start_ms,
            int(round(float(segment.get("end", 0.0)) * 1000.0))
            + FINAL_DIARIZATION_SPAN_PADDING_MS,
        )

        if spans and start_ms <= (
            int(spans[-1]["end_ms"]) + FINAL_DIARIZATION_BRIDGE_GAP_MS
        ):
            spans[-1]["end_ms"] = max(int(spans[-1]["end_ms"]), end_ms)
            spans[-1]["segment_count"] = int(spans[-1].get("segment_count", 0)) + 1
            continue

        spans.append(
            {
                "start_ms": int(start_ms),
                "end_ms": int(end_ms),
                "segment_count": 1,
            }
        )
    return spans


def _count_distinct_live_reuse_speakers(
    live_segments_for_reuse: Sequence[dict],
) -> int:
    speaker_labels: set[str] = set()
    for segment in live_segments_for_reuse:
        speaker_label = str(segment.get("speaker") or "UNKNOWN")
        if _is_unresolved_speaker_label(speaker_label):
            continue
        speaker_labels.add(speaker_label)
    return len(speaker_labels)


def _extract_completed_window_speaker_labels(raw_payload: object) -> set[str]:
    if not isinstance(raw_payload, dict):
        return set()

    speaker_labels: set[str] = set()
    for label in raw_payload.get("speaker_labels") or []:
        label_text = str(label or "").strip()
        if label_text:
            speaker_labels.add(label_text)

    speaker_metadata = raw_payload.get("speaker_metadata") or {}
    if isinstance(speaker_metadata, dict):
        for label in speaker_metadata.keys():
            label_text = str(label or "").strip()
            if label_text:
                speaker_labels.add(label_text)

    for turn_payload in raw_payload.get("turns") or []:
        if not isinstance(turn_payload, dict):
            continue
        label_text = str(turn_payload.get("local_speaker_key") or "").strip()
        if label_text:
            speaker_labels.add(label_text)

    return speaker_labels


def _summarize_completed_diarization_window_speaker_evidence_rows(
    window_results: Sequence[object],
) -> dict[str, int]:
    evidence = {
        "completed_window_count": 0,
        "multi_speaker_window_count": 0,
        "max_speaker_count": 0,
    }

    for window_result in window_results:
        evidence["completed_window_count"] += 1
        speaker_count = len(
            _extract_completed_window_speaker_labels(
                getattr(window_result, "raw_payload", None)
            )
        )
        evidence["max_speaker_count"] = max(
            evidence["max_speaker_count"], speaker_count
        )
        if speaker_count > 1:
            evidence["multi_speaker_window_count"] += 1

    return evidence


def _summarize_completed_diarization_window_speaker_evidence_impl(
    session,
    *,
    recording_id: int,
    effective_from_ms: int = 0,
) -> dict[str, int]:
    if not hasattr(session, "exec"):
        return {
            "completed_window_count": 0,
            "multi_speaker_window_count": 0,
            "max_speaker_count": 0,
        }

    window_results = session.exec(
        select(DiarizationWindowResult)
        .where(DiarizationWindowResult.recording_id == recording_id)
        .where(DiarizationWindowResult.status == "completed")
        .where(DiarizationWindowResult.window_end_ms > int(effective_from_ms))
    ).all()
    return _summarize_completed_diarization_window_speaker_evidence_rows(window_results)


def _completed_window_speaker_evidence_requires_final_diarization(
    live_segments_for_reuse: Sequence[dict],
    completed_window_speaker_evidence: dict[str, int] | None,
) -> bool:
    if not completed_window_speaker_evidence:
        return False

    max_speaker_count = int(
        completed_window_speaker_evidence.get("max_speaker_count", 0) or 0
    )
    multi_speaker_window_count = int(
        completed_window_speaker_evidence.get("multi_speaker_window_count", 0) or 0
    )
    if max_speaker_count <= 1 or multi_speaker_window_count <= 0:
        return False

    live_speaker_count = _count_distinct_live_reuse_speakers(live_segments_for_reuse)
    return max_speaker_count > live_speaker_count


def _build_final_diarization_plan_impl(
    *,
    live_segments_for_reuse: Sequence[dict],
    reused_live_transcript_segments: Sequence[dict],
    engine_override: dict | None,
    completed_window_replay_available: bool = False,
    completed_window_speaker_evidence: dict[str, int] | None = None,
) -> dict[str, object]:
    if engine_override:
        return {
            "should_run": True,
            "reason": "engine_override",
            "low_confidence_spans": [],
        }

    if not reused_live_transcript_segments or not live_segments_for_reuse:
        return {
            "should_run": True,
            "reason": "no_live_reuse",
            "low_confidence_spans": [],
        }

    low_confidence_spans = _collect_low_confidence_diarization_spans(
        live_segments_for_reuse
    )
    if low_confidence_spans:
        return {
            "should_run": True,
            "reason": "low_confidence_spans",
            "low_confidence_spans": low_confidence_spans,
            "completed_window_replay_available": bool(
                completed_window_replay_available
            ),
        }

    if _completed_window_speaker_evidence_requires_final_diarization(
        live_segments_for_reuse,
        completed_window_speaker_evidence,
    ):
        return {
            "should_run": True,
            "reason": "completed_window_speaker_mismatch",
            "low_confidence_spans": [],
            "completed_window_replay_available": bool(
                completed_window_replay_available
            ),
        }

    return {
        "should_run": False,
        "reason": "confident_live_reuse",
        "low_confidence_spans": [],
        "completed_window_replay_available": bool(completed_window_replay_available),
    }


def _build_catch_up_segments_impl(
    *,
    session,
    recording: Recording,
    processed_audio_path: str,
    merged_config: dict,
    transcribe_audio,
    extract_audio_clip,
    temp_files: list[str],
    log: logging.Logger,
) -> tuple[list[dict], set[int], ProcessingRun | None]:
    manifest_rows = _load_recording_audio_window_manifests(session, recording.id)
    chunk_rows = _load_recording_audio_chunks(session, recording.id)
    raw_pending_spans = collect_pending_chunk_spans(manifest_rows, chunk_rows)
    pending_manifest_rows = [
        row
        for row in manifest_rows
        if row.id is not None and not window_asr_is_processed(row)
    ]
    pending_window_ids = {int(row.id) for row in pending_manifest_rows}
    if not raw_pending_spans and not pending_window_ids:
        return [], set(), None

    span_start_ms = min(
        [int(row.window_start_ms) for row in pending_manifest_rows]
        or [span.start_ms for span in raw_pending_spans],
        default=0,
    )
    span_end_ms = max(
        [int(row.window_end_ms) for row in pending_manifest_rows]
        or [span.end_ms for span in raw_pending_spans],
        default=0,
    )
    catch_up_idempotency_parts = (
        ",".join(
            f"{span.start_sequence}-{span.end_sequence}" for span in raw_pending_spans
        )
        if raw_pending_spans
        else f"windows:{','.join(str(window_id) for window_id in sorted(pending_window_ids))}"
    )
    catch_up_run = ensure_processing_run(
        session,
        recording_id=recording.id,
        run_kind=ProcessingRunKind.CATCH_UP,
        status=ProcessingRunStatus.RUNNING,
        trigger_source="worker",
        transcription_backend=merged_config.get("transcription_backend"),
        span_start_ms=span_start_ms,
        span_end_ms=span_end_ms,
        idempotency_key=(
            "catch_up:"
            f"{recording.id}:"
            f"{_final_asr_config_hash(merged_config)}:"
            f"{catch_up_idempotency_parts}"
        ),
    )
    catch_up_run.status = ProcessingRunStatus.RUNNING
    catch_up_run.completed_at = None
    catch_up_run.error_summary = None
    session.add(catch_up_run)

    catch_up_segments: list[dict] = []
    status_counts = count_manifest_statuses(manifest_rows)
    ledger_enabled = bool(config_manager.get("enable_asr_window_result_ledger", True))
    pending_spans: list = []
    reused_span_count = 0
    reused_segment_count = 0
    legacy_payload_gap_count = 0

    for span in raw_pending_spans:
        existing_result = None
        reusable_segments = None
        if ledger_enabled:
            existing_result = get_recording_asr_window_result(
                session,
                recording_id=recording.id,
                source_kind="catch_up",
                span_start_ms=span.start_ms,
                span_end_ms=span.end_ms,
                chunk_start_sequence=span.start_sequence,
                chunk_end_sequence=span.end_sequence,
                config=merged_config,
                config_hash=_final_asr_config_hash(merged_config),
            )
            reusable_segments = get_reusable_catch_up_segments(existing_result)

        if reusable_segments is not None:
            reused_span_count += 1
            reused_segment_count += len(reusable_segments)
            catch_up_segments.extend(reusable_segments)
            continue

        if ledger_enabled and existing_result is not None:
            status_value = getattr(
                existing_result.status, "value", existing_result.status
            )
            if status_value == "completed":
                legacy_payload_gap_count += 1

        pending_spans.append(span)

    record_pipeline_metric(
        stage="catch_up_detected",
        recording_id=recording.id,
        payload={
            "pending_window_count": len(pending_window_ids),
            "pending_span_count": len(raw_pending_spans),
            "rerun_span_count": len(pending_spans),
            "reused_span_count": reused_span_count,
            "reused_segment_count": reused_segment_count,
            "legacy_payload_gap_count": legacy_payload_gap_count,
            "window_status_counts": status_counts,
        },
        log=log,
    )

    for span in pending_spans:
        clip_path = os.path.join(
            os.path.dirname(processed_audio_path),
            f"catch_up_{recording.id}_{span.start_sequence}_{span.end_sequence}.wav",
        )
        extract_audio_clip(
            processed_audio_path,
            clip_path,
            start_seconds=span.start_ms / 1000.0,
            end_seconds=span.end_ms / 1000.0,
        )
        temp_files.append(clip_path)

        with pipeline_metric_timer(
            stage="catch_up_asr_span",
            recording_id=recording.id,
            payload={
                "start_sequence": span.start_sequence,
                "end_sequence": span.end_sequence,
                "span_start_ms": span.start_ms,
                "span_end_ms": span.end_ms,
                "engine": merged_config.get("transcription_backend"),
            },
            log=log,
        ) as metric:
            if ledger_enabled:
                start_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    processing_run_id=catch_up_run.id if catch_up_run else None,
                    source_kind="catch_up",
                    span_start_ms=span.start_ms,
                    span_end_ms=span.end_ms,
                    chunk_start_sequence=span.start_sequence,
                    chunk_end_sequence=span.end_sequence,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                )
            try:
                result = transcribe_audio(clip_path, config=merged_config)
            except Exception as exc:
                if ledger_enabled:
                    fail_recording_asr_window_result(
                        session,
                        recording_id=recording.id,
                        processing_run_id=catch_up_run.id if catch_up_run else None,
                        source_kind="catch_up",
                        span_start_ms=span.start_ms,
                        span_end_ms=span.end_ms,
                        chunk_start_sequence=span.start_sequence,
                        chunk_end_sequence=span.end_sequence,
                        config=merged_config,
                        config_hash=_final_asr_config_hash(merged_config),
                        error_summary=str(exc).strip()[:500]
                        or "Catch-up ASR invocation failed.",
                        error_payload={"error_type": exc.__class__.__name__},
                    )
                raise
            metric["payload"]["segment_count"] = len((result or {}).get("segments", []))

        result_segments: list[dict] = []
        for segment in (result or {}).get("segments", []):
            text = str(segment.get("text", "")).strip()
            if not text:
                continue

            relative_start = float(segment.get("start", 0.0) or 0.0)
            relative_end = float(segment.get("end", 0.0) or 0.0)
            if relative_end <= relative_start:
                continue

            result_segments.append(
                {
                    "start": relative_start,
                    "end": relative_end,
                    "speaker": str(segment.get("speaker") or "UNKNOWN"),
                    "text": text,
                    "segment_source": "catch_up",
                }
            )
            catch_up_segments.append(
                {
                    "start": span.start_ms / 1000.0 + relative_start,
                    "end": span.start_ms / 1000.0 + relative_end,
                    "speaker": str(segment.get("speaker") or "UNKNOWN"),
                    "text": text,
                    "segment_source": "catch_up",
                }
            )

        if ledger_enabled:
            if result is None:
                fail_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    processing_run_id=catch_up_run.id if catch_up_run else None,
                    source_kind="catch_up",
                    span_start_ms=span.start_ms,
                    span_end_ms=span.end_ms,
                    chunk_start_sequence=span.start_sequence,
                    chunk_end_sequence=span.end_sequence,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                    error_summary="Catch-up ASR returned no result.",
                    error_payload={"error_type": "empty_result"},
                )
            else:
                complete_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    processing_run_id=catch_up_run.id if catch_up_run else None,
                    source_kind="catch_up",
                    span_start_ms=span.start_ms,
                    span_end_ms=span.end_ms,
                    chunk_start_sequence=span.start_sequence,
                    chunk_end_sequence=span.end_sequence,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                    result_payload={
                        "segment_count": len(result_segments),
                        "text_chars": len((result or {}).get("text") or ""),
                        "segments": result_segments,
                    },
                )

    catch_up_segments.sort(
        key=lambda segment: (
            float(segment.get("start", 0.0)),
            float(segment.get("end", 0.0)),
            str(segment.get("text", "")),
        )
    )

    return catch_up_segments, pending_window_ids, catch_up_run


def _recording_has_completed_diarization_windows_impl(
    session,
    *,
    recording_id: int,
    effective_from_ms: int = 0,
) -> bool:
    return (
        session.exec(
            select(DiarizationWindowResult)
            .where(DiarizationWindowResult.recording_id == recording_id)
            .where(DiarizationWindowResult.status == "completed")
            .where(DiarizationWindowResult.window_end_ms > int(effective_from_ms))
            .limit(1)
        ).first()
        is not None
    )


def _build_diarization_window_payload(
    diarization_result,
    *,
    window_start_ms: int,
    window_end_ms: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    turn_payloads: list[dict[str, object]] = []
    speaker_labels: set[str] = set()

    if diarization_result is not None and hasattr(diarization_result, "itertracks"):
        for segment, track, label in diarization_result.itertracks(yield_label=True):
            start_ms = window_start_ms + int(round(float(segment.start) * 1000.0))
            end_ms = window_start_ms + int(round(float(segment.end) * 1000.0))
            if end_ms <= start_ms:
                continue
            label_value = str(label)
            speaker_labels.add(label_value)
            turn_payloads.append(
                {
                    "local_speaker_key": label_value,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "track": str(track),
                }
            )

    turn_payloads.sort(
        key=lambda payload: (
            int(payload["start_ms"]),
            int(payload["end_ms"]),
            str(payload["local_speaker_key"]),
        )
    )
    return (
        {
            "window_start_ms": int(window_start_ms),
            "window_end_ms": int(window_end_ms),
            "speaker_labels": sorted(speaker_labels),
            "turn_count": len(turn_payloads),
            "turns": turn_payloads,
        },
        turn_payloads,
    )


def _catch_up_diarization_config_hash(merged_config: dict) -> str:
    return build_rolling_diarization_config_hash(
        merged_config,
        target_window_ms=int(
            merged_config.get("rolling_diarization_window_ms", 20_000)
        ),
        hop_ms=int(merged_config.get("rolling_diarization_hop_ms", 5_000)),
    )


def _persist_catch_up_diarization_window_impl(
    session,
    *,
    recording_id: int,
    manifest_row: RecordingAudioWindowManifest,
    processing_run_id: int | None,
    diarization_result,
    merged_config: dict,
    device: str,
    error_message: str | None = None,
) -> DiarizationWindowResult:
    return persist_diarization_window_result(
        session,
        recording_id=recording_id,
        manifest_row=manifest_row,
        processing_run_id=processing_run_id,
        diarization_result=diarization_result,
        config_hash=_catch_up_diarization_config_hash(merged_config),
        device=device,
        model_name=get_rolling_diarization_model_name(),
        error_message=error_message,
    )


def _run_catch_up_diarization_windows_impl(
    *,
    session,
    recording: Recording,
    processed_audio_path: str,
    merged_config: dict,
    diarize_audio,
    extract_audio_clip,
    processing_run_id: int | None,
    temp_files: list[str],
    log: logging.Logger,
) -> tuple[set[int], set[int]]:
    manifest_rows = _load_recording_audio_window_manifests(session, recording.id)
    config_hash = _catch_up_diarization_config_hash(merged_config)
    completed_window_indexes = {
        int(window_index)
        for window_index in session.exec(
            select(DiarizationWindowResult.window_index)
            .where(DiarizationWindowResult.recording_id == recording.id)
            .where(DiarizationWindowResult.config_hash == config_hash)
            .where(DiarizationWindowResult.status == "completed")
        ).all()
    }
    pending_manifest_rows = [
        row
        for row in manifest_rows
        if row.id is not None
        and window_asr_is_processed(row)
        and int(row.window_index) not in completed_window_indexes
        and not window_diarization_is_processed(
            row,
            config_hash=config_hash,
        )
    ]
    if not pending_manifest_rows:
        return set(), set()

    completed_window_ids: set[int] = set()
    failed_window_ids: set[int] = set()
    device = str(merged_config.get("processing_device", "auto"))

    for manifest_row in pending_manifest_rows:
        clip_path = os.path.join(
            os.path.dirname(processed_audio_path),
            f"catch_up_diarize_{recording.id}_{manifest_row.window_index}.wav",
        )
        extract_audio_clip(
            processed_audio_path,
            clip_path,
            start_seconds=float(manifest_row.window_start_ms) / 1000.0,
            end_seconds=float(manifest_row.window_end_ms) / 1000.0,
        )
        temp_files.append(clip_path)

        with pipeline_metric_timer(
            stage="catch_up_diarization_window",
            recording_id=recording.id,
            payload={
                "window_index": int(manifest_row.window_index),
                "window_start_ms": int(manifest_row.window_start_ms),
                "window_end_ms": int(manifest_row.window_end_ms),
                "chunk_start_sequence": int(manifest_row.chunk_start_sequence),
                "chunk_end_sequence": int(manifest_row.chunk_end_sequence),
            },
            log=log,
        ) as metric:
            diarization_result = diarize_audio(clip_path, config=merged_config)
            metric["payload"]["result_available"] = diarization_result is not None

        error_message = None
        if diarization_result is None:
            error_message = "Catch-up diarization returned no result"

        window_result = _persist_catch_up_diarization_window(
            session,
            recording_id=recording.id,
            manifest_row=manifest_row,
            processing_run_id=processing_run_id,
            diarization_result=diarization_result,
            merged_config=merged_config,
            device=device,
            error_message=error_message,
        )

        manifest_row.diarization_processing_run_id = processing_run_id
        manifest_row.diarization_config_hash = config_hash
        manifest_row.diarization_window_result_id = window_result.id
        manifest_row.processing_run_id = processing_run_id
        if error_message:
            manifest_row.diarization_status = WINDOW_DIARIZATION_STATUS_FAILED
            manifest_row.diarization_last_error = error_message
            manifest_row.status = WINDOW_STATUS_FAILED
            manifest_row.last_error = error_message
            failed_window_ids.add(int(manifest_row.id))
        else:
            manifest_row.diarization_status = WINDOW_DIARIZATION_STATUS_PROCESSED
            manifest_row.diarization_last_error = None
            manifest_row.last_error = None
            completed_window_ids.add(int(manifest_row.id))
        session.add(manifest_row)

    return completed_window_ids, failed_window_ids


__all__ = [name for name in globals() if not name.startswith("__")]
