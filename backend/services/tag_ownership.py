"""Tag ids a request supplies must name the caller's own tags.

Recording tags (``tags``) and people tags (``p_tags``) each belong to one
user. An id naming another user's tag, a tag with no owner or no tag at all
answers 404 with the same body, the rule the People routes apply to people.

Among one user's tags, a tag never sits under itself or one of its own
sub-tags, and no two share a name. A change that would break either answers
400.
"""

from collections.abc import Iterable
from typing import Optional, Union

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.models.people_tag import PeopleTag
from backend.models.tag import Tag

TagModel = Union[type[Tag], type[PeopleTag]]

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


async def require_unused_name(
    db: AsyncSession,
    model: TagModel,
    name: str,
    user_id: int,
    *,
    detail: str = TAG_NAME_TAKEN,
) -> None:
    """Answer 400 if the user already has a tag called ``name``.

    Any match will do, so a name held twice before this check existed answers
    400 rather than failing a lookup that expects one row.
    """
    result = await db.execute(
        select(model.id)
        .where(col(model.name) == name, col(model.user_id) == user_id)
        .limit(1)
    )
    if result.first() is not None:
        raise HTTPException(status_code=400, detail=detail)
