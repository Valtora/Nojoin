import logging
from typing import List, Optional
from uuid import uuid4
from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.api.deps import get_db, get_current_user, get_current_recording_client_user
from backend.models.user import User
from backend.models.recording import Recording, RecordingInitResponse, RecordingStatus
from backend.models.transcript import Transcript

from .router import router
import backend.api.v1.endpoints.recordings as recordings_module

logger = logging.getLogger(__name__)


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
    recordings = await recordings_module.get_recordings_by_public_ids(
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
    recordings = await recordings_module.get_recordings_by_public_ids(
        db,
        batch.recording_ids,
        user_id=current_user.id,
    )

    logger.info(f"Batch restore request for {len(batch.recording_ids)} IDs. Found {len(recordings)} recordings.")
    
    for recording in recordings:
        if recording.is_deleted:
            recording.is_deleted = False
        else:
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
    recordings = await recordings_module.get_recordings_by_public_ids(
        db,
        batch.recording_ids,
        user_id=current_user.id,
    )

    logger.info(f"Batch soft-delete request for {len(batch.recording_ids)} IDs. Found {len(recordings)} recordings.")
    
    for recording in recordings:
        recording.is_deleted = True
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
    recordings = await recordings_module.get_recordings_by_public_ids(
        db,
        batch.recording_ids,
        user_id=current_user.id,
    )
    
    for recording in recordings:
        recordings_module.delete_recording_artifacts(
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
    request: Request,
    name: Optional[str] = Query(None, description="Name of the recording"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Initialize a multipart upload.
    """
    active_recording = await recordings_module._get_active_capture_recording_for_user(db, current_user.id)
    if active_recording is not None:
        raise HTTPException(
            status_code=409,
            detail=recordings_module._build_active_recording_conflict(active_recording),
        )

    unique_filename = f"{uuid4()}.wav"
    file_path = str(recordings_module.recordings_root_dir() / unique_filename)
    
    if not name:
        name = recordings_module.generate_default_meeting_name()
    
    recording = Recording(
        name=name,
        audio_path=file_path,
        status=RecordingStatus.UPLOADING,
        user_id=current_user.id
    )
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    db.add(Transcript(recording_id=recording.id, transcript_status="processing"))
    await db.commit()

    recordings_module.recording_upload_temp_dir(recording.id, create=True)

    return RecordingInitResponse(
        id=recording.public_id,
        name=recording.name,
    )
