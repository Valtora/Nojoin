from typing import List, Optional, TYPE_CHECKING
from sqlmodel import Field, Relationship
from sqlalchemy import BigInteger
from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording

class RecordingTag(BaseDBModel, table=True):
    __tablename__ = "recording_tags"
    recording_id: int = Field(foreign_key="recordings.id", sa_type=BigInteger)
    tag_id: int = Field(foreign_key="tags.id", sa_type=BigInteger)
    
    recording: "Recording" = Relationship(back_populates="tags")
    tag: "Tag" = Relationship(back_populates="recordings")

class Tag(BaseDBModel, table=True):
    __tablename__ = "tags"
    name: str = Field(unique=True, index=True)
    color: Optional[str] = Field(default=None, description="Color key for UI display")
    
    recordings: List["RecordingTag"] = Relationship(back_populates="tag")
