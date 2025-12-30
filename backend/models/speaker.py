from typing import Optional, List, TYPE_CHECKING
from sqlmodel import Field, Relationship
from sqlalchemy import Column, BigInteger, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import computed_field
from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording
    from .user import User

from .people_tag import PeopleTagLink, PeopleTag
from .people_tag_schemas import PeopleTagRead

class GlobalSpeaker(BaseDBModel, table=True):
    __tablename__ = "global_speakers"
    name: str = Field(index=True) # Removed unique=True to allow same name for different users
    embedding: Optional[List[float]] = Field(default=None, sa_column=Column(JSONB))
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
    
    recording_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE")))
    global_speaker_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("global_speakers.id", ondelete="SET NULL")))
    
    diarization_label: str # e.g. SPEAKER_00
    
    # Local name for this speaker in this recording only (not promoted to global)
    local_name: Optional[str] = None
    
    # DEPRECATED: The resolved name for this speaker (kept for backward compatibility)
    # New code should use local_name or global_speaker.name
    name: Optional[str] = None

    # Optional snippet for identification
    snippet_start: Optional[float] = None
    snippet_end: Optional[float] = None
    voice_snippet_path: Optional[str] = None
    
    embedding: Optional[List[float]] = Field(default=None, sa_column=Column(JSONB))
    color: Optional[str] = None

    recording: "Recording" = Relationship(back_populates="speakers")
    global_speaker: Optional["GlobalSpeaker"] = Relationship(back_populates="recording_speakers")
    
    @computed_field
    @property
    def has_voiceprint(self) -> bool:
        """Returns True if this speaker has a voiceprint (embedding) stored."""
        return self.embedding is not None and len(self.embedding) > 0
