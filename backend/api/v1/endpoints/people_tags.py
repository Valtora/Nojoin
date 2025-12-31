from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_db, get_current_user
from backend.models.people_tag import PeopleTag
from backend.models.people_tag_schemas import PeopleTagCreate, PeopleTagRead, PeopleTagUpdate
from backend.models.user import User

router = APIRouter()

@router.get("/", response_model=List[PeopleTagRead])
async def list_people_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all tags for people.
    """
    query = select(PeopleTag).where(
        (PeopleTag.user_id == current_user.id) | (PeopleTag.user_id == None)
    ).order_by(PeopleTag.name)
    
    result = await db.execute(query)
    return result.scalars().all()

@router.post("/", response_model=PeopleTagRead)
async def create_people_tag(
    tag_in: PeopleTagCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new tag for people.
    """
    # Check if exists
    stmt = select(PeopleTag).where(
        PeopleTag.name == tag_in.name,
        (PeopleTag.user_id == current_user.id) | (PeopleTag.user_id == None)
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Tag already exists")
        
    tag = PeopleTag(
        name=tag_in.name,
        color=tag_in.color,
        user_id=current_user.id,
        parent_id=tag_in.parent_id
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
    current_user: User = Depends(get_current_user)
):
    """
    Update a tag.
    """
    tag = await db.get(PeopleTag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
        
    if tag.user_id and tag.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to update this tag")
        
    update_data = tag_in.model_dump(exclude_unset=True)
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
    current_user: User = Depends(get_current_user)
):
    """
    Delete a tag.
    """
    tag = await db.get(PeopleTag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
        
    # Check ownership if user_id is set
    if tag.user_id and tag.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this tag")
        
    await db.delete(tag)
    await db.commit()
    return {"ok": True}
