from typing import Optional, List, Dict, Any, TYPE_CHECKING
from sqlmodel import Field, Relationship, SQLModel
from enum import Enum
from .base import BaseDBModel

if TYPE_CHECKING:
    from .speaker import RecordingSpeaker
    from .tag import RecordingTag
    from .transcript import Transcript

class RecordingStatus(str, Enum):
    UPLOADING = "UPLOADING"
    RECORDED = "RECORDED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    ERROR = "ERROR"

class Recording(BaseDBModel, table=True):
    __tablename__ = "recordings"

    name: str
    audio_path: str = Field(unique=True, index=True)
    duration_seconds: Optional[float] = None
    file_size_bytes: Optional[int] = None
    status: RecordingStatus = Field(default=RecordingStatus.RECORDED)
    
    # Relationships
    speakers: List["RecordingSpeaker"] = Relationship(back_populates="recording", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    tags: List["RecordingTag"] = Relationship(back_populates="recording", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    transcript: Optional["Transcript"] = Relationship(back_populates="recording", sa_relationship_kwargs={"cascade": "all, delete-orphan"})

# Read Models
class GlobalSpeakerRead(BaseDBModel):
    name: str

class RecordingSpeakerRead(BaseDBModel):
    diarization_label: str
    global_speaker: Optional[GlobalSpeakerRead] = None

class TranscriptRead(BaseDBModel):
    text: Optional[str] = None
    segments: List[Dict[str, Any]] = []

class TagRead(BaseDBModel):
    name: str

class RecordingRead(BaseDBModel):
    name: str
    audio_path: str
    duration_seconds: Optional[float] = None
    file_size_bytes: Optional[int] = None
    status: RecordingStatus
    
    transcript: Optional[TranscriptRead] = None
    speakers: List[RecordingSpeakerRead] = []
    tags: List[TagRead] = []

class RecordingUpdate(SQLModel):
    name: Optional[str] = None
