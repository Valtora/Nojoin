from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.models.calendar import CalendarDashboardDayCountRead, CalendarEvent
from backend.models.chat import ChatMessage
from backend.models.document import (
    Document,
    DocumentParseMode,
    DocumentStatus,
    parse_looks_stalled,
)
from backend.models.live_settling_public import public_transcript_segment
from backend.models.recording import (
    ClientStatus,
    Recording,
    RecordingStatus,
    capture_looks_stalled,
    import_looks_stalled,
)
from backend.models.speaker import GlobalSpeakerRead, RecordingSpeaker
from backend.models.tag import Tag, TagRead
from backend.models.transcript import Transcript
from backend.processing.embedding import (
    LIBRARY_VOICEPRINT_MIN_SPEECH_S,
    LibraryVoiceprintUpdate,
    VoiceprintStatus,
)
from backend.utils.language_notice import language_notice
from backend.utils.reprocess_publication import (
    reprocess_publication_complete,
    reprocess_publication_pending,
)
from backend.utils.speaker_names.sources import name_is_pending


class PublicModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LiveSpeakerSuggestionsRead(BaseModel):
    """Live names and proposals after dismissing a live suggestion."""

    live_speaker_names: dict[str, str]
    live_speaker_suggestions: dict[str, str]


class LibraryVoiceprintRead(PublicModel):
    """What linking a recording speaker did to the person's library voiceprint."""

    status: VoiceprintStatus
    # Seconds of selected crop speech in the recording's voiceprint; None when
    # it predates measurement.
    speech_seconds: Optional[float] = None
    # The least speech a person's first voiceprint may be created from.
    required_speech_seconds: float = LIBRARY_VOICEPRINT_MIN_SPEECH_S

    @classmethod
    def from_update(cls, update: LibraryVoiceprintUpdate) -> LibraryVoiceprintRead:
        return cls(status=update.status, speech_seconds=update.speech_seconds)


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
    # "api" for the web app, "mcp" for a connected assistant, None where the
    # name came from the pipeline or predates provenance being recorded.
    name_last_edit_source: Optional[str] = None
    # The source that gave the name: heuristic, llm, voiceprint, auto (confirmed
    # by the setting) or user. None on "Speaker N" and on older rows.
    name_source: Optional[str] = None
    # A name shown as "Alex?" with Confirm and Dismiss; strong voice matches
    # aren't pending (backend/utils/speaker_names/sources.py, name_is_pending).
    name_pending: bool = False
    global_speaker: Optional[GlobalSpeakerRead] = None
    # Set only on the rename and promote responses, and only when the speaker's
    # voiceprint was offered to a person's library voiceprint.
    library_voiceprint: Optional[LibraryVoiceprintRead] = None


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
    evidence_spans: list[SpeakerNameSuggestionEvidenceRead] = Field(
        default_factory=list
    )
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
    change_sequence: int = 0
    settling_required: bool = Field(
        default=False, exclude_if=lambda value: value is False
    )
    text: Optional[str] = None
    segments: list[dict[str, Any]] = Field(default_factory=list)
    notes: Optional[str] = None
    meeting_edge_focus: Optional[str] = None
    meeting_edge_payload: Optional[dict[str, Any]] = None
    meeting_edge_status: str = "idle"
    meeting_edge_error_message: Optional[str] = None
    speaker_name_suggestions: list[SpeakerNameSuggestionRead] = Field(
        default_factory=list
    )
    notes_status: str = "pending"
    # True when a document finished parsing after these notes were written,
    # or a note's file was switched since, so they no longer reflect what
    # notes generation would read.
    notes_stale_documents: bool = False
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
    # NULL for documents uploaded before the column existed; the UI omits the
    # size rather than stat()-ing a file that may since have changed.
    file_size_bytes: Optional[int] = None
    status: DocumentStatus
    error_message: Optional[str] = None
    # Parse state. `parse_warning` is non-fatal: the document is READY and
    # searchable, but was parsed without visual analysis and says why.
    parse_mode: DocumentParseMode = DocumentParseMode.VISUAL
    parse_warning: Optional[str] = None
    page_count: Optional[int] = None
    pages_parsed: int = 0
    # Which phase a running parse is in, for display. None when not parsing.
    parse_stage: Optional[str] = None
    # Computed server-side. The client must not derive this from updated_at:
    # that timestamp carries no timezone, so a browser outside UTC misreads its
    # age by the whole offset and shows every running parse as stalled.
    is_stalled: bool = False


class CalendarEventLinkRead(PublicModel):
    id: int
    title: str
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class LanguageNoticeRead(PublicModel):
    """Language detection heard another language than the transcript used (#671)."""

    kind: Literal["mismatch"] = "mismatch"
    detected: str
    detected_label: str
    used: str
    used_label: str
    engine: str
    # The Whisper model the transcript used, or None when the record names none.
    model: Optional[str] = None
    # What "Reprocess in <detected>" sets: the detected code when it is listed,
    # otherwise "auto", on which Whisper detects it again.
    reprocess_language: str


class RecordingPublicRead(PublicModel):
    id: str
    created_at: datetime
    updated_at: datetime
    name: str
    meeting_uid: str
    audio_path: str
    has_proxy: bool = False
    # False when neither the recording's audio nor its playback proxy is on disk
    # (a recording restored from a backup taken without audio, for one), so the
    # interface can say so instead of waiting for a proxy that never comes.
    # None when the endpoint did not check: only the reads that load a recording
    # for display (list and detail) look at the disk.
    has_audio: Optional[bool] = None
    duration_seconds: Optional[float] = None
    file_size_bytes: Optional[int] = None
    status: RecordingStatus
    client_status: Optional[ClientStatus] = None
    upload_progress: int = 0
    processing_progress: int = 0
    previous_publication_available: bool = False
    reprocess_publication_complete: bool = False
    processing_step: Optional[str] = None
    processing_eta_seconds: Optional[int] = None
    processing_eta_learning: bool = False
    processing_eta_sample_size: int = 0
    max_speakers: Optional[int] = None
    # "auto" or a listed code; None means the owner's setting (#671).
    transcription_language: Optional[str] = None
    # How much audio the server actually holds, summed from the transcoded
    # segments. The live view compares this against its own wall clock to tell
    # whether capture is keeping up; it cannot derive the figure itself, because
    # a segment carries slightly more than the nominal timeslice and assuming
    # otherwise under-counts by around 10% over an hour.
    #
    # Runs about 2-3% high, because each segment's decoded WAV carries codec
    # priming that concatenation later trims. That bias is deliberate rather
    # than corrected for: it can only ever make a shortfall look smaller, so it
    # cannot manufacture a warning.
    #
    # None until the first segment is transcoded, and on the list endpoint,
    # where summing per row would be a query per recording for a figure no list
    # view shows.
    captured_audio_seconds: Optional[float] = None
    # Where on the audio timeline capture is now, in ms: the clock timeline
    # notes are anchored on. The end of the last transcoded segment, plus the
    # time since it arrived, or since the last resume if later, while recording
    # (at most one segment's worth), so
    # it trails the truth by the upload and transcode delay. Exact while
    # paused. None unless capture is open, and on the list endpoint.
    live_audio_ms: Optional[int] = None
    # Changes whenever the recording's notes list (GET .../timeline-notes)
    # would: a client holding that list fetches it again when this changes,
    # rather than receiving every note on every poll. Only the detail endpoint
    # fills it; None elsewhere.
    timeline_notes_list_signature: Optional[str] = None
    is_archived: bool = False
    is_deleted: bool = False
    last_activity_at: Optional[datetime] = None
    # A live capture that has uploaded nothing for STALLED_CAPTURE_AFTER: its tab
    # is gone or cut off. The client offers it for resume, stop or discard.
    # Computed server-side, like Document.is_stalled, because last_activity_at
    # carries no timezone and the browser's clock may disagree with the server's.
    capture_stalled: bool = False
    # A file import that has received no part for STALLED_IMPORT_AFTER: the page
    # sending it went away. The client offers to import it again or discard it.
    import_stalled: bool = False
    # Computed server-side when the detail read includes the transcript: shown
    # once processing is complete, until the recording has a language of its
    # own (backend/utils/language_notice.py).
    language_notice: Optional[LanguageNoticeRead] = None
    transcript: Optional[TranscriptPublicRead] = None
    speakers: list[RecordingSpeakerPublicRead] = Field(default_factory=list)
    tags: list[TagRead] = Field(default_factory=list)
    calendar_event: Optional[CalendarEventLinkRead] = None


class RecordingsCalendarRead(BaseModel):
    month: str
    timezone: str
    day_counts: List[CalendarDashboardDayCountRead] = Field(default_factory=list)


def serialize_recording_speaker(
    speaker: RecordingSpeaker,
    *,
    recording_public_id: str,
    library_update: Optional[LibraryVoiceprintUpdate] = None,
) -> RecordingSpeakerPublicRead:
    # serialize_* helpers only ever receive persisted rows, so the primary key
    # is always populated despite the ORM model typing it as Optional.
    assert speaker.id is not None
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
        name_last_edit_source=speaker.name_last_edit_source,
        name_source=speaker.name_source,
        name_pending=name_is_pending(speaker),
        global_speaker=(
            GlobalSpeakerRead.model_validate(speaker.global_speaker)
            if speaker.global_speaker is not None
            else None
        ),
        library_voiceprint=(
            LibraryVoiceprintRead.from_update(library_update)
            if library_update is not None
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
    from backend.utils.canonical_pipeline.guards import settling_required
    from backend.utils.speaker_names.live_suggestions import shown_in_transcript_read

    assert transcript.id is not None
    return TranscriptPublicRead(
        id=transcript.id,
        created_at=transcript.created_at,
        updated_at=transcript.updated_at,
        recording_id=recording_public_id,
        text=transcript.text if text_override is None else text_override,
        change_sequence=transcript.change_sequence,
        settling_required=settling_required(transcript),
        # The durable projection retains inference/protection evidence for
        # backfill and backup; public reads expose the ordinary segment fields.
        segments=[
            public_transcript_segment(
                segment, include_final_words=settling_required(transcript)
            )
            for segment in (
                transcript.segments if segments_override is None else segments_override
            )
        ],
        notes=transcript.notes,
        meeting_edge_focus=transcript.meeting_edge_focus,
        meeting_edge_payload=transcript.meeting_edge_payload,
        meeting_edge_status=transcript.meeting_edge_status,
        meeting_edge_error_message=transcript.meeting_edge_error_message,
        speaker_name_suggestions=[
            SpeakerNameSuggestionRead.model_validate(item)
            for item in (transcript.speaker_name_suggestions or [])
            if isinstance(item, dict) and shown_in_transcript_read(item)
        ],
        notes_status=transcript.notes_status,
        notes_stale_documents=bool(getattr(transcript, "notes_stale_documents", False)),
        transcript_status=transcript.transcript_status,
        error_message=transcript.error_message,
    )


def serialize_chat_message(
    message: ChatMessage,
    *,
    recording_public_id: str,
) -> ChatMessagePublicRead:
    assert message.id is not None
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
    assert document.id is not None
    return DocumentPublicRead(
        id=document.id,
        created_at=document.created_at,
        updated_at=document.updated_at,
        recording_id=recording_public_id,
        title=document.title,
        file_path=document.file_path,
        file_type=document.file_type,
        file_size_bytes=document.file_size_bytes,
        status=document.status,
        error_message=document.error_message,
        parse_mode=document.parse_mode,
        parse_warning=document.parse_warning,
        page_count=document.page_count,
        pages_parsed=document.pages_parsed,
        parse_stage=document.parse_stage,
        is_stalled=parse_looks_stalled(document),
    )


def serialize_recording(
    recording: Recording,
    *,
    has_proxy: bool = False,
    has_audio: Optional[bool] = None,
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
    captured_audio_seconds: Optional[float] = None,
    live_audio_ms: Optional[int] = None,
    timeline_notes_list_signature: Optional[str] = None,
) -> RecordingPublicRead:
    transcript = None
    notice = None
    if include_transcript and recording.transcript is not None:
        notice = language_notice(
            processed=recording.status == RecordingStatus.PROCESSED,
            recording_language=getattr(recording, "transcription_language", None),
            detection=recording.transcript.language_detection,
        )
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
        has_audio=has_audio,
        duration_seconds=recording.duration_seconds,
        file_size_bytes=recording.file_size_bytes,
        status=recording.status,
        client_status=recording.client_status,
        upload_progress=recording.upload_progress,
        processing_progress=recording.processing_progress,
        previous_publication_available=reprocess_publication_pending(
            recording.__dict__.get("transcript")
        ),
        reprocess_publication_complete=reprocess_publication_complete(
            recording.__dict__.get("transcript")
        ),
        processing_step=recording.processing_step,
        processing_eta_seconds=processing_eta_seconds,
        processing_eta_learning=processing_eta_learning,
        processing_eta_sample_size=processing_eta_sample_size,
        max_speakers=getattr(recording, "max_speakers", None),
        transcription_language=getattr(recording, "transcription_language", None),
        captured_audio_seconds=captured_audio_seconds,
        live_audio_ms=live_audio_ms,
        timeline_notes_list_signature=timeline_notes_list_signature,
        is_archived=recording.is_archived,
        is_deleted=recording.is_deleted,
        last_activity_at=recording.last_activity_at,
        capture_stalled=capture_looks_stalled(recording),
        import_stalled=import_looks_stalled(recording),
        language_notice=LanguageNoticeRead(**notice) if notice else None,
        transcript=transcript,
        speakers=speakers,
        tags=tags,
        calendar_event=calendar_event_link,
    )
