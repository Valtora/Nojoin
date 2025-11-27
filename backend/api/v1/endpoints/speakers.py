from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select
from pydantic import BaseModel

from backend.api.deps import get_db, get_current_user
from backend.models.speaker import GlobalSpeaker, GlobalSpeakerRead, GlobalSpeakerUpdate, GlobalSpeakerWithCount, RecordingSpeaker
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.embedding import merge_embeddings, extract_embedding_for_segments, find_matching_global_speaker, cosine_similarity
from backend.utils.config_manager import config_manager

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

class VoiceprintResult(BaseModel):
    """Response for voiceprint operations."""
    success: bool
    has_voiceprint: bool
    matched_speaker: Optional[dict] = None  # {id, name, similarity_score}
    message: Optional[str] = None

class GlobalSpeakerWithCount(BaseModel):
    """Global speaker with recording count."""
    id: int
    name: str
    has_voiceprint: bool
    recording_count: int
    created_at: str
    updated_at: str

class SpeakerColorUpdate(BaseModel):
    color: str

@router.get("", response_model=List[GlobalSpeakerWithCount])
async def read_speakers_root(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieve global speakers (root path).
    """
    return await read_speakers(skip=skip, limit=limit, db=db, current_user=current_user)

@router.get("/", response_model=List[GlobalSpeakerWithCount])
async def list_global_speakers(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all global speakers with their recording association counts.
    """
    from sqlalchemy import func
    
    # Query with left join to count recordings
    statement = (
        select(
            GlobalSpeaker,
            func.count(RecordingSpeaker.id).label('recording_count')
        )
        .outerjoin(RecordingSpeaker, GlobalSpeaker.id == RecordingSpeaker.global_speaker_id)
        .where(GlobalSpeaker.user_id == current_user.id)
        .group_by(GlobalSpeaker.id)
        .order_by(GlobalSpeaker.name)
        .offset(skip)
        .limit(limit)
    )
    
    result = await db.execute(statement)
    rows = result.all()
    
    # Build response
    speakers_with_counts = []
    for row in rows:
        speaker = row[0]
        count = row[1]
        speakers_with_counts.append(GlobalSpeakerWithCount(
            id=speaker.id,
            name=speaker.name,
            has_voiceprint=speaker.has_voiceprint,
            recording_count=count,
            created_at=speaker.created_at.isoformat(),
            updated_at=speaker.updated_at.isoformat()
        ))
    
    return speakers_with_counts

@router.post("/", response_model=GlobalSpeaker)
async def create_global_speaker(
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new global speaker manually.
    """
    # Check if exists
    statement = select(GlobalSpeaker).where(GlobalSpeaker.name == name, GlobalSpeaker.user_id == current_user.id)
    result = await db.execute(statement)
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Speaker already exists")
        
    speaker = GlobalSpeaker(name=name, user_id=current_user.id)
    db.add(speaker)
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
        # We also want to catch if the transcript has the *new* name stored directly
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
                    global_speaker.embedding = merge_embeddings(global_speaker.embedding, rs.embedding, alpha=0.3)
                else:
                    global_speaker.embedding = rs.embedding
                db.add(global_speaker)
        else:
            # Set as local name only (not promoted to global)
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
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.global_speaker_id == source.id)
    result = await db.execute(stmt)
    recording_speakers = result.scalars().all()
    
    for rs in recording_speakers:
        rs.global_speaker_id = target.id
        rs.name = None # Clear deprecated name
        rs.local_name = None # Clear local name to ensure target global name takes precedence
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
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Rename a global speaker.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    # Check name uniqueness
    stmt = select(GlobalSpeaker).where(GlobalSpeaker.name == name, GlobalSpeaker.user_id == current_user.id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing and existing.id != speaker_id:
        raise HTTPException(status_code=400, detail="Speaker name already exists")
        
    speaker.name = name
    db.add(speaker)
    
    # Propagate to RecordingSpeakers
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.global_speaker_id == speaker_id)
    result = await db.execute(stmt)
    linked_speakers = result.scalars().all()
    for rs in linked_speakers:
        rs.name = name
        db.add(rs)

    await db.commit()
    await db.refresh(speaker)
    return speaker

@router.delete("/{speaker_id}")
async def delete_global_speaker(
    speaker_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a global speaker.
    Sets global_speaker_id to NULL for all associated recording speakers.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")
        
    # The relationship is set to nullify on delete by default in SQLModel/SQLAlchemy 
    # if nullable=True (which it is), but let's be explicit if needed.
    # Actually, we defined the relationship in models/speaker.py.
    # Let's just delete and let the DB handle the foreign key set null if configured,
    # or we manually nullify.
    
    # Manually nullify to be safe and preserve name
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.global_speaker_id == speaker_id)
    result = await db.execute(stmt)
    recording_speakers = result.scalars().all()
    
    for rs in recording_speakers:
        # If the local name is missing, snapshot the global name so the transcript doesn't revert to SPEAKER_XX
        if not rs.name:
            rs.name = speaker.name
        rs.global_speaker_id = None
        db.add(rs)
        
    await db.delete(speaker)
    await db.commit()

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

    # 3. Find the source and target speaker entries
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

    # Identify all aliases for the source speaker to catch in transcript
    source_aliases = {merge_data.source_speaker_label}
    if source_speaker.local_name:
        source_aliases.add(source_speaker.local_name)
    if source_speaker.name:
        source_aliases.add(source_speaker.name)
    # Also check if it was linked to a global speaker
    if source_speaker.global_speaker_id:
        gs = await db.get(GlobalSpeaker, source_speaker.global_speaker_id)
        if gs:
            source_aliases.add(gs.name)

    # 4. Update Transcript Segments
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(statement)
    transcript = result.scalar_one_or_none()

    if transcript and transcript.segments:
        segments_updated = False
        # Create a new list to ensure SQLAlchemy detects the change
        new_segments = []
        for segment in transcript.segments:
            segment_copy = dict(segment)
            # Check if segment speaker matches any source alias
            if segment_copy.get("speaker") in source_aliases:
                segment_copy["speaker"] = merge_data.target_speaker_label
                segments_updated = True
            new_segments.append(segment_copy)
        
        if segments_updated:
            # Explicitly set the segments to trigger SQLAlchemy change detection
            transcript.segments = new_segments
            flag_modified(transcript, "segments")
            db.add(transcript)

    # 5. Merge embeddings if both speakers have them
    if source_speaker.embedding and target_speaker.embedding:
        target_speaker.embedding = merge_embeddings(
            target_speaker.embedding, 
            source_speaker.embedding, 
            alpha=0.5  # Equal weight for merge
        )
        db.add(target_speaker)
    elif source_speaker.embedding and not target_speaker.embedding:
        # Target has no embedding, copy from source
        target_speaker.embedding = source_speaker.embedding
        db.add(target_speaker)

    # 6. Delete the source speaker entry
    await db.delete(source_speaker)

    # 7. Flush to ensure changes are written before commit
    await db.flush()
    await db.commit()
    
    # 8. Refresh recording to get updated relationships
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
    embedding = extract_embedding_for_segments(recording.audio_path, speaker_segments, device_str)
    
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
        embedding = extract_embedding_for_segments(recording.audio_path, speaker_segments, device_str)
        
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
