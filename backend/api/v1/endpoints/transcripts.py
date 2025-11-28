from typing import List, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, Body, Response, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel
import uuid
import os
import logging

from backend.api.deps import get_db, get_current_user
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker, GlobalSpeaker
from backend.models.user import User
from backend.utils.config_manager import config_manager
from backend.celery_app import celery_app

router = APIRouter()
logger = logging.getLogger(__name__)


# --- Pydantic Models ---

class TranscriptSegmentTextUpdate(BaseModel):
    text: str

class FindReplaceRequest(BaseModel):
    find_text: str
    replace_text: str

class TranscriptSegmentsUpdate(BaseModel):
    segments: List[dict]

class NotesUpdate(BaseModel):
    notes: str


# --- Helper Functions ---

def _build_speaker_map(speakers) -> dict:
    """Build a mapping from diarization label to speaker name."""
    speaker_map = {}
    for rs in speakers:
        name = rs.local_name or (rs.global_speaker.name if rs.global_speaker else None) or rs.name or rs.diarization_label
        speaker_map[rs.diarization_label] = name
    return speaker_map

def _format_transcript_text(segments, speaker_map: dict) -> str:
    """Format transcript segments as text."""
    lines = []
    for seg in segments:
        speaker_label = seg.get('speaker', 'Unknown')
        speaker_name = speaker_map.get(speaker_label, speaker_label)
        start = seg.get('start', 0)
        minutes = int(start // 60)
        seconds = int(start % 60)
        time_str = f"[{minutes:02d}:{seconds:02d}]"
        text = seg.get('text', '').strip()
        lines.append(f"{time_str} {speaker_name}: {text}")
    return "\n".join(lines)

def _sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file output."""
    return "".join([c for c in filename if c.isalpha() or c.isdigit() or c in (' ', '-', '_', '.')]).strip()


def _apply_find_replace(transcript: Transcript, find_text: str, replace_text: str) -> int:
    """
    Apply find and replace to both transcript segments and notes.
    Returns the number of segment replacements made.
    """
    segment_count = 0
    
    # Replace in transcript segments
    for segment in transcript.segments:
        if find_text in segment['text']:
            segment['text'] = segment['text'].replace(find_text, replace_text)
            segment_count += 1
    
    if segment_count > 0:
        flag_modified(transcript, "segments")
        # Reconstruct full text
        full_text = " ".join([s['text'] for s in transcript.segments])
        transcript.text = full_text
    
    # Replace in notes
    if transcript.notes and find_text in transcript.notes:
        transcript.notes = transcript.notes.replace(find_text, replace_text)
    
    return segment_count


# --- Export Endpoint ---

@router.get("/{recording_id}/export")
async def export_content(
    recording_id: int,
    content_type: Literal["transcript", "notes", "both"] = Query(default="transcript"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Export the transcript and/or notes as a text file.
    content_type: 'transcript', 'notes', or 'both'
    """
    # 1. Fetch Recording with Speakers (for name resolution)
    stmt = select(Recording).where(Recording.id == recording_id).where(Recording.user_id == current_user.id).options(
        selectinload(Recording.speakers).options(selectinload(RecordingSpeaker.global_speaker))
    )
    result = await db.execute(stmt)
    recording = result.scalar_one_or_none()
    
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # 2. Fetch Transcript
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    # 3. Create Speaker Map
    speaker_map = _build_speaker_map(recording.speakers)

    # 4. Build Content based on content_type
    sections = []
    
    if content_type in ["transcript", "both"]:
        # Add Transcript Section
        sections.append(f"{recording.name} - Transcript")
        sections.append("=" * 50)
        sections.append("")
        sections.append(_format_transcript_text(transcript.segments, speaker_map))
    
    if content_type in ["notes", "both"]:
        if transcript.notes:
            if content_type == "both":
                sections.append("")
                sections.append("")
                sections.append(f"{recording.name} - Meeting Notes")
                sections.append("=" * 50)
                sections.append("")
            else:
                sections.append(f"{recording.name} - Meeting Notes")
                sections.append("=" * 50)
                sections.append("")
            sections.append(transcript.notes)
        elif content_type == "notes":
            raise HTTPException(status_code=404, detail="No meeting notes available for this recording")
    
    content = "\n".join(sections)
    
    # 5. Determine filename
    if content_type == "transcript":
        filename = f"{recording.name} - Transcript.txt"
    elif content_type == "notes":
        filename = f"{recording.name} - Notes.txt"
    else:
        filename = f"{recording.name} - Full Export.txt"
    
    filename = _sanitize_filename(filename)
    
    return Response(
        content=content,
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )

@router.put("/{recording_id}/segments/{segment_index}")
async def update_segment_speaker(
    recording_id: int,
    segment_index: int,
    new_speaker_name: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update the speaker for a specific transcript segment.
    Also updates the speaker embedding associations using the audio from this segment.
    """
    # 1. Fetch Recording and Transcript
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    if recording.user_id != current_user.id:
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
        if rs.local_name and rs.local_name.lower() == new_speaker_name.lower():
            target_label = rs.diarization_label
            target_recording_speaker = rs
            break
        if rs.name and rs.name.lower() == new_speaker_name.lower():
            target_label = rs.diarization_label
            target_recording_speaker = rs
            break
        # Check linked GlobalSpeaker name
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
            name=None # Deprecated, use global link
        )
        db.add(target_recording_speaker)
        await db.commit()
        await db.refresh(target_recording_speaker)

    # 3. Update Transcript Segment
    # CRITICAL: Store the LABEL, not the name
    transcript.segments[segment_index]['speaker'] = target_label
    flag_modified(transcript, "segments")
    db.add(transcript)
    
    # 4. Update Embeddings (Active Learning)
    # Dispatch task to worker
    try:
        if recording.audio_path and target_recording_speaker:
            start = segment['start']
            end = segment['end']
            duration = end - start
            
            if duration > 0.5:
                celery_app.send_task(
                    "backend.worker.tasks.update_speaker_embedding_task",
                    args=[
                        recording_id,
                        start,
                        end,
                        target_recording_speaker.id
                    ]
                )
    except Exception as e:
        # Log error but don't fail the request
        print(f"Failed to dispatch embedding update task: {e}")
        
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

@router.put("/{recording_id}/segments/{segment_index}/text", response_model=Transcript)
async def update_transcript_segment_text(
    recording_id: int,
    segment_index: int,
    update: TranscriptSegmentTextUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update the text content of a specific transcript segment.
    """
    # 0. Check Ownership
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Find and replace text across the entire transcript AND meeting notes.
    This ensures consistency between the diarized transcript and generated notes.
    """
    # 0. Check Ownership
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

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

    # 2. Apply find/replace to both transcript and notes
    _apply_find_replace(transcript, find_text, replace_text)
        
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
        
    return transcript

@router.put("/{recording_id}/segments", response_model=Transcript)
async def update_transcript_segments(
    recording_id: int,
    update: TranscriptSegmentsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Bulk update all segments of a transcript.
    Useful for Undo/Redo operations involving multiple segments.
    """
    # 0. Check Ownership
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    # 1. Fetch Transcript
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
        
    # 2. Update Segments
    transcript.segments = update.segments
    flag_modified(transcript, "segments")
    
    # 3. Reconstruct Full Text
    full_text = " ".join([s.get('text', '') for s in transcript.segments])
    transcript.text = full_text
    
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    
    return transcript


# --- Notes Endpoints ---

@router.get("/{recording_id}/notes")
async def get_notes(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get the meeting notes for a recording.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    
    return {"notes": transcript.notes}

@router.put("/{recording_id}/notes")
async def update_notes(
    recording_id: int,
    update: NotesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update the meeting notes for a recording.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    
    transcript.notes = update.notes
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    
    return {"notes": transcript.notes, "status": "success"}

@router.post("/{recording_id}/notes/generate")
async def generate_notes(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Generate meeting notes using the configured LLM provider.
    """
    # 1. Fetch Recording with Speakers
    stmt = select(Recording).where(Recording.id == recording_id).where(Recording.user_id == current_user.id).options(
        selectinload(Recording.speakers).options(selectinload(RecordingSpeaker.global_speaker))
    )
    result = await db.execute(stmt)
    recording = result.scalar_one_or_none()
    
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    # 2. Fetch Transcript
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()
    
    if not transcript or not transcript.segments:
        raise HTTPException(status_code=404, detail="Transcript not found or empty")
    
    # 3. Get User Settings
    user_settings = current_user.settings or {}
    provider = user_settings.get("llm_provider") or config_manager.get("llm_provider") or "gemini"
    api_key = user_settings.get(f"{provider}_api_key")
    model = user_settings.get(f"{provider}_model")
    
    if not api_key:
        raise HTTPException(status_code=400, detail=f"No API key configured for {provider}. Please configure it in settings.")
    
    # 4. Build Speaker Map and Transcript Text
    speaker_map = _build_speaker_map(recording.speakers)
    
    # Render transcript text for LLM
    lines = []
    for seg in transcript.segments:
        speaker_label = seg.get('speaker', 'Unknown')
        speaker_name = speaker_map.get(speaker_label, speaker_label)
        text = seg.get('text', '')
        lines.append(f"{speaker_name}: {text}")
    transcript_text = "\n".join(lines)
    
    # 5. Call LLM Service
    try:
        from backend.processing.LLM_Services import get_llm_backend
        
        llm = get_llm_backend(provider, api_key=api_key, model=model)
        notes = llm.generate_meeting_notes(transcript_text, speaker_map)
        
        # 6. Save Notes
        transcript.notes = notes
        db.add(transcript)
        await db.commit()
        await db.refresh(transcript)
        
        return {"notes": notes, "status": "success"}
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error(f"LLM error generating notes: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating notes: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error generating notes: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred while generating notes")

@router.post("/{recording_id}/notes/replace")
async def find_and_replace_notes(
    recording_id: int,
    replace_request: FindReplaceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Find and replace text in the meeting notes AND transcript.
    This ensures consistency between the diarized transcript and generated notes.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    
    find_text = replace_request.find_text
    replace_text = replace_request.replace_text
    
    if not find_text:
        raise HTTPException(status_code=400, detail="Find text cannot be empty")
    
    # Apply find/replace to both transcript and notes
    _apply_find_replace(transcript, find_text, replace_text)
        
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    
    return {"notes": transcript.notes, "status": "success"}
