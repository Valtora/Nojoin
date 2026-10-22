from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.models.people_tag import PeopleTag
from backend.models.people_tag_schemas import (
    PeopleTagCreate,
    PeopleTagRead,
    PeopleTagUpdate,
)
from backend.models.user import User
from backend.services.tag_ownership import (
    TAG_NOT_FOUND,
    require_own_parent,
    require_parent_outside_subtree,
    require_unused_name,
)

router = APIRouter()


async def _get_own_tag(db: AsyncSession, tag_id: int, user_id: int) -> PeopleTag:
    tag = await db.get(PeopleTag, tag_id)
    if not tag or tag.user_id != user_id:
        raise HTTPException(status_code=404, detail=TAG_NOT_FOUND)
    return tag


@router.get("/", response_model=List[PeopleTagRead])
async def list_people_tags(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    List the user's own tags for people.
    """
    query = (
        select(PeopleTag)
        .where(PeopleTag.user_id == current_user.id)
        .order_by(PeopleTag.name)
    )

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/", response_model=PeopleTagRead)
async def create_people_tag(
    tag_in: PeopleTagCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new tag for people.
    """
    await require_own_parent(db, PeopleTag, tag_in.parent_id, current_user.id)
    await require_unused_name(
        db, PeopleTag, tag_in.name, current_user.id, detail="Tag already exists"
    )

    tag = PeopleTag(
        name=tag_in.name,
        color=tag_in.color,
        user_id=current_user.id,
        parent_id=tag_in.parent_id,
    )
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


@router.patch("/{tag_id}", response_model=PeopleTagRead)
async def update_people_tag(
    tag_id: int,
    tag_in: PeopleTagUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a tag.
    """
    tag = await _get_own_tag(db, tag_id, current_user.id)

    update_data = tag_in.model_dump(exclude_unset=True)
    # A tag always has a name, so null leaves it as it is, as on PATCH /tags.
    if update_data.get("name") is None:
        update_data.pop("name", None)
    if "parent_id" in update_data:
        await require_own_parent(
            db, PeopleTag, update_data["parent_id"], current_user.id
        )
        await require_parent_outside_subtree(
            db, PeopleTag, tag_id, update_data["parent_id"], current_user.id
        )
    if update_data.get("name", tag.name) != tag.name:
        await require_unused_name(db, PeopleTag, update_data["name"], current_user.id)

    for field, value in update_data.items():
        setattr(tag, field, value)

    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


@router.delete("/{tag_id}")
async def delete_people_tag(
    tag_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a tag.
    """
    tag = await _get_own_tag(db, tag_id, current_user.id)

    await db.delete(tag)
    await db.commit()
    return {"ok": True}
