import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

import backend.api.v1.endpoints.recordings as recordings_module
from backend.core.task_dispatch import dispatch_task
from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource
from backend.models.chat import ChatMessage
from backend.models.context_chunk import ContextChunk
from backend.models.pipeline import (
    DiarizationWindowResult,
    ProcessingRun,
    RecordingAudioChunk,
    RecordingAudioWindowManifest,
    SpeakerCorrectionEvent,
    TranscriptUtterance,
)
from backend.models.recording import (
    IN_FLIGHT_TRANSCRIPT_STATUSES,
    ClientStatus,
    Recording,
    RecordingPipelineGeneration,
    RecordingStatus,
    capture_looks_stalled,
    live_capture_clause,
)
from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.transcript import Transcript
from backend.services.recording_identity_service import get_recording_by_public_id
from backend.utils.browser_capture_audio import BROWSER_MASTER_SUFFIX
from backend.utils.recording_audio_sync import (
    SEGMENT_CORRUPT_SUFFIX,
    STAGED_BROWSER_SEGMENT_SUFFIXES,
    TRANSCODE_FAILED_SUFFIX,
    build_recording_audio_chunk_fields,
    find_missing_chunk_sequences,
    find_pending_recording_upload_sequences,
    list_recording_audio_chunks,
    list_recording_upload_sequences,
    sync_recording_audio_chunks_from_directory,
    sync_recording_audio_chunks_from_entries,
    sync_recording_audio_window_manifests,
)
from backend.utils.recording_storage import (
    RECORDING_UPLOAD_RETENTION_HOURS,
)
from backend.utils.stored_paths import confined_recording_path
from backend.utils.time import utc_now
from backend.utils.transcript_change import change_sequence_bump
from backend.utils.transcript_settling import durable_settling_state

from .constants import (
    ACTIVE_RECORDING_CONFLICT_MESSAGE,
    CAPTURE_IN_PROGRESS_CONFLICT_MESSAGE,
    LOSSY_AUDIO_SUFFIXES,
    SEGMENT_CONTENT_TYPE_SUFFIXES,
    STATUS_UPDATES_CLOSED_DETAIL,
    UNSUPPORTED_SEGMENT_MEDIA_DETAIL,
    UPLOAD_CLOSED_DETAIL,
)

logger = logging.getLogger(__name__)


def _recording_has_proxy(recording: Recording) -> bool:
    proxy_path = confined_recording_path(recording.proxy_path)
    return bool(proxy_path and os.path.exists(proxy_path))


def _recording_has_audio(recording: Recording) -> bool:
    """Whether anything playable exists, or can be made, for this recording.

    An empty master, such as one left by an interrupted write, cannot make a
    proxy, so it does not count as audio.
    """
    if _recording_has_proxy(recording):
        return True
    audio_path = confined_recording_path(recording.audio_path)
    if not audio_path:
        return False
    try:
        return os.path.getsize(audio_path) > 0
    except OSError:
        return False


async def revoke_recording_task(recording: Recording) -> None:
    """Stop the recording's Celery task before its row and files go.

    ``terminate=True`` ends a task that is already running; a queued one is
    dropped. The revoke is a broker publish, so it runs off the event loop, and
    one that cannot reach the broker does not stop the delete.
    """
    if not recording.celery_task_id:
        return
    try:
        await asyncio.to_thread(
            recordings_module.celery_app.control.revoke,
            recording.celery_task_id,
            terminate=True,
        )
    except Exception:  # noqa: BLE001 -- boundary: a revoke that cannot reach the broker must not block the delete
        logger.warning(
            "Could not revoke task %s of recording %s before deleting it",
            recording.celery_task_id,
            recording.public_id,
            exc_info=True,
        )


def _estimated_audio_bitrate_bits_per_second(
    audio_info: dict[str, Any] | None,
) -> int | None:
    if not audio_info:
        return None

    bitrate = audio_info.get("bitrate")
    if isinstance(bitrate, int) and bitrate > 0:
        return bitrate

    size = audio_info.get("size")
    duration = audio_info.get("duration")
    if (
        isinstance(size, int)
        and size > 0
        and isinstance(duration, (int, float))
        and duration > 0
    ):
        return int((size * 8) / float(duration))

    return None


def _enforce_lossy_audio_bitrate_floor(file_path: str) -> None:
    suffix = Path(file_path).suffix.lower()
    if suffix not in LOSSY_AUDIO_SUFFIXES:
        return

    from backend.processing.audio_preprocessing import analyze_audio_file

    audio_info = analyze_audio_file(file_path)
    bitrate = _estimated_audio_bitrate_bits_per_second(audio_info)
    if bitrate is None:
        raise HTTPException(
            status_code=422,
            detail="Could not verify bitrate for this lossy recording.",
        )

    from backend.utils.audio import LOSSY_AUDIO_BITRATE_FLOOR_BITS_PER_SECOND

    if bitrate < LOSSY_AUDIO_BITRATE_FLOOR_BITS_PER_SECOND:
        raise HTTPException(
            status_code=422,
            detail=(
                "Lossy recordings below 128 kbps are not supported "
                f"(detected approximately {bitrate // 1000} kbps)."
            ),
        )


def _browser_master_output_path(recording: Recording) -> str:
    base_path, _ = os.path.splitext(recording.audio_path)
    return f"{base_path}{BROWSER_MASTER_SUFFIX}"


async def _mark_recording_upload_error(
    db: AsyncSession,
    recording: Recording,
    detail: str,
) -> None:
    recording.status = RecordingStatus.ERROR
    recording.client_status = ClientStatus.IDLE
    recording.processing_step = detail[:255]
    db.add(recording)
    await db.commit()
    await db.refresh(recording)


def _should_hide_in_flight_transcript_content(recording: Recording) -> bool:
    return recording.status in IN_FLIGHT_TRANSCRIPT_STATUSES


async def _get_owned_recording(
    db: AsyncSession,
    recording_public_id: str,
    user_id: int,
    *,
    options: tuple | None = None,
) -> Recording:
    recording = await get_recording_by_public_id(
        db,
        recording_public_id,
        user_id=user_id,
        options=options,
    )
    return _assert_recording_owner(recording, user_id)


def _assert_recording_owner(recording: Recording | None, user_id: int) -> Recording:
    if recording is None or recording.user_id != user_id:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


async def _get_active_capture_recording_for_user(
    db: AsyncSession,
    user_id: int,
) -> Recording | None:
    """The user's unfinished live capture, which holds the capture lock.

    A file import also sits in UPLOADING while its parts arrive, and one whose
    page closed stays there, but it is not a capture and must not block one
    (issue #569).
    """
    query = (
        select(Recording)
        .where(Recording.user_id == user_id)
        .where(
            Recording.status.in_([RecordingStatus.UPLOADING, RecordingStatus.PAUSED])
        )
        .where(live_capture_clause())
        .where(Recording.is_deleted == False)
        .order_by(Recording.updated_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().first()


def _get_last_uploaded_sequence(recording_id: int) -> int:
    temp_dir = recordings_module.recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        return -1

    last_sequence = -1
    for entry in temp_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            sequence = int(entry.stem)
        except ValueError:
            continue
        last_sequence = max(last_sequence, sequence)
    return last_sequence


def _build_active_recording_conflict(recording: Recording) -> dict[str, str]:
    message = ACTIVE_RECORDING_CONFLICT_MESSAGE
    # A capture still uploading is not offered anywhere yet: it may be running
    # in another tab or on another device, or its tab may have closed moments
    # ago without pausing it. Say which wait applies (issue #519).
    if (
        recording.status == RecordingStatus.UPLOADING
        and recording.client_status == ClientStatus.RECORDING
        and not capture_looks_stalled(recording)
    ):
        message = CAPTURE_IN_PROGRESS_CONFLICT_MESSAGE
    return {
        "code": "active_recording_exists",
        "message": message,
        "recording_id": recording.public_id,
        "status": recording.status.value,
    }


def get_initial_proxy_path(file_path: str) -> Optional[str]:
    _, file_ext = os.path.splitext(file_path)
    if file_ext.lower() == ".mp3":
        return file_path
    return None


def _chunk_cleanup_deadline() -> datetime:
    return utc_now() + timedelta(hours=RECORDING_UPLOAD_RETENTION_HOURS)


def _chunk_idempotency_key(*, source_kind: str, sequence: int, sha256: str) -> str:
    return f"{source_kind}:{sequence}:{sha256}"


def _normalize_segment_content_type(content_type: str | None) -> str:
    return str(content_type or "").split(";", 1)[0].strip().lower()


def _resolve_segment_upload_suffix(file: UploadFile) -> str:
    content_type = _normalize_segment_content_type(file.content_type)
    filename_suffix = Path(file.filename or "").suffix.lower()
    expected_suffix = SEGMENT_CONTENT_TYPE_SUFFIXES.get(content_type)
    if expected_suffix is None or filename_suffix != expected_suffix:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=UNSUPPORTED_SEGMENT_MEDIA_DETAIL,
        )
    return expected_suffix


async def _sync_recording_audio_chunks_from_entries(
    db: AsyncSession,
    *,
    recording_id: int,
    source_kind: str,
    disk_entries: list[tuple[int, Path]],
) -> list[RecordingAudioChunk]:
    # Hashing a whole imported file takes seconds for a video, and run_sync
    # holds the event loop, so the fields are built on a worker thread first.
    prebuilt_fields = await asyncio.to_thread(
        lambda: {
            sequence: build_recording_audio_chunk_fields(
                sequence=sequence,
                source_kind=source_kind,
                storage_path=storage_path,
            )
            for sequence, storage_path in disk_entries
        }
    )
    return await db.run_sync(
        lambda session: sync_recording_audio_chunks_from_entries(
            session,
            recording_id=recording_id,
            source_kind=source_kind,
            disk_entries=disk_entries,
            prebuilt_fields=prebuilt_fields,
        )
    )


async def _sync_recording_audio_chunks_from_directory(
    db: AsyncSession,
    *,
    recording_id: int,
    source_kind: str,
    suffix: str,
) -> list[RecordingAudioChunk]:
    temp_dir = recordings_module.recording_upload_temp_dir(recording_id, create=False)
    return await db.run_sync(
        lambda session: sync_recording_audio_chunks_from_directory(
            session,
            recording_id=recording_id,
            source_kind=source_kind,
            suffix=suffix,
            temp_dir=temp_dir,
        )
    )


def _stage_import_audio_chunk(
    *,
    recording_id: int,
    audio_path: str,
    sequence: int = 0,
) -> Path:
    source_path = Path(audio_path)
    suffix = source_path.suffix or ".bin"
    staged_path = (
        recordings_module.recording_upload_temp_dir(recording_id, create=True)
        / f"{sequence}{suffix}"
    )

    if staged_path.exists():
        try:
            if staged_path.samefile(source_path):
                return staged_path
        except OSError:
            pass
        staged_path.unlink()

    try:
        os.link(source_path, staged_path)
    except OSError:
        shutil.copy2(source_path, staged_path)

    return staged_path


async def _bootstrap_import_audio_windows(
    db: AsyncSession,
    *,
    recording_id: int,
    audio_path: str,
) -> list[RecordingAudioWindowManifest]:
    await db.execute(
        delete(RecordingAudioChunk)
        .where(RecordingAudioChunk.recording_id == recording_id)
        .where(RecordingAudioChunk.source_kind == "import")
    )
    await db.execute(
        delete(RecordingAudioWindowManifest)
        .where(RecordingAudioWindowManifest.recording_id == recording_id)
        .where(RecordingAudioWindowManifest.source_kind == "import")
    )

    staged_path = _stage_import_audio_chunk(
        recording_id=recording_id,
        audio_path=audio_path,
    )
    await _sync_recording_audio_chunks_from_entries(
        db,
        recording_id=recording_id,
        source_kind="import",
        disk_entries=[(0, staged_path)],
    )
    return await _sync_recording_audio_window_manifests(
        db,
        recording_id=recording_id,
        source_kind="import",
        seal_tail=True,
    )


async def _list_recording_audio_chunks(
    db: AsyncSession,
    recording_id: int,
    source_kind: str | None = None,
) -> list[RecordingAudioChunk]:
    return await db.run_sync(
        lambda session: list_recording_audio_chunks(
            session,
            recording_id,
            source_kind=source_kind,
        )
    )


async def _sync_recording_audio_window_manifests(
    db: AsyncSession,
    *,
    recording_id: int,
    source_kind: str,
    seal_tail: bool,
) -> list[RecordingAudioWindowManifest]:
    return await db.run_sync(
        lambda session: sync_recording_audio_window_manifests(
            session,
            recording_id=recording_id,
            source_kind=source_kind,
            seal_tail=seal_tail,
        )
    )


def _find_missing_chunk_sequences(chunk_rows: list[RecordingAudioChunk]) -> list[int]:
    return find_missing_chunk_sequences(chunk_rows)


def _find_pending_transcode_sequences(
    recording_id: int,
    *,
    chunk_rows: list[RecordingAudioChunk],
) -> list[int]:
    temp_dir = recordings_module.recording_upload_temp_dir(recording_id, create=False)
    return find_pending_recording_upload_sequences(
        recording_id,
        chunk_rows=chunk_rows,
        temp_dir=temp_dir,
    )


async def _transcode_pending_browser_segments_for_finalize(
    recording_id: int,
    pending_sequences: list[int],
) -> list[int]:
    failed_sequences: list[int] = []
    for sequence in pending_sequences:
        try:
            from backend.processing.segment_transcode import (
                transcode_staged_browser_segment,
            )

            await asyncio.to_thread(
                transcode_staged_browser_segment, recording_id, sequence
            )
        except Exception as exc:  # noqa: BLE001 -- boundary: one segment failing is recorded; finalise decides what to do with the rest
            failed_sequences.append(sequence)
            logger.warning(
                "Failed to transcode pending browser segment %s for recording %s during finalize: %s",
                sequence,
                recording_id,
                exc,
            )
    return failed_sequences


async def _rescue_pending_browser_segments_for_finalize(
    db: AsyncSession,
    *,
    recording_id: int,
    pending_sequences: list[int],
) -> tuple[list[RecordingAudioChunk], list[int]]:
    """Rescue-transcode pending segments and quarantine terminally corrupt ones.

    Returns the refreshed chunk rows and the sequences that are still pending
    after the rescue attempt (that is, genuinely still uploading).
    """
    failed_sequences = await _transcode_pending_browser_segments_for_finalize(
        recording_id,
        pending_sequences,
    )
    await _sync_recording_audio_chunks_from_directory(
        db,
        recording_id=recording_id,
        source_kind="browser",
        suffix=".wav",
    )
    await _sync_recording_audio_window_manifests(
        db,
        recording_id=recording_id,
        source_kind="browser",
        seal_tail=True,
    )
    chunk_rows = await _list_recording_audio_chunks(
        db,
        recording_id=recording_id,
        source_kind="browser",
    )
    still_pending = _find_pending_transcode_sequences(
        recording_id,
        chunk_rows=chunk_rows,
    )

    # The rescue transcode is the last chance for a staged segment: a sequence
    # that still failed here is terminally corrupt (for example a truncated
    # body from an interrupted upload). Quarantine it so finalize proceeds
    # with an audio gap instead of returning 409 forever.
    still_failed_sequences = [
        sequence for sequence in failed_sequences if sequence in set(still_pending)
    ]
    if still_failed_sequences:
        quarantined_sequences = set(
            _quarantine_corrupt_browser_segments(recording_id, still_failed_sequences)
        )
        still_pending = [
            sequence
            for sequence in still_pending
            if sequence not in quarantined_sequences
        ]
    return chunk_rows, still_pending


def _quarantine_corrupt_browser_segments(
    recording_id: int,
    sequences: list[int],
) -> list[int]:
    """Retire staged segments whose finalize rescue transcode failed.

    The corrupt payload is renamed to a durable ``{sequence}.corrupt``
    tombstone (or the tombstone records the failure reason when the payload is
    already gone) so subsequent finalize attempts treat the sequence as
    intentionally skipped rather than pending or missing.
    """
    temp_dir = recordings_module.recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        return []

    quarantined: list[int] = []
    for sequence in sequences:
        marker_path = temp_dir / f"{sequence}{TRANSCODE_FAILED_SUFFIX}"
        failure_reason = ""
        if marker_path.exists():
            try:
                failure_reason = marker_path.read_text(encoding="utf-8").strip()
            except OSError:
                failure_reason = ""

        corrupt_path = temp_dir / f"{sequence}{SEGMENT_CORRUPT_SUFFIX}"
        raw_path = None
        for suffix in sorted(STAGED_BROWSER_SEGMENT_SUFFIXES):
            candidate = temp_dir / f"{sequence}{suffix}"
            if candidate.exists():
                raw_path = candidate
                break

        try:
            if raw_path is not None:
                os.replace(raw_path, corrupt_path)
            elif not corrupt_path.exists():
                corrupt_path.write_text(f"{failure_reason}\n", encoding="utf-8")
            marker_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "Failed to quarantine corrupt browser segment %s for recording %s: %s",
                sequence,
                recording_id,
                exc,
            )
            continue

        quarantined.append(sequence)
        logger.warning(
            "Skipping corrupt browser segment %s for recording %s during finalize; "
            "the final audio will have a gap.",
            sequence,
            recording_id,
        )
        try:
            recordings_module.record_pipeline_metric(
                stage="segment_corrupt_skipped",
                recording_id=recording_id,
                payload={
                    "sequence": sequence,
                    "quarantined_path": str(corrupt_path),
                    "error": failure_reason,
                },
                log=logger,
            )
        except Exception as exc:  # noqa: BLE001 -- boundary: metrics must not block finalize
            logger.warning(
                "Failed to record corrupt segment metric for recording %s segment %s: %s",
                recording_id,
                sequence,
                exc,
            )
    return quarantined


def _list_corrupt_browser_segment_sequences(recording_id: int) -> set[int]:
    temp_dir = recordings_module.recording_upload_temp_dir(recording_id, create=False)
    return list_recording_upload_sequences(
        recording_id,
        suffixes={SEGMENT_CORRUPT_SUFFIX},
        temp_dir=temp_dir,
    )


async def _mark_recording_audio_chunks_ready_for_cleanup(
    db: AsyncSession,
    *,
    chunk_rows: list[RecordingAudioChunk],
    upload_status: str,
) -> None:
    deadline = _chunk_cleanup_deadline()
    for row in chunk_rows:
        row.upload_status = upload_status
        row.cleanup_eligible_at = deadline
        db.add(row)


async def _mark_recording_audio_chunks_failed(
    db: AsyncSession,
    *,
    recording_id: int,
    failed_root: Path | None,
) -> None:
    rows = (
        (
            await db.execute(
                select(RecordingAudioChunk).where(
                    RecordingAudioChunk.recording_id == recording_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return

    deadline = _chunk_cleanup_deadline()
    for row in rows:
        if failed_root is not None:
            row.storage_path = str(failed_root / Path(row.storage_path).name)
        row.upload_status = "failed"
        row.cleanup_eligible_at = deadline
        db.add(row)


async def _reset_generated_recording_state(db: AsyncSession, recording_id: int) -> None:
    """Delete generated meeting artefacts while preserving recording metadata and documents.

    The user's timeline notes hang off the recording, not the transcript, so
    they survive untouched.
    """
    transcript_result = await db.execute(
        select(Transcript).where(Transcript.recording_id == recording_id)
    )
    existing_transcript = transcript_result.scalar_one_or_none()
    preserve_live_work = bool(
        existing_transcript is not None
        and (existing_transcript.settling_state or {}).get("settled") is True
    )
    if preserve_live_work:
        from backend.utils.canonical_pipeline import (
            ensure_canonical_backfill,
            refresh_transcript_projection_from_canonical,
        )
        from backend.utils.speaker_names.durability import persist_name_anchors

        await db.run_sync(lambda session: persist_name_anchors(session, recording_id))
        # Restore may have queued, but not yet run, its derived-state backfill.
        # Rebuild archived protected lines before replacing their projection.
        await db.run_sync(
            lambda session: ensure_canonical_backfill(session, recording_id)
        )
        await db.run_sync(
            lambda session: refresh_transcript_projection_from_canonical(
                session, recording_id
            )
        )

    await db.execute(
        delete(ChatMessage).where(ChatMessage.recording_id == recording_id)
    )
    await db.execute(
        delete(ContextChunk)
        .where(ContextChunk.recording_id == recording_id)
        .where(ContextChunk.document_id.is_(None))
    )
    if not preserve_live_work:
        await db.execute(
            delete(RecordingSpeaker).where(
                RecordingSpeaker.recording_id == recording_id
            )
        )
    await db.execute(
        delete(ProcessingRun).where(ProcessingRun.recording_id == recording_id)
    )
    if not preserve_live_work:
        await db.execute(
            delete(TranscriptUtterance).where(
                TranscriptUtterance.recording_id == recording_id
            )
        )
    await db.execute(
        delete(SpeakerCorrectionEvent).where(
            SpeakerCorrectionEvent.recording_id == recording_id
        )
    )
    if not preserve_live_work:
        await db.execute(
            delete(SpeakerNameEvent).where(
                SpeakerNameEvent.recording_id == recording_id
            )
        )
    await db.execute(
        delete(DiarizationWindowResult).where(
            DiarizationWindowResult.recording_id == recording_id
        )
    )
    if existing_transcript is not None:
        # Reset in place, so retained settled lines keep their transcript. All
        # generated products return to the defaults a replacement row used to get.
        defaults = Transcript(recording_id=recording_id)
        defaults.settling_state = durable_settling_state(
            existing_transcript.settling_state
        )
        retained = {"id", "recording_id", "created_at", "updated_at", "change_sequence"}
        if preserve_live_work:
            retained.update({"text", "segments"})
        for field in Transcript.model_fields:
            if field not in retained:
                setattr(existing_transcript, field, getattr(defaults, field))
        db.add(existing_transcript)
        await db.execute(change_sequence_bump(recording_id))
        await db.flush()


async def _requeue_for_processing(
    db: AsyncSession,
    recording: Recording,
    *,
    engine_override: dict | None = None,
    queued_step: str = "Queued for processing...",
) -> None:
    """Reset generated state and re-dispatch the processing pipeline."""
    await _reset_generated_recording_state(db, recording.id)

    recording.status = RecordingStatus.QUEUED
    recording.client_status = ClientStatus.IDLE
    recording.processing_progress = 0
    recording.processing_step = queued_step
    recording.processing_started_at = None
    recording.processing_completed_at = None
    recording.celery_task_id = None
    recording.pipeline_generation = RecordingPipelineGeneration.UNIFIED.value
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    task = await dispatch_task(
        "backend.worker.tasks.process_recording_task",
        args=[recording.id, True, engine_override],
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, recording.user_id)


def get_ordinal_suffix(day: int) -> str:
    if 11 <= day <= 13:
        return "th"
    else:
        return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _ensure_recording_accepts_uploads(recording: Recording) -> None:
    # A pause stops new capture, but the browser may still be flushing the
    # in-memory tail that was already recorded. Keep accepting uploads in the
    # paused state so that late-arriving tail segments are preserved.
    if recording.status not in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        raise HTTPException(
            status_code=409,
            detail=UPLOAD_CLOSED_DETAIL,
        )


def _ensure_recording_accepts_status_updates(recording: Recording) -> None:
    if recording.status not in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        raise HTTPException(
            status_code=409,
            detail=STATUS_UPDATES_CLOSED_DETAIL,
        )


def _ensure_recording_can_finalize_upload(recording: Recording) -> None:
    # PAUSED is accepted so a capture whose browser runtime is gone can still be
    # finalized without re-acquiring the share picker (issue #166). Tail
    # truncation stays guarded downstream: finalize refuses missing sequences
    # and pending transcodes with a retryable 409, and uploads are accepted
    # while paused.
    if recording.status not in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        raise HTTPException(
            status_code=409,
            detail=UPLOAD_CLOSED_DETAIL,
        )


def generate_default_meeting_name() -> str:
    now = datetime.now()
    day_name = now.strftime("%A")
    day_num = now.day
    suffix = get_ordinal_suffix(day_num)
    short_month = now.strftime("%b")
    hour = now.hour

    time_of_day = ""

    if 5 <= hour < 12:
        if hour < 8:
            time_of_day = "Early Morning"
        elif hour >= 10:
            time_of_day = "Late Morning"
        else:
            time_of_day = "Morning"
    elif 12 <= hour < 17:
        if hour < 14:
            time_of_day = "Early Afternoon"
        elif hour >= 16:
            time_of_day = "Late Afternoon"
        else:
            time_of_day = "Afternoon"
    elif 17 <= hour < 21:
        if hour < 18:
            time_of_day = "Early Evening"
        elif hour >= 20:
            time_of_day = "Late Evening"
        else:
            time_of_day = "Evening"
    else:
        if 21 <= hour < 24:
            time_of_day = "Night"
        else:
            time_of_day = "Late Night"

    return f"{day_name} {day_num}{suffix} {short_month}, {time_of_day} Meeting"


async def _get_owned_calendar_event(
    db: AsyncSession,
    calendar_event_id: int,
    user_id: int,
) -> CalendarEvent:
    statement = (
        select(CalendarEvent)
        .join(CalendarSource, CalendarEvent.calendar_id == CalendarSource.id)
        .join(CalendarConnection, CalendarSource.connection_id == CalendarConnection.id)
        .where(
            CalendarEvent.id == calendar_event_id,
            CalendarConnection.user_id == user_id,
        )
    )
    event = (await db.execute(statement)).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Calendar event not found")
    return event
