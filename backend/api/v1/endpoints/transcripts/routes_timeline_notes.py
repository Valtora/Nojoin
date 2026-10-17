"""The user's timeline notes on a recording.

Notes belong to the recording, and only its owner reads or writes them: any
other account gets a 404 for the recording, as for every transcript route. A
note id from another recording is a 404 too. Each write is a row of its own, so
creates never collide; an edit names the revision it was made against and is
refused with 409 (carrying the note as it now is) when another device has
changed it since. A delete may name the revision too, and is refused the same
way; without one it is unconditional.
"""

from typing import Any, NoReturn

from fastapi import Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.models.recording import Recording
from backend.models.timeline_note import TimelineNote, TimelineNoteKind
from backend.models.timeline_note_public import (
    TimelineNoteCreate,
    TimelineNoteRead,
    TimelineNoteUpdate,
)
from backend.models.user import User
from backend.services.timeline_note_service import (
    load_author_names_async,
    load_recording_timeline_notes_async,
    serialize_recording_timeline_notes,
    serialize_timeline_note,
)
from backend.utils.config_manager import is_meeting_edge_enabled
from backend.utils.time import utc_now

from .helpers import _dispatch_meeting_edge_refresh, _get_owned_recording
from .router import router

NOTE_NOT_FOUND_DETAIL = "Note not found"
NOTE_REVISION_CONFLICT_DETAIL = "The note was changed elsewhere since you loaded it"
NOTE_ID_TAKEN_DETAIL = "That note id is already in use"


async def _read(
    db: AsyncSession, note: TimelineNote, recording_public_id: str
) -> TimelineNoteRead:
    return serialize_timeline_note(
        note,
        recording_public_id=recording_public_id,
        author_names=await load_author_names_async(db, [note]),
    )


async def _existing_for_retry(
    db: AsyncSession, recording_pk: int, note_id: str
) -> TimelineNote | None:
    """The note a retried create already made on this recording, if any.

    Looked up on the recording only, so a note on another recording is never
    returned. An id held there is refused at commit by the unique index, with
    409: that says the id is taken, which gives nothing away, since ids are
    random UUIDs that only the client that made a note knows.
    Takes plain ids because a rollback expires the request's ORM rows.
    """
    return (
        await db.execute(
            select(TimelineNote)
            .where(TimelineNote.public_id == note_id)
            .where(TimelineNote.recording_id == recording_pk)
        )
    ).scalar_one_or_none()


async def _get_note(
    db: AsyncSession, recording: Recording, note_id: str, *, for_update: bool = False
) -> TimelineNote:
    statement = (
        select(TimelineNote)
        .where(TimelineNote.public_id == note_id)
        .where(TimelineNote.recording_id == recording.id)
    )
    if for_update:
        # Holds the row from the revision check to the commit, so two edits
        # made against the same revision cannot both pass the check.
        statement = statement.with_for_update()
    note = (await db.execute(statement)).scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail=NOTE_NOT_FOUND_DETAIL)
    return note


async def _notes_changed(recording: Recording, user: User) -> None:
    await _dispatch_meeting_edge_refresh(
        recording.id,
        enabled=is_meeting_edge_enabled(getattr(user, "settings", None)),
    )


async def _refuse_stale_revision(
    db: AsyncSession, note: TimelineNote, recording_public_id: str
) -> NoReturn:
    current = await _read(db, note, recording_public_id)
    await db.rollback()
    raise HTTPException(
        status_code=409,
        detail={
            "message": NOTE_REVISION_CONFLICT_DETAIL,
            "note": current.model_dump(mode="json"),
        },
    )


@router.get("/{recording_id}/timeline-notes", response_model=list[TimelineNoteRead])
async def list_timeline_notes(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every note on the recording, in timeline order."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    return serialize_recording_timeline_notes(
        await load_recording_timeline_notes_async(db, recording.id),
        recording_public_id=recording.public_id,
    )


@router.post(
    "/{recording_id}/timeline-notes",
    response_model=TimelineNoteRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_timeline_note(
    recording_id: str,
    payload: TimelineNoteCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a note. Retrying with the same client ``id`` returns the first one (200)."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    recording_pk, recording_public_id = recording.id, recording.public_id
    client_id = str(payload.id) if payload.id is not None else None

    if client_id is not None:
        existing = await _existing_for_retry(db, recording_pk, client_id)
        if existing is not None:
            # The first create's note, as it is now: a retry never edits it.
            response.status_code = status.HTTP_200_OK
            return await _read(db, existing, recording_public_id)

    fields: dict[str, Any] = {
        "recording_id": recording.id,
        "created_by_user_id": current_user.id,
        "kind": payload.kind.value,
        "body": payload.body,
        "anchor_ms": payload.anchor_ms,
        "start_ms": payload.start_ms,
        "end_ms": payload.end_ms,
        "quoted_text": payload.quoted_text,
    }
    if client_id is not None:
        fields["public_id"] = client_id
    note = TimelineNote(**fields)
    db.add(note)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if client_id is None:
            raise
        # Two retries of one create raced and the other made the note, or the
        # id is held on another recording.
        existing = await _existing_for_retry(db, recording_pk, client_id)
        if existing is None:
            raise HTTPException(status_code=409, detail=NOTE_ID_TAKEN_DETAIL)
        response.status_code = status.HTTP_200_OK
        return await _read(db, existing, recording_public_id)
    await db.refresh(note)
    await _notes_changed(recording, current_user)
    return await _read(db, note, recording_public_id)


@router.patch(
    "/{recording_id}/timeline-notes/{note_id}", response_model=TimelineNoteRead
)
async def update_timeline_note(
    recording_id: str,
    note_id: str,
    payload: TimelineNoteUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit a note's text, or move a point note."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    note = await _get_note(db, recording, note_id, for_update=True)

    if note.revision != payload.expected_revision:
        await _refuse_stale_revision(db, note, recording.public_id)
    if payload.anchor_ms is not None and note.kind != TimelineNoteKind.POINT.value:
        await db.rollback()
        raise HTTPException(status_code=422, detail="Only a point note can be moved")

    body_changes = payload.body is not None and payload.body != note.body
    anchor_moves = payload.anchor_ms is not None and payload.anchor_ms != note.anchor_ms
    if not (body_changes or anchor_moves):
        # A repeat of what the note already holds: no new revision, so it
        # cannot refuse another device's next edit, and nothing to refresh.
        current = await _read(db, note, recording.public_id)
        await db.rollback()
        return current

    if payload.body is not None:
        note.body = payload.body
    if payload.anchor_ms is not None:
        note.anchor_ms = payload.anchor_ms
    note.revision += 1
    # Set here as well as by the column's onupdate, so the response carries it
    # without reloading the row.
    note.updated_at = utc_now()
    db.add(note)
    await db.commit()
    await db.refresh(note)
    await _notes_changed(recording, current_user)
    return await _read(db, note, recording.public_id)


@router.delete(
    "/{recording_id}/timeline-notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_timeline_note(
    recording_id: str,
    note_id: str,
    expected_revision: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a note; with ``expected_revision``, only if it is still that one."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    note = await _get_note(
        db, recording, note_id, for_update=expected_revision is not None
    )
    if expected_revision is not None and note.revision != expected_revision:
        await _refuse_stale_revision(db, note, recording.public_id)
    await db.delete(note)
    await db.commit()
    await _notes_changed(recording, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
