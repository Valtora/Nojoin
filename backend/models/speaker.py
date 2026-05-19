from typing import Optional, List, TYPE_CHECKING
from uuid import uuid4
from sqlmodel import Field, Relationship
from sqlalchemy import Column, BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import computed_field
from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording
    from .user import User

from .people_tag import PeopleTagLink, PeopleTag
from .people_tag_schemas import PeopleTagRead


def generate_recording_speaker_public_id() -> str:
    return str(uuid4())

class GlobalSpeaker(BaseDBModel, table=True):
    __tablename__ = "global_speakers"
    name: str = Field(index=True) # Removed unique=True to allow same name for different users
    embedding: Optional[List[float]] = Field(default=None, sa_column=Column(JSONB))
    is_voiceprint_locked: bool = Field(default=False, description="If True, voiceprint is manually verified and won't be auto-updated")
    color: Optional[str] = None
    
    # CRM Fields
    title: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    notes: Optional[str] = Field(default=None, description="Notes about the person")
    
    user_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE")))

    recording_speakers: List["RecordingSpeaker"] = Relationship(back_populates="global_speaker")
    
    # Tags
    tag_links: List["PeopleTagLink"] = Relationship(
        sa_relationship_kwargs={"primaryjoin": "GlobalSpeaker.id==PeopleTagLink.global_speaker_id", "lazy": "selectin", "cascade": "all, delete-orphan"}
    )
    
    @computed_field
    @property
    def has_voiceprint(self) -> bool:
        """Returns True if this speaker has a voiceprint (embedding) stored."""
        return self.embedding is not None and len(self.embedding) > 0

class GlobalSpeakerRead(BaseDBModel):
    name: str
    color: Optional[str] = None
    has_voiceprint: bool = False
    is_voiceprint_locked: bool = False
    # CRM Fields
    title: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    notes: Optional[str] = None
    tags: List[PeopleTagRead] = []

class GlobalSpeakerCreate(BaseDBModel):
    name: str
    color: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    notes: Optional[str] = None
    tag_ids: List[int] = []

class GlobalSpeakerUpdate(BaseDBModel):
    name: Optional[str] = None
    color: Optional[str] = None
    # CRM Fields
    title: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    notes: Optional[str] = None
    tag_ids: Optional[List[int]] = None

class GlobalSpeakerWithCount(GlobalSpeakerRead):
    recording_count: int = 0

class RecordingSpeaker(BaseDBModel, table=True):
    __tablename__ = "recording_speakers"

    public_id: str = Field(
        default_factory=generate_recording_speaker_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_recording_speaker_public_id,
        ),
    )
    
    recording_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE")))
    global_speaker_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("global_speakers.id", ondelete="SET NULL")))
    
    diarization_label: str # e.g. SPEAKER_00
    
    # Local name for this speaker in this recording only (not promoted to global)
    local_name: Optional[str] = None
    
    # DEPRECATED: The resolved name for this speaker (kept for backward compatibility)
    # New code should use local_name or global_speaker.name
    name: Optional[str] = None

    speaker_status: str = Field(default="active")
    speaker_kind: str = Field(default="automated")
    processing_run_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("processing_runs.id", ondelete="SET NULL"), index=True))
    last_speaker_correction_event_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("speaker_correction_events.id", ondelete="SET NULL"), index=True))
    last_diarization_window_result_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("diarization_window_results.id", ondelete="SET NULL"), index=True))
    first_seen_ms: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    last_seen_ms: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    identity_confidence: Optional[float] = None
    identity_locked: bool = Field(default=False)

    # Optional snippet for identification
    snippet_start: Optional[float] = None
    snippet_end: Optional[float] = None
    voice_snippet_path: Optional[str] = None
    
    embedding: Optional[List[float]] = Field(default=None, sa_column=Column(JSONB))
    color: Optional[str] = None

    recording: "Recording" = Relationship(back_populates="speakers")
    global_speaker: Optional["GlobalSpeaker"] = Relationship(back_populates="recording_speakers")
    
    # Self-referential merge pointer (if merged into another local speaker)
    merged_into_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("recording_speakers.id", ondelete="SET NULL")))
    merged_into: Optional["RecordingSpeaker"] = Relationship(
        sa_relationship_kwargs={"remote_side": "RecordingSpeaker.id"}
    )
    
    @computed_field
    @property
    def has_voiceprint(self) -> bool:
        """Returns True if this speaker has a voiceprint (embedding) stored."""
        return self.embedding is not None and len(self.embedding) > 0
