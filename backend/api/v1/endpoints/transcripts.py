from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel
import uuid
import os

from backend.api.deps import get_db
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker, GlobalSpeaker
from backend.processing.embedding import load_embedding_model, merge_embeddings
from backend.utils.config_manager import config_manager
import numpy as np
from pyannote.core import Segment

router = APIRouter()

@router.put("/{recording_id}/segments/{segment_index}")
async def update_segment_speaker(
    recording_id: int,
    segment_index: int,
    new_speaker_name: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db)
):
    """
    Update the speaker for a specific transcript segment.
    Also updates the speaker embedding associations using the audio from this segment.
    """
    # 1. Fetch Recording and Transcript
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    # Fetch transcript with segments
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
        
    if segment_index < 0 or segment_index >= len(transcript.segments):
        raise HTTPException(status_code=400, detail="Invalid segment index")
        
    segment = transcript.segments[segment_index]
    old_label = segment.get('speaker')
    
    # 2. Resolve Target Speaker
    # We need to find the diarization_label to use for the segment.
    
    target_label = None
    target_recording_speaker = None
    
    # Check if speaker exists in this recording (by name or global name)
    # We need to fetch all recording speakers to check names
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
    result = await db.execute(stmt)
    recording_speakers = result.scalars().all()
    
    # Try to find match
    for rs in recording_speakers:
        # Check explicit name on RecordingSpeaker
        if rs.name and rs.name.lower() == new_speaker_name.lower():
            target_label = rs.diarization_label
            target_recording_speaker = rs
            break
        # Check linked GlobalSpeaker name
        # Note: We need to ensure global_speaker is loaded. 
        # In async, we might need to explicit join or lazy load. 
        # For now, let's assume we can query it if needed or it's eager loaded?
        # SQLModel relationships are lazy by default.
        if rs.global_speaker_id:
            gs = await db.get(GlobalSpeaker, rs.global_speaker_id)
            if gs and gs.name.lower() == new_speaker_name.lower():
                target_label = rs.diarization_label
                target_recording_speaker = rs
                break
                
    if not target_label:
        # Speaker not found in recording. Check Global Speakers.
        stmt = select(GlobalSpeaker).where(GlobalSpeaker.name == new_speaker_name)
        result = await db.execute(stmt)
        global_speaker = result.scalar_one_or_none()
        
        if not global_speaker:
            # Create new Global Speaker
            global_speaker = GlobalSpeaker(name=new_speaker_name)
            db.add(global_speaker)
            await db.commit()
            await db.refresh(global_speaker)
            
        # Create new RecordingSpeaker
        # Use a unique manual label
        target_label = f"MANUAL_{uuid.uuid4().hex[:8]}"
        target_recording_speaker = RecordingSpeaker(
            recording_id=recording_id,
            diarization_label=target_label,
            global_speaker_id=global_speaker.id,
            name=new_speaker_name # Optional, but good for display
        )
        db.add(target_recording_speaker)
        await db.commit()
        await db.refresh(target_recording_speaker)

    # 3. Update Transcript Segment
    transcript.segments[segment_index]['speaker'] = target_label
    flag_modified(transcript, "segments")
    db.add(transcript)
    
    # 4. Update Embeddings (Active Learning)
    # Extract embedding for this segment
    try:
        if recording.audio_path and os.path.exists(recording.audio_path) and target_recording_speaker:
            # Load model
            device = "cuda" if config_manager.get("use_gpu", True) else "cpu"
            model = load_embedding_model(device)
            
            # Crop segment
            start = segment['start']
            end = segment['end']
            duration = end - start
            
            if duration > 0.5: # Only extract if segment is long enough
                # Pyannote Segment
                seg = Segment(start, end)
                
                # Extract
                emb = model.crop(recording.audio_path, seg)
                
                # Handle potential Tuple return (some pyannote versions)
                if isinstance(emb, tuple):
                    emb = emb[0]

                # Handle pyannote SlidingWindowFeature
                if hasattr(emb, 'data'):
                    emb_data = emb.data
                else:
                    emb_data = emb
                
                emb_array = np.array(emb_data)
                if len(emb_array.shape) == 2:
                    emb_array = np.mean(emb_array, axis=0)
                
                new_embedding = emb_array.tolist()
                
                # Merge into RecordingSpeaker
                # Use high alpha (0.5) because this is explicit user correction
                current_emb = target_recording_speaker.embedding if target_recording_speaker.embedding is not None else []
                
                target_recording_speaker.embedding = merge_embeddings(
                    current_emb, 
                    new_embedding, 
                    alpha=0.5
                )
                db.add(target_recording_speaker)
                
                # Merge into GlobalSpeaker
                if target_recording_speaker.global_speaker_id:
                    gs = await db.get(GlobalSpeaker, target_recording_speaker.global_speaker_id)
                    if gs:
                        gs_emb = gs.embedding if gs.embedding is not None else []
                        gs.embedding = merge_embeddings(
                            gs_emb,
                            new_embedding,
                            alpha=0.5
                        )
                        db.add(gs)
                        
    except Exception as e:
        # Log error but don't fail the request
        print(f"Failed to update embeddings: {e}")
        
    # 5. Cleanup Old Speaker (if unused)
    if old_label and old_label != target_label:
        # Check if old_label is still used in any segment
        is_used = False
        for s in transcript.segments:
            if s.get('speaker') == old_label:
                is_used = True
                break
        
        if not is_used:
            # Delete the RecordingSpeaker entry
            stmt = select(RecordingSpeaker).where(
                RecordingSpeaker.recording_id == recording_id,
                RecordingSpeaker.diarization_label == old_label
            )
            result = await db.execute(stmt)
            old_speaker_entry = result.scalar_one_or_none()
            
            if old_speaker_entry:
                await db.delete(old_speaker_entry)
                # Note: We don't delete the GlobalSpeaker, just the local association
    
    await db.commit()
    
    return {"status": "success", "speaker": target_label}

class TranscriptSegmentTextUpdate(BaseModel):
    text: str

class FindReplaceRequest(BaseModel):
    find_text: str
    replace_text: str

@router.put("/{recording_id}/segments/{segment_index}/text", response_model=Transcript)
async def update_transcript_segment_text(
    recording_id: int,
    segment_index: int,
    update: TranscriptSegmentTextUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    Update the text content of a specific transcript segment.
    """
    # 1. Fetch Transcript
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
        
    if segment_index < 0 or segment_index >= len(transcript.segments):
        raise HTTPException(status_code=400, detail="Invalid segment index")
        
    # 2. Update Segment
    transcript.segments[segment_index]['text'] = update.text
    flag_modified(transcript, "segments")
    
    # 3. Reconstruct Full Text
    full_text = " ".join([s['text'] for s in transcript.segments])
    transcript.text = full_text
    
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    
    return transcript

@router.post("/{recording_id}/replace", response_model=Transcript)
async def find_and_replace(
    recording_id: int,
    replace_request: FindReplaceRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Find and replace text across the entire transcript.
    """
    # 1. Fetch Transcript
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    
    find_text = replace_request.find_text
    replace_text = replace_request.replace_text
    
    if not find_text:
        raise HTTPException(status_code=400, detail="Find text cannot be empty")

    count = 0
    for segment in transcript.segments:
        if find_text in segment['text']:
            segment['text'] = segment['text'].replace(find_text, replace_text)
            count += 1
    
    if count > 0:
        flag_modified(transcript, "segments")
        
        # Reconstruct Full Text
        full_text = " ".join([s['text'] for s in transcript.segments])
        transcript.text = full_text
        
        db.add(transcript)
        await db.commit()
        await db.refresh(transcript)
        
    return transcript
