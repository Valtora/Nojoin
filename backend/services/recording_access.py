"""Who may do what with a recording: the owner, and anyone it is shared with.

Levels, lowest first: ``VIEW`` reads, ``EDIT`` changes shared content (the
transcript, speakers, notes, timeline notes, documents), ``OWNER`` covers the
recording's lifecycle and anything that spends the owner's processing or AI
budget. The owner is ``recordings.user_id``; everyone else gets the highest
level among the shares that apply to them, directly or through one of their
identity-provider groups (``user_identities.groups``).

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

from contextvars import ContextVar
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, exists, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.recording import Recording
from backend.models.recording_share import RecordingShare, ShareLevel
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.user import User
from backend.services.user_identities import display_names, groups_of

INSUFFICIENT_ACCESS_DETAIL = "insufficient_access"

# Whether the caller of the current request owns the recording it resolved.
# Speaker reads consult it to leave out the contact details the owner keeps on
# the people in their library (email, phone, notes): people a meeting is
# shared with see who spoke, not the owner's address book. True outside a
# request (workers, MCP), which only ever act for the owner.
viewer_owns_library: ContextVar[bool] = ContextVar("viewer_owns_library", default=True)
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


def share_applies_to(user: User, groups: Sequence[str]) -> ColumnElement[bool]:
    """A ``recording_shares`` row grants something to ``user``, a member of
    ``groups`` (from :func:`groups_of`)."""
    direct = RecordingShare.user_id == user.id
    if not groups:
        return direct
    return or_(direct, RecordingShare.group_name.in_(list(groups)))


def _shared_exists(user: User, groups: Sequence[str]) -> ColumnElement[bool]:
    return exists().where(
        and_(
            RecordingShare.recording_id == Recording.id,
            share_applies_to(user, groups),
        )
    )


def accessible_recordings_filter(
    user: User, groups: Sequence[str]
) -> ColumnElement[bool]:
    """Recordings ``user`` owns or has any share of. For list and search queries."""
    return or_(Recording.user_id == user.id, _shared_exists(user, groups))


def shared_with_filter(user: User, groups: Sequence[str]) -> ColumnElement[bool]:
    """Recordings shared with ``user`` that ``user`` does not own."""
    return and_(Recording.user_id != user.id, _shared_exists(user, groups))


def owned_filter(user: User, groups: Sequence[str] = ()) -> ColumnElement[bool]:
    return Recording.user_id == user.id


def no_recordings() -> ColumnElement[bool]:
    return false()


async def share_level_for(db: AsyncSession, recording_id: int, user: User) -> Access:
    """The highest level any share of ``recording_id`` gives ``user``.

    Reads the recording's shares first and the user's groups only when one of
    them is a group share, so the common case (not shared, or shared with
    named people) costs one small query.
    """
    rows = (
        await db.execute(
            select(
                RecordingShare.user_id, RecordingShare.group_name, RecordingShare.level
            ).where(RecordingShare.recording_id == recording_id)
        )
    ).all()
    if not rows:
        return Access.NONE
    groups: set[str] = set()
    if any(group_name is not None for _, group_name, _ in rows):
        groups = set(await groups_of(db, user))
    best = Access.NONE
    for user_id, group_name, level in rows:
        if user_id == user.id or (group_name is not None and group_name in groups):
            best = max(best, _LEVEL_ACCESS.get(level, Access.NONE))
    return best


def shelved_cap(recording: Recording, level: Access) -> Access:
    """A share's level on a meeting its owner archived or binned: view at most.

    The meeting stays readable for the people it is shared with, who can ask
    the owner to bring it back (``POST /recordings/{id}/shares/me/restore-request``),
    but nobody else changes a meeting its owner put away.
    """
    if level is Access.EDIT and (recording.is_archived or recording.is_deleted):
        return Access.VIEW
    return level


async def access_level(db: AsyncSession, recording: Recording, user: User) -> Access:
    if recording.user_id == user.id:
        return Access.OWNER
    assert recording.id is not None
    return shelved_cap(recording, await share_level_for(db, recording.id, user))


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
        groups = await groups_of(db, user)
        result = await db.execute(
            select(RecordingShare.recording_id, RecordingShare.level).where(
                RecordingShare.recording_id.in_(others),
                share_applies_to(user, groups),
            )
        )
        for recording_id, level in result.all():
            levels[recording_id] = max(
                levels[recording_id], _LEVEL_ACCESS.get(level, Access.NONE)
            )
        for recording in recordings:
            assert recording.id is not None
            levels[recording.id] = shelved_cap(recording, levels[recording.id])
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
    """A resolved :func:`require_recording`: the recording, the caller's level,
    and the caller. ``recording`` is loaded without relationship options;
    :meth:`load` reloads it with the ones a handler needs."""

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


@dataclass(frozen=True)
class PendingGrant:
    """What a route's ``require_recording`` dependency hands the handler: the
    level it declared, not yet checked. :func:`resolve_grant` checks it."""

    need: Access


def require_recording(need: Access) -> Callable[..., Any]:
    """Declare the level a route needs on ``{recording_id}``.

    The dependency itself touches nothing: FastAPI validates the request first,
    and the handler's first step is ``resolve_grant``, which loads the
    recording and refuses a caller below ``need``. Declaring the level here,
    rather than only in the handler, lets
    backend/tests/test_recording_access_matrix.py read every route's level.
    """

    def dependency() -> PendingGrant:
        return PendingGrant(need=need)

    dependency.required_access = need  # type: ignore[attr-defined]
    dependency.__name__ = f"require_recording_{need.label}"
    return dependency


async def resolve_grant(
    grant: Any,
    db: AsyncSession,
    recording_id: str,
    user: User,
    *,
    owned: Optional[Callable[..., Any]] = None,
) -> RecordingGrant:
    """Check the level a handler declared with ``require_recording``.

    ``owned`` is the route module's own owner-only loader
    (``_get_owned_recording(db, recording_id, user_id)``). It runs first, so
    the owner's path is exactly what it was before sharing existed; shares are
    consulted only when it answers 404.

    Over HTTP the handler receives a :class:`PendingGrant`. Called as a Python
    function (the MCP tools, tests) it receives the unresolved ``Depends(...)``
    default instead, and the level is read off that, so a direct caller is
    held to the route's level too.
    """
    if isinstance(grant, RecordingGrant):
        return grant
    if isinstance(grant, PendingGrant):
        need = grant.need
    else:
        need = getattr(getattr(grant, "dependency", None), "required_access", None)
    if not isinstance(need, Access):
        raise TypeError("grant must come from require_recording")
    if owned is not None:
        try:
            recording = await owned(db, recording_id, user.id)
        except HTTPException as refusal:
            if refusal.status_code != status.HTTP_404_NOT_FOUND:
                raise
        else:
            viewer_owns_library.set(True)
            return RecordingGrant(recording=recording, access=Access.OWNER, user=user)
    recording, level = await get_recording_for(db, recording_id, user, need)
    viewer_owns_library.set(level is Access.OWNER)
    return RecordingGrant(recording=recording, access=level, user=user)


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


async def owner_reads(
    db: AsyncSession, recordings: Sequence[Recording], viewer: User
) -> dict[int, Any]:
    """``RecordingOwnerRead`` for the owners of the recordings ``viewer`` does
    not own, keyed by owner id, in one query."""
    from backend.models.recording_public import RecordingOwnerRead

    owner_ids = {r.user_id for r in recordings if r.user_id != viewer.id}
    if not owner_ids:
        return {}
    rows = await db.execute(
        select(User.id, User.username).where(User.id.in_(owner_ids))
    )
    names = await display_names(db, owner_ids)
    return {
        user_id: RecordingOwnerRead(username=username, display_name=names.get(user_id))
        for user_id, username in rows.all()
    }


def people_in_reach(recording: Recording, actor_user_id: int) -> ColumnElement[bool]:
    """The people of the owner's library ``actor_user_id`` may pick in a meeting.

    The meeting's speakers belong to its owner's People library. The owner may
    pick anyone in it. Anyone else the meeting is shared with may pick only the
    people already linked to one of its speakers: they never see or link the
    rest of the owner's contacts, and a new name they type stays a name on the
    meeting (``RecordingSpeaker.local_name``) instead of joining the library.
    """
    in_library = GlobalSpeaker.user_id == recording.user_id
    if actor_user_id == recording.user_id:
        return in_library
    linked = select(RecordingSpeaker.global_speaker_id).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.global_speaker_id.is_not(None),
        RecordingSpeaker.merged_into_id.is_(None),
    )
    return and_(in_library, GlobalSpeaker.id.in_(linked))


async def require_person_in_reach(
    db: AsyncSession,
    recording: Recording,
    actor_user_id: int,
    global_speaker_id: Optional[int],
) -> None:
    """404 unless ``actor_user_id`` may pick person ``global_speaker_id`` here."""
    if global_speaker_id is None or actor_user_id == recording.user_id:
        return
    found = await db.scalar(
        select(GlobalSpeaker.id).where(
            GlobalSpeaker.id == global_speaker_id,
            people_in_reach(recording, actor_user_id),
        )
    )
    if found is None:
        raise HTTPException(status_code=404, detail="Global speaker not found")


async def directly_shared(
    db: AsyncSession, recording_ids: Sequence[int], user: User
) -> set[int]:
    """Which of ``recording_ids`` are shared with ``user`` by name.

    Those are the ones they can leave; a group share is the group's.
    """
    ids = list(recording_ids)
    if not ids:
        return set()
    result = await db.execute(
        select(RecordingShare.recording_id).where(
            RecordingShare.user_id == user.id,
            RecordingShare.recording_id.in_(ids),
        )
    )
    return set(result.scalars().all())
