import os
import shutil
from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
import aiofiles
from uuid import uuid4

from backend.api.deps import get_db
from backend.models.recording import Recording, RecordingStatus, RecordingRead, RecordingUpdate
from backend.worker.tasks import process_recording_task
from backend.utils.audio import concatenate_wavs, get_audio_duration

router = APIRouter()

# Configuration for recordings storage
# In production docker, this should be mapped to a volume
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "data/recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)
TEMP_DIR = os.path.join(RECORDINGS_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

@router.post("/init", response_model=Recording)
async def init_upload(
    name: str = Query(..., description="Name of the recording"),
    db: AsyncSession = Depends(get_db)
):
    """
    Initialize a multipart upload.
    """
    # Create a placeholder file path (will be used after finalization)
    unique_filename = f"{uuid4()}.wav"
    file_path = os.path.join(RECORDINGS_DIR, unique_filename)
    
    recording = Recording(
        name=name,
        audio_path=file_path,
        status=RecordingStatus.UPLOADING
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
        raise HTTPException(status_code=500, detail=f"Failed to concatenate segments: {str(e)}")
        
    # Update recording status
    file_stats = os.stat(recording.audio_path)
    recording.file_size_bytes = file_stats.st_size
    recording.status = RecordingStatus.RECORDED
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    # Trigger processing task
    process_recording_task.delay(recording.id)
    
    return recording

@router.post("/upload", response_model=Recording)
async def upload_recording(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a new audio recording.
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
    recording = Recording(
        name=file.filename,
        audio_path=file_path,
        file_size_bytes=file_stats.st_size,
        duration_seconds=duration,
        status=RecordingStatus.RECORDED
    )
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    # Trigger processing task
    process_recording_task.delay(recording.id)
    
    return recording

@router.get("/", response_model=List[Recording])
async def list_recordings(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    """
    List all recordings.
    """
    statement = select(Recording).offset(skip).limit(limit).order_by(Recording.created_at.desc())
    result = await db.execute(statement)
    recordings = result.scalars().all()
    return recordings

from sqlalchemy.orm import selectinload
from backend.models.speaker import RecordingSpeaker
from backend.models.tag import RecordingTag

@router.get("/{recording_id}", response_model=RecordingRead)
async def get_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific recording by ID with all relationships loaded.
    """
    statement = (
        select(Recording)
        .where(Recording.id == recording_id)
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
    db: AsyncSession = Depends(get_db)
):
    """
    Update a recording.
    """
    recording = await db.get(Recording, recording_id)
    if not recording:
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
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a recording and its associated file.
    """
    recording = await db.get(Recording, recording_id)
    if not recording:
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
    db: AsyncSession = Depends(get_db)
):
    """
    Stream the audio file for a recording.
    """
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    if not recording.audio_path or not os.path.exists(recording.audio_path):
        raise HTTPException(status_code=404, detail="Audio file not found on server")
        
    return FileResponse(recording.audio_path, media_type="audio/wav", filename=recording.name)

@router.post("/{recording_id}/retry", response_model=Recording)
async def retry_processing(
    recording_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Retry processing for a recording that is in ERROR or COMPLETED state.
    Useful if the pipeline failed or if code has been updated.
    """
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    # Reset status
    recording.status = RecordingStatus.PROCESSING
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    # Trigger Celery task
    process_recording_task.delay(recording.id)
    
    return recording
