import logging
from typing import Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.api.params import RowId
from backend.core.task_dispatch import dispatch_task
from backend.models.notes_public import (
    CancelNotesRequest,
    GenerateNotesResponse,
    NotesRunPublicRead,
    notes_run_state,
)
from backend.models.recording import Recording, is_live_capture
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import TRANSCRIPTION_FAILED_NOTES_MESSAGE, Transcript
from backend.models.user import User
from backend.utils.canonical_pipeline import build_transcript_segments_for_read
from backend.utils.config_manager import is_meeting_edge_enabled
from backend.utils.generation_claim import NOTES_RUN, generation_active
from backend.utils.generation_run import (
    acquire_run,
    lock_owned_run,
    lock_transcript,
    retire_run,
)
from backend.utils.llm_config import resolve_llm_config_async
from backend.utils.notes_templates import resolve_notes_template_async
from backend.utils.reprocess_publication import (
    REPROCESS_RUNNING_MESSAGE,
    reprocess_replacement_running,
)
from backend.utils.transcript_change import change_sequence_bump
from backend.utils.transcript_settling import (
    load_capture_publication,
    publication_is_current,
)

from .helpers import (
    MeetingEdgeFocusUpdate,
    NotesUpdate,
    _dispatch_meeting_edge_refresh,
    _get_owned_recording,
)
from .router import router

logger = logging.getLogger(__name__)


class GenerateNotesRequest(BaseModel):
    """Optional body for Regenerate Notes: which structure to use this run."""

    notes_template_id: Optional[RowId] = None


NOTES_DURING_CAPTURE_MESSAGE = "Notes can be generated once the recording has stopped."
NOTES_ALREADY_GENERATING_MESSAGE = "Meeting notes are already generating."


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

    return {
        "notes": transcript.notes,
        # Provenance for the UI: which structure produced these notes. NULL means
        # they predate templates, which is the shipped built-in structure.
        "notes_template_id": transcript.notes_template_id,
        "notes_template_sections": transcript.notes_template_sections,
    }


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
    transcript, recording = await db.run_sync(
        lambda session: load_capture_publication(session, recording.id)
    )
    if not publication_is_current(recording, transcript, None) or not is_live_capture(
        recording
    ):
        raise HTTPException(
            status_code=409, detail="Meeting Edge focus is read-only after capture."
        )
    if transcript is None:
        transcript = Transcript(recording_id=recording.id)

    transcript.meeting_edge_focus = update.meeting_edge_focus.strip() or None
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    await _dispatch_meeting_edge_refresh(
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
    await db.execute(change_sequence_bump(recording.id))
    await db.commit()
    await db.refresh(transcript)

    return {"notes": transcript.notes, "status": "success"}


@router.post("/{recording_id}/notes/generate", response_model=GenerateNotesResponse)
async def generate_notes(
    recording_id: str,
    request: Optional[GenerateNotesRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate meeting notes using the configured LLM provider.

    An optional ``notes_template_id`` picks the structure for this run. Omitting
    it falls through to the user's default, then the install default, then the
    built-in structure.

    A live or paused capture is refused (issue #851). The run claims the
    notes alone and leaves the recording's status as it is, so a run lost with
    its worker strands only the notes, and only until the claim expires
    (issue #828).
    """
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
    if is_live_capture(recording):
        raise HTTPException(status_code=409, detail=NOTES_DURING_CAPTURE_MESSAGE)

    transcript = await db.run_sync(
        lambda session: lock_transcript(session, recording.id)
    )

    if transcript and transcript.transcription_failed():
        raise HTTPException(status_code=409, detail=TRANSCRIPTION_FAILED_NOTES_MESSAGE)
    # The retained transcript is readable, but the pass replacing it owns notes.
    if reprocess_replacement_running(transcript):
        raise HTTPException(status_code=409, detail=REPROCESS_RUNNING_MESSAGE)

    segments = (
        await db.run_sync(
            lambda sync_session: build_transcript_segments_for_read(
                sync_session, recording.id, transcript=transcript
            )
        )
        if transcript is not None
        else []
    )
    if not segments:
        raise HTTPException(status_code=404, detail="Transcript not found or empty")

    # A claim older than the expiry belongs to a run lost with its worker
    # (issue #828), so it no longer refuses a new one.
    if generation_active(transcript.notes_status, transcript.updated_at):
        raise HTTPException(status_code=409, detail=NOTES_ALREADY_GENERATING_MESSAGE)

    llm_config = await resolve_llm_config_async(
        db, current_user.settings or {}, user_id=current_user.id
    )
    missing_llm_config = llm_config.missing_configuration_message()
    # Resolve the requested template, if any. Resolution is done here rather
    # than in the worker so an unusable choice is a 400 the user sees at once,
    # instead of a failed job they discover minutes later.
    requested_template_id = request.notes_template_id if request else None
    if requested_template_id is not None:
        resolved_template = await resolve_notes_template_async(
            db,
            current_user.settings or {},
            user_id=current_user.id,
            explicit_template_id=requested_template_id,
        )
        if resolved_template.template_id != requested_template_id:
            raise HTTPException(
                status_code=404,
                detail="Notes template not found",
            )

    transcript = await db.run_sync(
        lambda session: acquire_run(session, recording.id, NOTES_RUN)
    )
    if transcript is None:
        raise HTTPException(status_code=409, detail=NOTES_ALREADY_GENERATING_MESSAGE)
    if missing_llm_config:
        transcript.notes_status = "error"
        transcript.notes_run_id = None
        transcript.set_notes_error_message(missing_llm_config)
        db.add(transcript)
        await db.execute(change_sequence_bump(recording.id))
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"{missing_llm_config}. Configure an AI provider and model in Settings.",
        )
    db.add(transcript)
    await db.execute(change_sequence_bump(recording.id))
    response = GenerateNotesResponse(**notes_run_state(transcript).model_dump())
    run_id = str(transcript.notes_run_id)
    await db.commit()

    task = await dispatch_task(
        "backend.worker.tasks.generate_notes_task",
        args=[recording.id, requested_template_id, run_id],
    )
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, current_user.id)

    return response.model_dump(mode="json")


@router.post("/{recording_id}/notes/cancel", response_model=NotesRunPublicRead)
async def cancel_notes(
    recording_id: str,
    request: CancelNotesRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotesRunPublicRead:
    """Retire this run under its publication lock, preserving independent edits."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript = await db.run_sync(
        lambda session: lock_owned_run(
            session, recording.id, request.notes_run_id, NOTES_RUN
        )
    )
    if transcript is not None:
        retire_run(transcript, NOTES_RUN)
        db.add(transcript)
        await db.execute(change_sequence_bump(recording.id))
    else:
        transcript = await db.run_sync(
            lambda session: lock_transcript(session, recording.id)
        )
        if transcript is None:
            raise HTTPException(status_code=404, detail="Transcript not found")
        if generation_active(transcript.notes_status, transcript.updated_at):
            raise HTTPException(
                status_code=409,
                detail=notes_run_state(transcript).model_dump(mode="json"),
            )
    response = notes_run_state(transcript)
    await db.commit()
    return response
