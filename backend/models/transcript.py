from typing import Optional, List, Dict, Any, TYPE_CHECKING
from sqlmodel import Field, Relationship
from sqlalchemy import Column, BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording

class Transcript(BaseDBModel, table=True):
    __tablename__ = "transcripts"
    
    recording_id: int = Field(foreign_key="recordings.id", unique=True, sa_type=BigInteger)
    
    text: Optional[str] = None
    segments: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSONB))
    
    recording: "Recording" = Relationship(back_populates="transcript")
