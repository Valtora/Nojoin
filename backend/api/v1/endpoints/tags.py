from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from pydantic import BaseModel

from backend.api.deps import get_db
from backend.models.tag import Tag, RecordingTag
from backend.models.recording import Recording, TagRead

router = APIRouter()

class TagCreate(BaseModel):
    name: str
    color: Optional[str] = None

class TagUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None

class BatchTagOperation(BaseModel):
    recording_ids: List[int]
    tag_name: str

@router.get("/", response_model=List[TagRead])
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

@router.post("/", response_model=TagRead)
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
        
    tag = Tag(name=tag_in.name, color=tag_in.color)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag

@router.patch("/{tag_id}", response_model=TagRead)
async def update_tag(
    tag_id: int,
    tag_update: TagUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    Update a tag's name and/or color.
    """
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    if tag_update.name is not None:
        # Check for duplicate name
        if tag_update.name != tag.name:
            stmt = select(Tag).where(Tag.name == tag_update.name)
            result = await db.execute(stmt)
            if result.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Tag with this name already exists")
        tag.name = tag_update.name
    
    if tag_update.color is not None:
        tag.color = tag_update.color
    
    await db.commit()
    await db.refresh(tag)
    return tag

@router.post("/recordings/{recording_id}", response_model=TagRead)
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
        tag = Tag(name=tag_in.name, color=tag_in.color)
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

@router.post("/batch/add")
async def batch_add_tag(
    batch: BatchTagOperation,
    db: AsyncSession = Depends(get_db)
):
    """
    Add a tag to multiple recordings. Creates the tag if it doesn't exist.
    """
    # 1. Find or Create Tag
    statement = select(Tag).where(Tag.name == batch.tag_name)
    result = await db.execute(statement)
    tag = result.scalar_one_or_none()
    
    if not tag:
        tag = Tag(name=batch.tag_name)
        db.add(tag)
        await db.commit()
        await db.refresh(tag)
    
    # 2. Add to recordings
    # Get all recordings that don't already have this tag
    # This is a bit complex in SQLModel/SQLAlchemy async, so we'll iterate for simplicity 
    # given the likely scale (batch size usually < 100)
    
    count = 0
    for recording_id in batch.recording_ids:
        # Check if recording exists
        rec_stmt = select(Recording).where(Recording.id == recording_id)
        rec_result = await db.execute(rec_stmt)
        if not rec_result.scalar_one_or_none():
            continue

        # Check if link exists
        link_stmt = select(RecordingTag).where(
            RecordingTag.recording_id == recording_id,
            RecordingTag.tag_id == tag.id
        )
        link_result = await db.execute(link_stmt)
        if not link_result.scalar_one_or_none():
            link = RecordingTag(recording_id=recording_id, tag_id=tag.id)
            db.add(link)
            count += 1
            
    await db.commit()
    return {"ok": True, "count": count, "tag": tag}

@router.post("/batch/remove")
async def batch_remove_tag(
    batch: BatchTagOperation,
    db: AsyncSession = Depends(get_db)
):
    """
    Remove a tag from multiple recordings.
    """
    # 1. Find Tag
    statement = select(Tag).where(Tag.name == batch.tag_name)
    result = await db.execute(statement)
    tag = result.scalar_one_or_none()
    
    if not tag:
        return {"ok": True, "count": 0} # Tag doesn't exist, so nothing to remove
        
    # 2. Remove links
    stmt = select(RecordingTag).where(
        RecordingTag.recording_id.in_(batch.recording_ids),
        RecordingTag.tag_id == tag.id
    )
    result = await db.execute(stmt)
    links = result.scalars().all()
    
    for link in links:
        await db.delete(link)
        
    await db.commit()
    return {"ok": True, "count": len(links)}
