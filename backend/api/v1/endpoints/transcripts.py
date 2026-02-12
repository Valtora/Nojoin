from typing import List, Literal, Optional
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
from io import BytesIO
from datetime import datetime, timedelta
import html
import traceback

# ReportLab imports for PDF removed
# from reportlab.lib import colors
# ...

# Markdown PDF imports
from markdown_pdf import MarkdownPdf, Section

# Python-docx imports for DOCX
from docx import Document as DocxDocument
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn

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
from backend.models.context_chunk import ContextChunk
from backend.models.tag import Tag, RecordingTag
from backend.processing.text_embedding import get_text_embedding_service
from sqlalchemy import func


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
    tag_ids: Optional[List[int]] = None



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


import re2

def _apply_find_replace(transcript: Transcript, find_text: str, replace_text: str, case_sensitive: bool = False, use_regex: bool = False) -> int:
    """
    Apply find and replace to both transcript segments and notes.
    Returns the number of segment replacements made.
    """
    if len(find_text) > 1000:
        raise HTTPException(status_code=400, detail="Search pattern is too long (max 1000 characters)")

    total_segment_replacements = 0
    
    # Path 1: Simple string replacement (Case Sensitive + No Regex)
    # this is the fastest and safest path
    if not use_regex and case_sensitive:
        for segment in transcript.segments:
            if find_text in segment['text']:
                new_text = segment['text'].replace(find_text, replace_text)
                if new_text != segment['text']:
                   segment['text'] = new_text
                   total_segment_replacements += 1
                   
        if transcript.notes and find_text in transcript.notes:
             transcript.notes = transcript.notes.replace(find_text, replace_text)

    # Path 2: Regex-based replacement (Case Insensitive OR Explicit Regex)
    else:
        # Path 2: Regex-based replacement (Case Insensitive OR Explicit Regex)
        
        # google-re2 does not support flags arg in compile. We must use inline flags.
        # e.g. (?i) for ignore case.
        
        prefix = ""
        if not case_sensitive:
            prefix = "(?i)"
            
        if use_regex:
            pattern = prefix + find_text
        else:
            # Escape the text, then prepend flag
            pattern = prefix + re2.escape(find_text)
            
        try:
            regex = re2.compile(pattern)
        except re2.error:
             # Invalid regex provided by user
             raise HTTPException(status_code=400, detail="Invalid regular expression")

        # Replace in transcript segments
        for segment in transcript.segments:
            # google-re2 python bindings support subn.
            new_text, count = regex.subn(replace_text, segment['text'])
            if count > 0:
                segment['text'] = new_text
                total_segment_replacements += 1
        
        # Replace in notes
        if transcript.notes:
            transcript.notes = regex.sub(replace_text, transcript.notes)
    
    if total_segment_replacements > 0:
        flag_modified(transcript, "segments")
        # Reconstruct full text
        full_text = " ".join([s['text'] for s in transcript.segments])
        transcript.text = full_text
    
    return total_segment_replacements


def _parse_markdown_line(line: str) -> dict:
    """
    Parse a markdown line to identify type (heading, list, paragraph) and content.
    Basic parser for common notes format, handling indentation.
    """
    stripped_line = line.strip()
    if not stripped_line:
        return {"type": "empty", "content": ""}
    
    # Calculate indent level (2 spaces = 1 level)
    # We count leading spaces
    leading_spaces = len(line) - len(line.lstrip(' '))
    indent_level = leading_spaces // 2

    if stripped_line.startswith('#'):
        level = len(stripped_line.split(' ')[0])
        content = stripped_line.lstrip('#').strip()
        return {"type": "heading", "level": level, "content": content}
    
    # Handle indent levels
    if stripped_line.startswith('- ') or stripped_line.startswith('* '):
        content = stripped_line[2:].strip()
        return {"type": "list_item", "content": content, "indent": indent_level}
    
    # Simple numbered list detection (1. , 2. )
    if re.match(r'^\d+\.\s', stripped_line):
         content = re.sub(r'^\d+\.\s', '', stripped_line).strip()
         return {"type": "list_item", "content": content, "indent": indent_level}
        
    return {"type": "paragraph", "content": stripped_line}



def _generate_full_markdown(recording: Recording, transcript: Transcript, include_transcript: bool, include_notes: bool) -> str:
    """Generate a single markdown string for the entire export."""
    md_lines = []
    
    # Header
    md_lines.append(f"# {html.escape(recording.name)}")
    md_lines.append("")
    
    date_str = recording.created_at.strftime("%B %d, %Y at %I:%M %p") if recording.created_at else "Unknown Date"
    md_lines.append(f"**Date:** {date_str}")
    md_lines.append("")
    
    duration_str = str(timedelta(seconds=int(recording.duration_seconds))) if recording.duration_seconds else "Unknown"
    md_lines.append(f"**Duration:** {duration_str}")
    md_lines.append("")
    
    if recording.speakers:
        speaker_names = []
        for s in recording.speakers:
            name = s.local_name or (s.global_speaker.name if s.global_speaker else None) or s.name or s.diarization_label
            speaker_names.append(html.escape(name))
        md_lines.append(f"**Speakers:** {', '.join(speaker_names)}")
        md_lines.append("")
    
    md_lines.append("---")
    md_lines.append("")

    # Notes
    if include_notes and transcript.notes:
        md_lines.append("## Meeting Notes")
        md_lines.append("")
        md_lines.append(transcript.notes)
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")
        
    # Transcript
    if include_transcript and transcript.segments:
        md_lines.append("## Transcript")
        md_lines.append("")
        speaker_map = _build_speaker_map(recording.speakers)
        
        for seg in transcript.segments:
            speaker_label = seg.get('speaker', 'Unknown')
            speaker_name = speaker_map.get(speaker_label, speaker_label)
            text = seg.get('text', '').strip()
            
            start = seg.get('start', 0)
            minutes = int(start // 60)
            seconds = int(start % 60)
            time_str = f"[{minutes:02d}:{seconds:02d}]"
            
            # Use bold for speaker to make it visually distinct
            md_lines.append(f"**{time_str} {html.escape(speaker_name)}**")
            md_lines.append(f"{html.escape(text)}")
            md_lines.append("")

    return "\n".join(md_lines)

def _generate_pdf_export(recording: Recording, transcript: Transcript, include_transcript: bool, include_notes: bool) -> bytes:
    """Generate a PDF export using markdown-pdf."""
    markdown_content = _generate_full_markdown(recording, transcript, include_transcript, include_notes)
    
    pdf = MarkdownPdf(toc_level=2)
    css = "body { font-family: Helvetica, sans-serif; }"
    pdf.add_section(Section(markdown_content), user_css=css)
    
    # Save to buffer
    buffer = BytesIO()
    # Use a temporary file since the library requires a file path string for saving.
    
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
        
    try:
        pdf.save(tmp_path)
        with open(tmp_path, "rb") as f:
            pdf_bytes = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
            
    return pdf_bytes


def _generate_docx_export(recording: Recording, transcript: Transcript, include_transcript: bool, include_notes: bool) -> bytes:
    """Generate a DOCX export."""
    buffer = BytesIO()
    doc = DocxDocument()
    
    # --- Header ---
    doc.add_heading(recording.name, 0)
    
    # Metadata
    p = doc.add_paragraph()
    date_str = recording.created_at.strftime("%B %d, %Y at %I:%M %p") if recording.created_at else "Unknown Date"
    runner = p.add_run(f"Date: {date_str}")
    
    p = doc.add_paragraph()
    duration_str = str(timedelta(seconds=int(recording.duration_seconds))) if recording.duration_seconds else "Unknown"
    p.add_run(f"Duration: {duration_str}")
    
    if recording.speakers:
        speaker_names = []
        for s in recording.speakers:
            name = s.local_name or (s.global_speaker.name if s.global_speaker else None) or s.name or s.diarization_label
            speaker_names.append(name)
        p = doc.add_paragraph()
        p.add_run(f"Speakers: {', '.join(speaker_names)}")
    
    doc.add_paragraph() # Spacer

    # --- Notes ---
    if include_notes and transcript.notes:
        doc.add_heading("Meeting Notes", level=1)
        
        for line in transcript.notes.split('\n'):
            parsed = _parse_markdown_line(line)
            content = parsed['content']
            
            # Basic bold handling for DOCX
            def add_formatted_run(paragraph, text):
                parts = re.split(r'(\*\*.*?\*\*)', text)
                for part in parts:
                    if part.startswith('**') and part.endswith('**'):
                        run = paragraph.add_run(part[2:-2])
                        run.bold = True
                    else:
                        paragraph.add_run(part)

            if parsed['type'] == 'heading':
                doc.add_heading(content, level=min(parsed['level'] + 1, 9))
            elif parsed['type'] == 'list_item':
                # Map indent level to docx styles
                # 'List Bullet', 'List Bullet 2', 'List Bullet 3'
                indent = parsed.get('indent', 0)
                if indent == 0:
                    style = 'List Bullet'
                else:
                    style = f'List Bullet {min(indent + 1, 3)}'
                
                try:
                    p = doc.add_paragraph(style=style)
                except KeyError:
                    # Fallback if style doesn't exist
                    p = doc.add_paragraph(style='List Bullet')
                    # Could modify p.paragraph_format.left_indent manually if needed
                
                add_formatted_run(p, content)
            elif parsed['type'] == 'paragraph':
                p = doc.add_paragraph()
                add_formatted_run(p, content)

        if include_transcript:
            doc.add_page_break()

    # --- Transcript ---
    if include_transcript and transcript.segments:
        doc.add_heading("Transcript", level=1)
        
        speaker_map = _build_speaker_map(recording.speakers)
        
        for seg in transcript.segments:
            speaker_label = seg.get('speaker', 'Unknown')
            speaker_name = speaker_map.get(speaker_label, speaker_label)
            text = seg.get('text', '').strip()
            
            start = seg.get('start', 0)
            minutes = int(start // 60)
            seconds = int(start % 60)
            time_str = f"[{minutes:02d}:{seconds:02d}]"
            
            p = doc.add_paragraph()
            # Speaker and Timestamp bold
            run = p.add_run(f"{time_str} {speaker_name}: ")
            run.bold = True
            p.add_run(text)

    doc.save(buffer)
    return buffer.getvalue()



# --- Export Endpoint ---

@router.get("/{recording_id}/export")
async def export_content(
    recording_id: int,
    content_type: Literal["transcript", "notes", "both"] = Query(default="transcript"),
    export_format: Literal["txt", "pdf", "docx"] = Query(default="txt"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Export the transcript and/or notes as a file.
    content_type: 'transcript', 'notes', or 'both'
    export_format: 'txt', 'pdf', 'docx'
    """
    logger.info(f"Received export request: recording_id={recording_id}, content_type={content_type}, format={export_format}, user={current_user.id}")
    
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

    # 3. Handle Formats
    include_transcript = content_type in ["transcript", "both"]
    include_notes = content_type in ["notes", "both"]
    
    if include_notes and not transcript.notes:
         if content_type == "notes":
             raise HTTPException(status_code=404, detail="No meeting notes available to export")
    
    # Generate Filename
    timestamp = recording.created_at.strftime("%Y%m%d") if recording.created_at else "export"
    if content_type == "transcript":
        ftype = "Transcript"
    elif content_type == "notes":
        ftype = "Notes"
    else:
        ftype = "FullExport"
    
    filename = f"{timestamp}_{_sanitize_filename(recording.name)}_{ftype}.{export_format}"
    
    # 4. Generate Content
    try:
        if export_format == "pdf":
            content = _generate_pdf_export(recording, transcript, include_transcript, include_notes)
            media_type = "application/pdf"
        elif export_format == "docx":
            content = _generate_docx_export(recording, transcript, include_transcript, include_notes)
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else: # txt
            # Text Generation Logic
            speaker_map = _build_speaker_map(recording.speakers)
            sections = []
            
            # Add Header even for TXT
            sections.append(recording.name)
            if recording.created_at:
                 sections.append(f"Date: {recording.created_at.strftime('%B %d, %Y at %I:%M %p')}")
            if recording.duration_seconds:
                 sections.append(f"Duration: {str(timedelta(seconds=int(recording.duration_seconds)))}")
            if recording.speakers:
                s_names = [s.local_name or (s.global_speaker.name if s.global_speaker else None) or s.name or s.diarization_label for s in recording.speakers]
                sections.append(f"Speakers: {', '.join(s_names)}")
            sections.append("=" * 50)
            sections.append("")
    
            if include_transcript:
                sections.append("Transcript")
                sections.append("-" * 20)
                sections.append(_format_transcript_text(transcript.segments, speaker_map))
                sections.append("")
            
            if include_notes and transcript.notes:
                sections.append("Meeting Notes")
                sections.append("-" * 20)
                sections.append(transcript.notes)
                sections.append("")
                
            content = "\n".join(sections)
            media_type = "text/plain"
    except Exception as e:
        logger.error(f"Export generation failed: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Export generation failed")

    return Response(
        content=content,
        media_type=media_type,
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
        stmt = select(GlobalSpeaker).where(GlobalSpeaker.name == new_speaker_name, GlobalSpeaker.user_id == current_user.id)
        result = await db.execute(stmt)
        global_speaker = result.scalar_one_or_none()
        
        if not global_speaker:
            # Create new Global Speaker
            global_speaker = GlobalSpeaker(name=new_speaker_name, user_id=current_user.id)
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
    # Google Gemini: {"role": "user"|"model", "parts": [{"text": ...}]}
    # OpenAI/Anthropic: Adapted by backend logic, generally checks for standard roles.
    # Standardizing on Gemini format for internal consistency before backend adaptation.
    
    formatted_history = []
    for msg in db_messages:
        role = "user" if msg.role == "user" else "model" # Gemini uses 'model' instead of 'assistant'
        formatted_history.append({
            "role": role,
            "parts": [{"text": msg.content}]
        })
    
    user_msg = ChatMessage(
        recording_id=recording_id,
        user_id=current_user.id,
        role="user",
        content=request.message
    )
    db.add(user_msg)
    await db.commit()
    
    # --- RAG Context Retrieval ---
    context_text = ""
    relevant_chunks = []
    
    # --- RAG Context Retrieval ---
    context_text = ""
    relevant_chunks = []
    
    # Always attempt RAG, at least for the current recording
    try:
        # 1. Get embedding for the user query
        embedding_service = get_text_embedding_service()
        query_embedding = embedding_service.embed(request.message)[0] # Returns list of lists
        
        # 2. Build Query Condition
        if request.tag_ids:
            # Identify relevant recordings from tags
            subquery = select(RecordingTag.recording_id).where(RecordingTag.tag_id.in_(request.tag_ids))
            condition = (ContextChunk.recording_id.in_(subquery)) | (ContextChunk.recording_id == recording_id)
        else:
            # Only search current recording
            condition = (ContextChunk.recording_id == recording_id)
        
        # 3. Vector Search
        stmt = select(ContextChunk).where(
            condition
        ).order_by(
            ContextChunk.embedding.cosine_distance(query_embedding)
        ).limit(5)
            
        result = await db.execute(stmt)
        relevant_chunks = result.scalars().all()
        
        if relevant_chunks:
            context_sections = []
            for chunk in relevant_chunks:
                # Fetch recording with speakers for name resolution
                stmt = select(Recording).where(Recording.id == chunk.recording_id).options(
                    selectinload(Recording.speakers).options(selectinload(RecordingSpeaker.global_speaker))
                )
                rec_result = await db.execute(stmt)
                rec = rec_result.scalar_one_or_none()
                
                rec_name = rec.name if rec else f"Recording {chunk.recording_id}"
                
                content = chunk.content
                # If it's a transcript chunk with speaker info, resolve speaker names
                if chunk.meta and chunk.meta.get("source") == "transcript" and rec:
                    speaker_map = _build_speaker_map(rec.speakers)
                    # Replace raw labels with names
                    # Iterate to replace. Since labels are usually distinct (SPEAKER_00), simple replace is okay-ish
                    # but safer to do strict match if possible.
                    # Given the format "SPEAKER_XX: ", we can just replace that.
                    for label, name in speaker_map.items():
                        if label and name and label != name:
                            content = content.replace(f"{label}:", f"{name}:")
                    
                context_sections.append(f"--- From {rec_name} ---\n{content}")
            
            context_text = "\n\n".join(context_sections)
            logger.info(f"Retrieved {len(relevant_chunks)} context chunks for chat.")
                
    except Exception as e:
            logger.error(f"RAG Retrieval failed: {e}")
            # Continue without context rather than failing
            
    # Prepend Context to the last message or system prompt
    # Since we are using formatted_history, we can inject a system message or augment the last user message.
    # Augmenting last user message is usually effective.
    
    if context_text:
        augmented_message = f"Context from related meetings/documents:\n{context_text}\n\nUser Question: {request.message}"
        
        formatted_history.append({
            "role": "user",
            "parts": [{"text": augmented_message}]
        })
    else:
        formatted_history.append({
            "role": "user",
            "parts": [{"text": request.message}]
        })


    user_settings = current_user.settings or {}
    provider = user_settings.get("llm_provider") or config_manager.get("llm_provider") or "gemini"
    api_key = user_settings.get(f"{provider}_api_key")
    model = user_settings.get(f"{provider}_model")
    
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
                recording_id=recording_id
            )
            
            # Iterate over the generator response
            # Note: This generator might be synchronous (blocking) or asynchronous depending on the LLM client.
            # Ideally should be fully async, but current implementation assumes sync iterator that we wrap in StreamingResponse.
            for chunk in generator:
                if isinstance(chunk, dict) and chunk.get("type") == "notes_update":
                    yield f"event: notes_update\ndata: {json.dumps({'status': 'success'})}\n\n"
                else:
                    full_response += str(chunk)
                    # Yield SSE format
                    yield f"data: {json.dumps({'token': str(chunk)})}\n\n"
                 
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"data: {json.dumps({'error': 'An internal error occurred'})}\n\n"
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
