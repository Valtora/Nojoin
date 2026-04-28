import os
import logging
from typing import List, Optional, Any
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
import aiofiles
from uuid import uuid4

from backend.api.deps import get_current_companion_bootstrap_user, get_current_recording_client_user, get_db, get_current_user, get_current_user_stream
from backend.api.error_handling import sanitized_http_exception
from backend.core import security
from backend.models.recording import Recording, RecordingInitResponse, RecordingStatus, ClientStatus, RecordingUpdate, RecordingUploadTokenResponse
from backend.models.recording_public import RecordingPublicRead, serialize_recording
from backend.models.user import User
from backend.worker.tasks import process_recording_task, infer_speakers_task, generate_proxy_task
from backend.celery_app import celery_app
from backend.utils.audio import concatenate_wavs, get_audio_duration, concatenate_binary_files
from backend.processing.llm_services import get_llm_backend
from backend.utils.speaker_label_manager import SpeakerLabelManager
from backend.utils.time import utc_now
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker
from backend.models.chat import ChatMessage
from backend.models.context_chunk import ContextChunk
from backend.utils.config_manager import config_manager, is_llm_available
from backend.utils.processing_eta import estimate_processing_eta
from backend.utils.recording_storage import (
    delete_recording_artifacts,
    move_recording_upload_to_failed,
    recording_upload_temp_dir,
    recordings_root_dir,
)
from backend.services.recording_identity_service import get_recording_by_public_id, get_recordings_by_public_ids

router = APIRouter()
logger = logging.getLogger(__name__)


def _recording_has_proxy(recording: Recording) -> bool:
    return bool(recording.proxy_path and os.path.exists(recording.proxy_path))


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
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


def get_initial_proxy_path(file_path: str) -> Optional[str]:
    _, file_ext = os.path.splitext(file_path)
    if file_ext.lower() == ".mp3":
        return file_path
    return None


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
    if existing_transcript:
        await db.delete(existing_transcript)
        await db.flush()

    if preserved_user_notes:
        db.add(Transcript(recording_id=recording_id, user_notes=preserved_user_notes))

def get_ordinal_suffix(day: int) -> str:
    if 11 <= day <= 13:
        return "th"
    else:
        return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

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
        else: # 0-5
            time_of_day = "Late Night" 

    return f"{day_name} {day_num}{suffix} {short_month}, {time_of_day} Meeting"

from pydantic import BaseModel

class BatchRecordingIds(BaseModel):
    recording_ids: List[str]

@router.post("/batch/archive")
async def batch_archive_recordings(
    batch: BatchRecordingIds,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Archive multiple recordings.
    """
    recordings = await get_recordings_by_public_ids(
        db,
        batch.recording_ids,
        user_id=current_user.id,
    )
    
    logger.info(f"Batch archive request for {len(batch.recording_ids)} IDs. Found {len(recordings)} recordings.")

    for recording in recordings:
        if not recording.is_deleted:
            recording.is_archived = True
            db.add(recording)
            
    await db.commit()
    return {"ok": True, "count": len(recordings)}

@router.post("/batch/restore")
async def batch_restore_recordings(
    batch: BatchRecordingIds,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Restore multiple recordings from archive or trash.
    """
    recordings = await get_recordings_by_public_ids(
        db,
        batch.recording_ids,
        user_id=current_user.id,
    )

    logger.info(f"Batch restore request for {len(batch.recording_ids)} IDs. Found {len(recordings)} recordings.")
    
    for recording in recordings:
        if recording.is_deleted:
            # Restore from Deleted -> Previous State
            recording.is_deleted = False
        else:
            # Restore from Archived -> Active
            recording.is_archived = False
        db.add(recording)
            
    await db.commit()
    return {"ok": True, "count": len(recordings)}

@router.post("/batch/soft-delete")
async def batch_soft_delete_recordings(
    batch: BatchRecordingIds,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Soft-delete multiple recordings.
    """
    recordings = await get_recordings_by_public_ids(
        db,
        batch.recording_ids,
        user_id=current_user.id,
    )

    logger.info(f"Batch soft-delete request for {len(batch.recording_ids)} IDs. Found {len(recordings)} recordings.")
    
    for recording in recordings:
        recording.is_deleted = True
        # recording.is_archived = False # Keep previous state
        db.add(recording)
            
    await db.commit()
    return {"ok": True, "count": len(recordings)}

@router.post("/batch/permanent")
async def batch_permanently_delete_recordings(
    batch: BatchRecordingIds,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Permanently delete multiple recordings and their files.
    """
    recordings = await get_recordings_by_public_ids(
        db,
        batch.recording_ids,
        user_id=current_user.id,
    )
    
    for recording in recordings:
        delete_recording_artifacts(
            recording_id=recording.id,
            audio_path=recording.audio_path,
            proxy_path=recording.proxy_path,
            logger=logger,
        )
        await db.delete(recording)
            
    await db.commit()
    return {"ok": True, "count": len(recordings)}

@router.post("/init", response_model=RecordingInitResponse)
async def init_upload(
    name: Optional[str] = Query(None, description="Name of the recording"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user)
):
    """
    Initialize a multipart upload.
    """
    # Create a placeholder file path (will be used after finalization)
    unique_filename = f"{uuid4()}.wav"
    file_path = str(recordings_root_dir() / unique_filename)
    
    if not name:
        name = generate_default_meeting_name()
    
    recording = Recording(
        name=name,
        audio_path=file_path,
        status=RecordingStatus.UPLOADING,
        user_id=current_user.id
    )
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    

    recording_upload_temp_dir(recording.id, create=True)

    upload_token = security.create_access_token(
        current_user.username,
        token_type=security.COMPANION_TOKEN_TYPE,
        scopes=[security.COMPANION_RECORDING_SCOPE],
        expires_delta=timedelta(minutes=security.COMPANION_RECORDING_TOKEN_EXPIRE_MINUTES),
        extra_claims={"recording_public_id": recording.public_id},
    )

    return RecordingInitResponse(
        id=recording.public_id,
        name=recording.name,
        upload_token=upload_token,
    )

@router.post("/{recording_id}/segment")
async def upload_segment(
    recording_id: str,
    sequence: int = Query(..., description="Sequence number of the segment", ge=0),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user)
):
    """
    Upload a segment for a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(status_code=400, detail="Recording is not in uploading state")
    
    recording_temp_dir = recording_upload_temp_dir(recording.id, create=True)
        
    filename = os.path.basename(f"{int(sequence)}.wav")
    segment_path = recording_temp_dir / filename
    
    try:
        async with aiofiles.open(segment_path, 'wb') as out_file:
            content = await file.read()
            await out_file.write(content)
    except Exception as e:
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to save the uploaded segment.",
            log_message=f"Failed to save uploaded segment {sequence} for recording {recording_id}.",
            exc=e,
        )
        
    return {"status": "received", "segment": sequence}


@router.post("/{recording_id}/upload-token", response_model=RecordingUploadTokenResponse)
async def refresh_upload_token(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_companion_bootstrap_user),
):
    """
    Re-issue a companion upload token for an existing in-flight recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(
            status_code=409,
            detail="Recording is no longer accepting companion uploads",
        )

    upload_token = security.create_access_token(
        current_user.username,
        token_type=security.COMPANION_TOKEN_TYPE,
        scopes=[security.COMPANION_RECORDING_SCOPE],
        expires_delta=timedelta(minutes=security.COMPANION_RECORDING_TOKEN_EXPIRE_MINUTES),
        extra_claims={"recording_public_id": recording.public_id},
    )

    return RecordingUploadTokenResponse(
        recording_id=recording.public_id,
        upload_token=upload_token,
    )

@router.post("/{recording_id}/finalize", response_model=RecordingPublicRead)
async def finalize_upload(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user)
):
    """
    Finalize the upload, concatenate segments, and trigger processing.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(status_code=400, detail="Recording is not in uploading state")
        
    recording_temp_dir = recording_upload_temp_dir(recording.id, create=False)
    if not recording_temp_dir.exists():
        raise HTTPException(status_code=400, detail="No segments found for this recording")
        
    # List all segments and sort by sequence
    segments = []
    for filename in os.listdir(recording_temp_dir):
        if filename.endswith(".wav"):
            try:
                seq = int(os.path.splitext(filename)[0])
                segments.append((seq, str(recording_temp_dir / filename)))
            except ValueError:
                continue
                
    segments.sort(key=lambda x: x[0])
    
    if not segments:
        raise HTTPException(status_code=400, detail="No valid segments found")
        

    try:
        segment_paths = [path for _, path in segments]
        concatenate_wavs(segment_paths, recording.audio_path)
        
        # Cleanup temp dir
        delete_recording_artifacts(
            recording_id=recording.id,
            audio_path=None,
            proxy_path=None,
            logger=logger,
        )
        
        # Set duration
        recording.duration_seconds = get_audio_duration(recording.audio_path)
        
    except Exception as e:
        try:
            move_recording_upload_to_failed(recording.id, logger=logger)
        except Exception as move_error:
            logger.error(f"Failed to move segments to failed dir: {move_error}")
            
        delete_recording_artifacts(
            recording_id=None,
            audio_path=recording.audio_path,
            proxy_path=None,
            logger=logger,
        )
            
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to finalize the uploaded recording.",
            log_message=f"Failed to finalize segmented upload for recording {recording_id}.",
            exc=e,
        )
        
    # Update recording status
    file_stats = os.stat(recording.audio_path)
    recording.file_size_bytes = file_stats.st_size
    recording.status = RecordingStatus.QUEUED
    recording.processing_started_at = None
    recording.processing_completed_at = None
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    

    task = process_recording_task.delay(recording.id)
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    

    generate_proxy_task.delay(recording.id)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))

# Supported audio formats for import
SUPPORTED_AUDIO_FORMATS = {'.wav', '.mp3', '.m4a', '.aac', '.webm', '.ogg', '.flac', '.mp4', '.wma', '.opus'}


@router.post("/import", response_model=RecordingPublicRead)
async def import_audio(
    file: UploadFile = File(...),
    name: Optional[str] = Query(None, description="Custom name for the recording"),
    recorded_at: Optional[datetime] = Query(None, description="Original recording timestamp"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
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
            detail=f"Unsupported audio format '{file_ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}"
        )
    
    # Generate a unique filename to prevent collisions
    unique_filename = f"{uuid4()}{file_ext}"
    file_path = str(recordings_root_dir() / unique_filename)
    
    # Save the file
    try:
        async with aiofiles.open(file_path, 'wb') as out_file:
            while chunk := await file.read(1024 * 1024):  # Read in 1MB chunks
                await out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
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
    except Exception as e:
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
        user_id=current_user.id
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
    
    # Trigger processing task
    task = process_recording_task.delay(recording.id)
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    
    # Trigger proxy generation task
    if not recording.proxy_path:
        generate_proxy_task.delay(recording.id)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/import/chunked/init", response_model=RecordingPublicRead)
async def init_chunked_import(
    filename: str = Query(..., description="Original filename with extension"),
    name: Optional[str] = Query(None, description="Custom name for the recording"),
    recorded_at: Optional[datetime] = Query(None, description="Original recording timestamp"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Initialize a chunked import for large files.
    """
    # Validate file extension
    file_ext = os.path.splitext(filename)[1].lower()
    if file_ext not in SUPPORTED_AUDIO_FORMATS:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported audio format '{file_ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}"
        )
    
    # Generate a unique filename
    unique_filename = f"{uuid4()}{file_ext}"
    file_path = str(recordings_root_dir() / unique_filename)
    
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
        user_id=current_user.id
    )
    
    # Override created_at if recorded_at is provided
    if recorded_at:
        if recorded_at.tzinfo is not None:
            recorded_at = recorded_at.astimezone(timezone.utc).replace(tzinfo=None)
        recording.created_at = recorded_at
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    

    recording_upload_temp_dir(recording.id, create=True)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/import/chunked/segment")
async def upload_chunked_segment(
    recording_id: str,
    sequence: int = Query(..., description="Sequence number of the segment", ge=0),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Upload a binary segment for a chunked import.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(status_code=400, detail="Recording is not in uploading state")
    
    recording_temp_dir = recording_upload_temp_dir(recording.id, create=True)
        
    filename = os.path.basename(f"{int(sequence)}.part")
    segment_path = recording_temp_dir / filename
    
    try:
        async with aiofiles.open(segment_path, 'wb') as out_file:
            content = await file.read()
            await out_file.write(content)
    except Exception as e:
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
    current_user: User = Depends(get_current_user)
):
    """
    Finalize a chunked import, reassemble the file, and trigger processing.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(status_code=400, detail="Recording is not in uploading state")
        
    recording_temp_dir = recording_upload_temp_dir(recording.id, create=False)
    if not recording_temp_dir.exists():
        raise HTTPException(status_code=400, detail="No segments found for this recording")
        
    # List all segments and sort by sequence
    segments = []
    for filename in os.listdir(recording_temp_dir):
        if filename.endswith(".part"):
            try:
                seq = int(os.path.splitext(filename)[0])
                segments.append((seq, str(recording_temp_dir / filename)))
            except ValueError:
                continue
                
    segments.sort(key=lambda x: x[0])
    
    if not segments:
        raise HTTPException(status_code=400, detail="No valid segments found")
        

    try:
        segment_paths = [path for _, path in segments]
        concatenate_binary_files(segment_paths, recording.audio_path)
        
        # Cleanup temp dir
        delete_recording_artifacts(
            recording_id=recording.id,
            audio_path=None,
            proxy_path=None,
            logger=logger,
        )
        
        # Get file stats
        file_stats = os.stat(recording.audio_path)
        recording.file_size_bytes = file_stats.st_size
        
        # Get duration
        try:
            recording.duration_seconds = get_audio_duration(recording.audio_path)
        except Exception as e:
            logger.warning(f"Failed to get duration: {e}")
        
    except Exception as e:
        try:
            move_recording_upload_to_failed(recording.id, logger=logger)
        except Exception as move_error:
            logger.error(f"Failed to move failed chunked upload to failed dir: {move_error}")

        delete_recording_artifacts(
            recording_id=None,
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
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    

    task = process_recording_task.delay(recording.id)
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    

    if not recording.proxy_path:
        generate_proxy_task.delay(recording.id)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/upload", response_model=RecordingPublicRead)
async def upload_recording(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Upload a new audio recording (used by Companion app).
    """
    # Generate a unique filename to prevent collisions
    file_ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    if not file_ext:
        file_ext = ".wav" # Default to wav if unknown
    
    # Validate extension
    if file_ext not in SUPPORTED_AUDIO_FORMATS and file_ext != ".wav":
         raise HTTPException(
            status_code=400, 
            detail=f"Unsupported audio format '{file_ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}"
        )

    unique_filename = f"{uuid4()}{file_ext}"
    file_path = str(recordings_root_dir() / unique_filename)
    
    # Save the file
    try:
        async with aiofiles.open(file_path, 'wb') as out_file:
            content = await file.read()
            await out_file.write(content)
    except Exception as e:
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to save the uploaded recording.",
            log_message=f"Failed to persist uploaded recording '{file.filename}'.",
            exc=e,
        )
    

    file_stats = os.stat(file_path)
    
    try:
        duration = get_audio_duration(file_path)
    except Exception as e:
        logger.warning(f"Failed to get duration: {e}")
    
    # Create DB entry
    name = os.path.splitext(file.filename)[0]
    if name == "blob": # Common default name from blobs
        name = generate_default_meeting_name()

    recording = Recording(
        name=name,
        proxy_path=get_initial_proxy_path(file_path),
        audio_path=file_path,
        file_size_bytes=file_stats.st_size,
        duration_seconds=duration,
        status=RecordingStatus.QUEUED,
        user_id=current_user.id
    )
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    

    task = process_recording_task.delay(recording.id)
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    

    if not recording.proxy_path:
        generate_proxy_task.delay(recording.id)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))

from sqlalchemy.orm import selectinload
from sqlmodel import select, or_, col, distinct
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker
from backend.models.tag import RecordingTag, Tag

@router.get("", response_model=List[RecordingPublicRead])
async def list_recordings_root(
    skip: int = 0,
    limit: int = 100,
    q: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    speaker_ids: Optional[List[int]] = Query(None),
    tag_ids: Optional[List[int]] = Query(None),
    include_archived: bool = Query(False, description="Include archived recordings"),
    include_deleted: bool = Query(False, description="Include deleted recordings"),
    only_archived: bool = Query(False, description="Only show archived recordings"),
    only_deleted: bool = Query(False, description="Only show deleted recordings"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all recordings (root path).
    """
    return await list_recordings(
        skip=skip, limit=limit, q=q, start_date=start_date, end_date=end_date,
        speaker_ids=speaker_ids, tag_ids=tag_ids, include_archived=include_archived,
        include_deleted=include_deleted, only_archived=only_archived, only_deleted=only_deleted,
        db=db, current_user=current_user
    )

@router.get("/", response_model=List[RecordingPublicRead])
async def list_recordings(
    skip: int = 0,
    limit: int = 100,
    q: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    speaker_ids: Optional[List[int]] = Query(None),
    tag_ids: Optional[List[int]] = Query(None),
    include_archived: bool = Query(False, description="Include archived recordings"),
    include_deleted: bool = Query(False, description="Include deleted recordings"),
    only_archived: bool = Query(False, description="Only show archived recordings"),
    only_deleted: bool = Query(False, description="Only show deleted recordings"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all recordings with optional search and filtering.
    By default, excludes archived and deleted recordings.
    """
    query = select(Recording).where(Recording.user_id == current_user.id).distinct()
    
    # Archive/Delete filtering
    if only_deleted:
        query = query.where(Recording.is_deleted == True)
    elif only_archived:
        query = query.where(Recording.is_archived == True, Recording.is_deleted == False)
    else:
        if not include_deleted:
            query = query.where(Recording.is_deleted == False)
        if not include_archived:
            query = query.where(Recording.is_archived == False)
    
    # Joins for filtering and searching
    if q or speaker_ids or tag_ids:
        query = query.join(Transcript, isouter=True)
        query = query.join(RecordingSpeaker, isouter=True)
        query = query.join(RecordingTag, isouter=True).join(Tag, isouter=True)

    # 1. Text Search (OR condition across fields)
    if q:
        search_filter = or_(
            col(Recording.name).ilike(f"%{q}%"),
            col(Transcript.text).ilike(f"%{q}%"),
            col(RecordingSpeaker.name).ilike(f"%{q}%"),
            col(Tag.name).ilike(f"%{q}%")
        )
        query = query.where(search_filter)

    # 2. Filters (AND conditions)
    if start_date:
        query = query.where(Recording.created_at >= start_date)
    if end_date:
        query = query.where(Recording.created_at <= end_date)

    if speaker_ids:
        query = query.where(RecordingSpeaker.global_speaker_id.in_(speaker_ids))

    if tag_ids:
        query = query.where(Tag.id.in_(tag_ids))

    # Order and pagination
    query = query.order_by(Recording.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    recordings = result.scalars().all()
    return [
        serialize_recording(recording, has_proxy=_recording_has_proxy(recording))
        for recording in recordings
    ]

from sqlalchemy.orm import selectinload
from backend.models.speaker import RecordingSpeaker
from backend.models.tag import RecordingTag

@router.get("/{recording_id}", response_model=RecordingPublicRead)
async def get_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific recording by ID with all relationships loaded.
    """
    statement = (
        select(Recording)
        .where(Recording.public_id == recording_id)
        .where(Recording.user_id == current_user.id)
        .options(
            selectinload(Recording.transcript),
            selectinload(Recording.speakers).options(
                selectinload(RecordingSpeaker.global_speaker)
            ),
            selectinload(Recording.tags).selectinload(RecordingTag.tag)
        )
    )
    result = await db.execute(statement)
    recording = result.scalar_one_or_none()
    
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    processing_eta_seconds = None
    processing_eta_learning = False
    processing_eta_sample_size = 0

    if (
        recording.status == RecordingStatus.PROCESSING
        and recording.processing_started_at is not None
        and recording.processing_completed_at is None
    ):
        eta_statement = (
            select(
                Recording.processing_started_at,
                Recording.processing_completed_at,
                Recording.duration_seconds,
            )
            .where(Recording.id != recording.id)
            .where(Recording.status == RecordingStatus.PROCESSED)
            .where(Recording.processing_started_at.is_not(None))
            .where(Recording.processing_completed_at.is_not(None))
            .where(Recording.duration_seconds.is_not(None))
        )
        eta_result = await db.execute(eta_statement)
        history_samples = [
            (started_at, completed_at, duration_seconds)
            for started_at, completed_at, duration_seconds in eta_result.all()
        ]
        eta_estimate = estimate_processing_eta(
            history_samples,
            recording.duration_seconds,
            recording.processing_started_at,
            now=utc_now(),
        )
        processing_eta_seconds = eta_estimate.eta_seconds
        processing_eta_learning = eta_estimate.learning
        processing_eta_sample_size = eta_estimate.sample_size

    return serialize_recording(
        recording,
        has_proxy=_recording_has_proxy(recording),
        processing_eta_seconds=processing_eta_seconds,
        processing_eta_learning=processing_eta_learning,
        processing_eta_sample_size=processing_eta_sample_size,
        include_transcript=True,
        include_speakers=True,
        include_tags=True,
    )

@router.get("/{recording_id}/info")
async def get_recording_info(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get detailed technical info about the recording audio file.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    from backend.processing.audio_preprocessing import analyze_audio_file
    
    info = {
        "original": None,
        "proxy": None
    }
    
    if recording.audio_path and os.path.exists(recording.audio_path):
        info["original"] = analyze_audio_file(recording.audio_path)
        
    if recording.proxy_path and os.path.exists(recording.proxy_path):
        info["proxy"] = analyze_audio_file(recording.proxy_path)
        
    return info

@router.patch("/{recording_id}", response_model=RecordingPublicRead)
async def update_recording(
    recording_id: str,
    recording_update: RecordingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    if recording_update.name is not None:
        recording.name = recording_update.name
        
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))

@router.delete("/{recording_id}")
async def delete_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a recording and its associated file.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    
    delete_recording_artifacts(
        recording_id=recording.id,
        audio_path=recording.audio_path,
        proxy_path=recording.proxy_path,
        logger=logger,
    )
            
    await db.delete(recording)
    await db.commit()
    
    return {"ok": True}


@router.post("/{recording_id}/discard")
async def discard_companion_upload(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user)
):
    """
    Discard an in-flight companion recording before it is finalised.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(
            status_code=400,
            detail="Only in-flight companion uploads can be discarded",
        )

    delete_recording_artifacts(
        recording_id=recording.id,
        audio_path=recording.audio_path,
        proxy_path=recording.proxy_path,
        logger=logger,
    )

    await db.delete(recording)
    await db.commit()

    return {"ok": True}

@router.get("/{recording_id}/stream")
async def stream_recording(
    recording_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_stream)
):
    """
    Stream the audio file for a recording.
    Supports range requests and limits chunk size to avoid Cloudflare 100MB limit.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    # Only serve the proxy MP3. Raw audio formats (WAV, etc.) cannot be
    # chunked via 206 because subsequent chunks lack the header needed
    # for the browser to decode sample rate, bit depth, and channels.
    if not recording.proxy_path or not os.path.exists(recording.proxy_path):
        raise HTTPException(
            status_code=202,
            detail="Audio proxy is being prepared. Please try again shortly."
        )

    file_path = recording.proxy_path
    media_type = "audio/mpeg"
        
    file_size = os.path.getsize(file_path)
    
    # Cloudflare limit is 100MB.
    # Uses a smaller chunk size to support responsive seeking and scrubbing.
    # 2.5MB is approx 15 seconds of CD-quality WAV audio (44.1kHz/16bit/Stereo).
    # This balances request overhead vs. seek responsiveness.
    CHUNK_SIZE = 2500 * 1024 
    
    is_range_request = False
    start = 0
    end = min(file_size - 1, CHUNK_SIZE - 1)
    
    range_header = request.headers.get("range")
    if range_header:
        is_range_request = True
        try:
            # Parse "bytes=0-" or "bytes=0-100" or "bytes=-500"
            range_str = range_header.replace("bytes=", "")
            range_parts = range_str.split("-")
            
            if range_parts[0] == "":
                # Suffix range request (e.g., bytes=-500)
                suffix_length = int(range_parts[1])
                start = max(0, file_size - suffix_length)
                end = file_size - 1
            else:
                # Standard range request (e.g., bytes=0- or bytes=0-100)
                start = int(range_parts[0])
                if len(range_parts) > 1 and range_parts[1]:
                    requested_end = int(range_parts[1])
                    end = min(requested_end, file_size - 1)
                else:
                    # If no end specified, default to start + CHUNK_SIZE
                    # This is CRITICAL for seeking. Browsers often ask for "bytes=X-"
                    # If we default 'end' to 'file_size - 1', we might calculate a huge chunk
                    # which we then clamp below. But explicitly setting it here clarifies intent.
                    end = file_size - 1
        except ValueError:
            pass # Fallback to default
            
    # Apply Chunk Size Limit
    # Enforces a maximum of CHUNK_SIZE bytes per response.
    # If the client requested a huge range (or open-ended range), the value is clamped.
    chunk_end = min(end, start + CHUNK_SIZE - 1)
    end = chunk_end

    if start >= file_size:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    # Ensure end is within bounds
    if end >= file_size:
        end = file_size - 1
        
    # Calculate content length for this chunk
    content_length = end - start + 1
    
    async def iterfile():
        async with aiofiles.open(file_path, "rb") as f:
            await f.seek(start)
            bytes_to_read = content_length
            while bytes_to_read > 0:
                chunk_size = min(1024 * 64, bytes_to_read) # Read in 64KB blocks
                data = await f.read(chunk_size)
                if not data:
                    break
                yield data
                bytes_to_read -= len(data)
                
    # Proxy MP3 files are immutable after generation; allow browser caching.
    cache_control = "private, max-age=3600"

    # Return 200 for non-Range requests when the entire file fits in the chunk.
    # Return 206 for Range requests or when chunking is required.
    use_partial = is_range_request or content_length < file_size

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Cache-Control": cache_control,
    }

    if use_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    return StreamingResponse(
        iterfile(),
        status_code=206 if use_partial else 200,
        headers=headers,
        media_type=media_type
    )

@router.post("/{recording_id}/retry", response_model=RecordingPublicRead)
async def retry_processing(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Reset generated processing state and re-run the pipeline from the original audio.

    Preserves recording metadata, tags, and uploaded documents, but clears the
    transcript, speakers, notes, chat history, and derived transcript or note context.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    if recording.is_deleted:
        raise HTTPException(status_code=400, detail="Cannot retry a deleted recording")

    if recording.status in {
        RecordingStatus.UPLOADING,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
    }:
        raise HTTPException(
            status_code=400,
            detail="Recording is already uploading or processing",
        )

    await _reset_generated_recording_state(db, recording.id)
        
    # Reset processing state while preserving recording metadata and documents.
    recording.status = RecordingStatus.QUEUED
    recording.processing_progress = 0
    recording.processing_step = "Queued for processing..."
    recording.processing_started_at = None
    recording.processing_completed_at = None
    recording.celery_task_id = None
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    # Trigger Celery task
    task = process_recording_task.delay(recording.id, True)
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/{recording_id}/archive", response_model=RecordingPublicRead)
async def archive_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Archive a recording. Archived recordings are hidden from the main list.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    
    if recording.is_deleted:
        raise HTTPException(status_code=400, detail="Cannot archive a deleted recording")
        
    recording.is_archived = True
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/{recording_id}/restore", response_model=RecordingPublicRead)
async def restore_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Restore an archived or soft-deleted recording back to the main list.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    recording.is_archived = False
    if recording.is_deleted:
        # Restore from Deleted -> Previous State
        recording.is_deleted = False
        # If it was archived, it remains archived. If active, remains active.
    else:
        # Restore from Archived -> Active
        recording.is_archived = False
        
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/{recording_id}/soft-delete", response_model=RecordingPublicRead)
async def soft_delete_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Soft-delete a recording. It moves to the trash/deleted view.
    The recording can be restored or permanently deleted later.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    recording.is_deleted = True
    # Preserve is_archived state so the recording can be restored to its prior view.
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.delete("/{recording_id}/permanent")
async def permanently_delete_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Permanently delete a recording and its associated file.
    This action cannot be undone.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    
    # Cancel any running task
    if recording.celery_task_id:
        try:
            celery_app.control.revoke(recording.celery_task_id, terminate=True)
        except Exception:
            pass  # Task might not exist or already finished

    delete_recording_artifacts(
        recording_id=recording.id,
        audio_path=recording.audio_path,
        proxy_path=recording.proxy_path,
        logger=logger,
    )
            
    await db.delete(recording)
    await db.commit()
    
    return {"ok": True}



@router.put("/{recording_id}/client_status", response_model=RecordingPublicRead)
async def update_client_status(
    recording_id: str,
    status: ClientStatus = Query(..., description="Current status of the client"),
    upload_progress: Optional[int] = Query(None, description="Upload progress percentage (0-100)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user)
):
    """
    Update the client status (e.g. RECORDING, PAUSED) for a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    
    recording.client_status = status
    if upload_progress is not None:
        recording.upload_progress = upload_progress
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))

@router.post("/{recording_id}/infer-speakers")
async def infer_speakers_for_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Re-run speaker inference on an already processed meeting.
    Triggers a background Celery task.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    # Update status to PROCESSING so UI shows spinner
    recording.status = RecordingStatus.PROCESSING
    recording.processing_step = "Inferring speakers..."
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    # Trigger Celery task
    task = infer_speakers_task.delay(recording.id)
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    
    return {"status": "queued", "message": "Speaker inference started in background."}

@router.post("/{recording_id}/cancel", response_model=RecordingPublicRead)
async def cancel_processing(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Cancel the processing task for a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    if recording.status not in [RecordingStatus.PROCESSING, RecordingStatus.QUEUED, RecordingStatus.UPLOADING]:
        raise HTTPException(status_code=400, detail="Recording is not being processed")

    if recording.celery_task_id:
        try:
            # Revoke the task and terminate it immediately
            celery_app.control.revoke(recording.celery_task_id, terminate=True)
        except Exception:
            pass # Ignore if task is not found (maybe already finished or never started)
            
    recording.status = RecordingStatus.CANCELLED
    recording.processing_step = "Cancelled by user"
    recording.processing_progress = 0
    recording.processing_started_at = None
    recording.processing_completed_at = None
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))
