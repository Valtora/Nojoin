from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from pydantic import BaseModel

from backend.api.deps import get_db
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.recording import Recording
from backend.models.transcript import Transcript
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

@router.get("/", response_model=List[GlobalSpeaker])
async def list_global_speakers(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    """
    List all global speakers.
    """
    statement = select(GlobalSpeaker).order_by(GlobalSpeaker.name).offset(skip).limit(limit)
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
    logger.info(f"Updating speaker for recording {recording_id}: {update}")
    
    # 1. Verify recording exists
    recording = await db.get(Recording, recording_id)
    if not recording:
        logger.error(f"Recording {recording_id} not found")
        raise HTTPException(status_code=404, detail="Recording not found")

    # 2. Find or Create Global Speaker
    # Prevent creating a Global Speaker with placeholder names
    import re
    placeholder_pattern = re.compile(r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE)
    
    if placeholder_pattern.match(update.global_speaker_name):
        raise HTTPException(
            status_code=400, 
            detail="Cannot create a Global Speaker with a placeholder name (e.g., 'Speaker 1', 'SPEAKER_00'). Please use a real name."
        )

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
        except Exception as e:
            logger.error(f"Error creating global speaker: {e}")
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
        
    for rs in recording_speakers:
        rs.global_speaker_id = global_speaker.id
        rs.name = global_speaker.name
        db.add(rs)
        
        # Active Learning: Update Global Speaker embedding from user feedback
        if rs.embedding:
            if global_speaker.embedding:
                # Use a higher alpha (e.g., 0.3) because this is explicit user feedback
                global_speaker.embedding = merge_embeddings(global_speaker.embedding, rs.embedding, alpha=0.3)
            else:
                # Initialize if empty
                global_speaker.embedding = rs.embedding
            db.add(global_speaker)

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
        rs.name = target.name
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
    db: AsyncSession = Depends(get_db)
):
    """
    Merge two speakers in a recording.
    All segments belonging to source_speaker_label will be reassigned to target_speaker_label.
    The source_speaker_label will be removed from the recording's speaker list.
    """
    # 1. Verify recording exists
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # 2. Update Transcript Segments
    # We need to fetch the transcript first
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(statement)
    transcript = result.scalar_one_or_none()

    if transcript and transcript.segments:
        updated_segments = []
        changed = False
        for segment in transcript.segments:
            if segment.get("speaker") == merge_data.source_speaker_label:
                segment["speaker"] = merge_data.target_speaker_label
                changed = True
            updated_segments.append(segment)
        
        if changed:
            transcript.segments = updated_segments
            db.add(transcript)

    # 3. Update RecordingSpeaker entries
    # Find the source speaker entry
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == merge_data.source_speaker_label
    )
    result = await db.execute(statement)
    source_speaker = result.scalar_one_or_none()

    # Find the target speaker entry
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == merge_data.target_speaker_label
    )
    result = await db.execute(statement)
    target_speaker = result.scalar_one_or_none()

    if source_speaker:
        # If target speaker doesn't exist (edge case?), we might want to rename source instead?
        # But assuming target exists or is a valid label we want to use.
        
        # If target speaker entry exists, we can delete the source speaker entry
        # If target speaker entry does NOT exist (e.g. we are merging into a new label?), 
        # we should probably rename source to target.
        # But the UI will likely provide a list of existing speakers.
        
        if target_speaker:
            # Merge logic: Delete source. 
            # (Optional: Merge embeddings? For now, just keep target's embedding)
            await db.delete(source_speaker)
        else:
            # Target label doesn't have a RecordingSpeaker entry yet.
            # Rename source to target.
            source_speaker.diarization_label = merge_data.target_speaker_label
            # Reset name if it was specific to the old label? Or keep it?
            # Let's keep the name if it exists, or maybe not.
            # If we are merging "SPEAKER_01" into "SPEAKER_00", and SPEAKER_00 didn't exist...
            # That's a rename.
            db.add(source_speaker)

    await db.commit()
    await db.refresh(recording)
    return recording

@router.delete("/recordings/{recording_id}/speakers/{diarization_label}")
async def delete_recording_speaker(
    recording_id: int,
    diarization_label: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a speaker from a recording.
    Sets all segments associated with this speaker to 'UNKNOWN'.
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
