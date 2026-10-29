"""Canonical utterance queries and processing-run bookkeeping."""

from .constants import *


def recording_ready_for_canonical_backfill(
    status: RecordingStatus | str | None,
) -> bool:
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


def list_utterances_for_processing_run(
    session, processing_run_id: int
) -> list[TranscriptUtterance]:
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
    state_value = (
        state_override.value
        if hasattr(state_override, "value")
        else str(state_override or "")
    )
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
        json.dumps(
            signature, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    return f"{run_kind.value}:{digest}"


__all__ = [
    "recording_ready_for_canonical_backfill",
    "list_active_utterances",
    "list_utterances_for_processing_run",
    "get_canonical_transcript_revision",
    "ensure_processing_run",
    "_processing_run_idempotency_key",
]
