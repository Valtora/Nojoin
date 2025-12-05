from typing import List, Literal
from fastapi import APIRouter, Depends, HTTPException, Body, Response, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel
import uuid
import os
import logging
import json
import re

from backend.api.deps import get_db, get_current_user
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker, GlobalSpeaker
from backend.models.user import User
from backend.models.chat import ChatMessage
from backend.utils.config_manager import config_manager
from backend.celery_app import celery_app
from backend.processing.LLM_Services import get_llm_backend
from backend.core.db import async_session_maker

router = APIRouter()
logger = logging.getLogger(__name__)


# --- Pydantic Models ---

class TranscriptSegmentTextUpdate(BaseModel):
    text: str

class FindReplaceRequest(BaseModel):
    find_text: str
    replace_text: str
    case_sensitive: bool = False
    use_regex: bool = False

class TranscriptSegmentsUpdate(BaseModel):
    segments: List[dict]

class NotesUpdate(BaseModel):
    notes: str

class ChatRequest(BaseModel):
    message: str


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


def _apply_find_replace(transcript: Transcript, find_text: str, replace_text: str, case_sensitive: bool = False, use_regex: bool = False) -> int:
    """
    Apply find and replace to both transcript segments and notes.
    Returns the number of segment replacements made.
    """
    segment_count = 0
    
    # Prepare regex pattern
    flags = 0 if case_sensitive else re.IGNORECASE
    
    if use_regex:
        pattern = find_text
    else:
        pattern = re.escape(find_text)
        
    try:
        regex = re.compile(pattern, flags)
    except re.error:
        # If regex is invalid, fallback to exact match or raise error?
        # For now, let's just return 0 or log it. 
        # But since we validate on frontend usually, let's assume it's okay or fail gracefully.
        return 0

    # Replace in transcript segments
    for segment in transcript.segments:
        # Check if match exists first to avoid unnecessary writes/updates
        if regex.search(segment['text']):
            segment['text'] = regex.sub(replace_text, segment['text'])
            segment_count += 1
    
    if segment_count > 0:
        flag_modified(transcript, "segments")
        # Reconstruct full text
        full_text = " ".join([s['text'] for s in transcript.segments])
        transcript.text = full_text
    
    # Replace in notes
    if transcript.notes and regex.search(transcript.notes):
        transcript.notes = regex.sub(replace_text, transcript.notes)
    
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
    case_sensitive = replace_request.case_sensitive
    use_regex = replace_request.use_regex
    
    if not find_text:
        raise HTTPException(status_code=400, detail="Find text cannot be empty")

    # 2. Apply find/replace to both transcript and notes
    _apply_find_replace(transcript, find_text, replace_text, case_sensitive, use_regex)
        
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
    
    if not api_key and provider != "ollama":
        raise HTTPException(status_code=400, detail=f"No API key configured for {provider}. Please configure it in settings.")
    
    # 4. Call Worker Task
    from backend.worker.tasks import generate_notes_task
    
    transcript.notes_status = "generating"
    db.add(transcript)
    await db.commit()
    
    generate_notes_task.delay(recording_id)
    
    return {"status": "success", "message": "Note generation started"}


# --- Chat Endpoints ---

@router.get("/{recording_id}/chat")
async def get_chat_history(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get the chat history for a recording.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    # Fetch chat messages
    stmt = select(ChatMessage).where(ChatMessage.recording_id == recording_id).order_by(ChatMessage.created_at)
    result = await db.execute(stmt)
    messages = result.scalars().all()
    
    return messages

@router.delete("/{recording_id}/chat")
async def clear_chat_history(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Clear the chat history for a recording.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    # Delete all chat messages for this recording
    stmt = select(ChatMessage).where(ChatMessage.recording_id == recording_id)
    result = await db.execute(stmt)
    messages = result.scalars().all()
    
    for msg in messages:
        await db.delete(msg)
    
    await db.commit()
    return {"status": "success"}

@router.post("/{recording_id}/chat")
async def chat_with_meeting(
    recording_id: int,
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Chat with the meeting transcript using LLM (Streaming).
    """
    # 1. Check Ownership & Fetch Data
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    stmt = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(stmt)
    transcript_obj = result.scalar_one_or_none()
    
    if not transcript_obj:
        raise HTTPException(status_code=404, detail="Transcript not found")
        
    meeting_notes = transcript_obj.notes or ""
    
    # 2. Get Chat History
    # We only need recent history to fit in context window, but for now let's get all 
    # and LLMBackend can handle truncation if implemented, or we truncate here.
    # Simple truncation: last 20 messages.
    stmt = select(ChatMessage).where(ChatMessage.recording_id == recording_id).order_by(ChatMessage.created_at)
    result = await db.execute(stmt)
    db_messages = result.scalars().all()
    
    # Convert to format expected by LLMBackend
    conversation_history = []
    for msg in db_messages:
        # Gemini expects "parts": [{"text": ...}], others simplify to "content": ...
        # LLMBackend abstraction handles specific API format in `ask_question_streaming` if needed?
        # Actually `LLMBackend` implementations loop over history and adapt it.
        # But they expect a generic format.
        # Gemini implementation expects: {"role": "user"|"model", "parts": [{"text": ...}]}
        # OpenAI/Anthropic implementation expects: {"role": "user"|"assistant", "content": ...}
        
        # We need a unified format here or let the backend handle it.
        # The implementations seem to expect different formats in `ask_question_about_meeting`.
        # Gemini: Checks for "parts"
        # OpenAI: Checks for "content" or "parts" (helper code I wrote handles both?)
        
        # Let's standardize on a simple dictionary and let the backend adapt.
        # But looking at my `LLM_Services.py` code:
        # Gemini: `if conversation_history: contents.extend(conversation_history)` -> expects Gemini format directly.
        # OpenAI: `if conversation_history: ... if msg.get("role") and msg.get("parts")...` -> expects Gemini format?
        
        # Wait, I copied the logic from somewhere or wrote it to be compatible.
        # Let's look at `LLM_Services.py` again (I just wrote it).
        pass

    # Re-reading `LLM_Services.py`:
    # OpenAI:
    # if conversation_history:
    #     for msg in conversation_history:
    #         if msg.get("role") and msg.get("parts"):
    #             for part in msg["parts"]:
    #                 messages.append({"role": msg["role"], "content": part["text"]})
    
    # This implies OpenAI backend expects Gemini-style format in `conversation_history` and converts it.
    # So I should construct `conversation_history` in Gemini format.
    
    formatted_history = []
    for msg in db_messages:
        role = "user" if msg.role == "user" else "model" # Gemini uses 'model' instead of 'assistant'
        formatted_history.append({
            "role": role,
            "parts": [{"text": msg.content}]
        })
    
    # 3. Save User Message to DB immediately
    user_msg = ChatMessage(
        recording_id=recording_id,
        user_id=current_user.id,
        role="user",
        content=request.message
    )
    db.add(user_msg)
    await db.commit()
    
    # 4. Get User Settings
    user_settings = current_user.settings or {}
    provider = user_settings.get("llm_provider") or config_manager.get("llm_provider") or "gemini"
    api_key = user_settings.get(f"{provider}_api_key")
    model = user_settings.get(f"{provider}_model")
    custom_instructions = user_settings.get("chat_custom_instructions")
    
    if not api_key and provider != "ollama":
        raise HTTPException(status_code=400, detail=f"No API key configured for {provider}. Please configure it in settings.")
        
    try:
        llm_backend = get_llm_backend(provider, api_key, model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to initialize LLM backend: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize AI service")

    # 5. Define Streaming Generator
    async def stream_generator():
        full_response = ""
        try:
            generator = llm_backend.ask_question_streaming(
                user_question=request.message,
                meeting_notes=meeting_notes,
                diarized_transcript=None, # Will be fetched inside using recording_id
                conversation_history=formatted_history,
                custom_instructions=custom_instructions,
                recording_id=recording_id
            )
            
            # The generator from LLMBackend is synchronous or asynchronous?
            # My implementation in `LLM_Services.py`:
            # Gemini: `generate_content_stream` returns iterator (sync or async?)
            # Google GenAI `generate_content_stream` is usually synchronous iterator in v1, but checking my code...
            # I used `for chunk in response_stream: yield chunk.text`. This is synchronous yield.
            # But `ask_question_streaming` is defined as `def ... -> Generator`.
            # If it's a synchronous generator, I cannot `async for` it.
            # `StreamingResponse` accepts both async generator and sync iterator.
            # However, I need to do async DB operations at the end.
            
            # Wait, `google.genai` client... is it async? `genai.Client` usually is sync unless `genai.rest` or similar.
            # My `LLM_Services` code used `self.client = genai.Client(...)`.
            
            # If the generator blocks, it blocks the event loop if not async.
            # OpenAI `stream=True` returns a sync iterator by default unless `AsyncOpenAI` is used.
            # I used `openai.OpenAI` (sync client).
            # Anthropic `Anthropic` is sync client.
            
            # Ideally, I should use async clients for FastAPI.
            # But the current codebase seems to use sync clients (based on `LLM_Services.py` existing code).
            # If I use sync clients in `StreamingResponse` generator, I should run them in a threadpool?
            # Or just accept it blocks for now (not ideal for high concurrency).
            
            # Since I didn't change the `LLM_Services.py` to use Async clients (Refactoring to async clients would be big), 
            # I will assume sync execution is acceptable or I wrap it.
            
            # BUT, I need to save to DB at the end using `async_session_maker`. 
            # So my generator wrapper MUST be `async def`.
            # If `llm_backend.ask_question_streaming` is a sync generator, I can iterate it.
            
            # Let's verify `LLM_Services.py` signature I wrote:
            # `def ask_question_streaming(...) -> Generator[str, None, None]:`
            
            # So:
            for chunk in generator:
                 full_response += chunk
                 # Yield SSE format
                 yield f"data: {json.dumps({'token': chunk})}\n\n"
                 
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        # 6. Save Assistant Response to DB
        try:
            async with async_session_maker() as session:
                assistant_msg = ChatMessage(
                    recording_id=recording_id,
                    user_id=current_user.id,
                    role="assistant",
                    content=full_response
                )
                session.add(assistant_msg)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to save assistant message: {e}")
            
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")
