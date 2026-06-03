import os
import logging
from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.api.deps import get_db, get_current_user
from backend.models.user import User
from backend.models.recording import Recording, RecordingUpdate, RecordingStatus, ClientStatus
from backend.models.calendar import CalendarEvent
from backend.models.recording_public import RecordingPublicRead, serialize_recording
import backend.api.v1.endpoints.recordings as recordings_module

from .router import router
from .helpers import (
    _get_owned_recording,
    _recording_has_proxy,
    _get_owned_calendar_event,
    _requeue_for_processing,
)

logger = logging.getLogger(__name__)


class CalendarEventLink(BaseModel):
    calendar_event_id: int | None = None


@router.put("/{recording_id}/calendar-event", response_model=RecordingPublicRead)
async def link_recording_calendar_event(
    recording_id: str,
    body: CalendarEventLink,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Link, change or unlink the calendar event for a recording.

    A ``calendar_event_id`` of ``None`` unlinks. A non-null id must belong to
    the current user (verified via the calendar connection join) or the
    request is rejected with 404.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    linked_event: CalendarEvent | None = None
    if body.calendar_event_id is not None:
        linked_event = await _get_owned_calendar_event(
            db, body.calendar_event_id, current_user.id
        )
        recording.calendar_event_id = linked_event.id
    else:
        recording.calendar_event_id = None

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    return serialize_recording(
        recording,
        has_proxy=_recording_has_proxy(recording),
        include_calendar_event=True,
        calendar_event=linked_event,
    )


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
    
    recordings_module.delete_recording_artifacts(
        recording_id=recording.id,
        audio_path=recording.audio_path,
        proxy_path=recording.proxy_path,
        logger=logger,
    )
            
    await db.delete(recording)
    await db.commit()
    
    return {"ok": True}


@router.post("/{recording_id}/retry", response_model=RecordingPublicRead)
async def retry_processing(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Reset generated processing state and re-run the pipeline from the original audio.
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

    await _requeue_for_processing(db, recording)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


class ReprocessRequest(BaseModel):
    transcription_backend: str
    whisper_model_size: str | None = None
    parakeet_model: str | None = None
    canary_model: str | None = None


@router.post("/{recording_id}/reprocess", response_model=RecordingPublicRead)
async def reprocess_recording(
    recording_id: str,
    body: ReprocessRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Re-run the full processing pipeline with a caller-chosen transcription engine.
    """
    from backend.utils.config_manager import TRANSCRIPTION_BACKENDS

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

    if body.transcription_backend not in TRANSCRIPTION_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown transcription backend: {body.transcription_backend}",
        )

    engine_override: dict = {"transcription_backend": body.transcription_backend}
    if body.whisper_model_size is not None:
        engine_override["whisper_model_size"] = body.whisper_model_size
    if body.parakeet_model is not None:
        engine_override["parakeet_model"] = body.parakeet_model
    if body.canary_model is not None:
        engine_override["canary_model"] = body.canary_model

    queued_step = f"Queued for reprocessing with {body.transcription_backend}..."
    await _requeue_for_processing(
        db, recording, engine_override=engine_override, queued_step=queued_step
    )

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
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    recording.is_deleted = True
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
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    
    if recording.celery_task_id:
        try:
            recordings_module.celery_app.control.revoke(recording.celery_task_id, terminate=True)
        except Exception:  # noqa: BLE001
            pass

    recordings_module.delete_recording_artifacts(
        recording_id=recording.id,
        audio_path=recording.audio_path,
        proxy_path=recording.proxy_path,
        logger=logger,
    )
            
    await db.delete(recording)
    await db.commit()
    
    return {"ok": True}


@router.post("/{recording_id}/infer-speakers")
async def infer_speakers_for_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Re-run speaker inference on an already processed meeting.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    recording.status = RecordingStatus.PROCESSING
    recording.processing_step = "Inferring speakers..."
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    task = recordings_module.celery_app.send_task(
        "backend.worker.tasks.infer_speakers_task",
        args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership
    await register_task_ownership(db, task.id, recording.user_id)
    
    return {"status": "queued", "message": "Speaker suggestion refresh started in background."}


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
            recordings_module.celery_app.control.revoke(recording.celery_task_id, terminate=True)
        except Exception:  # noqa: BLE001
            pass

    recording.celery_task_id = None
    recording.status = RecordingStatus.CANCELLED
    recording.client_status = ClientStatus.IDLE
    recording.upload_progress = 0
    recording.processing_step = "Cancelled by user"
    recording.processing_progress = 0
    recording.processing_started_at = None
    recording.processing_completed_at = None
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    
    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))
