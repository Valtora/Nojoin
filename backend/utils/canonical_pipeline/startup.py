from .constants import *


def ensure_canonical_backfill(
    session,
    recording_id: int,
    *,
    force: bool = False,
) -> list[TranscriptUtterance]:
    from .core import (
        list_active_utterances,
        recording_ready_for_canonical_backfill,
        replace_utterances_from_segments,
    )

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


def list_pending_startup_cutover_recording_ids(
    session,
    *,
    batch_size: int = 100,
) -> list[int]:
    statement = (
        select(Recording.id)
        .where(Recording.pipeline_generation.is_(None))
        .order_by(Recording.id)
        .limit(max(int(batch_size), 1))
    )
    return [
        int(recording_id) for recording_id in session.execute(statement).scalars().all()
    ]


def _set_legacy_recording_generation(
    session,
    *,
    recording: Recording,
    generation: RecordingPipelineGeneration,
    reason: str | None = None,
) -> None:
    recording.pipeline_generation = generation.value

    if generation == RecordingPipelineGeneration.LEGACY_REPROCESS_REQUIRED:
        if recording.status in {
            RecordingStatus.UPLOADING,
            RecordingStatus.QUEUED,
            RecordingStatus.PROCESSING,
        }:
            recording.status = RecordingStatus.ERROR
            recording.processing_progress = 0
            recording.celery_task_id = None
        if reason:
            recording.processing_step = reason[:255]

    session.add(recording)


def process_startup_cutover_recording(
    session,
    *,
    recording_id: int,
) -> str:
    from .core import (
        _normalize_transcript_segments,
        list_active_utterances,
        recording_ready_for_canonical_backfill,
        refresh_transcript_projection_from_canonical,
        replace_utterances_from_segments,
    )

    recording = session.get(Recording, recording_id)
    if recording is None:
        return "missing"

    generation = str(getattr(recording, "pipeline_generation", "") or "")
    if generation == RecordingPipelineGeneration.UNIFIED.value:
        return "skipped_unified"
    if generation == RecordingPipelineGeneration.LEGACY_REPROCESS_REQUIRED.value:
        return "already_reprocess_required"
    if generation == RecordingPipelineGeneration.LEGACY_BACKFILLED.value:
        return "already_backfilled"

    transcript = _load_transcript(session, recording_id)
    active_utterances = list_active_utterances(session, recording_id)
    if active_utterances:
        refresh_transcript_projection_from_canonical(session, recording_id)
        _set_legacy_recording_generation(
            session,
            recording=recording,
            generation=RecordingPipelineGeneration.LEGACY_BACKFILLED,
        )
        return "already_canonical"

    if not recording_ready_for_canonical_backfill(recording.status):
        _set_legacy_recording_generation(
            session,
            recording=recording,
            generation=RecordingPipelineGeneration.LEGACY_REPROCESS_REQUIRED,
            reason="Legacy recording requires reprocess after upgrade",
        )
        return "classified_inflight"

    legacy_segments = _normalize_transcript_segments(
        getattr(transcript, "segments", None) if transcript is not None else None
    )
    if not transcript or not legacy_segments:
        _set_legacy_recording_generation(
            session,
            recording=recording,
            generation=RecordingPipelineGeneration.LEGACY_REPROCESS_REQUIRED,
            reason="Legacy recording requires reprocess after upgrade",
        )
        return "classified_missing_transcript"

    try:
        replace_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[dict(segment) for segment in legacy_segments],
            run_kind=ProcessingRunKind.BACKFILL,
            source="startup_cutover",
            force=True,
            trigger_source="migration",
        )
    except Exception as exc:
        from sqlalchemy.exc import SQLAlchemyError

        if isinstance(exc, SQLAlchemyError):
            raise
        _set_legacy_recording_generation(
            session,
            recording=recording,
            generation=RecordingPipelineGeneration.LEGACY_REPROCESS_REQUIRED,
            reason=f"Legacy recording requires reprocess after upgrade: {type(exc).__name__}",
        )
        return "classified_exception"

    _set_legacy_recording_generation(
        session,
        recording=recording,
        generation=RecordingPipelineGeneration.LEGACY_BACKFILLED,
    )
    return "backfilled"


__all__ = [name for name in globals() if not name.startswith("__")]
