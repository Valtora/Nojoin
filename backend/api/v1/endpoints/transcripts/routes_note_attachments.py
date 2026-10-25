"""The file a timeline note holds, such as a still of the shared screen (#653).

A note holds one file of any type, stored as a document with ``note_id`` set
(``backend.utils.note_attachments``), so download, backup and deletion work as
they do for any document, and the file takes its time from its note. The
client uploads it once the note is saved. The file is not parsed until its
switch is turned on, which only a readable format allows, and turning the
switch either way marks notes already written as stale. Deleting the note
deletes the file (``routes_timeline_notes``).
"""

from fastapi import Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.core.task_dispatch import dispatch_task
from backend.models.document import Document, DocumentParseMode, DocumentStatus
from backend.models.recording import Recording
from backend.models.timeline_note import TimelineNote
from backend.models.timeline_note_public import (
    NoteAttachmentRead,
    NoteAttachmentUpdate,
    TimelineNoteErrorCode,
)
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services.timeline_note_service import serialize_attachment
from backend.utils.note_attachments import clean_title, is_readable, media_type_for

from .. import documents as documents_ep
from .router import router
from .routes_timeline_notes import NOTE_NOT_FOUND_DETAIL, _owned_recording, _refused

ATTACHMENT_EXISTS_DETAIL = "This note already holds a file."
NO_ATTACHMENT_DETAIL = "This note holds no file."
UNREADABLE_DETAIL = "Nojoin can't read this kind of file, so it can't be used in notes."


async def _note(db: AsyncSession, recording: Recording, note_id: str) -> TimelineNote:
    note = (
        await db.execute(
            select(TimelineNote)
            .where(TimelineNote.public_id == note_id)
            .where(TimelineNote.recording_id == recording.id)
        )
    ).scalar_one_or_none()
    if note is None:
        raise _refused(404, TimelineNoteErrorCode.NOTE_NOT_FOUND, NOTE_NOT_FOUND_DETAIL)
    return note


async def _attachment(db: AsyncSession, note: TimelineNote) -> Document | None:
    return (
        await db.execute(select(Document).where(Document.note_id == note.id))
    ).scalar_one_or_none()


@router.post(
    "/{recording_id}/timeline-notes/{note_id}/attachment",
    response_model=NoteAttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def attach_file_to_note(  # noqa: PLR0913 - FastAPI dependencies are parameters
    request: Request,
    recording_id: str,
    note_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Store a file of any type on a note, switched off and unread. A second is a 409."""
    user_id = current_user.id
    recording = await _owned_recording(db, recording_id, user_id)
    note = await _note(db, recording, note_id)
    if await _attachment(db, note) is not None:
        raise HTTPException(status_code=409, detail=ATTACHMENT_EXISTS_DETAIL)
    file_path, size_bytes = await documents_ep.store_upload(
        request, file, user_id=user_id, recording=recording, any_format=True
    )
    title = clean_title(file.filename)
    document = Document(
        recording_id=recording.id,
        note_id=note.id,
        use_in_notes=False,
        title=title,
        file_path=file_path,
        # From the name, never the browser's word, which a download would echo.
        file_type=media_type_for(title),
        file_size_bytes=size_bytes,
        status=DocumentStatus.PENDING,
        parse_mode=DocumentParseMode.VISUAL,
    )
    try:
        # A note deleted meanwhile fails its key as a recording would: a 404.
        await documents_ep._insert_document(db, document)
    except IntegrityError:
        # Another upload for this note landed first; this one's file is gone.
        raise HTTPException(status_code=409, detail=ATTACHMENT_EXISTS_DETAIL) from None
    await db.commit()
    await db.refresh(document)
    return serialize_attachment(document)


@router.patch(
    "/{recording_id}/timeline-notes/{note_id}/attachment",
    response_model=NoteAttachmentRead,
)
async def set_note_file_use_in_notes(
    recording_id: str,
    note_id: str,
    payload: NoteAttachmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Turn a note's file on or off for the AI.

    Turning it on starts its parse, unless it was read already or is being
    read. Turning it either way marks notes already written as stale, as a
    document parsed after them does; regenerating stays the user's choice.
    """
    user_id = current_user.id
    recording = await _owned_recording(db, recording_id, user_id)
    document = await _attachment(db, await _note(db, recording, note_id))
    if document is None:
        raise HTTPException(status_code=404, detail=NO_ATTACHMENT_DETAIL)
    if bool(document.use_in_notes) == payload.use_in_notes:
        return serialize_attachment(document)
    if payload.use_in_notes and not is_readable(document):
        raise HTTPException(status_code=409, detail=UNREADABLE_DETAIL)
    document.use_in_notes = payload.use_in_notes
    reparse = document.status == DocumentStatus.ERROR
    parse = payload.use_in_notes and (
        reparse or document.status == DocumentStatus.PENDING
    )
    if parse:
        document.status = DocumentStatus.PENDING
        document.error_message = None
        document.parse_stage = "Queued"
    db.add(document)
    transcript = (
        await db.execute(
            select(Transcript).where(Transcript.recording_id == recording.id)
        )
    ).scalar_one_or_none()
    if (
        transcript is not None
        and transcript.notes_status == "completed"
        and (transcript.notes or "").strip()
    ):
        transcript.notes_stale_documents = True
        db.add(transcript)
    await db.commit()
    await db.refresh(document)
    if parse:
        task = await dispatch_task(
            "backend.worker.tasks.process_document_task", args=[document.id, reparse]
        )
        from backend.models.task import register_task_ownership

        await register_task_ownership(db, task.id, user_id)
    return serialize_attachment(document)
