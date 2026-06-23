import logging
import traceback
from datetime import timedelta
from typing import Literal

from fastapi import Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.canonical_pipeline import build_transcript_segments_for_read

from .helpers import (
    _build_speaker_map,
    _format_transcript_text,
    _generate_docx_export,
    _generate_pdf_export,
    _get_owned_recording,
    _sanitize_filename,
)
from .router import router

logger = logging.getLogger(__name__)


@router.get("/{recording_id}/export")
async def export_content(
    recording_id: str,
    content_type: Literal["transcript", "notes", "both"] = Query(default="transcript"),
    export_format: Literal["txt", "pdf", "docx"] = Query(default="txt"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Export the transcript and/or notes as a file.
    content_type: 'transcript', 'notes', or 'both'
    export_format: 'txt', 'pdf', 'docx'
    """
    logger.info(
        f"Received export request: recording_id={recording_id}, content_type={content_type}, format={export_format}, user={current_user.id}"
    )

    # 1. Fetch Recording with Speakers (for name resolution)
    recording = await _get_owned_recording(
        db,
        recording_id,
        current_user.id,
        options=(
            selectinload(Recording.speakers).options(
                selectinload(RecordingSpeaker.global_speaker)
            ),
        ),
    )

    # Exclude soft-merged speakers from export output
    if recording.speakers:
        recording.speakers = [s for s in recording.speakers if not s.merged_into_id]

    # 2. Fetch Transcript
    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    segments = await db.run_sync(
        lambda sync_session: build_transcript_segments_for_read(
            sync_session, recording.id
        )
    )
    effective_duration = recording.duration_seconds

    # 3. Handle Formats
    include_transcript = content_type in ["transcript", "both"]
    include_notes = content_type in ["notes", "both"]

    if include_notes and not transcript.notes:
        if content_type == "notes":
            raise HTTPException(
                status_code=404, detail="No meeting notes available to export"
            )

    # Generate Filename
    timestamp = (
        recording.created_at.strftime("%Y%m%d") if recording.created_at else "export"
    )
    if content_type == "transcript":
        ftype = "Transcript"
    elif content_type == "notes":
        ftype = "Notes"
    else:
        ftype = "FullExport"

    filename = (
        f"{timestamp}_{_sanitize_filename(recording.name)}_{ftype}.{export_format}"
    )

    # 4. Generate Content
    try:
        if export_format == "pdf":
            content = _generate_pdf_export(
                recording,
                transcript,
                include_transcript,
                include_notes,
                segments=segments,
                effective_duration=effective_duration,
            )
            media_type = "application/pdf"
        elif export_format == "docx":
            content = _generate_docx_export(
                recording,
                transcript,
                include_transcript,
                include_notes,
                segments=segments,
                effective_duration=effective_duration,
            )
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:  # txt
            # Text Generation Logic
            speaker_map = _build_speaker_map(recording.speakers)
            sections = []

            # Add Header even for TXT
            sections.append(recording.name)
            if recording.created_at:
                sections.append(
                    f"Date: {recording.created_at.strftime('%B %d, %Y at %I:%M %p')}"
                )
            if effective_duration:
                sections.append(
                    f"Duration: {str(timedelta(seconds=int(effective_duration)))}"
                )
            if recording.speakers:
                s_names = [
                    s.local_name
                    or (s.global_speaker.name if s.global_speaker else None)
                    or s.name
                    or s.diarization_label
                    for s in recording.speakers
                ]
                sections.append(f"Speakers: {', '.join(s_names)}")
            sections.append("=" * 50)
            sections.append("")

            if include_transcript:
                sections.append("Transcript")
                sections.append("-" * 20)
                sections.append(_format_transcript_text(segments, speaker_map))
                sections.append("")

            if include_notes and transcript.notes:
                sections.append("Meeting Notes")
                sections.append("-" * 20)
                sections.append(transcript.notes)
                sections.append("")

            content = "\n".join(sections)
            media_type = "text/plain"
    except Exception as e:  # noqa: BLE001
        logger.error(f"Export generation failed: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Export generation failed")

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
