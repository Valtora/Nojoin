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
from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource
from backend.models.chat import ChatMessage
from backend.models.context_chunk import ContextChunk
from backend.models.pipeline import (
    DiarizationWindowResult,
    ProcessingRun,
    RecordingAsrWindowResult,
    RecordingAudioChunk,
    RecordingAudioWindowManifest,
    SpeakerCorrectionEvent,
    TranscriptUtterance,
)
from backend.models.recording import (
    ClientStatus,
    Recording,
    RecordingPipelineGeneration,
    RecordingStatus,
)
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.services.recording_identity_service import get_recording_by_public_id
from backend.utils.recording_audio_sync import (
    BROWSER_AUDIO_SEGMENT_SUFFIXES,
    find_missing_chunk_sequences,
    find_pending_recording_upload_sequences,
    list_recording_audio_chunks,
    sync_recording_audio_chunks_from_directory,
    sync_recording_audio_chunks_from_entries,
    sync_recording_audio_window_manifests,
)
from backend.utils.recording_storage import (
    RECORDING_UPLOAD_RETENTION_HOURS,
)
from backend.utils.time import utc_now

from .constants import (
    LOSSY_AUDIO_SUFFIXES,
    SEGMENT_CONTENT_TYPE_SUFFIXES,
    STATUS_UPDATES_CLOSED_DETAIL,
    UNSUPPORTED_SEGMENT_MEDIA_DETAIL,
    UPLOAD_CLOSED_DETAIL,
)

logger = logging.getLogger(__name__)


def _recording_has_proxy(recording: Recording) -> bool:
    return bool(recording.proxy_path and os.path.exists(recording.proxy_path))


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


def _list_staged_browser_master_segments(recording_id: int) -> list[Path]:
    temp_dir = recordings_module.recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        return []

    segment_entries: list[tuple[int, Path]] = []
    for entry in temp_dir.iterdir():
        if not entry.is_file() or entry.suffix not in BROWSER_AUDIO_SEGMENT_SUFFIXES:
            continue

        try:
            sequence = int(entry.stem)
        except ValueError:
            continue

        segment_entries.append((sequence, entry))

    return [path for _, path in sorted(segment_entries)]


def _resolve_browser_master_suffix(segment_paths: list[Path]) -> str:
    suffixes = {path.suffix.lower() for path in segment_paths}
    if len(suffixes) != 1:
        raise HTTPException(
            status_code=400,
            detail="Browser capture segments use inconsistent container formats.",
        )

    return next(iter(suffixes))


def _browser_master_output_path(recording: Recording, master_suffix: str) -> str:
    base_path, _ = os.path.splitext(recording.audio_path)
    return f"{base_path}{master_suffix}"


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
    return recording.status in {
        RecordingStatus.PAUSED,
        RecordingStatus.UPLOADING,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
    }


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
    query = (
        select(Recording)
        .where(Recording.user_id == user_id)
        .where(
            Recording.status.in_([RecordingStatus.UPLOADING, RecordingStatus.PAUSED])
        )
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
    return {
        "code": "active_recording_exists",
        "message": "Handle the existing active recording before starting a new one.",
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
    return await db.run_sync(
        lambda session: sync_recording_audio_chunks_from_entries(
            session,
            recording_id=recording_id,
            source_kind=source_kind,
            disk_entries=disk_entries,
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
        except Exception as exc:  # noqa: BLE001
            failed_sequences.append(sequence)
            logger.warning(
                "Failed to transcode pending browser segment %s for recording %s during finalize: %s",
                sequence,
                recording_id,
                exc,
            )
    return failed_sequences


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
    """Delete generated meeting artefacts while preserving recording metadata and documents."""
    preserved_user_notes: Optional[str] = None

    transcript_result = await db.execute(
        select(Transcript).where(Transcript.recording_id == recording_id)
    )
    existing_transcript = transcript_result.scalar_one_or_none()
    if existing_transcript:
        preserved_user_notes = existing_transcript.user_notes

    await db.execute(
        delete(ChatMessage).where(ChatMessage.recording_id == recording_id)
    )
    await db.execute(
        delete(ContextChunk)
        .where(ContextChunk.recording_id == recording_id)
        .where(ContextChunk.document_id.is_(None))
    )
    await db.execute(
        delete(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
    )
    await db.execute(
        delete(ProcessingRun).where(ProcessingRun.recording_id == recording_id)
    )
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
    await db.execute(
        delete(DiarizationWindowResult).where(
            DiarizationWindowResult.recording_id == recording_id
        )
    )
    await db.execute(
        delete(RecordingAsrWindowResult).where(
            RecordingAsrWindowResult.recording_id == recording_id
        )
    )
    if existing_transcript:
        await db.delete(existing_transcript)
        await db.flush()

    if preserved_user_notes:
        db.add(Transcript(recording_id=recording_id, user_notes=preserved_user_notes))


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

    task = recordings_module.celery_app.send_task(
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
    if recording.status == RecordingStatus.PAUSED:
        raise HTTPException(
            status_code=409,
            detail="Recording is paused. Resume or discard it before finalizing.",
        )

    if recording.status != RecordingStatus.UPLOADING:
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
