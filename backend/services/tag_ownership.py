"""Tag ids a request supplies must name the caller's own tags.

Recording tags (``tags``) and people tags (``p_tags``) each belong to one
user. An id naming another user's tag, a tag with no owner or no tag at all
answers 404 with the same body, the rule the People routes apply to people.

Among one user's tags, a tag never sits under itself or one of its own
sub-tags, and no two share a name. A change that would break either answers
400. Names compare exactly, so "Alpha" and "alpha" are two names.

The routes check a name before they write it, and a unique index on
``(user_id, name)`` in each table settles a race between two requests that
both passed the check: the second write fails there, and the helpers below
answer it as the check would have.
"""

from collections.abc import Iterable
from typing import Any, Optional, TypeVar, Union

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.models.people_tag import PeopleTag
from backend.models.tag import Tag

TagModel = Union[type[Tag], type[PeopleTag]]
TagRow = TypeVar("TagRow", Tag, PeopleTag)

TAG_NOT_FOUND = "Tag not found"
PARENT_TAG_NOT_FOUND = "Parent tag not found"
TAG_OWN_PARENT = "Tag cannot be its own parent"
TAG_UNDER_OWN_SUB_TAG = "Tag cannot be nested under one of its own sub-tags"
TAG_NAME_TAKEN = "Tag with this name already exists"


async def require_own_tag_ids(
    db: AsyncSession,
    model: TagModel,
    tag_ids: Iterable[int],
    user_id: int,
    *,
    detail: str = TAG_NOT_FOUND,
) -> list[int]:
    """Return ``tag_ids`` without repeats, or answer 404 if any is not the user's."""
    unique_ids = list(dict.fromkeys(tag_ids))
    if not unique_ids:
        return []
    result = await db.execute(
        select(model.id).where(
            col(model.id).in_(unique_ids), col(model.user_id) == user_id
        )
    )
    if len(set(result.scalars().all())) != len(unique_ids):
        raise HTTPException(status_code=404, detail=detail)
    return unique_ids


async def require_own_parent(
    db: AsyncSession, model: TagModel, parent_id: Optional[int], user_id: int
) -> None:
    """Answer 404 unless ``parent_id`` is empty or names one of the user's tags."""
    if parent_id is not None:
        await require_own_tag_ids(
            db, model, [parent_id], user_id, detail=PARENT_TAG_NOT_FOUND
        )


async def require_parent_outside_subtree(
    db: AsyncSession,
    model: TagModel,
    tag_id: int,
    parent_id: Optional[int],
    user_id: int,
) -> None:
    """Answer 400 if ``parent_id`` is the tag itself or one of its sub-tags.

    Walks up from the new parent through the user's tags. The walk ends at a
    tag it has already passed, so a loop saved before this check existed
    cannot hold the request. Moving a tag in such a loop to a parent outside
    it is allowed, and is how the loop is broken.
    """
    if parent_id is None:
        return
    if parent_id == tag_id:
        raise HTTPException(status_code=400, detail=TAG_OWN_PARENT)
    result = await db.execute(
        select(model.id, model.parent_id).where(col(model.user_id) == user_id)
    )
    parent_of = {row[0]: row[1] for row in result.all()}
    passed: set[int] = set()
    ancestor = parent_of.get(parent_id)
    while ancestor is not None and ancestor not in passed:
        if ancestor == tag_id:
            raise HTTPException(status_code=400, detail=TAG_UNDER_OWN_SUB_TAG)
        passed.add(ancestor)
        ancestor = parent_of.get(ancestor)


async def find_tag_by_name(
    db: AsyncSession,
    model: type[TagRow],
    name: str,
    user_id: int,
    *,
    exclude_id: Optional[int] = None,
) -> Optional[TagRow]:
    """The user's tag called ``name``, other than ``exclude_id``, if any."""
    statement = select(model).where(
        col(model.name) == name, col(model.user_id) == user_id
    )
    if exclude_id is not None:
        statement = statement.where(col(model.id) != exclude_id)
    result = await db.execute(statement.limit(1))
    return result.scalars().first()


async def require_unused_name(
    db: AsyncSession,
    model: type[TagRow],
    name: str,
    user_id: int,
    *,
    detail: str = TAG_NAME_TAKEN,
) -> None:
    """Answer 400 if the user already has a tag called ``name``."""
    if await find_tag_by_name(db, model, name, user_id) is not None:
        raise HTTPException(status_code=400, detail=detail)


async def get_or_create_tag(
    db: AsyncSession,
    model: type[TagRow],
    name: str,
    user_id: int,
    **fields: Any,
) -> tuple[TagRow, bool]:
    """The user's tag called ``name``, created with ``fields`` when they have none.

    Returns the tag and whether it was created. The insert is flushed in a
    savepoint, so when another request created the name first, only the
    insert is undone, the caller's other changes stay pending, and the tag
    that request made is returned. The caller commits.
    """
    existing = await find_tag_by_name(db, model, name, user_id)
    if existing is not None:
        return existing, False
    tag = model(name=name, user_id=user_id, **fields)
    try:
        async with db.begin_nested():
            db.add(tag)
    except IntegrityError:
        existing = await find_tag_by_name(db, model, name, user_id)
        if existing is None:
            raise
        return existing, False
    return tag, True


async def commit_tag_name(
    db: AsyncSession, tag: Union[Tag, PeopleTag], *, detail: str = TAG_NAME_TAKEN
) -> None:
    """Commit ``tag``, new or renamed, after its name has been checked.

    When another request saved the name between that check and this commit,
    the unique index refuses the commit, which is rolled back and answered
    with the 400 the check gives. Any other failure is raised as it was. The
    rollback expires every loaded row, the current user included, so the
    caller reads nothing from them afterwards.
    """
    model, name, user_id, tag_id = type(tag), tag.name, tag.user_id, tag.id
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        held = (
            await find_tag_by_name(db, model, name, user_id, exclude_id=tag_id)
            if user_id is not None
            else None
        )
        if held is None:
            raise
        raise HTTPException(status_code=400, detail=detail) from None
