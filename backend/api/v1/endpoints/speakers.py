from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select
from pydantic import BaseModel

from backend.api.deps import get_db, get_current_user
from backend.models.people_tag_schemas import PeopleTagRead
from backend.models.speaker import GlobalSpeaker, GlobalSpeakerRead, GlobalSpeakerUpdate, GlobalSpeakerWithCount, RecordingSpeaker, GlobalSpeakerCreate
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.embedding import merge_embeddings, find_matching_global_speaker, cosine_similarity
from backend.utils.config_manager import config_manager
from backend.celery_app import celery_app

router = APIRouter()

import logging

logger = logging.getLogger(__name__)

class SpeakerUpdate(BaseModel):
    diarization_label: str
    global_speaker_name: str

    class Config:
        extra = "ignore"

class MergeRequest(BaseModel):
    source_speaker_id: int
    target_speaker_id: int

class MergeRequestLabels(BaseModel):
    target_speaker_label: str
    source_speaker_label: str

class VoiceprintAction(BaseModel):
    """Request body for voiceprint creation/linking actions."""
    action: str  # "create_new", "link_existing", "local_only", "force_link"
    global_speaker_id: Optional[int] = None  # Required for "link_existing" and "force_link"
    new_speaker_name: Optional[str] = None  # Required for "create_new"

class SpeakerSegment(BaseModel):
    recording_id: int
    recording_name: Optional[str] = None
    recording_date: Optional[str] = None
    start: float
    end: float
    text: str

class SegmentSelection(BaseModel):
    recording_id: int
    start: float
    end: float

class VoiceprintResult(BaseModel):
    """Response for voiceprint operations."""
    success: bool
    has_voiceprint: bool
    matched_speaker: Optional[dict] = None  # {id, name, similarity_score}
    message: Optional[str] = None

@router.get("/", response_model=List[GlobalSpeakerWithCount])
async def list_global_speakers(
    skip: int = 0,
    limit: int = 100,
    q: Optional[str] = None,
    tags: Optional[List[int]] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all global speakers (People) with filtering.
    """
    from sqlalchemy import func, or_
    from sqlalchemy.orm import selectinload
    from backend.models.people_tag import PeopleTagLink
    
    # Query with left join to count recordings
    query = (
        select(
            GlobalSpeaker,
            func.count(RecordingSpeaker.id).label('recording_count')
        )
        .outerjoin(RecordingSpeaker, GlobalSpeaker.id == RecordingSpeaker.global_speaker_id)
        .where(GlobalSpeaker.user_id == current_user.id)
    )

    if q:
        search_term = f"%{q}%"
        query = query.where(
            or_(
                GlobalSpeaker.name.ilike(search_term),
                GlobalSpeaker.email.ilike(search_term),
                GlobalSpeaker.company.ilike(search_term),
                GlobalSpeaker.notes.ilike(search_term),
                GlobalSpeaker.title.ilike(search_term)
            )
        )
        
    if tags:
        # Filter by tags (has ANY of the tags)
        query = query.join(GlobalSpeaker.tag_links).where(PeopleTagLink.tag_id.in_(tags))

    query = query.group_by(GlobalSpeaker.id).order_by(GlobalSpeaker.name).offset(skip).limit(limit)
    
    # Ensure tag_links and tag are loaded using selectinload for consistency
    query = query.options(
        selectinload(GlobalSpeaker.tag_links).selectinload(PeopleTagLink.tag)
    )
    
    result = await db.execute(query)
    rows = result.all()
    
    # Build response
    speakers_with_counts = []
    for row in rows:
        speaker: GlobalSpeaker = row[0]
        count = row[1]
        
        # Build tag list
        tag_list = []
        for link in speaker.tag_links:
            if link.tag:
                tag_list.append(PeopleTagRead(
                    id=link.tag.id,
                    name=link.tag.name,
                    color=link.tag.color,
                    parent_id=link.tag.parent_id
                ))
        
        speakers_with_counts.append(GlobalSpeakerWithCount(
            id=speaker.id,
            name=speaker.name,
            color=speaker.color,
            has_voiceprint=speaker.has_voiceprint,
            recording_count=count,
            created_at=speaker.created_at.isoformat(),
            updated_at=speaker.updated_at.isoformat(),
            title=speaker.title,
            company=speaker.company,
            email=speaker.email,
            phone_number=speaker.phone_number,
            notes=speaker.notes,
            tags=tag_list
        ))
    
    return speakers_with_counts

@router.post("/", response_model=GlobalSpeaker)
async def create_global_speaker(
    speaker_in: GlobalSpeakerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new global speaker (Person).
    """
    # Check if exists
    statement = select(GlobalSpeaker).where(GlobalSpeaker.name == speaker_in.name, GlobalSpeaker.user_id == current_user.id)
    result = await db.execute(statement)
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"A person with the name '{speaker_in.name}' already exists in your library.")
        
    import backend.models.people_tag as pt

    speaker = GlobalSpeaker(
        name=speaker_in.name, 
        user_id=current_user.id,
        color=speaker_in.color,
        title=speaker_in.title,
        company=speaker_in.company,
        email=speaker_in.email,
        phone_number=speaker_in.phone_number,
        notes=speaker_in.notes
    )
    db.add(speaker)
    await db.commit()
    await db.refresh(speaker)
    
    if speaker_in.tag_ids:
        for tag_id in speaker_in.tag_ids:
            link = pt.PeopleTagLink(global_speaker_id=speaker.id, tag_id=tag_id)
            db.add(link)
        await db.commit()
        await db.refresh(speaker)
        
    return speaker

@router.put("/recordings/{recording_id}", response_model=List[RecordingSpeaker])
async def update_recording_speaker(
    recording_id: int,
    update: SpeakerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update a speaker label in a recording with a name.
    
    - If the name matches an existing Global Speaker, link to it
    - Otherwise, set as local_name (local to this recording only)
    - Does NOT auto-create global speakers
    """
    logger.info(f"Updating speaker for recording {recording_id}: {update}")
    
    # 1. Verify recording exists
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        logger.error(f"Recording {recording_id} not found")
        raise HTTPException(status_code=404, detail="Recording not found")

    # 2. Check if a Global Speaker with this name already exists
    statement = select(GlobalSpeaker).where(GlobalSpeaker.name == update.global_speaker_name, GlobalSpeaker.user_id == current_user.id)
    result = await db.execute(statement)
    global_speaker = result.scalar_one_or_none()
        
    # 3. Update RecordingSpeakers
    stmt = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == update.diarization_label
    )
    result = await db.execute(stmt)
    recording_speakers = result.scalars().all()
    
    if not recording_speakers:
        raise HTTPException(status_code=404, detail=f"No speakers found with label {update.diarization_label} in this recording")
        
    # Capture old names for transcript repair
    old_names = set()
    for rs in recording_speakers:
        if rs.local_name: old_names.add(rs.local_name)
        if rs.name: old_names.add(rs.name)
        # Also captures the new name in case the transcript already stores it directly.
        old_names.add(update.global_speaker_name)

    for rs in recording_speakers:
        if global_speaker:
            # Link to existing global speaker
            rs.global_speaker_id = global_speaker.id
            rs.local_name = None  # Clear local name to enforce global precedence
            rs.name = None  # Deprecated field
            
            # Active Learning: Update Global Speaker embedding from user feedback
            if rs.embedding:
                if global_speaker.embedding:
                    if not global_speaker.is_voiceprint_locked:
                        global_speaker.embedding = merge_embeddings(global_speaker.embedding, rs.embedding, alpha=0.3)
                else:
                    global_speaker.embedding = rs.embedding
                db.add(global_speaker)
        else:
            # Match not found in Global Library.
            # Treat as a local rename only.
            rs.local_name = update.global_speaker_name
            rs.global_speaker_id = None
            rs.name = None  # Deprecated field
        
        db.add(rs)

    # 4. Transcript Repair: Ensure segments use diarization_label
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if transcript and transcript.segments:
        segments_updated = False
        new_segments = []
        for segment in transcript.segments:
            segment_copy = dict(segment)
            current_speaker = segment_copy.get("speaker")
            
            # If the segment uses one of the old names (or the new name), revert to label
            if current_speaker in old_names:
                segment_copy["speaker"] = update.diarization_label
                segments_updated = True
            
            new_segments.append(segment_copy)
        
        if segments_updated:
            transcript.segments = new_segments
            flag_modified(transcript, "segments")
            db.add(transcript)

    await db.commit()
    
    # Return updated list
    return recording_speakers

@router.post("/recordings/{recording_id}/speakers/{diarization_label}/promote", response_model=RecordingSpeaker)
async def promote_speaker_to_global(
    recording_id: int,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Promote a recording speaker to the global speaker library.
    
    - Creates a new Global Speaker with the speaker's current name
    - Links the recording speaker to the new global speaker
    - Copies the embedding to the global speaker if available
    """
    # 1. Find the recording speaker
    # Ensure recording belongs to user
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(statement)
    recording_speaker = result.scalar_one_or_none()
    
    if not recording_speaker:
        raise HTTPException(status_code=404, detail="Speaker not found in this recording")
    
    # 2. Get the name to use (local_name, deprecated name, or diarization_label)
    speaker_name = recording_speaker.local_name or recording_speaker.name or recording_speaker.diarization_label
    
    # Validate name is not a placeholder
    import re
    placeholder_pattern = re.compile(r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE)
    if placeholder_pattern.match(speaker_name):
        raise HTTPException(
            status_code=400, 
            detail="Cannot promote a speaker with a placeholder name. Please rename them first."
        )
    
    # 3. Check if global speaker already exists
    statement = select(GlobalSpeaker).where(GlobalSpeaker.name == speaker_name, GlobalSpeaker.user_id == current_user.id)
    result = await db.execute(statement)
    existing_global = result.scalar_one_or_none()
    
    if existing_global:
        # Already exists, just link to it
        recording_speaker.global_speaker_id = existing_global.id
        recording_speaker.local_name = None
        recording_speaker.name = None
        
        # Merge embeddings
        if recording_speaker.embedding:
            if existing_global.embedding:
                existing_global.embedding = merge_embeddings(existing_global.embedding, recording_speaker.embedding, alpha=0.5)
            else:
                existing_global.embedding = recording_speaker.embedding
            db.add(existing_global)
    else:
        # Create new global speaker
        global_speaker = GlobalSpeaker(
            name=speaker_name,
            embedding=recording_speaker.embedding,
            user_id=current_user.id
        )
        db.add(global_speaker)
        await db.flush()  # Get the ID
        
        # Link the recording speaker
        recording_speaker.global_speaker_id = global_speaker.id
        recording_speaker.local_name = None
        recording_speaker.name = None
    
    db.add(recording_speaker)
    await db.commit()
    await db.refresh(recording_speaker)
    
    return recording_speaker

async def _merge_local_speakers(
    db: AsyncSession,
    recording_id: int,
    source_label: str,
    target_label: str
):
    """
    Helper to merge two local speakers (by diarization label) within a single recording.
    Updates transcript segments, merges embeddings, and deletes source recording speaker.
    """
    # 1. Find the source and target speaker entries
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == source_label
    )
    result = await db.execute(statement)
    source_speaker = result.scalar_one_or_none()

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == target_label
    )
    result = await db.execute(statement)
    target_speaker = result.scalar_one_or_none()

    if not source_speaker or not target_speaker:
        # Should not happen if confirmed before calling, but safe to return or log
        return

    # Identify aliases for source to catch in transcript
    source_aliases = {source_label}
    if source_speaker.local_name:
        source_aliases.add(source_speaker.local_name)
    if source_speaker.name:
        source_aliases.add(source_speaker.name)
    # Check global link for alias
    if source_speaker.global_speaker_id:
        gs = await db.get(GlobalSpeaker, source_speaker.global_speaker_id)
        if gs:
            source_aliases.add(gs.name)

    # 2. Update Transcript Segments
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(statement)
    transcript = result.scalar_one_or_none()

    if transcript and transcript.segments:
        segments_updated = False
        new_segments = []
        for segment in transcript.segments:
            segment_copy = dict(segment)
            if segment_copy.get("speaker") in source_aliases:
                segment_copy["speaker"] = target_label
                segments_updated = True
            new_segments.append(segment_copy)
        
        if segments_updated:
            transcript.segments = new_segments
            flag_modified(transcript, "segments")
            db.add(transcript)

    # 3. Merge embeddings
    if source_speaker.embedding and target_speaker.embedding:
        target_speaker.embedding = merge_embeddings(
            target_speaker.embedding, 
            source_speaker.embedding, 
            alpha=0.5
        )
        db.add(target_speaker)
    elif source_speaker.embedding and not target_speaker.embedding:
        target_speaker.embedding = source_speaker.embedding
        db.add(target_speaker)

    # 4. Soft-Merge source speaker (set merged_into_id)
    # Instead of deleting, we keep it to preserve the merge history for reprocessing
    source_speaker.merged_into_id = target_speaker.id
    source_speaker.embedding = None # Clear embedding as it's merged
    db.add(source_speaker)
    # kw: was await db.delete(source_speaker)

@router.post("/merge", response_model=GlobalSpeaker)
async def merge_speakers(
    request: MergeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
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
        
    if source.user_id != current_user.id or target.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Cannot merge speaker into itself")

    # 2. Reassign all recording speakers
    # 2. Reassign all recording speakers
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.global_speaker_id == source.id)
    result = await db.execute(stmt)
    source_recording_speakers = result.scalars().all()
    
    for rs in source_recording_speakers:
        # Check if target already has a speaker in this recording
        stmt = select(RecordingSpeaker).where(
            RecordingSpeaker.recording_id == rs.recording_id,
            RecordingSpeaker.global_speaker_id == target.id
        )
        result = await db.execute(stmt)
        target_rs = result.scalar_one_or_none()

        if target_rs:
            # COLLISION: Target exists in this recording.
            # Merges the local speakers to prevent duplicates in the meeting view.
            if rs.id != target_rs.id: # Sanity check
                await _merge_local_speakers(db, rs.recording_id, rs.diarization_label, target_rs.diarization_label)
        else:
            # No collision: Just reassign
            rs.global_speaker_id = target.id
            rs.name = target.name
            rs.local_name = None 
            db.add(rs)
        
    # 3. Merge embeddings
    if source.embedding and target.embedding:
        target.embedding = merge_embeddings(target.embedding, source.embedding, alpha=0.5)
    elif source.embedding and not target.embedding:
        target.embedding = source.embedding
    db.add(target)

    # 4. Delete source speaker
    await db.delete(source)
    
    await db.commit()
    await db.refresh(target)
    return target

@router.put("/{speaker_id}", response_model=GlobalSpeaker)
async def update_global_speaker(
    speaker_id: int,
    speaker_in: GlobalSpeakerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update a global speaker (Person).
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    if speaker_in.name is not None and speaker_in.name != speaker.name:
        # Check name uniqueness
        stmt = select(GlobalSpeaker).where(GlobalSpeaker.name == speaker_in.name, GlobalSpeaker.user_id == current_user.id)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing and existing.id != speaker_id:
            raise HTTPException(status_code=400, detail="Speaker name already exists")
        speaker.name = speaker_in.name
        
        # Propagate name change to RecordingSpeakers
        stmt = select(RecordingSpeaker).where(RecordingSpeaker.global_speaker_id == speaker_id)
        result = await db.execute(stmt)
        linked_speakers = result.scalars().all()
        for rs in linked_speakers:
            rs.name = speaker_in.name
            db.add(rs)

    if speaker_in.color is not None:
        speaker.color = speaker_in.color
        
    # CRM Fields
    if speaker_in.title is not None: speaker.title = speaker_in.title
    if speaker_in.company is not None: speaker.company = speaker_in.company
    if speaker_in.email is not None: speaker.email = speaker_in.email
    if speaker_in.phone_number is not None: speaker.phone_number = speaker_in.phone_number
    if speaker_in.notes is not None: speaker.notes = speaker_in.notes
    
    if speaker_in.tag_ids is not None:
        # Update tags: Clear existing and add new
        # Note: This is a full replacement strategy
        from backend.models.people_tag import PeopleTagLink
        stmt = select(PeopleTagLink).where(PeopleTagLink.global_speaker_id == speaker_id)
        result = await db.execute(stmt)
        existing_links = result.scalars().all()
        
        existing_tag_ids = {link.tag_id for link in existing_links}
        new_tag_ids = set(speaker_in.tag_ids)
        
        # Remove tags not in new list
        for link in existing_links:
            if link.tag_id not in new_tag_ids:
                await db.delete(link)
                
        # Add new tags
        for tag_id in new_tag_ids:
            if tag_id not in existing_tag_ids:
                link = PeopleTagLink(global_speaker_id=speaker_id, tag_id=tag_id)
                db.add(link)
        
    db.add(speaker)
    await db.commit()
    await db.refresh(speaker)
    return speaker

@router.delete("/{speaker_id}")
async def delete_global_speaker(
    speaker_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    await db.delete(speaker)
    await db.commit()
    return {"ok": True}

@router.get("/{speaker_id}/segments", response_model=List[SpeakerSegment])
async def get_speaker_segments(
    speaker_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get recent audio segments attributed to this global speaker.
    Used for manual voiceprint recalibration.
    """
    # Verify speaker
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    # Fetch recordings with this speaker
    # Join RecordingSpeaker, Recording, Transcript
    statement = (
        select(RecordingSpeaker, Recording, Transcript)
        .join(Recording, Recording.id == RecordingSpeaker.recording_id)
        .outerjoin(Transcript, Transcript.recording_id == RecordingSpeaker.recording_id)
        .where(RecordingSpeaker.global_speaker_id == speaker_id)
        .where(Recording.is_deleted == False)
        .order_by(Recording.created_at.desc())
        .limit(20) # Scan last 20 recordings
    )
    result = await db.execute(statement)
    rows = result.all() 
    
    segments = []
    
    for rs, rec, trans in rows:
        if not trans or not trans.segments: continue
        
        # Determine labels to look for
        labels = {rs.diarization_label}
        if rs.local_name: labels.add(rs.local_name)
        if rs.name: labels.add(rs.name)
        labels.add(speaker.name)
        
        # Find matching segments
        rec_segments = []
        for seg in trans.segments:
            if seg.get("speaker") in labels:
                 # Skip segments > 10 seconds (often noisy/interrupted)
                 duration = seg["end"] - seg["start"]
                 if duration > 10.0: continue
                 
                 rec_segments.append(SpeakerSegment(
                     recording_id=rec.id,
                     recording_name=rec.name,
                     recording_date=rec.created_at.isoformat() if rec.created_at else None,
                     start=seg["start"],
                     end=seg["end"],
                     text=seg["text"]
                 ))
        
        segments.extend(rec_segments)
        
        if len(segments) >= limit: 
            break
            
    return segments[:limit]

@router.post("/{speaker_id}/recalibrate")
async def recalibrate_voiceprint(
    speaker_id: int,
    segments: List[SegmentSelection],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Manually recalibrate (reset) a speaker's voiceprint using specific audio segments.
    Locks the voiceprint to prevent auto-updates.
    """
    # 1. Verify speaker
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    if not segments:
         raise HTTPException(status_code=400, detail="No segments provided")
         
    # 2. Group segments by recording
    from collections import defaultdict
    recording_segments = defaultdict(list)
    for s in segments:
        recording_segments[s.recording_id].append((s.start, s.end))
        
    all_embeddings = []
    device_str = config_manager.get("processing_device", "cpu")
    
    # 3. Extract embeddings
    for rec_id, segs in recording_segments.items():
        rec = await db.get(Recording, rec_id)
        if not rec or rec.user_id != current_user.id: continue
        
        # Call Celery task synchronously
        # Try to get token from user settings, then config
        user_settings = current_user.settings or {}
        hf_token = user_settings.get("hf_token") or config_manager.get("hf_token")
        
        task = celery_app.send_task(
            "backend.worker.tasks.extract_embedding_task",
            args=[rec.audio_path, segs, device_str, hf_token]
        )
        try:
            emb = await run_in_threadpool(task.get, timeout=120) # 2 min timeout
            if emb:
                all_embeddings.append(emb)
        except Exception as e:
            logger.error(f"Failed to extract embedding for recalibration (Rec {rec_id}): {e}")
            
    if not all_embeddings:
        raise HTTPException(status_code=500, detail="Failed to extract embeddings from selected segments")
        
    # 4. Average embeddings
    final_emb = all_embeddings[0]
    for i in range(1, len(all_embeddings)):
        final_emb = merge_embeddings(final_emb, all_embeddings[i])
        
    # 5. Update and Lock
    speaker.embedding = final_emb
    speaker.is_voiceprint_locked = True
    db.add(speaker)
    await db.commit()
    
    return {"success": True, "message": "Voiceprint recalibrated and locked."}

@router.delete("/{speaker_id}/embedding")
async def delete_global_speaker_embedding(
    speaker_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete voiceprint (embedding) for a global speaker.
    Does not delete the speaker itself.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    speaker.embedding = None
    speaker.is_voiceprint_locked = False
    db.add(speaker)
    await db.commit()
    return {"ok": True}

class SpeakerSplitRequest(BaseModel):
    new_speaker_name: str
    segments: List[SegmentSelection]

@router.post("/{speaker_id}/split", response_model=GlobalSpeaker)
async def split_speaker(
    speaker_id: int,
    request: SpeakerSplitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Split a global speaker into a new speaker based on selected audio segments.
    Recalibrates both the new speaker (using selected segments) and the original speaker (using remaining segments).
    """
    # 1. Verify Source Speaker
    original_speaker = await db.get(GlobalSpeaker, speaker_id)
    if not original_speaker or original_speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Original speaker not found")

    if not request.segments:
         raise HTTPException(status_code=400, detail="No segments provided for splitting")

    # 2. Create New Speaker
    # Check if name exists
    stmt = select(GlobalSpeaker).where(GlobalSpeaker.name == request.new_speaker_name, GlobalSpeaker.user_id == current_user.id)
    result = await db.execute(stmt)
    existing_new = result.scalar_one_or_none()
    
    new_speaker = None
    if existing_new:
        # If it exists, we are effectively merging *into* another existing speaker manually
        new_speaker = existing_new
    else:
        new_speaker = GlobalSpeaker(
            name=request.new_speaker_name,
            user_id=current_user.id,
            is_voiceprint_locked=True 
        )
        db.add(new_speaker)
        await db.flush()

    # Group segments by recording
    from collections import defaultdict
    recording_segments = defaultdict(list)
    for s in request.segments:
        recording_segments[s.recording_id].append(s)

    # 3. Process each affected recording
    import time
    timestamp_suffix = int(time.time())
    
    device_str = config_manager.get("processing_device", "cpu")
    user_settings = current_user.settings or {}
    hf_token = user_settings.get("hf_token") or config_manager.get("hf_token")
    
    new_speaker_embeddings = []
    
    for rec_id, segments in recording_segments.items():
        rec = await db.get(Recording, rec_id)
        if not rec or rec.user_id != current_user.id: continue
        
        # Generate a unique label to separate split segments in the transcript
        split_label = f"SPLIT_{timestamp_suffix}_{new_speaker.id}"
        
        # Update Transcript Segments
        stmt = select(Transcript).where(Transcript.recording_id == rec_id)
        result = await db.execute(stmt)
        transcript = result.scalar_one_or_none()
        
        if transcript and transcript.segments:
            new_trans_segments = []
            segments_modified = False
            
            for t_seg in transcript.segments:
                # Check if this segment overlaps significantly with any selected segment
                is_selected = False
                t_start = t_seg['start']
                t_end = t_seg['end']
                
                for sel in segments:
                    # Simple intersection check with tolerance
                    # If the selected range covers most of the segment
                    overlap_start = max(t_start, sel.start)
                    overlap_end = min(t_end, sel.end)
                    overlap = max(0, overlap_end - overlap_start)
                    duration = t_end - t_start
                    
                    if duration > 0 and (overlap / duration) > 0.5:
                        is_selected = True
                        break
                
                seg_copy = dict(t_seg)
                if is_selected:
                    seg_copy['speaker'] = split_label
                    segments_modified = True
                new_trans_segments.append(seg_copy)
            
            if segments_modified:
                transcript.segments = new_trans_segments
                flag_modified(transcript, "segments")
                db.add(transcript)
        
        # Create RecordingSpeaker entry for the new label
        # Check if one already exists (unlikely given timestamp, but safe)
        stmt = select(RecordingSpeaker).where(
            RecordingSpeaker.recording_id == rec_id, 
            RecordingSpeaker.diarization_label == split_label
        )
        result = await db.execute(stmt)
        existing_rs = result.scalar_one_or_none()
        
        if not existing_rs:
            # Extract embedding for this new speaker's segments in this recording
            # Uses the selected segments for embedding extraction.
            seg_tuples = [(s.start, s.end) for s in segments]
            
            # Run extraction task synchronously
            task = celery_app.send_task(
                "backend.worker.tasks.extract_embedding_task",
                args=[rec.audio_path, seg_tuples, device_str, hf_token]
            )
            try:
                emb = await run_in_threadpool(task.get, timeout=60)
                if emb:
                    new_speaker_embeddings.append(emb)
            except Exception as e:
                logger.error(f"Failed embedding extract during split (Rec {rec_id}): {e}")
                emb = None

            rs = RecordingSpeaker(
                recording_id=rec_id,
                diarization_label=split_label,
                name=new_speaker.name,
                global_speaker_id=new_speaker.id,
                embedding=emb
            )
            db.add(rs)

    # 4. Update New Speaker Voiceprint
    if new_speaker_embeddings:
        final_new_emb = new_speaker_embeddings[0]
        for i in range(1, len(new_speaker_embeddings)):
            final_new_emb = merge_embeddings(final_new_emb, new_speaker_embeddings[i])
        
        if new_speaker.embedding:
             new_speaker.embedding = merge_embeddings(new_speaker.embedding, final_new_emb)
        else:
             new_speaker.embedding = final_new_emb
        
        new_speaker.is_voiceprint_locked = True
        db.add(new_speaker)

    # 5. Recalibrate Original Speaker
    # Re-calculate the original speaker's embedding using only the remaining valid segments
    # from the affected recordings.
    
    original_speaker_embeddings = []
    
    for rec_id in recording_segments.keys():
        rec = await db.get(Recording, rec_id)
        if not rec: continue
        
        stmt = select(Transcript).where(Transcript.recording_id == rec_id)
        result = await db.execute(stmt)
        transcript = result.scalar_one_or_none()
        if not transcript: continue
        
        # Identify transcript labels still mapped to the original speaker.
        
        stmt = select(RecordingSpeaker).where(
            RecordingSpeaker.recording_id == rec_id,
            RecordingSpeaker.global_speaker_id == original_speaker.id
        )
        result = await db.execute(stmt)
        original_rss = result.scalars().all()
        valid_labels = {rs.diarization_label for rs in original_rss}
        
        remaining_seg_tuples = []
        for t_seg in transcript.segments:
            if t_seg.get("speaker") in valid_labels:
                remaining_seg_tuples.append((t_seg['start'], t_seg['end']))
        
        if remaining_seg_tuples:
             # Extract
            task = celery_app.send_task(
                "backend.worker.tasks.extract_embedding_task",
                args=[rec.audio_path, remaining_seg_tuples, device_str, hf_token]
            )
            try:
                emb = await run_in_threadpool(task.get, timeout=60)
                if emb:
                    original_speaker_embeddings.append(emb)
            except Exception as e:
                logger.error(f"Failed embedding extract for original (Rec {rec_id}): {e}")

    if original_speaker_embeddings:
        # Calculate the new "gold standard" embedding from the remaining verified segments.
        # This replaces the previous embedding entirely to ensure purity.
        
        final_orig_emb = original_speaker_embeddings[0]
        for i in range(1, len(original_speaker_embeddings)):
            final_orig_emb = merge_embeddings(final_orig_emb, original_speaker_embeddings[i])
            
        original_speaker.embedding = final_orig_emb
        original_speaker.is_voiceprint_locked = True
        db.add(original_speaker)

    await db.commit()
    await db.refresh(new_speaker)
    
    return new_speaker

@router.post("/recordings/{recording_id}/merge", response_model=Recording)
async def merge_recording_speakers(
    recording_id: int,
    merge_data: MergeRequestLabels,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Merge two speakers in a recording.
    All segments belonging to source_speaker_label will be reassigned to target_speaker_label.
    The source_speaker_label will be removed from the recording's speaker list.
    """
    # 1. Verify recording exists
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    # 2. Validate that source and target are different
    if merge_data.source_speaker_label == merge_data.target_speaker_label:
        raise HTTPException(status_code=400, detail="Cannot merge speaker into itself")

    # 3. Find the source and target speaker entries (validation only here, logic in helper)
    # Verifies existence of both entries to raise proper HTTP errors before calling the helper.
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == merge_data.source_speaker_label
    )
    result = await db.execute(statement)
    source_speaker = result.scalar_one_or_none()

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == merge_data.target_speaker_label
    )
    result = await db.execute(statement)
    target_speaker = result.scalar_one_or_none()

    if not source_speaker:
        raise HTTPException(status_code=404, detail=f"Source speaker '{merge_data.source_speaker_label}' not found")
    
    if not target_speaker:
        raise HTTPException(status_code=404, detail=f"Target speaker '{merge_data.target_speaker_label}' not found")

    # 4. Perform Merge
    await _merge_local_speakers(
        db, 
        recording_id, 
        merge_data.source_speaker_label, 
        merge_data.target_speaker_label
    )

    # 5. Flush and Commit
    await db.flush()
    await db.commit()
    
    # 6. Refresh recording
    await db.refresh(recording)
    return recording

@router.delete("/recordings/{recording_id}/speakers/{diarization_label}")
async def delete_recording_speaker(
    recording_id: int,
    diarization_label: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Remove a speaker from a recording.
    
    - Sets all transcript segments to 'UNKNOWN'
    - Deletes the RecordingSpeaker entry
    - If linked to a Global Speaker, only removes the association (does NOT delete the global speaker)
    """
    # 1. Verify recording exists
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # 2. Update Transcript Segments
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(statement)
    transcript = result.scalar_one_or_none()

    if transcript and transcript.segments:
        updated_segments = []
        changed = False
        for segment in transcript.segments:
            if segment.get("speaker") == diarization_label:
                segment["speaker"] = "UNKNOWN"
                changed = True
            updated_segments.append(segment)
        
        if changed:
            transcript.segments = updated_segments
            db.add(transcript)

    # 3. Delete RecordingSpeaker entry
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(statement)
    speaker_entry = result.scalar_one_or_none()

    if speaker_entry:
        await db.delete(speaker_entry)
    else:
        raise HTTPException(status_code=404, detail="Speaker not found in recording")

    await db.commit()
    return {"ok": True}


# ============================================================================
# Voiceprint (Embedding) Management Endpoints
# ============================================================================

@router.post("/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/extract")
async def extract_voiceprint(
    recording_id: int,
    diarization_label: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Extract a voiceprint (embedding) for a specific speaker in a recording.
    
    This is the first step of voiceprint creation. It extracts the embedding
    and returns potential matches from Global Speakers. The client then
    calls the /voiceprint/apply endpoint with the user's chosen action.
    
    Returns:
        - embedding_extracted: Whether extraction was successful
        - matched_speaker: Best matching GlobalSpeaker (if any)
        - all_speakers: List of all GlobalSpeakers for force-link option
    """
    # 1. Verify recording exists
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    # 2. Find the RecordingSpeaker
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(statement)
    rec_speaker = result.scalar_one_or_none()
    
    if not rec_speaker:
        raise HTTPException(status_code=404, detail="Speaker not found in recording")
    
    # 3. Get transcript segments for this speaker
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(statement)
    transcript = result.scalar_one_or_none()
    
    if not transcript or not transcript.segments:
        raise HTTPException(status_code=400, detail="No transcript segments found for this recording")
    
    # Find segments belonging to this speaker (match by diarization_label or resolved name)
    speaker_segments = []
    speaker_name = rec_speaker.name or diarization_label
    
    for seg in transcript.segments:
        seg_speaker = seg.get("speaker", "")
        if seg_speaker == diarization_label or seg_speaker == speaker_name:
            speaker_segments.append((seg["start"], seg["end"]))
    
    if not speaker_segments:
        raise HTTPException(status_code=400, detail="No audio segments found for this speaker")
    
    # 4. Extract embedding
    device_str = config_manager.get("processing_device", "cpu")
    # Offload to worker
    device_str = config_manager.get("processing_device", "cpu")
    
    # Try to get token from user settings, then config
    user_settings = current_user.settings or {}
    hf_token = user_settings.get("hf_token") or config_manager.get("hf_token")
    
    task = celery_app.send_task(
        "backend.worker.tasks.extract_embedding_task",
        args=[recording.audio_path, speaker_segments, device_str, hf_token]
    )
    embedding = await run_in_threadpool(task.get)
    
    if not embedding:
        raise HTTPException(status_code=500, detail="Failed to extract voiceprint from audio segments")
    
    # 5. Store embedding temporarily on the RecordingSpeaker
    rec_speaker.embedding = embedding
    db.add(rec_speaker)
    await db.commit()
    await db.refresh(rec_speaker)
    
    # 6. Find potential matches
    all_global_stmt = select(GlobalSpeaker)
    all_global_result = await db.execute(all_global_stmt)
    all_global_speakers = all_global_result.scalars().all()
    
    # Find best match
    matched_speaker = None
    best_score = 0.0
    
    for gs in all_global_speakers:
        if gs.embedding:
            score = cosine_similarity(embedding, gs.embedding)
            if score > best_score:
                best_score = score
                matched_speaker = gs
    
    # Prepare response
    match_info = None
    if matched_speaker and best_score >= 0.5:  # Lower threshold for showing potential matches
        match_info = {
            "id": matched_speaker.id,
            "name": matched_speaker.name,
            "similarity_score": round(best_score, 3),
            "is_strong_match": best_score >= 0.65  # Strong match threshold
        }
    
    # Return all global speakers for force-link dropdown
    all_speakers_list = [
        {"id": gs.id, "name": gs.name, "has_voiceprint": gs.embedding is not None}
        for gs in all_global_speakers
    ]
    
    return {
        "embedding_extracted": True,
        "matched_speaker": match_info,
        "all_global_speakers": all_speakers_list,
        "speaker_id": rec_speaker.id,
        "diarization_label": diarization_label
    }


@router.post("/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/apply")
async def apply_voiceprint_action(
    recording_id: int,
    diarization_label: str,
    action: VoiceprintAction,
    db: AsyncSession = Depends(get_db)
):
    """
    Apply a voiceprint action after extraction.
    
    Actions:
        - "create_new": Create a new GlobalSpeaker with the extracted embedding
        - "link_existing": Link to an existing GlobalSpeaker (merges embeddings)
        - "local_only": Keep the embedding only on RecordingSpeaker (no GlobalSpeaker)
        - "force_link": Force link to a GlobalSpeaker (user override, trains the embedding)
    """
    # 1. Verify recording and speaker exist
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(statement)
    rec_speaker = result.scalar_one_or_none()
    
    if not rec_speaker:
        raise HTTPException(status_code=404, detail="Speaker not found in recording")
    
    if not rec_speaker.embedding:
        raise HTTPException(status_code=400, detail="No voiceprint extracted. Call /voiceprint/extract first.")
    
    embedding = rec_speaker.embedding
    
    if action.action == "create_new":
        # Create a new GlobalSpeaker
        if not action.new_speaker_name:
            raise HTTPException(status_code=400, detail="new_speaker_name is required for create_new action")
        
        # Check for placeholder names
        import re
        placeholder_pattern = re.compile(r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE)
        if placeholder_pattern.match(action.new_speaker_name):
            raise HTTPException(status_code=400, detail="Cannot use a placeholder name for Global Speaker")
        
        # Check if name already exists
        existing_stmt = select(GlobalSpeaker).where(GlobalSpeaker.name == action.new_speaker_name)
        existing_result = await db.execute(existing_stmt)
        if existing_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="A Global Speaker with this name already exists")
        
        new_gs = GlobalSpeaker(name=action.new_speaker_name, embedding=embedding)
        db.add(new_gs)
        await db.commit()
        await db.refresh(new_gs)
        
        # Link RecordingSpeaker to the new GlobalSpeaker
        rec_speaker.global_speaker_id = new_gs.id
        rec_speaker.name = new_gs.name
        db.add(rec_speaker)
        await db.commit()
        
        return VoiceprintResult(
            success=True,
            has_voiceprint=True,
            matched_speaker={"id": new_gs.id, "name": new_gs.name},
            message=f"Created new Global Speaker: {new_gs.name}"
        )
    
    elif action.action == "link_existing" or action.action == "force_link":
        # Link to existing GlobalSpeaker
        if not action.global_speaker_id:
            raise HTTPException(status_code=400, detail="global_speaker_id is required")
        
        gs = await db.get(GlobalSpeaker, action.global_speaker_id)
        if not gs:
            raise HTTPException(status_code=404, detail="Global Speaker not found")
        
        # Merge embeddings (if GlobalSpeaker has one)
        # Use higher alpha for force_link as it's explicit user correction
        alpha = 0.4 if action.action == "force_link" else 0.3
        if gs.embedding:
            gs.embedding = merge_embeddings(gs.embedding, embedding, alpha=alpha)
        else:
            gs.embedding = embedding
        db.add(gs)
        
        # Link RecordingSpeaker
        rec_speaker.global_speaker_id = gs.id
        rec_speaker.name = gs.name
        db.add(rec_speaker)
        await db.commit()
        
        action_verb = "Force-linked" if action.action == "force_link" else "Linked"
        return VoiceprintResult(
            success=True,
            has_voiceprint=True,
            matched_speaker={"id": gs.id, "name": gs.name},
            message=f"{action_verb} to Global Speaker: {gs.name}"
        )
    
    elif action.action == "local_only":
        # Keep embedding on RecordingSpeaker only
        # The embedding is already saved from the extract step
        return VoiceprintResult(
            success=True,
            has_voiceprint=True,
            matched_speaker=None,
            message="Voiceprint saved locally (not linked to Global Speaker)"
        )
    
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action.action}")


@router.delete("/recordings/{recording_id}/speakers/{diarization_label}/voiceprint")
async def delete_voiceprint(
    recording_id: int,
    diarization_label: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Delete the voiceprint (embedding) from a RecordingSpeaker.
    Does NOT affect the linked GlobalSpeaker's embedding.
    """
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(statement)
    rec_speaker = result.scalar_one_or_none()
    
    if not rec_speaker:
        raise HTTPException(status_code=404, detail="Speaker not found in recording")
    
    rec_speaker.embedding = None
    db.add(rec_speaker)
    await db.commit()
    
    return {"ok": True, "message": "Voiceprint deleted"}


@router.post("/recordings/{recording_id}/voiceprints/extract-all")
async def extract_all_voiceprints(
    recording_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Extract voiceprints for all speakers in a recording that don't have one.
    Returns extraction results for each speaker, to be processed by the client.
    """
    # 1. Verify recording exists
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    # 2. Get all speakers without voiceprints
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id
    )
    result = await db.execute(statement)
    all_speakers = result.scalars().all()
    
    speakers_needing_voiceprint = [s for s in all_speakers if not s.embedding]
    
    if not speakers_needing_voiceprint:
        return {
            "message": "All speakers already have voiceprints",
            "speakers_processed": 0,
            "results": []
        }
    
    # 3. Get transcript
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(statement)
    transcript = result.scalar_one_or_none()
    
    if not transcript or not transcript.segments:
        raise HTTPException(status_code=400, detail="No transcript segments found")
    
    # 4. Get all global speakers for matching
    all_global_stmt = select(GlobalSpeaker)
    all_global_result = await db.execute(all_global_stmt)
    all_global_speakers = list(all_global_result.scalars().all())
    
    device_str = config_manager.get("processing_device", "cpu")
    results = []
    
    # 5. Extract voiceprint for each speaker
    for rec_speaker in speakers_needing_voiceprint:
        speaker_name = rec_speaker.name or rec_speaker.diarization_label
        
        # Find segments for this speaker
        speaker_segments = []
        for seg in transcript.segments:
            seg_speaker = seg.get("speaker", "")
            if seg_speaker == rec_speaker.diarization_label or seg_speaker == speaker_name:
                speaker_segments.append((seg["start"], seg["end"]))
        
        if not speaker_segments:
            results.append({
                "diarization_label": rec_speaker.diarization_label,
                "speaker_name": speaker_name,
                "success": False,
                "error": "No audio segments found"
            })
            continue
        
        # Extract embedding
        # Offload to worker
        # Try to get token from user settings, then config
        user_settings = current_user.settings or {}
        hf_token = user_settings.get("hf_token") or config_manager.get("hf_token")
        
        task = celery_app.send_task(
            "backend.worker.tasks.extract_embedding_task",
            args=[recording.audio_path, speaker_segments, device_str, hf_token]
        )
        embedding = await run_in_threadpool(task.get)
        
        if not embedding:
            results.append({
                "diarization_label": rec_speaker.diarization_label,
                "speaker_name": speaker_name,
                "success": False,
                "error": "Extraction failed"
            })
            continue
        
        # Save embedding
        rec_speaker.embedding = embedding
        db.add(rec_speaker)
        
        # Find best match
        matched_speaker = None
        best_score = 0.0
        
        for gs in all_global_speakers:
            if gs.embedding:
                score = cosine_similarity(embedding, gs.embedding)
                if score > best_score:
                    best_score = score
                    matched_speaker = gs
        
        match_info = None
        if matched_speaker and best_score >= 0.5:
            match_info = {
                "id": matched_speaker.id,
                "name": matched_speaker.name,
                "similarity_score": round(best_score, 3),
                "is_strong_match": best_score >= 0.65
            }
        
        results.append({
            "diarization_label": rec_speaker.diarization_label,
            "speaker_name": speaker_name,
            "speaker_id": rec_speaker.id,
            "success": True,
            "matched_speaker": match_info
        })
    
    await db.commit()
    
    # Return all global speakers for UI dropdown
    all_speakers_list = [
        {"id": gs.id, "name": gs.name, "has_voiceprint": gs.embedding is not None}
        for gs in all_global_speakers
    ]
    
    return {
        "speakers_processed": len(results),
        "results": results,
        "all_global_speakers": all_speakers_list
    }

class SpeakerColorUpdate(BaseModel):
    color: str

@router.put("/recordings/{recording_id}/speakers/{label}/color", response_model=dict)
async def update_speaker_color(
    recording_id: int,
    label: str,
    update: SpeakerColorUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    Update the color for a speaker.
    If the speaker is linked to a Global Speaker, updates the Global Speaker's color.
    Otherwise, updates the Recording Speaker's color.
    """
    # 1. Verify recording exists
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # 2. Find the RecordingSpeaker
    stmt = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == label
    )
    result = await db.execute(stmt)
    recording_speaker = result.scalar_one_or_none()
    
    if not recording_speaker:
        raise HTTPException(status_code=404, detail=f"Speaker {label} not found in recording")

    # 3. Update color
    if recording_speaker.global_speaker_id:
        # Update Global Speaker
        global_speaker = await db.get(GlobalSpeaker, recording_speaker.global_speaker_id)
        if global_speaker:
            global_speaker.color = update.color
            db.add(global_speaker)
    else:
        # Update Recording Speaker
        recording_speaker.color = update.color
        db.add(recording_speaker)
        
    await db.commit()
    
    return {"status": "success", "color": update.color}

    speaker.embedding = None
    db.add(speaker)
    await db.commit()
    await db.refresh(speaker)
    return {"ok": True}

@router.post("/{speaker_id}/scan-matches")
async def scan_for_matches(
    speaker_id: int,
    threshold: float = 0.65,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Scan all 'unlinked' speakers in the library and link them to this Global Speaker
    if their embedding similarity is above the threshold.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    if not speaker.embedding:
         raise HTTPException(status_code=400, detail="Speaker has no voiceprint to match against")
         
    # Find all RecordingSpeakers that:
    # 1. Are NOT linked to a Global Speaker
    # 2. Have an embedding
    # 3. Are in recordings owned by this user
    stmt = (
        select(RecordingSpeaker)
        .join(Recording)
        .where(
            RecordingSpeaker.global_speaker_id == None,
            RecordingSpeaker.embedding != None,
            Recording.user_id == current_user.id
        )
    )
    result = await db.execute(stmt)
    candidates = result.scalars().all()
    
    matches_found = 0
    recordings_updated = set()
    
    for cand in candidates:
        if not cand.embedding: continue
        
        score = cosine_similarity(speaker.embedding, cand.embedding)
        if score >= threshold:
            cand.global_speaker_id = speaker.id
            cand.name = speaker.name
            db.add(cand)
            matches_found += 1
            recordings_updated.add(cand.recording_id)
            
    if matches_found > 0:
        await db.commit()
        
    return {
        "success": True,
        "matches_found": matches_found,
        "recordings_updated": len(recordings_updated)
    }

@router.post("/recordings/{recording_id}/speakers/{diarization_label}/split", response_model=List[RecordingSpeaker])
async def split_local_speaker(
    recording_id: int,
    diarization_label: str,
    request: SpeakerSplitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Split a LOCAL speaker in a specific recording.
    Moves selected segments to a NEW or EXISTING speaker label (local name).
    """
    # 1. Verify Recording and Speaker
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    stmt = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(stmt)
    source_speaker = result.scalar_one_or_none()
    if not source_speaker:
         raise HTTPException(status_code=404, detail="Source speaker not found")
         
    if not request.segments:
         raise HTTPException(status_code=400, detail="No segments provided")

    # 2. Determine Target Speaker (New Name)
    target_label = None
    
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
    result = await db.execute(stmt)
    all_rec_speakers = result.scalars().all()
    
    for rs in all_rec_speakers:
        if (rs.local_name == request.new_speaker_name or 
            rs.name == request.new_speaker_name or 
            rs.diarization_label == request.new_speaker_name):
            target_label = rs.diarization_label
            break
            
    if not target_label:
        import uuid
        target_label = f"SPLIT_{uuid.uuid4().hex[:8]}"
        new_rs = RecordingSpeaker(
            recording_id=recording_id,
            diarization_label=target_label,
            local_name=request.new_speaker_name,
        )
        db.add(new_rs)
        await db.flush()
    
    # 3. Update Transcript Segments
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()
    
    if transcript and transcript.segments:
        segments_to_move = set()
        for s in request.segments:
            segments_to_move.add((s.recording_id, s.start, s.end))
            
        new_segments = []
        segments_updated = False
        
        for segment in transcript.segments:
            seg_copy = dict(segment)
            matches = False
            for r_id, start, end in segments_to_move:
                if r_id == recording_id and abs(segment["start"] - start) < 0.01 and abs(segment["end"] - end) < 0.01:
                    matches = True
                    break
            
            if matches:
                seg_copy["speaker"] = target_label
                segments_updated = True
                
            new_segments.append(seg_copy)
            
        if segments_updated:
            transcript.segments = new_segments
            flag_modified(transcript, "segments")
            db.add(transcript)
            
    await db.commit()
    
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
    result = await db.execute(stmt)
    return result.scalars().all()
