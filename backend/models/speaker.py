from typing import Optional, List, TYPE_CHECKING
from sqlmodel import Field, Relationship
from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording

class GlobalSpeaker(BaseDBModel, table=True):
    __tablename__ = "global_speakers"
    name: str = Field(unique=True, index=True)
    
    recording_speakers: List["RecordingSpeaker"] = Relationship(back_populates="global_speaker")

class RecordingSpeaker(BaseDBModel, table=True):
    __tablename__ = "recording_speakers"
    
    recording_id: int = Field(foreign_key="recordings.id")
    global_speaker_id: Optional[int] = Field(default=None, foreign_key="global_speakers.id")
    
    diarization_label: str # e.g. SPEAKER_00
    
    # Optional snippet for identification
    snippet_start: Optional[float] = None
    snippet_end: Optional[float] = None
    voice_snippet_path: Optional[str] = None

    recording: "Recording" = Relationship(back_populates="speakers")
    global_speaker: Optional["GlobalSpeaker"] = Relationship(back_populates="recording_speakers")
