from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.models.calendar import CalendarDashboardDayCountRead
from backend.models.chat import ChatMessage
from backend.models.document import Document, DocumentStatus
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.models.speaker import GlobalSpeakerRead, RecordingSpeaker
from backend.models.tag import Tag, TagRead
from backend.models.transcript import Transcript


class PublicModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RecordingSpeakerPublicRead(PublicModel):
    id: int
    public_id: str
    created_at: datetime
    updated_at: datetime
    recording_id: str
    diarization_label: str
    global_speaker_id: Optional[int] = None
    local_name: Optional[str] = None
    name: Optional[str] = None
    color: Optional[str] = None
    has_voiceprint: bool = False
    merged_into_id: Optional[int] = None
    speaker_status: str = "active"
    speaker_kind: str = "automated"
    first_seen_ms: Optional[int] = None
    last_seen_ms: Optional[int] = None
    identity_confidence: Optional[float] = None
    identity_locked: bool = False
    global_speaker: Optional[GlobalSpeakerRead] = None


class SpeakerNameSuggestionEvidenceRead(PublicModel):
    quote: str
    reason: str
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None


class SpeakerNameSuggestionRead(PublicModel):
    id: str
    diarization_label: str
    recording_speaker_id: Optional[int] = None
    suggested_name: str
    suggested_global_speaker_id: Optional[int] = None
    confidence: float
    status: str
    origin: str
    source: str
    provider: Optional[str] = None
    rationale: Optional[str] = None
    evidence_spans: list[SpeakerNameSuggestionEvidenceRead] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None
    resolution_reason: Optional[str] = None
    resolution_actor_user_id: Optional[int] = None


class TranscriptPublicRead(PublicModel):
    id: int
    created_at: datetime
    updated_at: datetime
    recording_id: str
    text: Optional[str] = None
    segments: list[dict[str, Any]] = Field(default_factory=list)
    notes: Optional[str] = None
    user_notes: Optional[str] = None
    meeting_edge_focus: Optional[str] = None
    meeting_edge_payload: Optional[dict[str, Any]] = None
    meeting_edge_status: str = "idle"
    meeting_edge_error_message: Optional[str] = None
    speaker_name_suggestions: list[SpeakerNameSuggestionRead] = Field(default_factory=list)
    notes_status: str = "pending"
    transcript_status: str = "pending"
    error_message: Optional[str] = None


class ChatMessagePublicRead(PublicModel):
    id: int
    created_at: datetime
    updated_at: datetime
    recording_id: str
    user_id: int
    role: str
    content: str


class DocumentPublicRead(PublicModel):
    id: int
    created_at: datetime
    updated_at: datetime
    recording_id: str
    title: str
    file_path: str
    file_type: str
    status: DocumentStatus
    error_message: Optional[str] = None


class CalendarEventLinkRead(PublicModel):
    id: int
    title: str
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class RecordingPipelineLaneStateRead(PublicModel):
    total_windows: int = 0
    processed_windows: int = 0
    processing_windows: int = 0
    failed_windows: int = 0
    pending_windows: int = 0
    coverage_ratio: float = 0.0
    status_counts: dict[str, int] = Field(default_factory=dict)


class RecordingPipelineStateRead(PublicModel):
    transcript_revision: int = 0
    total_window_count: int = 0
    sealed_window_count: int = 0
    partial_window_count: int = 0
    first_sequence: Optional[int] = None
    latest_sequence: Optional[int] = None
    asr: RecordingPipelineLaneStateRead = Field(default_factory=RecordingPipelineLaneStateRead)
    diarization: RecordingPipelineLaneStateRead = Field(default_factory=RecordingPipelineLaneStateRead)


class RecordingPublicRead(PublicModel):
    id: str
    created_at: datetime
    updated_at: datetime
    name: str
    meeting_uid: str
    audio_path: str
    has_proxy: bool = False
    duration_seconds: Optional[float] = None
    file_size_bytes: Optional[int] = None
    status: RecordingStatus
    client_status: Optional[ClientStatus] = None
    upload_progress: int = 0
    processing_progress: int = 0
    processing_step: Optional[str] = None
    processing_eta_seconds: Optional[int] = None
    processing_eta_learning: bool = False
    processing_eta_sample_size: int = 0
    is_archived: bool = False
    is_deleted: bool = False
    transcript: Optional[TranscriptPublicRead] = None
    speakers: list[RecordingSpeakerPublicRead] = Field(default_factory=list)
    tags: list[TagRead] = Field(default_factory=list)
    calendar_event: Optional[CalendarEventLinkRead] = None
    pipeline_state: Optional[RecordingPipelineStateRead] = None


class RecordingsCalendarRead(BaseModel):
    month: str
    timezone: str
    day_counts: List[CalendarDashboardDayCountRead] = Field(default_factory=list)


def serialize_recording_speaker(
    speaker: RecordingSpeaker,
    *,
    recording_public_id: str,
) -> RecordingSpeakerPublicRead:
    return RecordingSpeakerPublicRead(
        id=speaker.id,
        public_id=speaker.public_id,
        created_at=speaker.created_at,
        updated_at=speaker.updated_at,
        recording_id=recording_public_id,
        diarization_label=speaker.diarization_label,
        global_speaker_id=speaker.global_speaker_id,
        local_name=speaker.local_name,
        name=speaker.name,
        color=speaker.color,
        has_voiceprint=speaker.has_voiceprint,
        merged_into_id=speaker.merged_into_id,
        speaker_status=speaker.speaker_status,
        speaker_kind=speaker.speaker_kind,
        first_seen_ms=speaker.first_seen_ms,
        last_seen_ms=speaker.last_seen_ms,
        identity_confidence=speaker.identity_confidence,
        identity_locked=speaker.identity_locked,
        global_speaker=(
            GlobalSpeakerRead.model_validate(speaker.global_speaker)
            if speaker.global_speaker is not None
            else None
        ),
    )


def serialize_transcript(
    transcript: Transcript,
    *,
    recording_public_id: str,
    segments_override: Optional[list[dict]] = None,
    text_override: Optional[str] = None,
) -> TranscriptPublicRead:
    return TranscriptPublicRead(
        id=transcript.id,
        created_at=transcript.created_at,
        updated_at=transcript.updated_at,
        recording_id=recording_public_id,
        text=transcript.text if text_override is None else text_override,
        segments=transcript.segments if segments_override is None else segments_override,
        notes=transcript.notes,
        user_notes=transcript.user_notes,
        meeting_edge_focus=transcript.meeting_edge_focus,
        meeting_edge_payload=transcript.meeting_edge_payload,
        meeting_edge_status=transcript.meeting_edge_status,
        meeting_edge_error_message=transcript.meeting_edge_error_message,
        speaker_name_suggestions=[
            SpeakerNameSuggestionRead.model_validate(item)
            for item in (transcript.speaker_name_suggestions or [])
            if isinstance(item, dict)
        ],
        notes_status=transcript.notes_status,
        transcript_status=transcript.transcript_status,
        error_message=transcript.error_message,
    )


def serialize_chat_message(
    message: ChatMessage,
    *,
    recording_public_id: str,
) -> ChatMessagePublicRead:
    return ChatMessagePublicRead(
        id=message.id,
        created_at=message.created_at,
        updated_at=message.updated_at,
        recording_id=recording_public_id,
        user_id=message.user_id,
        role=message.role,
        content=message.content,
    )


def serialize_document(
    document: Document,
    *,
    recording_public_id: str,
) -> DocumentPublicRead:
    return DocumentPublicRead(
        id=document.id,
        created_at=document.created_at,
        updated_at=document.updated_at,
        recording_id=recording_public_id,
        title=document.title,
        file_path=document.file_path,
        file_type=document.file_type,
        status=document.status,
        error_message=document.error_message,
    )


def serialize_recording(
    recording: Recording,
    *,
    has_proxy: bool = False,
    processing_eta_seconds: Optional[int] = None,
    processing_eta_learning: bool = False,
    processing_eta_sample_size: int = 0,
    include_transcript: bool = False,
    include_speakers: bool = False,
    include_tags: bool = False,
    include_calendar_event: bool = False,
    calendar_event: Optional[CalendarEvent] = None,
    transcript_segments_override: Optional[list[dict]] = None,
    transcript_text_override: Optional[str] = None,
    speakers_override: Optional[list[RecordingSpeaker]] = None,
    pipeline_state: Optional[RecordingPipelineStateRead] = None,
) -> RecordingPublicRead:
    transcript = None
    if include_transcript and recording.transcript is not None:
        transcript = serialize_transcript(
            recording.transcript,
            recording_public_id=recording.public_id,
            segments_override=transcript_segments_override,
            text_override=transcript_text_override,
        )

    speakers: list[RecordingSpeakerPublicRead] = []
    source_speakers = (
        speakers_override
        if speakers_override is not None
        else (getattr(recording, "speakers", None) if include_speakers else None)
    )
    if include_speakers and source_speakers:
        speakers = [
            serialize_recording_speaker(
                speaker,
                recording_public_id=recording.public_id,
            )
            for speaker in source_speakers
            if not speaker.merged_into_id
        ]

    tags: list[TagRead] = []
    if include_tags and getattr(recording, "tags", None):
        for recording_tag in recording.tags:
            tag = getattr(recording_tag, "tag", recording_tag)
            if isinstance(tag, Tag):
                tags.append(TagRead.model_validate(tag))

    calendar_event_link: Optional[CalendarEventLinkRead] = None
    if include_calendar_event and calendar_event is not None:
        calendar_event_link = CalendarEventLinkRead.model_validate(calendar_event)

    return RecordingPublicRead(
        id=recording.public_id,
        created_at=recording.created_at,
        updated_at=recording.updated_at,
        name=recording.name,
        meeting_uid=recording.meeting_uid,
        audio_path=recording.audio_path,
        has_proxy=has_proxy,
        duration_seconds=recording.duration_seconds,
        file_size_bytes=recording.file_size_bytes,
        status=recording.status,
        client_status=recording.client_status,
        upload_progress=recording.upload_progress,
        processing_progress=recording.processing_progress,
        processing_step=recording.processing_step,
        processing_eta_seconds=processing_eta_seconds,
        processing_eta_learning=processing_eta_learning,
        processing_eta_sample_size=processing_eta_sample_size,
        is_archived=recording.is_archived,
        is_deleted=recording.is_deleted,
        transcript=transcript,
        speakers=speakers,
        tags=tags,
        calendar_event=calendar_event_link,
        pipeline_state=pipeline_state,
    )
