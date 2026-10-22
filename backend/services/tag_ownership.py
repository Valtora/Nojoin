"""Tag ids a request supplies must name the caller's own tags.

Recording tags (``tags``) and people tags (``p_tags``) each belong to one
user. An id naming another user's tag, a tag with no owner or no tag at all
answers 404 with the same body, the rule the People routes apply to people.
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
