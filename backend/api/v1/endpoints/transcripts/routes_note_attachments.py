"""The file a timeline note holds, such as a still of the shared screen (#653).

A note holds one file of any type, stored as a document with ``note_id`` set
(``backend.utils.note_attachments``), so download, backup and deletion work as
they do for any document, and the file takes its time from its note. The
client uploads it once the note is saved. The file is not parsed until its
switch is turned on, which only a readable format allows, and turning the
switch either way marks notes already written as stale. Deleting the note
deletes the file (``routes_timeline_notes``).

Removing the file (#734) is one request, so the file and what becomes of its
note are decided in one transaction: a note whose text is still the one its
file gave it, "Screen capture" or the file's name, goes with its file
(``note_goes_with_its_file``), and any other note stays as a text note,
fenced (``TimelineNote.file_removed``) so that an upload still on its way for
it cannot bring the file back.
"""

import logging

from fastapi import Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.api.deps import get_current_user, get_db
from backend.core.task_dispatch import dispatch_task
from backend.models.document import Document, DocumentParseMode, DocumentStatus
from backend.models.recording import Recording
from backend.models.timeline_note import TimelineNote
from backend.models.timeline_note_public import (
    NoteAttachmentRead,
    NoteAttachmentUpdate,
    NoteFileRemovedRead,
    TimelineNoteErrorCode,
)
from backend.models.user import User
from backend.services.timeline_note_service import serialize_attachment
from backend.utils.config_manager import is_meeting_edge_enabled
from backend.utils.document_storage import delete_document_files
from backend.utils.note_attachments import (
    clean_title,
    is_readable,
    media_type_for,
    note_goes_with_its_file,
)

from .. import documents as documents_ep
from .router import router
from .routes_timeline_notes import (
    NOTE_NOT_FOUND_DETAIL,
    _delete_note_and_its_file,
    _lock_note_id,
    _locked_note,
    _mark_written_notes_stale,
    _missing,
    _owned_recording,
    _read,
    _refused,
    _tombstone_on,
)

logger = logging.getLogger(__name__)

ATTACHMENT_EXISTS_DETAIL = "This note already holds a file."
FILE_REMOVED_DETAIL = "This note's file was removed, so it takes no other."
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


async def _file_was_removed(db: AsyncSession, note_pk: int) -> bool:
    """Whether the note's file was removed, read under a share lock to the commit.

    The lock waits for a removal of the note's file in progress, so an upload
    that passed the first check while the file was being removed still sees
    the fence before its row goes in.
    """
    removed = await db.execute(
        select(TimelineNote.file_removed)
        .where(TimelineNote.id == note_pk)
        .with_for_update(read=True)
    )
    return bool(removed.scalar_one_or_none())


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
    """Store a file of any type on a note, switched off and unread.

    A second file is a 409, and any file for a note whose file was removed a 410.
    """
    user_id = current_user.id
    recording = await _owned_recording(db, recording_id, user_id)
    note = await _note(db, recording, note_id)
    note_pk = note.id
    if note.file_removed:
        raise HTTPException(status_code=410, detail=FILE_REMOVED_DETAIL)
    if await _attachment(db, note) is not None:
        raise HTTPException(status_code=409, detail=ATTACHMENT_EXISTS_DETAIL)
    file_path, size_bytes = await documents_ep.store_upload(
        request, file, user_id=user_id, recording=recording, any_format=True
    )
    if await _file_was_removed(db, note_pk):
        # Removed while this upload was stored: it was the removed file's.
        await db.rollback()
        await delete_document_files([file_path], logger=logger)
        raise HTTPException(status_code=410, detail=FILE_REMOVED_DETAIL)
    title = clean_title(file.filename)
    document = Document(
        recording_id=recording.id,
        note_id=note_pk,
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
    await _mark_written_notes_stale(db, recording.id)
    await db.commit()
    await db.refresh(document)
    if parse:
        task = await dispatch_task(
            "backend.worker.tasks.process_document_task", args=[document.id, reparse]
        )
        from backend.models.task import register_task_ownership

        await register_task_ownership(db, task.id, user_id)
    return serialize_attachment(document)


@router.delete(
    "/{recording_id}/timeline-notes/{note_id}/attachment",
    response_model=NoteFileRemovedRead,
)
async def remove_file_from_note(
    recording_id: str,
    note_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a note's file, and the note with it if nobody captioned it.

    A capture's point note whose text is still only "Screen capture", or a
    note whose text is still only the file's name, is deleted as the note's
    own delete does, leaving its tombstone; the answer's ``note`` is then
    null. Any other note stays without its file and takes no other. A
    file switched into the notes marks notes already written as stale, as
    switching it off does. A note that is gone is the 404 or 410 its delete
    would give, and one with no file a 404.
    """
    # Read before any rollback, which expires the request's user.
    user_id = current_user.id
    edge_enabled = is_meeting_edge_enabled(getattr(current_user, "settings", None))
    recording = await _owned_recording(db, recording_id, user_id)
    recording_pk, recording_public_id = recording.id, recording.public_id
    # The same locks as the note's delete, in the same order: the id, then the row.
    await _lock_note_id(db, note_id)
    note = await _locked_note(db, recording_pk, note_id)
    if note is None:
        refusal = _missing(await _tombstone_on(db, recording_pk, note_id), None)
        await db.rollback()
        raise refusal
    document = await _attachment(db, note)
    if document is None:
        await db.rollback()
        raise HTTPException(status_code=404, detail=NO_ATTACHMENT_DETAIL)
    if note_goes_with_its_file(note, document.title):
        await _delete_note_and_its_file(
            db, note, write_token=None, edge_enabled=edge_enabled
        )
        return NoteFileRemovedRead(note=None)
    file_path, fed_notes = document.file_path, bool(document.use_in_notes)
    # Its pages and chunks cascade, so search and chat lose it with the row.
    await db.execute(delete(Document).where(col(Document.id) == document.id))
    note.file_removed = True
    db.add(note)
    if fed_notes:
        await _mark_written_notes_stale(db, recording_pk)
    await db.commit()
    await delete_document_files([file_path], logger=logger)
    await db.refresh(note)
    return NoteFileRemovedRead(note=await _read(db, note, recording_public_id))
