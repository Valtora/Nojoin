from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy.orm.attributes import flag_modified
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
        if recording.audio_path and os.path.exists(recording.audio_path):
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
                
                # Convert to list
                if hasattr(emb, 'data'):
                    emb = emb.data
                emb = np.array(emb)
                if len(emb.shape) == 2:
                    emb = np.mean(emb, axis=0)
                
                new_embedding = emb.tolist()
                
                # Merge into RecordingSpeaker
                # Use high alpha (0.5) because this is explicit user correction
                target_recording_speaker.embedding = merge_embeddings(
                    target_recording_speaker.embedding, 
                    new_embedding, 
                    alpha=0.5
                )
                db.add(target_recording_speaker)
                
                # Merge into GlobalSpeaker
                if target_recording_speaker.global_speaker_id:
                    gs = await db.get(GlobalSpeaker, target_recording_speaker.global_speaker_id)
                    if gs:
                        gs.embedding = merge_embeddings(
                            gs.embedding,
                            new_embedding,
                            alpha=0.5
                        )
                        db.add(gs)
                        
    except Exception as e:
        # Log error but don't fail the request
        print(f"Failed to update embeddings: {e}")
        
    await db.commit()
    
    return {"status": "success", "speaker": target_label}
