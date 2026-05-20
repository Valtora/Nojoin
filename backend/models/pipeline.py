from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional, TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import BigInteger, Column, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.models.base import BaseDBModel
from backend.utils.time import utc_now

if TYPE_CHECKING:
    from backend.models.recording import Recording
    from backend.models.speaker import RecordingSpeaker
    from backend.models.user import User


def generate_pipeline_public_id() -> str:
    return str(uuid4())


class ProcessingRunKind(str, Enum):
    LIVE = "live"
    ROLLING_DIARIZATION = "rolling_diarization"
    CATCH_UP = "catch_up"
    FINALIZE = "finalize"
    REPROCESS = "reprocess"
    IMPORT = "import"
    BACKFILL = "backfill"


class ProcessingRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecordingAsrWindowResultStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class TranscriptUtteranceState(str, Enum):
    PROVISIONAL = "provisional"
    STABLE = "stable"
    SUPERSEDED = "superseded"
    FINALIZED = "finalized"
    DELETED = "deleted"


class RecordingSpeakerAliasType(str, Enum):
    DIARIZATION_LABEL = "diarization_label"
    LIVE_LABEL = "live_label"
    MANUAL_LABEL = "manual_label"
    DISPLAY_NAME = "display_name"
    GLOBAL_NAME = "global_name"
    IMPORT_LABEL = "import_label"


class SpeakerCorrectionScope(str, Enum):
    UTTERANCE_ONLY = "utterance_only"
    SPEAKER_EVERYWHERE_IN_RECORDING = "speaker_everywhere_in_recording"
    FROM_THIS_UTTERANCE_FORWARD = "from_this_utterance_forward"
    MERGE_INTO_SPEAKER = "merge_into_speaker"


class SpeakerCorrectionEventType(str, Enum):
    RENAME = "rename"
    ASSIGN_UTTERANCE = "assign_utterance"
    ASSIGN_RECORDING_SPEAKER = "assign_recording_speaker"
    ASSIGN_FROM_NOW_ON = "assign_from_now_on"
    MERGE_SPEAKERS = "merge_speakers"
    LINK_GLOBAL_SPEAKER = "link_global_speaker"
    PROMOTE_GLOBAL_SPEAKER = "promote_global_speaker"


class RecordingAudioChunk(BaseDBModel, table=True):
    __tablename__ = "recording_audio_chunks"
    __table_args__ = (
        UniqueConstraint("recording_id", "sequence_no", name="uq_recording_audio_chunks_recording_sequence"),
        UniqueConstraint("recording_id", "idempotency_key", name="uq_recording_audio_chunks_recording_idempotency"),
    )

    public_id: str = Field(
        default_factory=generate_pipeline_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_pipeline_public_id,
        ),
    )
    recording_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False, index=True))
    sequence_no: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_kind: str = Field(default="companion")
    absolute_start_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    absolute_end_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    duration_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    sample_rate_hz: int = Field(sa_column=Column(BigInteger, nullable=False))
    channel_count: int = Field(sa_column=Column(BigInteger, nullable=False))
    byte_size: int = Field(sa_column=Column(BigInteger, nullable=False))
    sha256: str = Field(sa_column=Column(String(128), nullable=False))
    storage_path: str = Field(sa_column=Column(Text, nullable=False))
    upload_status: str = Field(default="received")
    idempotency_key: Optional[str] = Field(default=None, sa_column=Column(String(255), index=True))
    received_at: datetime = Field(default_factory=utc_now)
    cleanup_eligible_at: Optional[datetime] = None


class RecordingAudioWindowManifest(BaseDBModel, table=True):
    __tablename__ = "recording_audio_window_manifests"
    __table_args__ = (
        UniqueConstraint(
            "recording_id",
            "window_index",
            name="uq_recording_audio_window_manifests_recording_window",
        ),
    )

    public_id: str = Field(
        default_factory=generate_pipeline_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_pipeline_public_id,
        ),
    )
    recording_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("recordings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    window_index: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_kind: str = Field(default="companion")
    target_window_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    hop_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    window_start_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    window_end_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    chunk_start_sequence: int = Field(sa_column=Column(BigInteger, nullable=False))
    chunk_end_sequence: int = Field(sa_column=Column(BigInteger, nullable=False))
    status: str = Field(default="pending")
    is_partial: bool = Field(default=False)
    is_sealed: bool = Field(default=False)
    processing_run_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("processing_runs.id", ondelete="SET NULL"),
            index=True,
        ),
    )
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text))


class ProcessingRun(BaseDBModel, table=True):
    __tablename__ = "processing_runs"
    __table_args__ = (
        UniqueConstraint("recording_id", "idempotency_key", name="uq_processing_runs_recording_idempotency"),
    )

    public_id: str = Field(
        default_factory=generate_pipeline_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_pipeline_public_id,
        ),
    )
    recording_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False, index=True))
    parent_run_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("processing_runs.id", ondelete="SET NULL")))
    run_kind: ProcessingRunKind = Field(default=ProcessingRunKind.BACKFILL)
    trigger_source: str = Field(default="system")
    requested_by_user_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL")))
    status: ProcessingRunStatus = Field(default=ProcessingRunStatus.PENDING)
    config_hash: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    transcription_backend: Optional[str] = None
    diarization_backend: Optional[str] = None
    model_metadata: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    span_start_ms: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    span_end_ms: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    reused_live_asr: bool = Field(default=False)
    idempotency_key: Optional[str] = Field(default=None, sa_column=Column(String(255), index=True))
    metrics: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    error_summary: Optional[str] = Field(default=None, sa_column=Column(Text))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class RecordingAsrWindowResult(BaseDBModel, table=True):
    __tablename__ = "recording_asr_window_results"
    __table_args__ = (
        UniqueConstraint(
            "recording_id",
            "idempotency_key",
            name="uq_recording_asr_window_results_recording_idempotency",
        ),
    )

    public_id: str = Field(
        default_factory=generate_pipeline_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_pipeline_public_id,
        ),
    )
    recording_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("recordings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    processing_run_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("processing_runs.id", ondelete="SET NULL"),
            index=True,
        ),
    )
    source_kind: str = Field(default="live")
    span_start_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    span_end_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    chunk_start_sequence: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    chunk_end_sequence: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    transcription_backend: str = Field(sa_column=Column(String(255), nullable=False))
    model_name: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    config_hash: str = Field(sa_column=Column(String(255), nullable=False))
    status: RecordingAsrWindowResultStatus = Field(default=RecordingAsrWindowResultStatus.PENDING)
    idempotency_key: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    error_summary: Optional[str] = Field(default=None, sa_column=Column(Text))
    error_payload: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    result_payload: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    produced_utterance_public_ids: Optional[list[str]] = Field(default=None, sa_column=Column(JSONB))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class TranscriptUtterance(BaseDBModel, table=True):
    __tablename__ = "transcript_utterances"

    public_id: str = Field(
        default_factory=generate_pipeline_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_pipeline_public_id,
        ),
    )
    recording_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False, index=True))
    sort_key: str = Field(sa_column=Column(String(64), nullable=False, index=True))
    start_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    end_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    text: str = Field(sa_column=Column(Text, nullable=False))
    speaker_label: Optional[str] = Field(default=None, sa_column=Column(String(255), index=True))
    recording_speaker_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("recording_speakers.id", ondelete="SET NULL"), index=True))
    state: TranscriptUtteranceState = Field(default=TranscriptUtteranceState.STABLE)
    source_kind: str = Field(default="legacy")
    processing_run_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("processing_runs.id", ondelete="SET NULL"), index=True))
    last_utterance_event_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("transcript_utterance_events.id", ondelete="SET NULL"), index=True))
    last_diarization_window_result_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("diarization_window_results.id", ondelete="SET NULL"), index=True))
    revision: int = Field(default=1)
    overlap_group_id: Optional[str] = Field(default=None, sa_column=Column(String(64), index=True))
    overlap_rank: Optional[int] = Field(default=0)
    manual_text_locked: bool = Field(default=False)
    manual_speaker_locked: bool = Field(default=False)
    text_confidence: Optional[float] = None
    speaker_confidence: Optional[float] = None
    confidence_payload: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))


class TranscriptUtteranceEvent(BaseDBModel, table=True):
    __tablename__ = "transcript_utterance_events"

    recording_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False, index=True))
    utterance_id: int = Field(sa_column=Column(BigInteger, ForeignKey("transcript_utterances.id", ondelete="CASCADE"), nullable=False, index=True))
    processing_run_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("processing_runs.id", ondelete="SET NULL"), index=True))
    actor_user_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True))
    event_type: str = Field(sa_column=Column(String(64), nullable=False, index=True))
    source: str = Field(default="system")
    old_values: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    new_values: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    resulting_revision: int = Field(default=1)


class RecordingSpeakerAlias(BaseDBModel, table=True):
    __tablename__ = "recording_speaker_aliases"

    recording_speaker_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recording_speakers.id", ondelete="CASCADE"), nullable=False, index=True))
    alias_type: RecordingSpeakerAliasType = Field(default=RecordingSpeakerAliasType.DIARIZATION_LABEL)
    alias_value: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    source_run_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("processing_runs.id", ondelete="SET NULL"), index=True))
    active: bool = Field(default=True)
    valid_from_ms: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    valid_to_ms: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    confidence: Optional[float] = None


class SpeakerCorrectionEvent(BaseDBModel, table=True):
    __tablename__ = "speaker_correction_events"

    public_id: str = Field(
        default_factory=generate_pipeline_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_pipeline_public_id,
        ),
    )
    recording_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False, index=True))
    actor_user_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True))
    utterance_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("transcript_utterances.id", ondelete="SET NULL"), index=True))
    source_recording_speaker_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("recording_speakers.id", ondelete="SET NULL"), index=True))
    target_recording_speaker_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("recording_speakers.id", ondelete="SET NULL"), index=True))
    target_global_speaker_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("global_speakers.id", ondelete="SET NULL"), index=True))
    event_type: SpeakerCorrectionEventType = Field(default=SpeakerCorrectionEventType.ASSIGN_UTTERANCE)
    scope: SpeakerCorrectionScope = Field(default=SpeakerCorrectionScope.UTTERANCE_ONLY)
    effective_from_ms: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    payload: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))


class DiarizationWindowResult(BaseDBModel, table=True):
    __tablename__ = "diarization_window_results"

    public_id: str = Field(
        default_factory=generate_pipeline_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_pipeline_public_id,
        ),
    )
    recording_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False, index=True))
    processing_run_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("processing_runs.id", ondelete="SET NULL"), index=True))
    window_index: int = Field(default=0)
    window_start_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    window_end_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    chunk_start_sequence: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    chunk_end_sequence: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    device: Optional[str] = None
    config_hash: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    status: str = Field(default="pending")
    raw_payload: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))


class DiarizationWindowTurn(BaseDBModel, table=True):
    __tablename__ = "diarization_window_turns"

    window_result_id: int = Field(sa_column=Column(BigInteger, ForeignKey("diarization_window_results.id", ondelete="CASCADE"), nullable=False, index=True))
    local_speaker_key: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    start_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    end_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    confidence: Optional[float] = None
    matched_recording_speaker_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("recording_speakers.id", ondelete="SET NULL"), index=True))
    metadata_payload: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))