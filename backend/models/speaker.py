from typing import Optional, List, TYPE_CHECKING
from sqlmodel import Field, Relationship
from sqlalchemy import Column, BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import computed_field
from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording

class GlobalSpeaker(BaseDBModel, table=True):
    __tablename__ = "global_speakers"
    name: str = Field(unique=True, index=True)
    embedding: Optional[List[float]] = Field(default=None, sa_column=Column(JSONB))
    
    recording_speakers: List["RecordingSpeaker"] = Relationship(back_populates="global_speaker")
    
    @computed_field
    @property
    def has_voiceprint(self) -> bool:
        """Returns True if this speaker has a voiceprint (embedding) stored."""
        return self.embedding is not None and len(self.embedding) > 0

class RecordingSpeaker(BaseDBModel, table=True):
    __tablename__ = "recording_speakers"
    
    recording_id: int = Field(foreign_key="recordings.id", sa_type=BigInteger)
    global_speaker_id: Optional[int] = Field(default=None, foreign_key="global_speakers.id", sa_type=BigInteger)
    
    diarization_label: str # e.g. SPEAKER_00
    
    # The resolved name for this speaker in this recording (e.g. "John Doe" or "SPEAKER_00")
    name: Optional[str] = None

    # Optional snippet for identification
    snippet_start: Optional[float] = None
    snippet_end: Optional[float] = None
    voice_snippet_path: Optional[str] = None
    
    embedding: Optional[List[float]] = Field(default=None, sa_column=Column(JSONB))

    recording: "Recording" = Relationship(back_populates="speakers")
    global_speaker: Optional["GlobalSpeaker"] = Relationship(back_populates="recording_speakers")
    
    @computed_field
    @property
    def has_voiceprint(self) -> bool:
        """Returns True if this speaker has a voiceprint (embedding) stored."""
        return self.embedding is not None and len(self.embedding) > 0
