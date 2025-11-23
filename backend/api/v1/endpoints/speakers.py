from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from pydantic import BaseModel

from backend.api.deps import get_db
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.recording import Recording

router = APIRouter()

class SpeakerUpdate(BaseModel):
    diarization_label: str
    global_speaker_name: str

class MergeRequest(BaseModel):
    source_speaker_id: int
    target_speaker_id: int

@router.get("/", response_model=List[GlobalSpeaker])
async def list_global_speakers(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    """
    List all global speakers.
    """
    statement = select(GlobalSpeaker).offset(skip).limit(limit)
    result = await db.execute(statement)
    return result.scalars().all()

@router.post("/", response_model=GlobalSpeaker)
async def create_global_speaker(
    name: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new global speaker manually.
    """
    # Check if exists
    statement = select(GlobalSpeaker).where(GlobalSpeaker.name == name)
    result = await db.execute(statement)
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Speaker already exists")
        
    speaker = GlobalSpeaker(name=name)
    db.add(speaker)
    await db.commit()
    await db.refresh(speaker)
    return speaker

@router.put("/recordings/{recording_id}", response_model=List[RecordingSpeaker])
async def update_recording_speaker(
    recording_id: int,
    update: SpeakerUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    Update a speaker label in a recording to map to a Global Speaker.
    If the Global Speaker name doesn't exist, it will be created.
    """
    # 1. Verify recording exists
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # 2. Find or Create Global Speaker
    # Prevent creating a Global Speaker named "UNKNOWN"
    if update.global_speaker_name.upper() == "UNKNOWN":
        raise HTTPException(status_code=400, detail="Cannot create a Global Speaker named 'UNKNOWN'. Please choose a different name.")

    statement = select(GlobalSpeaker).where(GlobalSpeaker.name == update.global_speaker_name)
    result = await db.execute(statement)
    global_speaker = result.scalar_one_or_none()
    
    if not global_speaker:
        # Double check if it exists (race condition)
        try:
            global_speaker = GlobalSpeaker(name=update.global_speaker_name)
            db.add(global_speaker)
            await db.commit()
            await db.refresh(global_speaker)
        except Exception:
            await db.rollback()
            # Try fetching again
            statement = select(GlobalSpeaker).where(GlobalSpeaker.name == update.global_speaker_name)
            result = await db.execute(statement)
            global_speaker = result.scalar_one_or_none()
            if not global_speaker:
                raise HTTPException(status_code=500, detail="Failed to create or find global speaker")
        
    # 3. Update RecordingSpeakers
    # Find all segments with this label for this recording
    stmt = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == update.diarization_label
    )
    result = await db.execute(stmt)
    recording_speakers = result.scalars().all()
    
    if not recording_speakers:
        raise HTTPException(status_code=404, detail=f"No speakers found with label {update.diarization_label} in this recording")
        
    import numpy as np

    for rs in recording_speakers:
        rs.global_speaker_id = global_speaker.id
        db.add(rs)
        
        # Update Global Speaker embedding
        if rs.embedding:
            if global_speaker.embedding is None:
                global_speaker.embedding = rs.embedding
            else:
                # Update centroid
                try:
                    g_emb = np.array(global_speaker.embedding)
                    r_emb = np.array(rs.embedding)
                    
                    # Simple average for now
                    new_emb = (g_emb + r_emb) / 2.0
                    
                    # Normalize
                    norm = np.linalg.norm(new_emb)
                    if norm > 0:
                        new_emb = new_emb / norm
                        
                    global_speaker.embedding = new_emb.tolist()
                    db.add(global_speaker)
                except Exception as e:
                    # Log error but don't fail request
                    print(f"Failed to update global speaker embedding: {e}")
        
    await db.commit()
    
    # Return updated list
    return recording_speakers

@router.post("/merge", response_model=GlobalSpeaker)
async def merge_speakers(
    request: MergeRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Merge source speaker into target speaker.
    Reassigns all recording speakers from source to target, then deletes source.
    """
    # 1. Get speakers
    source = await db.get(GlobalSpeaker, request.source_speaker_id)
    target = await db.get(GlobalSpeaker, request.target_speaker_id)
    
    if not source or not target:
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Cannot merge speaker into itself")

    # 2. Reassign all recording speakers
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.global_speaker_id == source.id)
    result = await db.execute(stmt)
    recording_speakers = result.scalars().all()
    
    for rs in recording_speakers:
        rs.global_speaker_id = target.id
        db.add(rs)
        
    # 3. Delete source speaker
    await db.delete(source)
    
    await db.commit()
    await db.refresh(target)
    return target

@router.put("/{speaker_id}", response_model=GlobalSpeaker)
async def update_global_speaker(
    speaker_id: int,
    name: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Rename a global speaker.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker:
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    # Check name uniqueness
    stmt = select(GlobalSpeaker).where(GlobalSpeaker.name == name)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing and existing.id != speaker_id:
        raise HTTPException(status_code=400, detail="Speaker name already exists")
        
    speaker.name = name
    db.add(speaker)
    await db.commit()
    await db.refresh(speaker)
    return speaker

@router.delete("/{speaker_id}")
async def delete_global_speaker(
    speaker_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a global speaker.
    Sets global_speaker_id to NULL for all associated recording speakers.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker:
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    # The relationship is set to nullify on delete by default in SQLModel/SQLAlchemy 
    # if nullable=True (which it is), but let's be explicit if needed.
    # Actually, we defined the relationship in models/speaker.py.
    # Let's just delete and let the DB handle the foreign key set null if configured,
    # or we manually nullify.
    
    # Manually nullify to be safe
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.global_speaker_id == speaker_id)
    result = await db.execute(stmt)
    recording_speakers = result.scalars().all()
    
    for rs in recording_speakers:
        rs.global_speaker_id = None
        db.add(rs)
        
    await db.delete(speaker)
    await db.commit()
    return {"ok": True}
