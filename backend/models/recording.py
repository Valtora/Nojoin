from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

from pydantic import field_validator
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, or_
from sqlmodel import Field, Relationship, SQLModel, col

from backend.utils.languages import validate_transcription_language
from backend.utils.time import utc_now

from .base import BaseDBModel

if TYPE_CHECKING:
    from .chat import ChatMessage
    from .context_chunk import ContextChunk
    from .document import Document
    from .speaker import RecordingSpeaker
    from .tag import RecordingTag
    from .transcript import Transcript


class RecordingStatus(str, Enum):
    UPLOADING = "UPLOADING"
    PAUSED = "PAUSED"
    RECORDED = "RECORDED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class ClientStatus(str, Enum):
    RECORDING = "RECORDING"
    PAUSED = "PAUSED"
    UPLOADING = "UPLOADING"
    IDLE = "IDLE"


class RecordingPipelineGeneration(str, Enum):
    UNIFIED = "unified"
    LEGACY_BACKFILLED = "legacy_backfilled"
    LEGACY_REPROCESS_REQUIRED = "legacy_reprocess_required"


LEGACY_RECORDING_REPROCESS_REQUIRED_DETAIL = "This recording must be reprocessed before transcript or speaker edits are supported"

# Statuses in which a recording's transcript is still being captured or rebuilt.
# The recording view blanks the transcript while in one, and Meeting Chat
# refuses to answer from it.
IN_FLIGHT_TRANSCRIPT_STATUSES = frozenset(
    {
        RecordingStatus.PAUSED,
        RecordingStatus.UPLOADING,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
    }
)


def generate_meeting_uid() -> str:
    return str(uuid4())


def generate_public_id() -> str:
    return str(uuid4())


class Recording(BaseDBModel, table=True):
    __tablename__ = "recordings"  # pyright: ignore[reportAssignmentType]

    name: str
    public_id: str = Field(
        default_factory=generate_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_public_id,
        ),
    )
    meeting_uid: str = Field(
        default_factory=generate_meeting_uid,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_meeting_uid,
        ),
    )
    audio_path: str = Field(unique=True, index=True)
    proxy_path: Optional[str] = Field(default=None)
    celery_task_id: Optional[str] = Field(default=None)
    duration_seconds: Optional[float] = None
    file_size_bytes: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    status: RecordingStatus = Field(default=RecordingStatus.RECORDED)
    client_status: Optional[ClientStatus] = Field(default=None)
    upload_progress: int = Field(default=0)
    processing_progress: int = Field(default=0)
    processing_step: Optional[str] = Field(default=None)
    processing_started_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
    processing_completed_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
    pipeline_generation: Optional[str] = Field(
        default=RecordingPipelineGeneration.UNIFIED.value,
        sa_column=Column(String(32), nullable=True, index=True),
    )
    max_speakers: Optional[int] = Field(
        default=None,
        description=(
            "Optional upper bound on the number of speakers for this recording. "
            "NULL means auto-detect, which is the default and leaves diarization "
            "unconstrained. Applied as pyannote's max_speakers, never num_speakers."
        ),
    )
    # The language this recording is transcribed in: "auto" or a listed code.
    # NULL means the owner's setting (``apply_recording_language``, #671).
    transcription_language: Optional[str] = Field(
        default=None, sa_column=Column(String(16), nullable=True)
    )
    is_archived: bool = Field(default=False, index=True)
    is_deleted: bool = Field(default=False, index=True)
    last_activity_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
    # Which tab or device holds a live capture. Init starts it at 0 and a resume
    # that takes the capture over issues the next one, after which a segment,
    # pause or finalize sent with an earlier epoch is refused (issue #570).
    capture_epoch: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0", default=0),
    )

    user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE")),
    )
    calendar_event_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger, ForeignKey("calendar_events.id", ondelete="SET NULL")
        ),
    )

    # Relationships
    speakers: List["RecordingSpeaker"] = Relationship(
        back_populates="recording",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    tags: List["RecordingTag"] = Relationship(
        back_populates="recording",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    transcript: Optional["Transcript"] = Relationship(
        back_populates="recording",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    chat_messages: List["ChatMessage"] = Relationship(
        back_populates="recording",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    documents: List["Document"] = Relationship(
        back_populates="recording",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    context_chunks: List["ContextChunk"] = Relationship(
        back_populates="recording",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


def is_live_capture(recording: "Recording") -> bool:
    """Whether a recording is a live browser capture, running or paused.

    Status alone cannot say: a file import sits in UPLOADING too while its parts
    arrive. Only the capture endpoints set a client status of RECORDING or
    PAUSED, and only a capture reaches the PAUSED status, which also covers one
    paused before captures recorded a client status. Mirrors
    ``isLiveCaptureInProgress`` in frontend/src/lib/liveCapture.ts.
    """
    return recording.status == RecordingStatus.PAUSED or recording.client_status in {
        ClientStatus.RECORDING,
        ClientStatus.PAUSED,
    }


def live_capture_clause() -> Any:
    """``is_live_capture`` as a SQL condition on ``Recording``."""
    return or_(
        col(Recording.status) == RecordingStatus.PAUSED,
        col(Recording.client_status).in_([ClientStatus.RECORDING, ClientStatus.PAUSED]),
    )


# How long a live capture can go without uploading before another tab or device
# may offer it for resume or discard. A capture uploads a segment every 2 s and
# writes last_activity_at at most every 15 s, and the longest gap seen in a
# capture that carried on afterwards was 33 s, across an API restart. Nothing
# acts on a capture by itself once this passes: it only becomes something the
# user can resume, stop or discard from somewhere else (issue #519).
STALLED_CAPTURE_AFTER = timedelta(minutes=2)


def capture_looks_stalled(
    recording: "Recording", *, now: Optional[datetime] = None
) -> bool:
    """Whether a live capture has stopped reaching the server.

    True for a capture still marked UPLOADING whose last upload, start or resume
    is older than ``STALLED_CAPTURE_AFTER``: its tab closed without its pause
    arriving (signed out, offline or crashed), or it is cut off from the server.
    The server cannot tell those apart, so this never pauses anything. The
    client offers such a capture to the user, and resume accepts one, so a
    capture that is still uploading can never be taken over.

    Computed here rather than in the browser, whose clock may disagree with the
    server's. Imports also sit in UPLOADING, so only a capture counts: init and
    resume set its client status to RECORDING.
    """
    if recording.status != RecordingStatus.UPLOADING:
        return False
    if recording.client_status != ClientStatus.RECORDING:
        return False
    last_activity = recording.last_activity_at or recording.created_at
    if last_activity is None:
        return False
    return (now or utc_now()) - last_activity > STALLED_CAPTURE_AFTER


# How long a file import can go without a part arriving before the client says
# it did not finish. The browser sends one 10 MB part at a time, and
# last_activity_at moves when each has arrived, so a slow link leaves a long gap
# between them: 15 minutes is a part at about 90 kbit/s. Nothing acts on an
# import by itself once this passes: the user is told and chooses (issue #569).
STALLED_IMPORT_AFTER = timedelta(minutes=15)


def import_looks_stalled(
    recording: "Recording", *, now: Optional[datetime] = None
) -> bool:
    """Whether a file import stopped before its upload finished.

    True for a recording still marked UPLOADING that is not a live capture and
    whose last part, or start, is older than ``STALLED_IMPORT_AFTER``: the page
    sending it closed or lost its connection. An import cannot be resumed, since
    the file is on the user's device, so the client offers to import it again
    or discard what arrived.
    """
    if recording.status != RecordingStatus.UPLOADING or is_live_capture(recording):
        return False
    last_activity = recording.last_activity_at or recording.created_at
    if last_activity is None:
        return False
    return (now or utc_now()) - last_activity > STALLED_IMPORT_AFTER


# Read Models
# Safe to import here; speaker.py only references Recording under TYPE_CHECKING.
from .speaker import GlobalSpeakerRead


class RecordingSpeakerRead(BaseDBModel):
    recording_id: int
    diarization_label: str
    global_speaker_id: Optional[int] = None
    local_name: Optional[str] = None
    name: Optional[str] = None
    color: Optional[str] = None
    has_voiceprint: bool = False
    global_speaker: Optional[GlobalSpeakerRead] = None


class TranscriptRead(BaseDBModel):
    text: Optional[str] = None
    segments: List[Dict[str, Any]] = []
    notes: Optional[str] = None
    meeting_edge_focus: Optional[str] = None
    meeting_edge_payload: Optional[Dict[str, Any]] = None
    meeting_edge_status: str = "idle"
    meeting_edge_error_message: Optional[str] = None
    notes_status: str = "pending"
    transcript_status: str = "pending"
    error_message: Optional[str] = None


class TagRead(BaseDBModel):
    name: str
    color: Optional[str] = None


class RecordingRead(BaseDBModel):
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

    transcript: Optional[TranscriptRead] = None
    speakers: List[RecordingSpeakerRead] = []
    tags: List[TagRead] = []


class RecordingUpdate(SQLModel):
    name: Optional[str] = None
    # Three states, told apart by model_fields_set: absent leaves the
    # recording's language alone, null returns it to the owner's setting, and
    # "auto" or a listed code sets it.
    transcription_language: Optional[str] = None

    @field_validator("transcription_language")
    @classmethod
    def _validate_transcription_language(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_transcription_language(value)


class RecordingInitResponse(SQLModel):
    id: str
    name: str
    upload_token: Optional[str] = None
    capture_epoch: int = 0


class RecordingCaptureLifecycleResponse(SQLModel):
    recording_id: str
    status: RecordingStatus
    last_sequence: int
    # The capture epoch once the call is done: unchanged by a pause or by the
    # holding tab's resume, the next one after a resume that takes over.
    capture_epoch: int = 0


class CaptureTrackReport(SQLModel):
    kind: str
    label: Optional[str] = None
    enabled: bool = True
    muted: bool = False
    ready_state: str
    settings: Dict[str, Any] = Field(default_factory=dict)


class CaptureDeviceReport(SQLModel):
    device_id: str
    label: str


class CaptureSourceReportCreate(SQLModel):
    attempt_kind: str
    outcome: str
    mode: str
    requested_microphone_device_id: Optional[str] = None
    requested_microphone_label: Optional[str] = None
    available_microphones: List[CaptureDeviceReport] = Field(default_factory=list)
    browser_microphone_track: Optional[CaptureTrackReport] = None
    browser_display_audio_track: Optional[CaptureTrackReport] = None
    browser_display_video_track: Optional[CaptureTrackReport] = None
    shared_audio_available: bool = False
    configured_microphone_gain: float = 1.0
    configured_system_gain: float = 1.0
    configured_echo_cancellation: bool = True
    configured_noise_suppression: bool = False
    configured_auto_gain_control: bool = True
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


def recording_supports_unified_mutations(recording: Optional["Recording"]) -> bool:
    if recording is None:
        return False

    generation = getattr(recording, "pipeline_generation", None)
    # Accept both the enum and its raw string value; getattr falls back to the
    # original when there is no .value (e.g. already a plain string or None).
    generation_value = getattr(generation, "value", generation)

    return str(generation_value or "") == RecordingPipelineGeneration.UNIFIED.value
