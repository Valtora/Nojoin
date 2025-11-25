from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from pydantic import BaseModel

from backend.api.deps import get_db
from backend.models.tag import Tag, RecordingTag
from backend.models.recording import Recording

router = APIRouter()

class TagCreate(BaseModel):
    name: str

@router.get("/", response_model=List[Tag])
async def list_tags(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    """
    List all available tags.
    """
    statement = select(Tag).offset(skip).limit(limit)
    result = await db.execute(statement)
    return result.scalars().all()

@router.post("/", response_model=Tag)
async def create_tag(
    tag_in: TagCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new tag.
    """
    statement = select(Tag).where(Tag.name == tag_in.name)
    result = await db.execute(statement)
    existing = result.scalar_one_or_none()
    if existing:
        return existing
        
    tag = Tag(name=tag_in.name)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag

@router.post("/recordings/{recording_id}", response_model=Tag)
async def add_tag_to_recording(
    recording_id: int,
    tag_in: TagCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Add a tag to a recording. Creates the tag if it doesn't exist.
    """
    # 1. Verify recording exists
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # 2. Find or Create Tag
    statement = select(Tag).where(Tag.name == tag_in.name)
    result = await db.execute(statement)
    tag = result.scalar_one_or_none()
    
    if not tag:
        tag = Tag(name=tag_in.name)
        db.add(tag)
        await db.commit()
        await db.refresh(tag)
        
    # 3. Check if association exists
    stmt = select(RecordingTag).where(
        RecordingTag.recording_id == recording_id,
        RecordingTag.tag_id == tag.id
    )
    result = await db.execute(stmt)
    existing_link = result.scalar_one_or_none()
    
    if not existing_link:
        link = RecordingTag(recording_id=recording_id, tag_id=tag.id)
        db.add(link)
        await db.commit()
        
    return tag

@router.delete("/recordings/{recording_id}/{tag_name}")
async def remove_tag_from_recording(
    recording_id: int,
    tag_name: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Remove a tag from a recording.
    """
    # 1. Find Tag
    statement = select(Tag).where(Tag.name == tag_name)
    result = await db.execute(statement)
    tag = result.scalar_one_or_none()
    
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
        
    # 2. Find Association
    stmt = select(RecordingTag).where(
        RecordingTag.recording_id == recording_id,
        RecordingTag.tag_id == tag.id
    )
    result = await db.execute(stmt)
    link = result.scalar_one_or_none()
    
    if link:
        await db.delete(link)
        await db.commit()
        
    return {"ok": True}

@router.delete("/{tag_id}")
async def delete_tag(
    tag_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a global tag.
    """
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
        
    await db.delete(tag)
    await db.commit()
    
    return {"ok": True}
