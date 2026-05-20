from typing import Optional, List, Dict, Any, TYPE_CHECKING
from sqlmodel import Field, Relationship
from sqlalchemy import Column, BigInteger, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording

class Transcript(BaseDBModel, table=True):
    __tablename__ = "transcripts"
    
    recording_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), unique=True))
    
    text: Optional[str] = None
    segments: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSONB))
    notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    user_notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    meeting_edge_focus: Optional[str] = Field(default=None, sa_column=Column(Text))
    meeting_edge_payload: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    meeting_edge_status: str = Field(default="idle")
    meeting_edge_error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    meeting_edge_source_signature: Optional[str] = Field(default=None, sa_column=Column(Text))
    speaker_name_suggestions: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
    notes_status: str = Field(default="pending") # pending, generating, completed, error
    transcript_status: str = Field(default="pending") # pending, processing, completed, error
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    
    recording: "Recording" = Relationship(back_populates="transcript")
