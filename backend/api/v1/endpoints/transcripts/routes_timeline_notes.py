"""The user's timeline notes on a recording.

Notes belong to the recording, and only its owner reads or writes them: any
other account gets a 404 for the recording, as for every transcript route. A
note id from another recording is a 404 too. Each write is a row of its own, so
creates never collide; an edit names the revision it was made against and is
refused with 409 (carrying the note as it now is) when another device has
changed it since. A delete may name the revision too, and is refused the same
way; without one it is unconditional.

Every write may carry a ``write_token``. The note keeps the token of the write
that made its revision, and a delete leaves a tombstone holding the delete's
token, so a write for a deleted note hears who deleted it (410
``note_deleted_by_you`` or ``note_deleted_elsewhere``) rather than a bare 404,
and a retried create cannot bring a deleted note back. A delete with a token
of an id that no note has held leaves a tombstone too, so a create whose answer
was lost cannot land after the user cleared it. Creates and deletes of one id
are serialised by an advisory lock on it. Every refusal's detail is
``{code, message}``, the ``code`` stable (``TimelineNoteErrorCode``).
"""

from dataclasses import dataclass
from typing import Any, NoReturn
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.models.recording import Recording
from backend.models.timeline_note import (
    TimelineNote,
    TimelineNoteKind,
    TimelineNoteTombstone,
)
from backend.models.timeline_note_public import (
    TimelineNoteCreate,
    TimelineNoteErrorCode,
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

RECORDING_NOT_FOUND_DETAIL = "Recording not found"
NOTE_NOT_FOUND_DETAIL = "Note not found"
NOTE_DELETED_BY_YOU_DETAIL = "The note was deleted by this write's own token"
NOTE_DELETED_ELSEWHERE_DETAIL = "The note was deleted elsewhere"
NOTE_REVISION_CONFLICT_DETAIL = "The note was changed elsewhere since you loaded it"
NOTE_ID_TAKEN_DETAIL = "That note id is already in use"
NOTE_NOT_MOVABLE_DETAIL = "Only a point note can be moved"


def _refused(
    status_code: int, code: TimelineNoteErrorCode, message: str, **extra: Any
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code.value, "message": message, **extra},
    )


def _token(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


async def _owned_recording(
    db: AsyncSession, recording_id: str, user_id: int
) -> Recording:
    try:
        return await _get_owned_recording(db, recording_id, user_id)
    except HTTPException as error:
        if error.status_code != status.HTTP_404_NOT_FOUND:
            raise
        raise _refused(
            404, TimelineNoteErrorCode.RECORDING_NOT_FOUND, RECORDING_NOT_FOUND_DETAIL
        ) from error


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


async def _tombstone(db: AsyncSession, note_id: str) -> TimelineNoteTombstone | None:
    return (
        await db.execute(
            select(TimelineNoteTombstone).where(
                TimelineNoteTombstone.public_id == note_id
            )
        )
    ).scalar_one_or_none()


def _deleted_by(tombstone: TimelineNoteTombstone, write_token: str | None) -> bool:
    """Whether the delete that left this tombstone carried this write's token."""
    return write_token is not None and tombstone.deleted_write_token == write_token


def _deleted(
    tombstone: TimelineNoteTombstone, write_token: str | None
) -> HTTPException:
    """The 410 for a write to a deleted note, naming whose delete it was."""
    if _deleted_by(tombstone, write_token):
        return _refused(
            410, TimelineNoteErrorCode.NOTE_DELETED_BY_YOU, NOTE_DELETED_BY_YOU_DETAIL
        )
    return _refused(
        410,
        TimelineNoteErrorCode.NOTE_DELETED_ELSEWHERE,
        NOTE_DELETED_ELSEWHERE_DETAIL,
    )


def _missing(
    tombstone: TimelineNoteTombstone | None, write_token: str | None
) -> HTTPException:
    """The refusal for a note this recording does not have: deleted, or never there."""
    if tombstone is not None:
        return _deleted(tombstone, write_token)
    return _refused(404, TimelineNoteErrorCode.NOTE_NOT_FOUND, NOTE_NOT_FOUND_DETAIL)


async def _lock_note_id(db: AsyncSession, note_id: str) -> None:
    """Hold a note id until the commit, against creates and deletes of it.

    A create checks that no tombstone holds its id and then inserts; a delete
    of an id with no note on the recording records a tombstone for it. Both
    take this lock first, so neither runs between the other's check and its
    commit: a create retried while its note is cleared can never leave the
    note beside its tombstone. On PostgreSQL this is a transaction-scoped
    advisory lock on the id's hash (a clash of hashes only makes two writes
    wait for each other). Other databases run only in tests, one request at a
    time, and take no lock.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:note_id))"),
        {"note_id": note_id},
    )


def _canonical_note_id(note_id: str) -> str | None:
    """The id as a create stores it (a lowercase hyphenated UUID), or None."""
    try:
        canonical = str(UUID(note_id))
    except ValueError:
        return None
    return canonical if canonical == note_id else None


async def _note_id_in_use(db: AsyncSession, note_id: str) -> bool:
    """Whether any note holds this id, so a refused insert clashed on it."""
    held = await db.execute(
        select(TimelineNote.id).where(TimelineNote.public_id == note_id)
    )
    return held.first() is not None


async def _locked_note(
    db: AsyncSession, recording_pk: int, note_id: str
) -> TimelineNote | None:
    return (
        await db.execute(
            select(TimelineNote)
            .where(TimelineNote.public_id == note_id)
            .where(TimelineNote.recording_id == recording_pk)
            # Holds the row from the revision check to the commit, so two
            # writes made against the same revision cannot both pass the
            # check, and a write that waited on a delete finds no row and
            # then the tombstone.
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _tombstone_on(
    db: AsyncSession, recording_pk: int, note_id: str
) -> TimelineNoteTombstone | None:
    """The tombstone of a note deleted from this recording; another's is a 404."""
    tombstone = await _tombstone(db, note_id)
    if tombstone is None or tombstone.recording_id != recording_pk:
        return None
    return tombstone


async def _refuse_stale_revision(
    db: AsyncSession, note: TimelineNote, recording_public_id: str
) -> NoReturn:
    current = await _read(db, note, recording_public_id)
    await db.rollback()
    raise _refused(
        409,
        TimelineNoteErrorCode.REVISION_CONFLICT,
        NOTE_REVISION_CONFLICT_DETAIL,
        note=current.model_dump(mode="json"),
    )


async def _replayed_create(
    db: AsyncSession, recording_pk: int, note_id: str, write_token: str | None
) -> TimelineNote | None:
    """What a create under a client id already used finds, before inserting.

    The note an earlier attempt made (returned, as it is now), or the
    tombstone it left when deleted (refused, so it is not made again).
    """
    existing = await _existing_for_retry(db, recording_pk, note_id)
    if existing is not None:
        return existing
    tombstone = await _tombstone(db, note_id)
    if tombstone is None:
        return None
    if tombstone.recording_id != recording_pk:
        # A public id names one note while its recording exists, deleted or not.
        raise _refused(409, TimelineNoteErrorCode.NOTE_ID_IN_USE, NOTE_ID_TAKEN_DETAIL)
    raise _deleted(tombstone, write_token)


async def _record_unused_id(
    db: AsyncSession, recording_pk: int, note_id: str, write_token: str | None
) -> bool:
    """Tombstone an id no note or tombstone holds, for a delete with a token.

    Such a delete clears a create whose answer was lost and which has not
    landed (yet): the tombstone makes that create a 410 when it arrives. Only
    an id a create could have stored is recorded, and none held elsewhere.
    """
    canonical = _canonical_note_id(note_id)
    if write_token is None or canonical is None:
        return False
    if await _note_id_in_use(db, canonical) or await _tombstone(db, canonical):
        return False
    db.add(
        TimelineNoteTombstone(
            public_id=canonical,
            recording_id=recording_pk,
            deleted_write_token=write_token,
        )
    )
    await db.commit()
    return True


@dataclass(frozen=True)
class _DeleteConditions:
    expected_revision: int | None
    write_token: str | None


def _delete_conditions(
    expected_revision: int | None = Query(default=None, ge=1),
    write_token: UUID | None = Query(default=None),
) -> _DeleteConditions:
    """A delete's query: the revision it was made against, and its write token."""
    return _DeleteConditions(expected_revision, _token(write_token))


@router.get("/{recording_id}/timeline-notes", response_model=list[TimelineNoteRead])
async def list_timeline_notes(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every note on the recording, in timeline order."""
    recording = await _owned_recording(db, recording_id, current_user.id)
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
    # Read before any rollback, which expires every row the session holds,
    # the request's user included.
    user_id = current_user.id
    edge_enabled = is_meeting_edge_enabled(getattr(current_user, "settings", None))
    recording = await _owned_recording(db, recording_id, user_id)
    recording_pk, recording_public_id = recording.id, recording.public_id
    client_id = str(payload.id) if payload.id is not None else None
    write_token = _token(payload.write_token)

    if client_id is not None:
        await _lock_note_id(db, client_id)
        existing = await _replayed_create(db, recording_pk, client_id, write_token)
        if existing is not None:
            # The first create's note, as it is now: a retry never edits it.
            response.status_code = status.HTTP_200_OK
            return await _read(db, existing, recording_public_id)

    fields: dict[str, Any] = {
        "recording_id": recording_pk,
        "created_by_user_id": user_id,
        "kind": payload.kind.value,
        "body": payload.body,
        "anchor_ms": payload.anchor_ms,
        "start_ms": payload.start_ms,
        "end_ms": payload.end_ms,
        "quoted_text": payload.quoted_text,
        "last_write_token": write_token,
    }
    if client_id is not None:
        fields["public_id"] = client_id
    note = TimelineNote(**fields)
    db.add(note)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if client_id is not None:
            # Two retries of one create raced and the other made the note, or
            # the id is held on another recording.
            existing = await _existing_for_retry(db, recording_pk, client_id)
            if existing is not None:
                response.status_code = status.HTTP_200_OK
                return await _read(db, existing, recording_public_id)
            if await _note_id_in_use(db, client_id):
                raise _refused(
                    409, TimelineNoteErrorCode.NOTE_ID_IN_USE, NOTE_ID_TAKEN_DETAIL
                ) from None
        # Not a clash of ids: the recording was deleted meanwhile (404), or
        # something else refused the row.
        await _owned_recording(db, recording_id, user_id)
        raise
    await db.refresh(note)
    await _dispatch_meeting_edge_refresh(recording_pk, enabled=edge_enabled)
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
    edge_enabled = is_meeting_edge_enabled(getattr(current_user, "settings", None))
    recording = await _owned_recording(db, recording_id, current_user.id)
    recording_pk, recording_public_id = recording.id, recording.public_id
    write_token = _token(payload.write_token)
    note = await _locked_note(db, recording_pk, note_id)
    if note is None:
        raise _missing(await _tombstone_on(db, recording_pk, note_id), write_token)

    if note.revision != payload.expected_revision:
        await _refuse_stale_revision(db, note, recording_public_id)
    if payload.anchor_ms is not None and note.kind != TimelineNoteKind.POINT.value:
        await db.rollback()
        raise _refused(
            422, TimelineNoteErrorCode.NOTE_NOT_MOVABLE, NOTE_NOT_MOVABLE_DETAIL
        )

    body_changes = payload.body is not None and payload.body != note.body
    anchor_moves = payload.anchor_ms is not None and payload.anchor_ms != note.anchor_ms
    if not (body_changes or anchor_moves):
        # A repeat of what the note already holds: no new revision, so it
        # cannot refuse another device's next edit, and nothing to refresh.
        current = await _read(db, note, recording_public_id)
        await db.rollback()
        return current

    if payload.body is not None:
        note.body = payload.body
    if payload.anchor_ms is not None:
        note.anchor_ms = payload.anchor_ms
    note.revision += 1
    note.last_write_token = write_token
    # Set here as well as by the column's onupdate, so the response carries it
    # without reloading the row.
    note.updated_at = utc_now()
    db.add(note)
    await db.commit()
    await db.refresh(note)
    await _dispatch_meeting_edge_refresh(recording_pk, enabled=edge_enabled)
    return await _read(db, note, recording_public_id)


@router.delete(
    "/{recording_id}/timeline-notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_timeline_note(
    recording_id: str,
    note_id: str,
    conditions: _DeleteConditions = Depends(_delete_conditions),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a note; with ``expected_revision``, only if it is still that one.

    Deleting a note already deleted with the same ``write_token`` is a 204: a
    retry of that delete. A delete with a ``write_token`` of an id no note or
    tombstone holds is a 204 too, and leaves a tombstone: it clears a create
    whose answer was lost, which must not land later.
    """
    edge_enabled = is_meeting_edge_enabled(getattr(current_user, "settings", None))
    recording = await _owned_recording(db, recording_id, current_user.id)
    recording_pk, recording_public_id = recording.id, recording.public_id
    token = conditions.write_token
    await _lock_note_id(db, note_id)
    note = await _locked_note(db, recording_pk, note_id)
    if note is None:
        tombstone = await _tombstone_on(db, recording_pk, note_id)
        if tombstone is not None and _deleted_by(tombstone, token):
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if tombstone is None and await _record_unused_id(
            db, recording_pk, note_id, token
        ):
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        raise _missing(tombstone, token)
    expected_revision = conditions.expected_revision
    if expected_revision is not None and note.revision != expected_revision:
        await _refuse_stale_revision(db, note, recording_public_id)
    db.add(
        TimelineNoteTombstone(
            public_id=note.public_id,
            recording_id=recording_pk,
            deleted_write_token=token,
        )
    )
    await db.delete(note)
    await db.commit()
    await _dispatch_meeting_edge_refresh(recording_pk, enabled=edge_enabled)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
