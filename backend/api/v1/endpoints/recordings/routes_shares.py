"""Sharing a recording with other users or identity-provider groups.

Only the owner manages shares. Someone a meeting is shared with directly can
remove themselves (``DELETE .../shares/me``); a group share applies to the
group as a whole, so its members cannot leave it one by one. Anyone it is
shared with can ask the owner to bring it back once it is archived or binned
(``POST .../shares/me/restore-request``).
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.api.deps import get_current_user, get_db
from backend.api.params import RowId
from backend.models.recording_share import RecordingShare, ShareLevel
from backend.models.task import UserTask, UserTaskRecording
from backend.models.user import User
from backend.services.recording_access import (
    Access,
    RecordingGrant,
    require_recording,
    resolve_grant,
)
from backend.services.user_identities import display_names, groups_of

from .router import router

GROUP_NAME_MAX_LENGTH = 200


class ShareUserRead(BaseModel):
    id: int
    username: str
    display_name: Optional[str] = None


class RecordingShareRead(BaseModel):
    id: int
    user: Optional[ShareUserRead] = None
    group: Optional[str] = None
    level: ShareLevel


class RecordingShareUpsert(BaseModel):
    """Grant ``level`` to one user (``user_id``) or one group (``group``)."""

    user_id: Optional[RowId] = None
    group: Optional[str] = Field(
        default=None, min_length=1, max_length=GROUP_NAME_MAX_LENGTH
    )
    level: ShareLevel

    @model_validator(mode="after")
    def _one_grantee(self) -> "RecordingShareUpsert":
        if (self.user_id is None) == (self.group is None):
            raise ValueError("Give either user_id or group, not both")
        if self.group is not None:
            self.group = self.group.strip()
            if not self.group:
                raise ValueError("The group name is empty")
        return self


async def _share_reads(db: AsyncSession, recording_id: int) -> List[RecordingShareRead]:
    rows = (
        await db.execute(
            select(RecordingShare, User)
            .outerjoin(User, col(User.id) == col(RecordingShare.user_id))
            .where(RecordingShare.recording_id == recording_id)
            .order_by(col(RecordingShare.group_name).is_(None), col(RecordingShare.id))
        )
    ).all()
    names = await display_names(db, [user.id for _, user in rows if user is not None])
    reads: List[RecordingShareRead] = []
    for share, user in rows:
        assert share.id is not None
        reads.append(
            RecordingShareRead(
                id=share.id,
                user=(
                    ShareUserRead(
                        id=user.id,
                        username=user.username,
                        display_name=names.get(user.id),
                    )
                    if user is not None
                    else None
                ),
                group=share.group_name,
                level=ShareLevel(share.level),
            )
        )
    return reads


@router.get("/{recording_id}/shares", response_model=List[RecordingShareRead])
async def list_recording_shares(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    grant: RecordingGrant = Depends(require_recording(Access.OWNER)),
) -> List[RecordingShareRead]:
    grant = await resolve_grant(grant, db, recording_id, current_user)
    assert grant.recording.id is not None
    return await _share_reads(db, grant.recording.id)


@router.put("/{recording_id}/shares", response_model=List[RecordingShareRead])
async def upsert_recording_share(
    recording_id: str,
    body: RecordingShareUpsert,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    grant: RecordingGrant = Depends(require_recording(Access.OWNER)),
) -> List[RecordingShareRead]:
    """Share the recording, or change the level of an existing share."""
    grant = await resolve_grant(grant, db, recording_id, current_user)
    recording = grant.recording
    assert recording.id is not None
    if body.user_id is not None:
        target = await db.get(User, body.user_id)
        if target is None or not target.is_active:
            raise HTTPException(status_code=404, detail="User not found")
        if target.id == recording.user_id:
            raise HTTPException(status_code=400, detail="The owner already has access")
        existing_query = select(RecordingShare).where(
            RecordingShare.recording_id == recording.id,
            RecordingShare.user_id == target.id,
        )
    else:
        # Only to a group the sharer is in: a typo, or a group they cannot see
        # into, would otherwise hand the meeting to people they never meant.
        if body.group not in await groups_of(db, grant.user):
            raise HTTPException(status_code=400, detail="not_a_member_of_group")
        existing_query = select(RecordingShare).where(
            RecordingShare.recording_id == recording.id,
            RecordingShare.group_name == body.group,
        )
    share = (await db.execute(existing_query)).scalar_one_or_none()
    if share is None:
        share = RecordingShare(
            recording_id=recording.id,
            user_id=body.user_id,
            group_name=body.group if body.user_id is None else None,
            level=body.level.value,
            created_by_id=grant.user.id,
        )
    else:
        share.level = body.level.value
    db.add(share)
    await db.commit()
    return await _share_reads(db, recording.id)


# Declared before /shares/{share_id}, which would otherwise try to read "me"
# as a share id.
@router.delete("/{recording_id}/shares/me", status_code=status.HTTP_204_NO_CONTENT)
async def leave_recording_share(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    grant: RecordingGrant = Depends(require_recording(Access.VIEW)),
) -> None:
    """Stop seeing a meeting that was shared with you directly."""
    grant = await resolve_grant(grant, db, recording_id, current_user)
    if grant.is_owner:
        raise HTTPException(status_code=400, detail="The owner cannot leave")
    share = (
        await db.execute(
            select(RecordingShare).where(
                RecordingShare.recording_id == grant.recording.id,
                RecordingShare.user_id == grant.user.id,
            )
        )
    ).scalar_one_or_none()
    if share is None:
        raise HTTPException(
            status_code=409,
            detail="shared_through_group",
        )
    await db.delete(share)
    await db.commit()


class RestoreRequestRead(BaseModel):
    """The task added to the owner's list, or found there from an earlier ask."""

    task_id: int
    already_asked: bool


TASK_TITLE_MAX_LENGTH = 255


# Declared before /shares/{share_id} for the same reason as /shares/me.
@router.post(
    "/{recording_id}/shares/me/restore-request", response_model=RestoreRequestRead
)
async def request_recording_restore(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    grant: RecordingGrant = Depends(require_recording(Access.VIEW)),
) -> RestoreRequestRead:
    """Ask the owner to bring back a shared meeting they archived or binned.

    While it is put away, the people it is shared with can only read it. The
    ask lands in the owner's Tasks, linked to the meeting, and asking again
    while that task is open adds nothing.
    """
    grant = await resolve_grant(grant, db, recording_id, current_user)
    if grant.is_owner:
        raise HTTPException(status_code=400, detail="The owner cannot ask themselves")
    recording = grant.recording
    if not (recording.is_archived or recording.is_deleted):
        raise HTTPException(status_code=409, detail="recording_not_shelved")

    names = await display_names(db, [current_user.id])
    asker = names.get(current_user.id) or current_user.username
    verb = "restore" if recording.is_deleted else "unarchive"
    title = f'{asker} asks you to {verb} "{recording.name}"'
    if len(title) > TASK_TITLE_MAX_LENGTH:
        title = title[: TASK_TITLE_MAX_LENGTH - 2] + '…"'

    existing = (
        await db.execute(
            select(UserTask).where(
                UserTask.user_id == recording.user_id,
                UserTask.title == title,
                col(UserTask.completed_at).is_(None),
                col(UserTask.archived_at).is_(None),
                UserTask.recording_links.any(
                    UserTaskRecording.recording_id == recording.id
                ),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        assert existing.id is not None
        return RestoreRequestRead(task_id=existing.id, already_asked=True)

    place = "the bin" if recording.is_deleted else "the archive"
    task = UserTask(
        user_id=recording.user_id,
        title=title,
        body=(
            f"You shared this meeting with {asker}. While it is in {place} they "
            "can only read it."
        ),
    )
    db.add(task)
    await db.flush()
    assert task.id is not None
    db.add(UserTaskRecording(task_id=task.id, recording_id=recording.id))
    await db.commit()
    return RestoreRequestRead(task_id=task.id, already_asked=False)


@router.delete(
    "/{recording_id}/shares/{share_id}", response_model=List[RecordingShareRead]
)
async def delete_recording_share(
    recording_id: str,
    share_id: RowId,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    grant: RecordingGrant = Depends(require_recording(Access.OWNER)),
) -> List[RecordingShareRead]:
    grant = await resolve_grant(grant, db, recording_id, current_user)
    share = await db.get(RecordingShare, share_id)
    if share is None or share.recording_id != grant.recording.id:
        raise HTTPException(status_code=404, detail="Share not found")
    await db.delete(share)
    await db.commit()
    assert grant.recording.id is not None
    return await _share_reads(db, grant.recording.id)
