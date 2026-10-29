import logging
from typing import Literal, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.core.task_dispatch import dispatch_task
from backend.models.notes_output import NotesOutput, NotesOutputRead
from backend.models.recording import Recording, is_live_capture
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import TRANSCRIPTION_FAILED_NOTES_MESSAGE, Transcript
from backend.models.user import User
from backend.utils.canonical_pipeline import build_transcript_segments_for_read
from backend.utils.config_manager import is_meeting_edge_enabled
from backend.utils.llm_config import resolve_llm_config_async
from backend.utils.notes_templates import (
    ResolvedNotesTemplate,
    builtin_notes_template,
    resolve_notes_template_async,
)
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
    _get_notes_output,
    _get_owned_recording,
)
from .router import router

logger = logging.getLogger(__name__)


class GenerateNotesRequest(BaseModel):
    """Optional body for Regenerate Notes.

    ``notes_template_id`` picks the structure for this run of the meeting's
    notes, or for a new extra set (``create_output``): a template, not the
    built-in structure. ``output_id`` reruns an extra set, always with the
    structure saved when it was created. ``kind="glossary"`` creates the
    built-in glossary output; ``extract_more`` appends new terms to an existing
    glossary output instead of replacing its suggestions.
    """

    notes_template_id: Optional[int] = None
    create_output: bool = False
    output_id: int | None = None
    kind: Literal["notes", "glossary"] = "notes"
    extract_more: bool = False


async def _requested_template(
    db: AsyncSession, user: User, template_id: int
) -> ResolvedNotesTemplate:
    # Resolution is done here rather than in the worker so an unusable choice
    # is a 404 the user sees at once, instead of a failed job they discover
    # minutes later.
    template = await resolve_notes_template_async(
        db, user.settings or {}, user_id=user.id, explicit_template_id=template_id
    )
    if template.template_id != template_id:
        raise HTTPException(status_code=404, detail="Notes template not found")
    return template


async def _refuse_repeated_set(
    db: AsyncSession, transcript: Transcript, template: ResolvedNotesTemplate
) -> None:
    """A new set adds a structure the meeting lacks; Regenerate reruns one it has."""
    if template.is_builtin:
        raise HTTPException(
            status_code=400, detail="Choose a notes template for the new set."
        )
    result = await db.execute(
        select(NotesOutput.notes_template_id).where(
            NotesOutput.recording_id == transcript.recording_id
        )
    )
    used = set(result.scalars())
    if transcript.notes:
        used.add(transcript.notes_template_id)
    if template.template_id in used:
        raise HTTPException(
            status_code=409,
            detail="This meeting already has a set from that template; "
            "regenerate that set instead.",
        )


async def _generation_output(
    db: AsyncSession,
    transcript: Transcript,
    request: GenerateNotesRequest,
    user: User,
) -> NotesOutput | None:
    """The extra set a run writes, or None for the meeting's own notes."""
    if request.output_id is None and not request.create_output:
        if request.notes_template_id is not None:
            await _requested_template(db, user, request.notes_template_id)
        return None
    if request.output_id is not None and (
        request.create_output or request.notes_template_id is not None
    ):
        raise HTTPException(
            status_code=400,
            detail="An existing set reruns its own structure; "
            "choose a structure only for a new set.",
        )
    if transcript.transcript_status != "completed" or not transcript.notes:
        raise HTTPException(
            status_code=409,
            detail="Generate the meeting's first notes before adding another set.",
        )
    if request.output_id is not None:
        output = await _get_notes_output(db, transcript.recording_id, request.output_id)
        if output.notes_status == "generating":
            raise HTTPException(
                status_code=409, detail="Meeting notes are already generating."
            )
        return output
    if request.kind == "glossary":
        return NotesOutput(
            recording_id=transcript.recording_id, kind="glossary", name="Glossary"
        )
    template = builtin_notes_template()
    if request.notes_template_id is not None:
        template = await _requested_template(db, user, request.notes_template_id)
    await _refuse_repeated_set(db, transcript, template)
    return NotesOutput(
        recording_id=transcript.recording_id,
        name=template.name,
        notes_template_id=template.template_id,
        notes_template_sections=template.sections,
    )


@router.get("/{recording_id}/notes/outputs", response_model=list[NotesOutputRead])
async def list_notes_outputs(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[NotesOutput]:
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    result = await db.execute(
        select(NotesOutput)
        .where(NotesOutput.recording_id == recording.id)
        .order_by(NotesOutput.id)
    )
    return list(result.scalars().all())


@router.put("/{recording_id}/notes/outputs/{output_id}", response_model=NotesOutputRead)
async def update_notes_output(
    recording_id: str,
    output_id: int,
    update: NotesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotesOutput:
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    output = await _get_notes_output(db, recording.id, output_id)
    if output.kind == "glossary":
        raise HTTPException(
            status_code=400,
            detail="Regenerate glossary suggestions to replace their lines.",
        )
    if output.notes_status == "generating":
        raise HTTPException(
            status_code=409, detail="Meeting notes are already generating."
        )
    output.notes = update.notes
    db.add(output)
    await db.commit()
    await db.refresh(output)
    return output


@router.delete("/{recording_id}/notes/outputs/{output_id}", status_code=204)
async def delete_notes_output(
    recording_id: str,
    output_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    output = await _get_notes_output(db, recording.id, output_id)
    if output.notes_status == "generating":
        raise HTTPException(
            status_code=409, detail="Meeting notes are already generating."
        )
    await db.delete(output)
    await db.commit()


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


@router.post("/{recording_id}/notes/generate")
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
    built-in structure. See ``GenerateNotesRequest`` for extra sets.
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

    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

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

    if transcript.notes_status == "generating":
        raise HTTPException(
            status_code=409, detail="Meeting notes are already generating."
        )

    request = request or GenerateNotesRequest()
    output = await _generation_output(db, transcript, request, current_user)

    llm_config = await resolve_llm_config_async(
        db, current_user.settings or {}, user_id=current_user.id
    )
    missing_llm_config = llm_config.missing_configuration_message()
    if missing_llm_config:
        if output is None:
            transcript.notes_status = "error"
            transcript.set_notes_error_message(missing_llm_config)
            db.add(transcript)
            await db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"{missing_llm_config}. Configure an AI provider and model in Settings.",
        )

    target = output if output is not None else transcript
    target.notes_status = "generating"
    target.set_notes_error_message(None)
    db.add(target)
    await db.commit()
    await db.refresh(target)

    task = await dispatch_task(
        "backend.worker.tasks.generate_notes_task",
        args=(
            [recording.id, None, output.id]
            if output is not None
            else [recording.id, request.notes_template_id]
        ),
        kwargs={"extract_more": True} if request.extract_more else None,
    )
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, current_user.id)

    return {
        "status": "success",
        "notes_status": target.notes_status,
        "error_message": target.error_message,
        "output": NotesOutputRead.model_validate(output)
        if output is not None
        else None,
        "message": "Note generation started",
    }
