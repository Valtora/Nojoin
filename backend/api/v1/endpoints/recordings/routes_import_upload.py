import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import aiofiles
from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

import backend.api.v1.endpoints.recordings as recordings_module
from backend.api.deps import get_current_user, get_db
from backend.api.error_handling import sanitized_http_exception
from backend.models.pipeline import RecordingAudioChunk, RecordingAudioWindowManifest
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.models.recording_public import RecordingPublicRead, serialize_recording
from backend.models.user import User
from backend.utils.audio import concatenate_binary_files
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.upload_limit import (
    UPLOAD_LIMIT_LEGACY_RECORDING,
    stream_and_validate_upload,
)

from .helpers import (
    _bootstrap_import_audio_windows,
    _find_missing_chunk_sequences,
    _get_owned_recording,
    _mark_recording_audio_chunks_failed,
    _mark_recording_upload_error,
    _recording_has_proxy,
    _sync_recording_audio_chunks_from_directory,
    generate_default_meeting_name,
    get_initial_proxy_path,
)
from .router import router

logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_FORMATS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".webm",
    ".ogg",
    ".flac",
    ".mp4",
    ".wma",
    ".opus",
}


@router.post("/import", response_model=RecordingPublicRead)
async def import_audio(
    file: UploadFile = File(...),
    name: Optional[str] = Query(None, description="Custom name for the recording"),
    recorded_at: Optional[datetime] = Query(
        None, description="Original recording timestamp"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Import an external audio recording (e.g., from Zoom, Teams, Google Meet).
    Supports: WAV, MP3, M4A, AAC, WebM, OGG, FLAC, MP4, WMA, Opus.
    """
    # Validate file extension
    file_ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    if file_ext not in SUPPORTED_AUDIO_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{file_ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}",
        )

    # Generate a unique filename to prevent collisions
    unique_filename = f"{uuid4()}{file_ext}"
    file_path = str(recordings_module.recordings_root_dir() / unique_filename)

    # Save the file
    try:
        async with aiofiles.open(file_path, "wb") as out_file:
            while chunk := await file.read(1024 * 1024):  # Read in 1MB chunks
                await out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        if os.path.exists(file_path):
            os.remove(file_path)
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to save the uploaded recording.",
            log_message=f"Failed to persist imported audio '{file.filename}'.",
            exc=e,
        )

    # Get file stats
    file_stats = os.stat(file_path)

    # Get duration
    duration = 0.0
    try:
        duration = get_audio_duration(file_path)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to get duration: {e}")

    # Determine recording name
    if name:
        recording_name = name
    else:
        recording_name = os.path.splitext(file.filename)[0] if file.filename else ""
        if not recording_name or recording_name == "blob":
            recording_name = generate_default_meeting_name()

    recording = Recording(
        name=recording_name,
        proxy_path=get_initial_proxy_path(file_path),
        audio_path=file_path,
        file_size_bytes=file_stats.st_size,
        duration_seconds=duration,
        status=RecordingStatus.QUEUED,
        user_id=current_user.id,
    )

    # Override created_at if recorded_at is provided
    if recorded_at:
        # Ensure naive UTC datetime for database compatibility
        if recorded_at.tzinfo is not None:
            recorded_at = recorded_at.astimezone(timezone.utc).replace(tzinfo=None)
        recording.created_at = recorded_at

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    await _bootstrap_import_audio_windows(
        db,
        recording_id=recording.id,
        audio_path=file_path,
    )
    await db.commit()

    # Trigger processing task
    task = recordings_module.celery_app.send_task(
        "backend.worker.tasks.process_recording_task", args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, recording.user_id)

    # Trigger proxy generation task
    if not recording.proxy_path:
        proxy_task = recordings_module.celery_app.send_task(
            "backend.worker.tasks.generate_proxy_task", args=[recording.id]
        )
        if proxy_task:
            await register_task_ownership(db, proxy_task.id, recording.user_id)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/import/chunked/init", response_model=RecordingPublicRead)
async def init_chunked_import(
    filename: str = Query(..., description="Original filename with extension"),
    name: Optional[str] = Query(None, description="Custom name for the recording"),
    recorded_at: Optional[datetime] = Query(
        None, description="Original recording timestamp"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Initialize a chunked import for large files.
    """
    # Validate file extension
    file_ext = os.path.splitext(filename)[1].lower()
    if file_ext not in SUPPORTED_AUDIO_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{file_ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}",
        )

    # Generate a unique filename
    unique_filename = f"{uuid4()}{file_ext}"
    file_path = str(recordings_module.recordings_root_dir() / unique_filename)

    # Determine recording name
    if name:
        recording_name = name
    else:
        recording_name = os.path.splitext(filename)[0]
        if not recording_name:
            recording_name = generate_default_meeting_name()

    recording = Recording(
        name=recording_name,
        proxy_path=get_initial_proxy_path(file_path),
        audio_path=file_path,
        status=RecordingStatus.UPLOADING,
        user_id=current_user.id,
    )

    # Override created_at if recorded_at is provided
    if recorded_at:
        if recorded_at.tzinfo is not None:
            recorded_at = recorded_at.astimezone(timezone.utc).replace(tzinfo=None)
        recording.created_at = recorded_at

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    recordings_module.recording_upload_temp_dir(recording.id, create=True)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/import/chunked/segment")
async def upload_chunked_segment(
    recording_id: str,
    sequence: int = Query(..., description="Sequence number of the segment", ge=0),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a binary segment for a chunked import.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(
            status_code=400, detail="Recording is not in uploading state"
        )

    recording_temp_dir = recordings_module.recording_upload_temp_dir(
        recording.id, create=True
    )

    filename = os.path.basename(f"{int(sequence)}.part")
    segment_path = recording_temp_dir / filename

    try:
        async with aiofiles.open(segment_path, "wb") as out_file:
            content = await file.read()
            await out_file.write(content)
        await _sync_recording_audio_chunks_from_directory(
            db,
            recording_id=recording.id,
            source_kind="import_part",
            suffix=".part",
        )
        await db.commit()
    except Exception as e:  # noqa: BLE001
        try:
            if segment_path.exists():
                segment_path.unlink()
        except OSError:
            pass
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to save the uploaded segment.",
            log_message=f"Failed to save chunked import segment {sequence} for recording {recording_id}.",
            exc=e,
        )

    return {"status": "received", "segment": sequence}


@router.post("/import/chunked/finalize", response_model=RecordingPublicRead)
async def finalize_chunked_import(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Finalize a chunked import, reassemble the file, and trigger processing.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(
            status_code=400, detail="Recording is not in uploading state"
        )

    await _sync_recording_audio_chunks_from_directory(
        db,
        recording_id=recording.id,
        source_kind="import_part",
        suffix=".part",
    )
    chunk_rows = await recordings_module._list_recording_audio_chunks(
        db,
        recording_id=recording.id,
        source_kind="import_part",
    )
    if not chunk_rows:
        raise HTTPException(status_code=400, detail="No valid segments found")

    missing_sequences = _find_missing_chunk_sequences(chunk_rows)
    if missing_sequences:
        raise HTTPException(
            status_code=409,
            detail="Recording upload is still in progress; finalize after all segment uploads complete.",
        )

    try:
        segment_paths = [row.storage_path for row in chunk_rows]
        concatenate_binary_files(segment_paths, recording.audio_path)

        # Get file stats
        file_stats = os.stat(recording.audio_path)
        recording.file_size_bytes = file_stats.st_size

        # Get duration
        try:
            recording.duration_seconds = get_audio_duration(recording.audio_path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to get duration: {e}")

        await db.execute(
            delete(RecordingAudioChunk)
            .where(RecordingAudioChunk.recording_id == recording.id)
            .where(RecordingAudioChunk.source_kind == "import_part")
        )
        await db.execute(
            delete(RecordingAudioWindowManifest)
            .where(RecordingAudioWindowManifest.recording_id == recording.id)
            .where(RecordingAudioWindowManifest.source_kind == "import_part")
        )
        await _bootstrap_import_audio_windows(
            db,
            recording_id=recording.id,
            audio_path=recording.audio_path,
        )
    except HTTPException as exc:
        recordings_module.delete_recording_artifacts(
            recording_id=recording.id,
            audio_path=recording.audio_path,
            proxy_path=None,
            logger=logger,
        )
        await _mark_recording_upload_error(db, recording, str(exc.detail))
        raise
    except Exception as e:  # noqa: BLE001
        failed_root: Path | None = None
        try:
            failed_root = recordings_module.move_recording_upload_to_failed(
                recording.id, logger=logger
            )
        except Exception as move_error:  # noqa: BLE001
            logger.error(
                f"Failed to move failed chunked upload to failed dir: {move_error}"
            )

        await _mark_recording_audio_chunks_failed(
            db,
            recording_id=recording.id,
            failed_root=failed_root,
        )
        await db.commit()

        recordings_module.delete_recording_artifacts(
            recording_id=recording.id,
            audio_path=recording.audio_path,
            proxy_path=None,
            logger=logger,
        )

        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to finalize the uploaded recording.",
            log_message=f"Failed to finalize chunked import for recording {recording_id}.",
            exc=e,
        )

    # Update recording status
    recording.status = RecordingStatus.QUEUED
    recording.client_status = ClientStatus.IDLE

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    task = recordings_module.celery_app.send_task(
        "backend.worker.tasks.process_recording_task", args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, recording.user_id)

    if not recording.proxy_path:
        proxy_task = recordings_module.celery_app.send_task(
            "backend.worker.tasks.generate_proxy_task", args=[recording.id]
        )
        if proxy_task:
            await register_task_ownership(db, proxy_task.id, recording.user_id)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/upload", response_model=RecordingPublicRead)
async def upload_recording(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a new audio recording.
    """
    # Generate a unique filename to prevent collisions
    file_ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    if not file_ext:
        file_ext = ".wav"  # Default to wav if unknown

    # Validate extension
    if file_ext not in SUPPORTED_AUDIO_FORMATS and file_ext != ".wav":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{file_ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}",
        )

    unique_filename = f"{uuid4()}{file_ext}"
    file_path = str(recordings_module.recordings_root_dir() / unique_filename)

    # Save the file
    async with enforce_upload_concurrency(
        request, "upload_recording", str(current_user.id), 2
    ):
        try:
            await stream_and_validate_upload(
                file=file,
                dest_path=file_path,
                max_size=UPLOAD_LIMIT_LEGACY_RECORDING,
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise sanitized_http_exception(
                logger=logger,
                status_code=500,
                client_message="Failed to save the uploaded recording.",
                log_message=f"Failed to persist uploaded recording '{file.filename}'.",
                exc=e,
            )

    try:
        recordings_module._enforce_lossy_audio_bitrate_floor(file_path)
    except HTTPException:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise

    file_stats = os.stat(file_path)

    duration = 0.0
    try:
        duration = recordings_module.get_audio_duration(file_path)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to get duration: {e}")

    # Create DB entry
    name = os.path.splitext(file.filename)[0]
    if name == "blob":  # Common default name from blobs
        name = generate_default_meeting_name()

    recording = Recording(
        name=name,
        proxy_path=get_initial_proxy_path(file_path),
        audio_path=file_path,
        file_size_bytes=file_stats.st_size,
        duration_seconds=duration,
        status=RecordingStatus.QUEUED,
        user_id=current_user.id,
    )

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    task = recordings_module.celery_app.send_task(
        "backend.worker.tasks.process_recording_task", args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, recording.user_id)

    if not recording.proxy_path:
        proxy_task = recordings_module.celery_app.send_task(
            "backend.worker.tasks.generate_proxy_task", args=[recording.id]
        )
        if proxy_task:
            await register_task_ownership(db, proxy_task.id, recording.user_id)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))
