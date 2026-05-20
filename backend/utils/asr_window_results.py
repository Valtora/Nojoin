from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Iterable

from sqlmodel import select

from backend.models.pipeline import RecordingAsrWindowResult, RecordingAsrWindowResultStatus
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)


def get_transcription_model_name(config: dict[str, Any] | None) -> str | None:
    normalized = dict(config or {})
    backend = str(normalized.get("transcription_backend") or "whisper")
    if backend == "parakeet":
        return str(normalized.get("parakeet_model") or "parakeet-tdt-0.6b-v3")
    if backend == "canary":
        return str(normalized.get("canary_model") or "nemo-canary-1b-v2")
    return str(normalized.get("whisper_model_size") or "turbo")


def build_recording_asr_window_result_config_hash(config: dict[str, Any] | None) -> str:
    normalized = dict(config or {})
    backend = str(normalized.get("transcription_backend") or "whisper")
    payload = {
        "transcription_backend": backend,
        "model_name": get_transcription_model_name(normalized),
        "processing_device": normalized.get("processing_device", "auto"),
        "use_gpu": bool(normalized.get("use_gpu", True)),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return digest


def build_recording_asr_window_result_idempotency_key(
    *,
    source_kind: str,
    span_start_ms: int,
    span_end_ms: int,
    chunk_start_sequence: int | None,
    chunk_end_sequence: int | None,
    transcription_backend: str,
    model_name: str | None,
    config_hash: str,
) -> str:
    payload = {
        "source_kind": str(source_kind),
        "span_start_ms": int(span_start_ms),
        "span_end_ms": int(span_end_ms),
        "chunk_start_sequence": None if chunk_start_sequence is None else int(chunk_start_sequence),
        "chunk_end_sequence": None if chunk_end_sequence is None else int(chunk_end_sequence),
        "transcription_backend": str(transcription_backend),
        "model_name": str(model_name or ""),
        "config_hash": str(config_hash),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return f"asr:{source_kind}:{digest}"


def _lookup_recording_asr_window_result(session, *, recording_id: int, idempotency_key: str) -> RecordingAsrWindowResult | None:
    if not hasattr(session, "exec"):
        return None

    try:
        return session.exec(
            select(RecordingAsrWindowResult)
            .where(RecordingAsrWindowResult.recording_id == recording_id)
            .where(RecordingAsrWindowResult.idempotency_key == idempotency_key)
        ).first()
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        logger.debug(
            "Skipping ASR window result lookup for recording %s idempotency %s",
            recording_id,
            idempotency_key,
            exc_info=True,
        )
        return None


def upsert_recording_asr_window_result(
    session,
    *,
    recording_id: int,
    source_kind: str,
    span_start_ms: int,
    span_end_ms: int,
    config: dict[str, Any] | None = None,
    chunk_start_sequence: int | None = None,
    chunk_end_sequence: int | None = None,
    processing_run_id: int | None = None,
    transcription_backend: str | None = None,
    model_name: str | None = None,
    config_hash: str | None = None,
    status: RecordingAsrWindowResultStatus = RecordingAsrWindowResultStatus.PENDING,
    idempotency_key: str | None = None,
    error_summary: str | None = None,
    error_payload: dict[str, Any] | None = None,
    result_payload: dict[str, Any] | None = None,
    produced_utterance_public_ids: Iterable[str] | None = None,
) -> RecordingAsrWindowResult | None:
    if not hasattr(session, "add"):
        return None

    normalized_config = dict(config or {})
    normalized_source = str(source_kind or "live")
    normalized_start = int(min(span_start_ms, span_end_ms))
    normalized_end = int(max(span_start_ms, span_end_ms))
    normalized_backend = str(transcription_backend or normalized_config.get("transcription_backend") or "whisper")
    normalized_model_name = model_name or get_transcription_model_name(normalized_config)
    normalized_config_hash = config_hash or build_recording_asr_window_result_config_hash(normalized_config)
    normalized_idempotency_key = idempotency_key or build_recording_asr_window_result_idempotency_key(
        source_kind=normalized_source,
        span_start_ms=normalized_start,
        span_end_ms=normalized_end,
        chunk_start_sequence=chunk_start_sequence,
        chunk_end_sequence=chunk_end_sequence,
        transcription_backend=normalized_backend,
        model_name=normalized_model_name,
        config_hash=normalized_config_hash,
    )

    existing_row = _lookup_recording_asr_window_result(
        session,
        recording_id=recording_id,
        idempotency_key=normalized_idempotency_key,
    )
    now = utc_now()

    row = existing_row or RecordingAsrWindowResult(
        recording_id=recording_id,
        source_kind=normalized_source,
        span_start_ms=normalized_start,
        span_end_ms=normalized_end,
        chunk_start_sequence=chunk_start_sequence,
        chunk_end_sequence=chunk_end_sequence,
        transcription_backend=normalized_backend,
        model_name=normalized_model_name,
        config_hash=normalized_config_hash,
        status=status,
        idempotency_key=normalized_idempotency_key,
    )

    row.processing_run_id = processing_run_id
    row.source_kind = normalized_source
    row.span_start_ms = normalized_start
    row.span_end_ms = normalized_end
    row.chunk_start_sequence = chunk_start_sequence
    row.chunk_end_sequence = chunk_end_sequence
    row.transcription_backend = normalized_backend
    row.model_name = normalized_model_name
    row.config_hash = normalized_config_hash
    row.status = status
    row.idempotency_key = normalized_idempotency_key

    if status in {RecordingAsrWindowResultStatus.PENDING, RecordingAsrWindowResultStatus.RUNNING}:
        row.started_at = row.started_at or now
        row.completed_at = None
        row.error_summary = None
        row.error_payload = None
    else:
        row.started_at = row.started_at or now
        row.completed_at = now
        if status == RecordingAsrWindowResultStatus.COMPLETED:
            row.error_summary = None
            row.error_payload = None
        else:
            row.error_summary = error_summary
            row.error_payload = error_payload

    if result_payload is not None:
        row.result_payload = result_payload
    if produced_utterance_public_ids is not None:
        row.produced_utterance_public_ids = list(produced_utterance_public_ids)

    try:
        session.add(row)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        logger.warning(
            "Failed to stage ASR window result for recording %s (%s)",
            recording_id,
            normalized_source,
            exc_info=True,
        )
        return None

    return row


def start_recording_asr_window_result(session, **kwargs) -> RecordingAsrWindowResult | None:
    return upsert_recording_asr_window_result(
        session,
        status=RecordingAsrWindowResultStatus.RUNNING,
        **kwargs,
    )


def complete_recording_asr_window_result(session, **kwargs) -> RecordingAsrWindowResult | None:
    return upsert_recording_asr_window_result(
        session,
        status=RecordingAsrWindowResultStatus.COMPLETED,
        **kwargs,
    )


def fail_recording_asr_window_result(session, **kwargs) -> RecordingAsrWindowResult | None:
    return upsert_recording_asr_window_result(
        session,
        status=RecordingAsrWindowResultStatus.FAILED,
        **kwargs,
    )