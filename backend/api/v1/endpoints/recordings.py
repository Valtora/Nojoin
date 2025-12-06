import os
import shutil
from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
import aiofiles
from uuid import uuid4

from backend.api.deps import get_db, get_current_user
from backend.models.recording import Recording, RecordingStatus, ClientStatus, RecordingRead, RecordingUpdate
from backend.models.user import User
from backend.worker.tasks import process_recording_task, infer_speakers_task
from backend.utils.audio import concatenate_wavs, get_audio_duration
from backend.processing.LLM_Services import get_llm_backend
from backend.utils.speaker_label_manager import SpeakerLabelManager
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker
from backend.utils.config_manager import config_manager, is_llm_available

router = APIRouter()

# Configuration for recordings storage
# In production docker, this should be mapped to a volume
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "data/recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)
TEMP_DIR = os.path.join(RECORDINGS_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)
FAILED_DIR = os.path.join(RECORDINGS_DIR, "failed")
os.makedirs(FAILED_DIR, exist_ok=True)

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

def generate_timestamp_id() -> int:
    """
    Generates a unique ID based on the current timestamp with centisecond precision (10ms).
    Format: YYYYMMDDHHMMSSmm (16 digits)
    This ensures the ID fits within JavaScript's Number.MAX_SAFE_INTEGER (2^53 - 1).
    """
    now = datetime.now()
    # mm is microseconds // 10000 (0-99)
    timestamp_str = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 10000:02d}"
    return int(timestamp_str)

@router.post("/init", response_model=Recording)
async def init_upload(
    name: Optional[str] = Query(None, description="Name of the recording"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Initialize a multipart upload.
    """
    # Create a placeholder file path (will be used after finalization)
    unique_filename = f"{uuid4()}.wav"
    file_path = os.path.join(RECORDINGS_DIR, unique_filename)
    
    if not name:
        name = generate_default_meeting_name()
    
    recording = Recording(
        id=generate_timestamp_id(),
        name=name,
        audio_path=file_path,
        status=RecordingStatus.UPLOADING,
        user_id=current_user.id
    )
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    # Create temp directory for this recording's segments
    recording_temp_dir = os.path.join(TEMP_DIR, str(recording.id))
    os.makedirs(recording_temp_dir, exist_ok=True)
    
    return recording

@router.post("/{recording_id}/segment")
async def upload_segment(
    recording_id: int,
    sequence: int = Query(..., description="Sequence number of the segment"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a segment for a recording.
    """
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(status_code=400, detail="Recording is not in uploading state")
    
    recording_temp_dir = os.path.join(TEMP_DIR, str(recording.id))
    if not os.path.exists(recording_temp_dir):
        os.makedirs(recording_temp_dir, exist_ok=True)
        
    segment_path = os.path.join(recording_temp_dir, f"{sequence}.wav")
    
    try:
        async with aiofiles.open(segment_path, 'wb') as out_file:
            content = await file.read()
            await out_file.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save segment: {str(e)}")
        
    return {"status": "received", "segment": sequence}

@router.post("/{recording_id}/finalize", response_model=Recording)
async def finalize_upload(
    recording_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Finalize the upload, concatenate segments, and trigger processing.
    """
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(status_code=400, detail="Recording is not in uploading state")
        
    recording_temp_dir = os.path.join(TEMP_DIR, str(recording.id))
    if not os.path.exists(recording_temp_dir):
        raise HTTPException(status_code=400, detail="No segments found for this recording")
        
    # List all segments and sort by sequence
    segments = []
    for filename in os.listdir(recording_temp_dir):
        if filename.endswith(".wav"):
            try:
                seq = int(os.path.splitext(filename)[0])
                segments.append((seq, os.path.join(recording_temp_dir, filename)))
            except ValueError:
                continue
                
    segments.sort(key=lambda x: x[0])
    
    if not segments:
        raise HTTPException(status_code=400, detail="No valid segments found")
        
    # Concatenate using ffmpeg
    try:
        segment_paths = [path for _, path in segments]
        concatenate_wavs(segment_paths, recording.audio_path)
        
        # Cleanup temp dir
        shutil.rmtree(recording_temp_dir)
        
        # Set duration
        recording.duration_seconds = get_audio_duration(recording.audio_path)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        
        # Move failed segments to failed directory for inspection
        failed_path = os.path.join(FAILED_DIR, f"{recording.id}_failed_{int(datetime.now().timestamp())}")
        try:
            if os.path.exists(recording_temp_dir):
                shutil.move(recording_temp_dir, failed_path)
                print(f"Moved failed segments to {failed_path}")
        except Exception as move_error:
            print(f"Failed to move segments to failed dir: {move_error}")
            
        # Cleanup potential partial output file
        if os.path.exists(recording.audio_path):
            try:
                os.remove(recording.audio_path)
                print(f"Removed partial recording file: {recording.audio_path}")
            except Exception as cleanup_error:
                print(f"Failed to remove partial recording file: {cleanup_error}")
            
        raise HTTPException(status_code=500, detail=f"Failed to concatenate segments: {str(e)}")
        
    # Update recording status
    file_stats = os.stat(recording.audio_path)
    recording.file_size_bytes = file_stats.st_size
    recording.status = RecordingStatus.QUEUED
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    # Trigger processing task
    process_recording_task.delay(recording.id)
    
    return recording

# Supported audio formats for import
SUPPORTED_AUDIO_FORMATS = {'.wav', '.mp3', '.m4a', '.aac', '.webm', '.ogg', '.flac', '.mp4', '.wma', '.opus'}
MAX_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB


@router.post("/import", response_model=Recording)
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
    file_path = os.path.join(RECORDINGS_DIR, unique_filename)
    
    # Save the file with size validation
    try:
        total_size = 0
        async with aiofiles.open(file_path, 'wb') as out_file:
            while chunk := await file.read(1024 * 1024):  # Read in 1MB chunks
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE_BYTES:
                    await out_file.close()
                    os.remove(file_path)
                    raise HTTPException(
                        status_code=413, 
                        detail=f"File too large. Maximum size is {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB"
                    )
                await out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
    
    # Get file stats
    file_stats = os.stat(file_path)
    
    # Get duration
    duration = 0.0
    try:
        duration = get_audio_duration(file_path)
    except Exception as e:
        print(f"Failed to get duration: {e}")
    
    # Determine recording name
    if name:
        recording_name = name
    else:
        recording_name = os.path.splitext(file.filename)[0] if file.filename else ""
        if not recording_name or recording_name == "blob":
            recording_name = generate_default_meeting_name()

    recording = Recording(
        id=generate_timestamp_id(),
        name=recording_name,
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
    process_recording_task.delay(recording.id)
    
    return recording


@router.post("/upload", response_model=Recording)
async def upload_recording(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Upload a new audio recording (used by Companion app).
    """
    # Generate a unique filename to prevent collisions
    file_ext = os.path.splitext(file.filename)[1]
    if not file_ext:
        file_ext = ".wav" # Default to wav if unknown
        
    unique_filename = f"{uuid4()}{file_ext}"
    file_path = os.path.join(RECORDINGS_DIR, unique_filename)
    
    # Save the file
    try:
        async with aiofiles.open(file_path, 'wb') as out_file:
            content = await file.read()
            await out_file.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
    
    # Get file stats
    file_stats = os.stat(file_path)
    
    # Get duration
    duration = 0.0
    try:
        duration = get_audio_duration(file_path)
    except Exception as e:
        print(f"Failed to get duration: {e}")
    
    # Create DB entry
    name = os.path.splitext(file.filename)[0]
    if name == "blob": # Common default name from blobs
        name = generate_default_meeting_name()

    recording = Recording(
        id=generate_timestamp_id(),
        name=name,
        audio_path=file_path,
        file_size_bytes=file_stats.st_size,
        duration_seconds=duration,
        status=RecordingStatus.QUEUED,
        user_id=current_user.id
    )
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    # Trigger processing task
    process_recording_task.delay(recording.id)
    
    return recording

from sqlalchemy.orm import selectinload
from sqlmodel import select, or_, col, distinct
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker
from backend.models.tag import RecordingTag, Tag

@router.get("", response_model=List[Recording])
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

@router.get("/", response_model=List[Recording])
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
    return recordings

from sqlalchemy.orm import selectinload
from backend.models.speaker import RecordingSpeaker
from backend.models.tag import RecordingTag

@router.get("/{recording_id}", response_model=RecordingRead)
async def get_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific recording by ID with all relationships loaded.
    """
    statement = (
        select(Recording)
        .where(Recording.id == recording_id)
        .where(Recording.user_id == current_user.id)
        .options(
            selectinload(Recording.transcript),
            selectinload(Recording.speakers).selectinload(RecordingSpeaker.global_speaker),
            selectinload(Recording.tags).selectinload(RecordingTag.tag)
        )
    )
    result = await db.execute(statement)
    recording = result.scalar_one_or_none()
    
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    # Transform tags for response model
    # The relationship is Recording -> RecordingTag -> Tag
    # But RecordingRead expects a list of TagRead (which has name)
    # We need to manually construct the response or adjust the model
    
    # Let's adjust the response construction manually for now to match Pydantic model
    recording_dict = recording.model_dump()
    
    # Manually populate relationships
    if recording.transcript:
        recording_dict['transcript'] = recording.transcript
        
    if recording.speakers:
        recording_dict['speakers'] = recording.speakers
        
    if recording.tags:
        # Extract the actual Tag object from RecordingTag association
        recording_dict['tags'] = [rt.tag for rt in recording.tags]
    else:
        recording_dict['tags'] = []
        
    return recording_dict

@router.patch("/{recording_id}", response_model=Recording)
async def update_recording(
    recording_id: int,
    recording_update: RecordingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update a recording.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    if recording_update.name is not None:
        recording.name = recording_update.name
        
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return recording

@router.delete("/{recording_id}")
async def delete_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a recording and its associated file.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    # Delete file from disk
    if recording.audio_path and os.path.exists(recording.audio_path):
        try:
            os.remove(recording.audio_path)
        except OSError:
            pass # Log error but continue to delete DB entry
            
    await db.delete(recording)
    await db.commit()
    
    return {"ok": True}

@router.get("/{recording_id}/stream")
async def stream_recording(
    recording_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Stream the audio file for a recording.
    Supports range requests and limits chunk size to avoid Cloudflare 100MB limit.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    if not recording.audio_path or not os.path.exists(recording.audio_path):
        raise HTTPException(status_code=404, detail="Audio file not found on server")
        
    # Determine media type based on extension
    media_type = "audio/wav" # Default
    ext = os.path.splitext(recording.audio_path)[1].lower()
    if ext == ".opus":
        media_type = "audio/ogg"
    elif ext == ".mp3":
        media_type = "audio/mpeg"
    elif ext == ".m4a":
        media_type = "audio/mp4"
    elif ext == ".ogg":
        media_type = "audio/ogg"
    elif ext == ".flac":
        media_type = "audio/flac"
        
    file_path = recording.audio_path
    file_size = os.path.getsize(file_path)
    
    # Cloudflare limit is 100MB.
    # We use a smaller chunk size to support responsive seeking and scrubbing.
    # 2.5MB is approx 15 seconds of CD-quality WAV audio (44.1kHz/16bit/Stereo).
    # This balances request overhead vs. seek responsiveness.
    CHUNK_SIZE = 2500 * 1024 
    
    start = 0
    end = min(file_size - 1, CHUNK_SIZE - 1)
    
    range_header = request.headers.get("range")
    if range_header:
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
                    end = file_size - 1
        except ValueError:
            pass # Fallback to default
            
    # Apply Chunk Size Limit
    # We enforce that we never send more than CHUNK_SIZE
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
                
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Cache-Control": "no-cache, no-store, must-revalidate", # Prevent caching of partial responses
        "Pragma": "no-cache",
        "Expires": "0",
    }
    
    return StreamingResponse(
        iterfile(),
        status_code=206,
        headers=headers,
        media_type=media_type
    )

@router.post("/{recording_id}/retry", response_model=Recording)
async def retry_processing(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retry processing for a recording that is in ERROR or COMPLETED state.
    Useful if the pipeline failed or if code has been updated.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    # Reset status
    recording.status = RecordingStatus.PROCESSING
    recording.processing_step = "Queued for retry..."
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    # Trigger Celery task
    process_recording_task.delay(recording.id)
    
    return recording


@router.post("/{recording_id}/archive", response_model=Recording)
async def archive_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Archive a recording. Archived recordings are hidden from the main list.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    if recording.is_deleted:
        raise HTTPException(status_code=400, detail="Cannot archive a deleted recording")
        
    recording.is_archived = True
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return recording


@router.post("/{recording_id}/restore", response_model=Recording)
async def restore_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Restore an archived or soft-deleted recording back to the main list.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    recording.is_archived = False
    recording.is_deleted = False
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return recording


@router.post("/{recording_id}/soft-delete", response_model=Recording)
async def soft_delete_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Soft-delete a recording. It moves to the trash/deleted view.
    The recording can be restored or permanently deleted later.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    recording.is_deleted = True
    recording.is_archived = False  # Remove from archived if it was there
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return recording


@router.delete("/{recording_id}/permanent")
async def permanently_delete_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Permanently delete a recording and its associated file.
    This action cannot be undone.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    # Delete file from disk
    if recording.audio_path and os.path.exists(recording.audio_path):
        try:
            os.remove(recording.audio_path)
        except OSError:
            pass  # Log error but continue to delete DB entry
            
    await db.delete(recording)
    await db.commit()
    
    return {"ok": True}

from pydantic import BaseModel

class BatchRecordingIds(BaseModel):
    recording_ids: List[int]

@router.post("/batch/archive")
async def batch_archive_recordings(
    batch: BatchRecordingIds,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Archive multiple recordings.
    """
    stmt = select(Recording).where(Recording.id.in_(batch.recording_ids), Recording.user_id == current_user.id)
    result = await db.execute(stmt)
    recordings = result.scalars().all()
    
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
    stmt = select(Recording).where(Recording.id.in_(batch.recording_ids), Recording.user_id == current_user.id)
    result = await db.execute(stmt)
    recordings = result.scalars().all()
    
    for recording in recordings:
        recording.is_archived = False
        recording.is_deleted = False
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
    stmt = select(Recording).where(Recording.id.in_(batch.recording_ids), Recording.user_id == current_user.id)
    result = await db.execute(stmt)
    recordings = result.scalars().all()
    
    for recording in recordings:
        recording.is_deleted = True
        recording.is_archived = False
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
    stmt = select(Recording).where(Recording.id.in_(batch.recording_ids), Recording.user_id == current_user.id)
    result = await db.execute(stmt)
    recordings = result.scalars().all()
    
    for recording in recordings:
        # Delete file from disk
        if recording.audio_path and os.path.exists(recording.audio_path):
            try:
                os.remove(recording.audio_path)
            except OSError:
                pass
        await db.delete(recording)
            
    await db.commit()
    return {"ok": True, "count": len(recordings)}

@router.put("/{recording_id}/client_status", response_model=Recording)
async def update_client_status(
    recording_id: int,
    status: ClientStatus = Query(..., description="Current status of the client"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update the client status (e.g. RECORDING, PAUSED) for a recording.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    recording.client_status = status
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    return recording

@router.post("/{recording_id}/infer-speakers")
async def infer_speakers_for_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Re-run speaker inference on an already processed meeting.
    Triggers a background Celery task.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    # Update status to PROCESSING so UI shows spinner
    recording.status = RecordingStatus.PROCESSING
    recording.processing_step = "Inferring speakers..."
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    # Trigger Celery task
    infer_speakers_task.delay(recording.id)
    
    return {"status": "queued", "message": "Speaker inference started in background."}
