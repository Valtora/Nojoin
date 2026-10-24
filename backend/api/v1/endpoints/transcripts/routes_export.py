import logging
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services.exports import (
    MEDIA_TYPES,
    MeetingParts,
    build_document,
    ordered_sections,
    render,
)
from backend.services.exports.speaker_colors import (
    analytics_speaker_colors,
    parse_client_colors,
    resolve_label_colors,
)
from backend.services.timeline_note_service import (
    load_recording_timeline_notes_async,
)
from backend.utils.canonical_pipeline import build_transcript_segments_for_read
from backend.utils.config_manager import EXPORT_BRANDING_KEY, config_manager
from backend.utils.speaker_names.live import with_live_speaker_names
from backend.utils.timezones import get_user_timezone_name

from .helpers import _build_speaker_map, _get_owned_recording, _sanitize_filename
from .router import router
from .routes_analytics import read_recording_analytics

logger = logging.getLogger(__name__)

ExportSection = Literal["notes", "analytics", "transcript"]

# The single-choice values the export dialog sent before sections existed.
# Kept for API clients written against them.
LEGACY_CONTENT_TYPES: dict[str, tuple[ExportSection, ...]] = {
    "transcript": ("transcript",),
    "notes": ("notes",),
    "both": ("notes", "transcript"),
}

FILE_LABELS = {"notes": "Notes", "analytics": "Analytics", "transcript": "Transcript"}


class ExportQuery(BaseModel):
    # Repeat the parameter for several: ?sections=notes&sections=analytics.
    sections: list[ExportSection] = Field(default_factory=list)
    content_type: Literal["transcript", "notes", "both"] | None = None
    export_format: Literal["txt", "pdf", "docx"] = "txt"
    # JSON object of diarisation label (or speaker name) to palette key, so the
    # file uses the colours the reader sees in the app. See speaker_colors.py.
    speaker_colors: str | None = Field(default=None, max_length=16_384)
    # The reader's own notes: in the transcript section, and the ``## User
    # Notes`` section that ends the generated notes. Off for a file shared
    # without them.
    include_notes: bool = True

    def requested_sections(self) -> tuple[str, ...]:
        if self.sections:
            return ordered_sections(self.sections)
        return LEGACY_CONTENT_TYPES[self.content_type or "transcript"]


def _speaker_name(speaker: RecordingSpeaker) -> str:
    return (
        speaker.local_name
        or (speaker.global_speaker.name if speaker.global_speaker else None)
        or speaker.name
        or speaker.diarization_label
    )


def _filename(
    recording: Recording, sections: tuple[str, ...], export_format: str
) -> str:
    timestamp = (
        recording.created_at.strftime("%Y%m%d") if recording.created_at else "export"
    )
    label = FILE_LABELS[sections[0]] if len(sections) == 1 else "FullExport"
    return f"{timestamp}_{_sanitize_filename(recording.name)}_{label}.{export_format}"


async def _load_recording(db: AsyncSession, recording_id: str, user: User) -> Recording:
    recording = await _get_owned_recording(
        db,
        recording_id,
        user.id,
        options=(
            selectinload(Recording.speakers).options(
                selectinload(RecordingSpeaker.global_speaker)
            ),
        ),
    )
    # Soft-merged speakers are aliases of the survivor and never appear.
    if recording.speakers:
        recording.speakers = [s for s in recording.speakers if not s.merged_into_id]
    return recording


async def _meeting_parts(
    db: AsyncSession,
    recording: Recording,
    transcript: Transcript,
    query: ExportQuery,
    user: User,
) -> MeetingParts:
    sections = query.requested_sections()
    speakers = recording.speakers or []
    segments, speaker_names_by_label = await db.run_sync(
        lambda sync_session: (
            build_transcript_segments_for_read(sync_session, recording.id),
            with_live_speaker_names(
                sync_session, recording.id, _build_speaker_map(speakers)
            ),
        )
    )
    analytics = (
        await read_recording_analytics(db, recording, transcript)
        if "analytics" in sections
        else None
    )
    timeline_notes = (
        (await load_recording_timeline_notes_async(db, recording.id)).notes
        if query.include_notes and "transcript" in sections
        else ()
    )
    label_colors = resolve_label_colors(
        [str(segment.get("speaker") or "Unknown") for segment in segments]
        + [speaker.diarization_label for speaker in speakers],
        {speaker.diarization_label: speaker.color for speaker in speakers},
        parse_client_colors(query.speaker_colors),
    )
    return MeetingParts(
        title=recording.name,
        created_at=recording.created_at,
        timezone_name=get_user_timezone_name(user.settings),
        duration_seconds=recording.duration_seconds,
        speaker_names=tuple(_speaker_name(speaker) for speaker in speakers),
        notes=transcript.notes,
        segments=segments,
        speaker_names_by_label=speaker_names_by_label,
        speaker_colors_by_label=label_colors,
        analytics=analytics,
        analytics_colors=(
            analytics_speaker_colors(analytics["speakers"], label_colors)
            if analytics
            else {}
        ),
        timeline_notes=timeline_notes,
        include_user_notes=query.include_notes,
    )


@router.get("/{recording_id}/export")
async def export_content(
    recording_id: str,
    query: Annotated[ExportQuery, Query()],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export any of the notes, analytics and transcript as one file.

    Sections always appear in the order notes, analytics, transcript, whatever
    order they were requested in. ``content_type`` is the older single-choice
    form and is used only when ``sections`` is absent.
    """
    sections = query.requested_sections()
    logger.info(
        "Export requested: recording_id=%s sections=%s format=%s user=%s",
        recording_id,
        ",".join(sections),
        query.export_format,
        current_user.id,
    )
    recording = await _load_recording(db, recording_id, current_user)

    result = await db.execute(
        select(Transcript).where(Transcript.recording_id == recording.id)
    )
    transcript = result.scalar_one_or_none()
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if sections == ("notes",) and not (transcript.notes or "").strip():
        raise HTTPException(
            status_code=404, detail="No meeting notes available to export"
        )

    parts = await _meeting_parts(db, recording, transcript, query, current_user)
    document = build_document(
        parts,
        sections,
        branded=config_manager.get(EXPORT_BRANDING_KEY, True) is not False,
    )
    try:
        # Laying out a long transcript takes seconds; off the event loop, it
        # does not stall every other request this process is serving.
        content = await run_in_threadpool(render, document, query.export_format)
    except Exception:  # noqa: BLE001 -- boundary: any failure to render the export becomes one 500; the traceback is logged
        logger.exception("Export generation failed for recording %s", recording_id)
        raise HTTPException(status_code=500, detail="Export generation failed")

    filename = _filename(recording, sections, query.export_format)
    return Response(
        content=content,
        media_type=MEDIA_TYPES[query.export_format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
