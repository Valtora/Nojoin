import os
import logging
from datetime import datetime, timezone, UTC, timedelta
from typing import List, Optional, Any
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select, or_, col
import aiofiles

from backend.api.deps import get_db, get_current_user, get_current_user_stream
from backend.models.user import User
from backend.models.recording import Recording, RecordingStatus
from backend.models.recording_public import RecordingPublicRead, RecordingsCalendarRead, CalendarEventLinkRead, serialize_recording
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker
from backend.models.tag import RecordingTag, Tag
from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource, CalendarDashboardDayCountRead
from backend.services.calendar_link_service import CANDIDATE_WINDOW_PADDING, score_event_match
from backend.utils.timezones import get_timezone, get_user_timezone_name, utc_naive_to_timezone
from backend.utils.time import utc_now
from backend.utils.processing_eta import estimate_processing_eta
from backend.utils.canonical_pipeline import (
    build_transcript_segments_for_read,
    build_transcript_text_for_read,
)
import backend.api.v1.endpoints.recordings as recordings_module

from .router import router
from .helpers import (
    _get_owned_recording,
    _recording_has_proxy,
    _should_hide_in_flight_transcript_content,
)

logger = logging.getLogger(__name__)


@router.get("", response_model=List[RecordingPublicRead])
async def list_recordings_root(
    skip: int = 0,
    limit: int = 100,
    q: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    speaker_ids: Optional[List[int]] = Query(None),
    tag_ids: Optional[List[int]] = Query(None),
    include_archived: bool = Query(False, description="Include archived recordings"),
    include_deleted: bool = Query(False, description="Include deleted recordings"),
    only_archived: bool = Query(False, description="Only show archived recordings"),
    only_deleted: bool = Query(False, description="Only show deleted recordings"),
    status_filters: Optional[List[RecordingStatus]] = Query(None, alias="status"),
    user_filter: Optional[str] = Query(None, alias="user"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all recordings (root path).
    """
    return await list_recordings(
        skip=skip, limit=limit, q=q, start_date=start_date, end_date=end_date,
        speaker_ids=speaker_ids, tag_ids=tag_ids, include_archived=include_archived,
        include_deleted=include_deleted, only_archived=only_archived, only_deleted=only_deleted,
        status_filters=status_filters, user_filter=user_filter,
        db=db, current_user=current_user
    )


@router.get("/", response_model=List[RecordingPublicRead])
async def list_recordings(
    skip: int = 0,
    limit: int = 100,
    q: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    speaker_ids: Optional[List[int]] = Query(None),
    tag_ids: Optional[List[int]] = Query(None),
    include_archived: bool = Query(False, description="Include archived recordings"),
    include_deleted: bool = Query(False, description="Include deleted recordings"),
    only_archived: bool = Query(False, description="Only show archived recordings"),
    only_deleted: bool = Query(False, description="Only show deleted recordings"),
    status_filters: Optional[List[RecordingStatus]] = Query(None, alias="status"),
    user_filter: Optional[str] = Query(None, alias="user"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all recordings with optional search and filtering.
    By default, excludes archived and deleted recordings.
    """
    if user_filter is not None and user_filter != "me":
        raise HTTPException(status_code=400, detail="Only user=me is supported")

    query = select(Recording).where(Recording.user_id == current_user.id).distinct()

    if status_filters:
        query = query.where(Recording.status.in_(list(status_filters)))
    
    # Archive/Delete filtering
    if only_deleted:
        query = query.where(Recording.is_deleted == True)
    elif only_archived:
        query = query.where(Recording.is_archived == True, Recording.is_deleted == False)
    else:
        if not include_deleted:
            query = query.where(Recording.is_deleted == False)
        if not include_archived:
            query = query.where(Recording.is_archived == False)
    
    # Joins for filtering and searching
    if q or speaker_ids or tag_ids:
        query = query.join(Transcript, isouter=True)
        query = query.join(RecordingSpeaker, isouter=True)
        query = query.join(RecordingTag, isouter=True).join(Tag, isouter=True)

    # 1. Text Search (OR condition across fields)
    if q:
        search_filter = or_(
            col(Recording.name).ilike(f"%{q}%"),
            col(Transcript.text).ilike(f"%{q}%"),
            col(RecordingSpeaker.name).ilike(f"%{q}%"),
            col(Tag.name).ilike(f"%{q}%")
        )
        query = query.where(search_filter)

    # 2. Filters (AND conditions)
    if start_date:
        if start_date.tzinfo is not None:
            start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)
        query = query.where(Recording.created_at >= start_date)
    if end_date:
        if end_date.tzinfo is not None:
            end_date = end_date.astimezone(timezone.utc).replace(tzinfo=None)
        query = query.where(Recording.created_at <= end_date)

    if speaker_ids:
        query = query.where(RecordingSpeaker.global_speaker_id.in_(speaker_ids))

    if tag_ids:
        query = query.where(Tag.id.in_(tag_ids))

    # Order and pagination
    query = query.order_by(Recording.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    recordings = result.scalars().all()
    return [
        serialize_recording(recording, has_proxy=_recording_has_proxy(recording))
        for recording in recordings
    ]


@router.get(
    "/{recording_id}/calendar-event/candidates",
    response_model=List[CalendarEventLinkRead],
)
async def get_recording_calendar_event_candidates(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return scored, owner-scoped calendar events near the recording window.

    Timed events on the user's selected calendars only, best score first.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    if not recording.duration_seconds or recording.duration_seconds <= 0:
        return []
    if recording.created_at is None:
        return []

    window_start = recording.created_at
    window_end = window_start + timedelta(seconds=recording.duration_seconds)

    statement = (
        select(CalendarEvent)
        .join(CalendarSource, CalendarEvent.calendar_id == CalendarSource.id)
        .join(CalendarConnection, CalendarSource.connection_id == CalendarConnection.id)
        .where(
            CalendarConnection.user_id == current_user.id,
            CalendarSource.is_selected.is_(True),
            CalendarEvent.is_all_day.is_(False),
            CalendarEvent.starts_at.is_not(None),
            CalendarEvent.ends_at.is_not(None),
            CalendarEvent.starts_at < window_end + CANDIDATE_WINDOW_PADDING,
            CalendarEvent.ends_at > window_start - CANDIDATE_WINDOW_PADDING,
        )
    )
    events = list((await db.execute(statement)).scalars().all())
    scored = sorted(
        (
            (event, score_event_match(window_start, window_end, event.starts_at, event.ends_at))
            for event in events
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return [CalendarEventLinkRead.model_validate(event) for event, _score in scored]


@router.get("/calendar", response_model=RecordingsCalendarRead)
async def get_recordings_calendar(
    month: str,
    timezone: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Per-day recording counts for a given month.
    """
    try:
        viewed_month = datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Month must use YYYY-MM format",
        ) from exc

    effective_timezone = get_user_timezone_name(
        current_user.settings or {},
        fallback=timezone,
    )
    tz = get_timezone(effective_timezone)

    month_start_local = datetime(viewed_month.year, viewed_month.month, 1, tzinfo=tz)
    if viewed_month.month == 12:
        month_end_local = datetime(viewed_month.year + 1, 1, 1, tzinfo=tz)
    else:
        month_end_local = datetime(viewed_month.year, viewed_month.month + 1, 1, tzinfo=tz)

    month_start = month_start_local.astimezone(UTC).replace(tzinfo=None)
    month_end = month_end_local.astimezone(UTC).replace(tzinfo=None)

    query = select(Recording.created_at).where(
        Recording.user_id == current_user.id,
        Recording.is_deleted == False,
        Recording.is_archived == False,
        Recording.created_at >= month_start,
        Recording.created_at < month_end,
    )
    result = await db.execute(query)
    created_at_values = result.scalars().all()

    day_counts: dict = {}
    for created_at in created_at_values:
        local_date = utc_naive_to_timezone(created_at, effective_timezone).date()
        day_counts[local_date] = day_counts.get(local_date, 0) + 1

    return RecordingsCalendarRead(
        month=month,
        timezone=effective_timezone,
        day_counts=[
            CalendarDashboardDayCountRead(date=day, count=count)
            for day, count in sorted(day_counts.items())
        ],
    )


@router.get("/{recording_id}", response_model=RecordingPublicRead)
async def get_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific recording by ID with all relationships loaded.
    """
    statement = (
        select(Recording)
        .where(Recording.public_id == recording_id)
        .where(Recording.user_id == current_user.id)
        .options(
            selectinload(Recording.transcript),
            selectinload(Recording.speakers).options(
                selectinload(RecordingSpeaker.global_speaker)
            ),
            selectinload(Recording.tags).selectinload(RecordingTag.tag)
        )
    )
    result = await db.execute(statement)
    recording = result.scalar_one_or_none()
    
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
        
    processing_eta_seconds = None
    processing_eta_learning = False
    processing_eta_sample_size = 0

    if (
        recording.status == RecordingStatus.PROCESSING
        and recording.processing_started_at is not None
        and recording.processing_completed_at is None
    ):
        eta_statement = (
            select(
                Recording.processing_started_at,
                Recording.processing_completed_at,
                Recording.duration_seconds,
            )
            .where(Recording.id != recording.id)
            .where(Recording.status == RecordingStatus.PROCESSED)
            .where(Recording.processing_started_at.is_not(None))
            .where(Recording.processing_completed_at.is_not(None))
            .where(Recording.duration_seconds.is_not(None))
        )
        eta_result = await db.execute(eta_statement)
        history_samples = [
            (started_at, completed_at, duration_seconds)
            for started_at, completed_at, duration_seconds in eta_result.all()
        ]
        eta_estimate = estimate_processing_eta(
            history_samples,
            recording.duration_seconds,
            recording.processing_started_at,
            now=utc_now(),
        )
        processing_eta_seconds = eta_estimate.eta_seconds
        processing_eta_learning = eta_estimate.learning
        processing_eta_sample_size = eta_estimate.sample_size

    linked_event: CalendarEvent | None = None
    if recording.calendar_event_id is not None:
        linked_event = await db.get(CalendarEvent, recording.calendar_event_id)

    transcript_segments_override: list[dict] | None = None
    transcript_text_override: str | None = None
    speakers_override = None
    if recording.transcript is not None:
        transcript_segments_override = await db.run_sync(
            lambda sync_session: build_transcript_segments_for_read(sync_session, recording.id)
        )
        transcript_text_override = await db.run_sync(
            lambda sync_session: build_transcript_text_for_read(
                sync_session,
                recording.id,
                segments=transcript_segments_override,
            )
        )
        speakers_override = await db.run_sync(
            lambda sync_session: recordings_module.filter_recording_speakers_for_public_read(
                sync_session,
                recording.id,
                recording.speakers,
            )
        )

        if _should_hide_in_flight_transcript_content(recording):
            transcript_segments_override = []
            transcript_text_override = ""

    return serialize_recording(
        recording,
        has_proxy=_recording_has_proxy(recording),
        processing_eta_seconds=processing_eta_seconds,
        processing_eta_learning=processing_eta_learning,
        processing_eta_sample_size=processing_eta_sample_size,
        include_transcript=True,
        include_speakers=True,
        include_tags=True,
        include_calendar_event=True,
        calendar_event=linked_event,
        transcript_segments_override=transcript_segments_override,
        transcript_text_override=transcript_text_override,
        speakers_override=speakers_override,
    )


@router.get("/{recording_id}/info")
async def get_recording_info(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get detailed technical info about the recording audio file.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    from backend.processing.audio_preprocessing import analyze_audio_file
    
    info = {
        "original": None,
        "proxy": None
    }
    
    if recording.audio_path and os.path.exists(recording.audio_path):
        info["original"] = analyze_audio_file(recording.audio_path)
        
    if recording.proxy_path and os.path.exists(recording.proxy_path):
        info["proxy"] = analyze_audio_file(recording.proxy_path)
        
    return info


@router.get("/{recording_id}/stream")
async def stream_recording(
    recording_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_stream)
):
    """
    Stream the audio file for a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
        
    if not recording.proxy_path or not os.path.exists(recording.proxy_path):
        raise HTTPException(
            status_code=202,
            detail="Audio proxy is being prepared. Please try again shortly."
        )

    file_path = recording.proxy_path
    media_type = "audio/mpeg"
        
    file_size = os.path.getsize(file_path)
    CHUNK_SIZE = 2500 * 1024 
    
    is_range_request = False
    start = 0
    end = min(file_size - 1, CHUNK_SIZE - 1)
    
    range_header = request.headers.get("range")
    if range_header:
        is_range_request = True
        try:
            range_str = range_header.replace("bytes=", "")
            range_parts = range_str.split("-")
            
            if range_parts[0] == "":
                suffix_length = int(range_parts[1])
                start = max(0, file_size - suffix_length)
                end = file_size - 1
            else:
                start = int(range_parts[0])
                if len(range_parts) > 1 and range_parts[1]:
                    requested_end = int(range_parts[1])
                    end = min(requested_end, file_size - 1)
                else:
                    end = file_size - 1
        except ValueError:
            pass
            
    chunk_end = min(end, start + CHUNK_SIZE - 1)
    end = chunk_end

    if start >= file_size:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    if end >= file_size:
        end = file_size - 1
        
    content_length = end - start + 1
    
    async def iterfile():
        async with aiofiles.open(file_path, "rb") as f:
            await f.seek(start)
            bytes_to_read = content_length
            while bytes_to_read > 0:
                chunk_size = min(1024 * 64, bytes_to_read)
                data = await f.read(chunk_size)
                if not data:
                    break
                yield data
                bytes_to_read -= len(data)
                
    cache_control = "private, max-age=3600"
    use_partial = is_range_request or content_length < file_size

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Cache-Control": cache_control,
    }

    if use_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    return StreamingResponse(
        iterfile(),
        status_code=206 if use_partial else 200,
        headers=headers,
        media_type=media_type
    )
