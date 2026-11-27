"""Asks to bring back a shared meeting its owner archived or binned.

While the owner has a shared meeting archived or in the bin, the people it is
shared with can only read it (recording_access.shelved_cap). Any of them can
ask the owner to bring it back. The ask is a task in the owner's own list,
linked to the meeting, and a ``recording_restore_requests`` row records it by
meeting and kind (``unarchive`` or ``restore``): there is at most one open ask
of each kind per meeting, whatever the meeting is called, and the owner
restoring the meeting completes it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.recording import Recording
from backend.models.recording_restore_request import (
    RecordingRestoreRequest,
    RestoreKind,
)
from backend.models.task import UserTask, UserTaskRecording
from backend.models.user import User
from backend.services.user_identities import display_names
from backend.utils.time import utc_now

TASK_TITLE_MAX_LENGTH = 255


@dataclass(frozen=True)
class RestoreAsk:
    task_id: int
    already_asked: bool


def kind_for(recording: Recording) -> RestoreKind:
    """What bringing the meeting back takes: out of the bin, or out of the archive."""
    return RestoreKind.RESTORE if recording.is_deleted else RestoreKind.UNARCHIVE


async def _open_ask(
    db: AsyncSession, recording_id: int, kind: RestoreKind
) -> tuple[RecordingRestoreRequest | None, UserTask | None]:
    """The meeting's ask of ``kind``, and its task while that is still open."""
    row = (
        await db.execute(
            select(RecordingRestoreRequest).where(
                RecordingRestoreRequest.recording_id == recording_id,
                RecordingRestoreRequest.kind == kind.value,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None, None
    task = await db.get(UserTask, row.task_id)
    if task is None or task.completed_at is not None or task.archived_at is not None:
        return row, None
    return row, task


async def ask_to_bring_back(
    db: AsyncSession, recording: Recording, asker: User
) -> RestoreAsk:
    """Put the ask in the owner's tasks, unless one of this kind is open."""
    assert recording.id is not None and asker.id is not None
    kind = kind_for(recording)
    row, open_task = await _open_ask(db, recording.id, kind)
    if open_task is not None:
        assert open_task.id is not None
        return RestoreAsk(task_id=open_task.id, already_asked=True)

    names = await display_names(db, [asker.id])
    who = names.get(asker.id) or asker.username
    title = f'{who} asks you to {kind.value} "{recording.name}"'
    if len(title) > TASK_TITLE_MAX_LENGTH:
        title = title[: TASK_TITLE_MAX_LENGTH - 2] + '…"'
    place = "the bin" if kind is RestoreKind.RESTORE else "the archive"
    task = UserTask(
        user_id=recording.user_id,
        title=title,
        body=(
            f"You shared this meeting with {who}. While it is in {place} they "
            "can only read it. This task is completed when you bring it back."
        ),
    )
    db.add(task)
    await db.flush()
    assert task.id is not None
    db.add(UserTaskRecording(task_id=task.id, recording_id=recording.id))
    if row is None:
        db.add(
            RecordingRestoreRequest(
                recording_id=recording.id,
                kind=kind.value,
                task_id=task.id,
                requested_by_id=asker.id,
            )
        )
    else:
        # The last ask's task was completed or put away without the meeting
        # coming back: this ask takes its place.
        row.task_id = task.id
        row.requested_by_id = asker.id
        db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # Someone else asked at the same moment; theirs stands.
        await db.rollback()
        _, open_task = await _open_ask(db, recording.id, kind)
        if open_task is None:
            raise
        assert open_task.id is not None
        return RestoreAsk(task_id=open_task.id, already_asked=True)
    return RestoreAsk(task_id=task.id, already_asked=False)


async def settle_asks(db: AsyncSession, recording_ids: Sequence[int]) -> None:
    """Complete the asks for meetings the owner just brought back.

    Restoring clears both the bin and the archive, so asks of both kinds are
    settled. The caller commits.
    """
    ids = [recording_id for recording_id in recording_ids if recording_id is not None]
    if not ids:
        return
    rows = (
        (
            await db.execute(
                select(RecordingRestoreRequest).where(
                    RecordingRestoreRequest.recording_id.in_(ids)
                )
            )
        )
        .scalars()
        .all()
    )
    now = utc_now()
    for row in rows:
        task = await db.get(UserTask, row.task_id)
        if task is not None and task.completed_at is None:
            task.completed_at = now
            db.add(task)
    await db.execute(
        delete(RecordingRestoreRequest).where(
            RecordingRestoreRequest.recording_id.in_(ids)
        )
    )
