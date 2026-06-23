import logging

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.celery_app import celery_app
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.config_manager import is_meeting_edge_enabled
from backend.utils.llm_config import resolve_llm_config_async

from .helpers import (
    MeetingEdgeFocusUpdate,
    NotesUpdate,
    UserNotesUpdate,
    _dispatch_meeting_edge_refresh,
    _get_owned_recording,
)
from .router import router

logger = logging.getLogger(__name__)


@router.get("/{recording_id}/notes")
async def get_notes(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get the meeting notes for a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    return {"notes": transcript.notes}


@router.get("/{recording_id}/user-notes")
async def get_user_notes(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get the user-authored processing notes for a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    return {"user_notes": transcript.user_notes if transcript else None}


@router.put("/{recording_id}/user-notes")
async def update_user_notes(
    recording_id: str,
    update: UserNotesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the user-authored processing notes for a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript:
        transcript = Transcript(recording_id=recording.id)

    transcript.user_notes = update.user_notes.strip() or None
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    _dispatch_meeting_edge_refresh(
        recording.id,
        enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
    )

    return {"user_notes": transcript.user_notes, "status": "success"}


@router.put("/{recording_id}/meeting-edge-focus")
async def update_meeting_edge_focus(
    recording_id: str,
    update: MeetingEdgeFocusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the user-provided Meeting Edge focus prompt for a live meeting.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript:
        transcript = Transcript(recording_id=recording.id)

    transcript.meeting_edge_focus = update.meeting_edge_focus.strip() or None
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    _dispatch_meeting_edge_refresh(
        recording.id,
        enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
    )

    return {
        "meeting_edge_focus": transcript.meeting_edge_focus,
        "status": "success",
    }


@router.put("/{recording_id}/notes")
async def update_notes(
    recording_id: str,
    update: NotesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the meeting notes for a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    transcript.notes = update.notes
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)

    return {"notes": transcript.notes, "status": "success"}


@router.post("/{recording_id}/notes/generate")
async def generate_notes(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate meeting notes using the configured LLM provider.
    """
    # 1. Fetch Recording with Speakers
    recording = await _get_owned_recording(
        db,
        recording_id,
        current_user.id,
        options=(
            selectinload(Recording.speakers).options(
                selectinload(RecordingSpeaker.global_speaker)
            ),
        ),
    )

    # 2. Fetch Transcript
    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript or not transcript.segments:
        raise HTTPException(status_code=404, detail="Transcript not found or empty")

    if transcript.notes_status == "generating":
        raise HTTPException(
            status_code=409, detail="Meeting notes are already generating."
        )

    llm_config = await resolve_llm_config_async(db, current_user.settings or {})
    missing_llm_config = llm_config.missing_configuration_message()
    if missing_llm_config:
        transcript.notes_status = "error"
        transcript.error_message = missing_llm_config
        db.add(transcript)
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"{missing_llm_config}. Configure an AI provider and model in Settings.",
        )

    # 4. Call Worker Task
    transcript.notes_status = "generating"
    transcript.error_message = None
    db.add(transcript)
    await db.commit()

    task = celery_app.send_task(
        "backend.worker.tasks.generate_notes_task", args=[recording.id]
    )
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, current_user.id)

    return {
        "status": "success",
        "notes_status": transcript.notes_status,
        "error_message": transcript.error_message,
        "message": "Note generation started",
    }
