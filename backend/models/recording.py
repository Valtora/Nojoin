from typing import Optional, List, Dict, Any, TYPE_CHECKING
from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy import BigInteger, ForeignKey, Column
from enum import Enum
from .base import BaseDBModel
from datetime import datetime

if TYPE_CHECKING:
    from .speaker import RecordingSpeaker
    from .tag import RecordingTag
    from .transcript import Transcript
    from .user import User

class RecordingStatus(str, Enum):
    UPLOADING = "UPLOADING"
    RECORDED = "RECORDED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    ERROR = "ERROR"

class ClientStatus(str, Enum):
    RECORDING = "RECORDING"
    PAUSED = "PAUSED"
    UPLOADING = "UPLOADING"
    IDLE = "IDLE"

class Recording(BaseDBModel, table=True):
    __tablename__ = "recordings"

    name: str
    audio_path: str = Field(unique=True, index=True)
    duration_seconds: Optional[float] = None
    file_size_bytes: Optional[int] = None
    status: RecordingStatus = Field(default=RecordingStatus.RECORDED)
    client_status: Optional[ClientStatus] = Field(default=None)
    processing_step: Optional[str] = Field(default=None)
    is_archived: bool = Field(default=False, index=True)
    is_deleted: bool = Field(default=False, index=True)
    
    user_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE")))

    # Relationships
    speakers: List["RecordingSpeaker"] = Relationship(back_populates="recording", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    tags: List["RecordingTag"] = Relationship(back_populates="recording", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    transcript: Optional["Transcript"] = Relationship(back_populates="recording", sa_relationship_kwargs={"cascade": "all, delete-orphan"})

# Read Models
# GlobalSpeakerRead is imported from .speaker to avoid circular imports if possible, 
# but here we need it for RecordingSpeakerRead.
# Ideally, we should import it. 
# However, due to potential circular imports (speaker imports recording for type checking), 
# we might need to be careful. 
# Let's try importing it inside the file or at top if not circular.
# Actually, speaker.py only imports Recording for TYPE_CHECKING.
# So we can import GlobalSpeakerRead from .speaker here.

from .speaker import GlobalSpeakerRead

class RecordingSpeakerRead(BaseDBModel):
    recording_id: int
    diarization_label: str
    local_name: Optional[str] = None
    name: Optional[str] = None
    color: Optional[str] = None
    has_voiceprint: bool = False
    global_speaker: Optional[GlobalSpeakerRead] = None

class TranscriptRead(BaseDBModel):
    text: Optional[str] = None
    segments: List[Dict[str, Any]] = []
    notes: Optional[str] = None
    notes_status: str = "pending"
    transcript_status: str = "pending"
    error_message: Optional[str] = None

class TagRead(BaseDBModel):
    name: str
    color: Optional[str] = None

class RecordingRead(BaseDBModel):
    name: str
    audio_path: str
    duration_seconds: Optional[float] = None
    file_size_bytes: Optional[int] = None
    status: RecordingStatus
    client_status: Optional[ClientStatus] = None
    processing_step: Optional[str] = None
    is_archived: bool = False
    is_deleted: bool = False
    
    transcript: Optional[TranscriptRead] = None
    speakers: List[RecordingSpeakerRead] = []
    tags: List[TagRead] = []

class RecordingUpdate(SQLModel):
    name: Optional[str] = None
