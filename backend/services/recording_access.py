"""Who may do what with a recording: the owner, and anyone it is shared with.

Levels, lowest first: ``VIEW`` reads, ``EDIT`` changes shared content (the
transcript, speakers, notes, timeline notes, documents), ``OWNER`` covers the
recording's lifecycle and anything that spends the owner's processing or AI
budget. The owner is ``recordings.user_id``; everyone else gets the highest
level among the shares that apply to them, directly or through one of their
identity-provider groups (``users.oidc_groups``).

A route opts in to sharing by declaring :func:`require_recording` with the
level it needs. Routes that do not keep the owner-only check in
``api/v1/endpoints/recordings/helpers.py``, so a route nobody migrated stays
closed to everyone but the owner. backend/tests/test_recording_access_matrix.py
calls every route that declares a level as owner, editor, viewer and stranger.

Two identities matter inside a shared recording: the **library owner**
(``recording.user_id``), whose people, voiceprints, AI settings and quotas the
recording uses, and the **actor** (the signed-in user), who is recorded as
having made a change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable, Optional, Sequence

from fastapi import Depends, HTTPException, status
from sqlalchemy import ColumnElement, and_, exists, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.models.recording import Recording
from backend.models.recording_share import RecordingShare, ShareLevel
from backend.models.user import User

INSUFFICIENT_ACCESS_DETAIL = "insufficient_access"
NOT_FOUND_DETAIL = "Recording not found"


class Access(IntEnum):
    NONE = 0
    VIEW = 1
    EDIT = 2
    OWNER = 3

    @property
    def label(self) -> str:
        return {Access.VIEW: "view", Access.EDIT: "edit", Access.OWNER: "owner"}.get(
            self, "none"
        )


_LEVEL_ACCESS = {ShareLevel.VIEW.value: Access.VIEW, ShareLevel.EDIT.value: Access.EDIT}


def user_groups(user: User) -> list[str]:
    groups = getattr(user, "oidc_groups", None) or []
    return [group for group in groups if isinstance(group, str) and group]


def share_applies_to(user: User) -> ColumnElement[bool]:
    """A ``recording_shares`` row grants something to ``user``."""
    groups = user_groups(user)
    direct = RecordingShare.user_id == user.id
    if not groups:
        return direct
    return or_(direct, RecordingShare.group_name.in_(groups))


def accessible_recordings_filter(user: User) -> ColumnElement[bool]:
    """Recordings ``user`` owns or has any share of. For list and search queries."""
    shared = exists().where(
        and_(RecordingShare.recording_id == Recording.id, share_applies_to(user))
    )
    return or_(Recording.user_id == user.id, shared)


def shared_with_filter(user: User) -> ColumnElement[bool]:
    """Recordings shared with ``user`` that ``user`` does not own."""
    shared = exists().where(
        and_(RecordingShare.recording_id == Recording.id, share_applies_to(user))
    )
    return and_(Recording.user_id != user.id, shared)


def owned_filter(user: User) -> ColumnElement[bool]:
    return Recording.user_id == user.id


def no_recordings() -> ColumnElement[bool]:
    return false()


async def share_level_for(db: AsyncSession, recording_id: int, user: User) -> Access:
    """The highest level any share of ``recording_id`` gives ``user``."""
    result = await db.execute(
        select(RecordingShare.level).where(
            RecordingShare.recording_id == recording_id, share_applies_to(user)
        )
    )
    levels = [_LEVEL_ACCESS.get(level, Access.NONE) for level in result.scalars()]
    return max(levels, default=Access.NONE)


async def access_level(db: AsyncSession, recording: Recording, user: User) -> Access:
    if recording.user_id == user.id:
        return Access.OWNER
    assert recording.id is not None
    return await share_level_for(db, recording.id, user)


async def access_levels_for(
    db: AsyncSession, recordings: Sequence[Recording], user: User
) -> dict[int, Access]:
    """Levels for a page of recordings in one query, keyed by recording id."""
    levels: dict[int, Access] = {}
    others: list[int] = []
    for recording in recordings:
        assert recording.id is not None
        if recording.user_id == user.id:
            levels[recording.id] = Access.OWNER
        else:
            levels[recording.id] = Access.NONE
            others.append(recording.id)
    if others:
        result = await db.execute(
            select(RecordingShare.recording_id, RecordingShare.level).where(
                RecordingShare.recording_id.in_(others), share_applies_to(user)
            )
        )
        for recording_id, level in result.all():
            levels[recording_id] = max(
                levels[recording_id], _LEVEL_ACCESS.get(level, Access.NONE)
            )
    return levels


async def get_recording_for(
    db: AsyncSession,
    public_id: str,
    user: User,
    need: Access,
    *,
    options: Optional[Sequence[Any]] = None,
) -> tuple[Recording, Access]:
    """Load a recording ``user`` may use at level ``need``.

    404 when the user may not see it at all (the same answer a missing
    recording gets, so sharing reveals nothing to strangers), 403 when they
    may see it but ``need`` is above their level.
    """
    statement = select(Recording).where(Recording.public_id == public_id)
    for option in options or ():
        statement = statement.options(option)
    recording = (await db.execute(statement)).scalar_one_or_none()
    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL
        )
    level = await access_level(db, recording, user)
    if level < Access.VIEW:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL
        )
    if level < need:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=INSUFFICIENT_ACCESS_DETAIL
        )
    return recording, level


@dataclass(frozen=True)
class RecordingGrant:
    """What :func:`require_recording` resolved: the recording, the caller's
    level, and the caller. ``recording`` is loaded without relationship
    options; :meth:`load` reloads it with the ones a handler needs."""

    recording: Recording
    access: Access
    user: User

    @property
    def is_owner(self) -> bool:
        return self.access is Access.OWNER

    @property
    def library_owner_id(self) -> int:
        """Whose people, voiceprints and AI settings this recording uses."""
        return self.recording.user_id

    async def load(
        self, db: AsyncSession, *, options: Optional[Sequence[Any]] = None
    ) -> Recording:
        if not options:
            return self.recording
        statement = select(Recording).where(Recording.id == self.recording.id)
        for option in options:
            statement = statement.options(option)
        statement = statement.execution_options(populate_existing=True)
        return (await db.execute(statement)).scalar_one()


def require_recording(need: Access) -> Callable[..., Any]:
    """A dependency that resolves ``{recording_id}`` for a caller with ``need``.

    Runs before FastAPI validates the request body, so a refused caller gets
    the 404 or 403 whatever they sent.
    """

    async def dependency(
        recording_id: str,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> RecordingGrant:
        recording, level = await get_recording_for(db, recording_id, current_user, need)
        return RecordingGrant(recording=recording, access=level, user=current_user)

    dependency.required_access = need  # type: ignore[attr-defined]
    dependency.__name__ = f"require_recording_{need.label}"
    return dependency


def declared_access(route: Any) -> Optional[Access]:
    """The level a route declares through :func:`require_recording`, if any."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return None
    stack = list(dependant.dependencies)
    while stack:
        current = stack.pop()
        level = getattr(current.call, "required_access", None)
        if isinstance(level, Access):
            return level
        stack.extend(current.dependencies)
    return None
