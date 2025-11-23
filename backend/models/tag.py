from typing import List, TYPE_CHECKING
from sqlmodel import Field, Relationship
from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording

class RecordingTag(BaseDBModel, table=True):
    __tablename__ = "recording_tags"
    recording_id: int = Field(foreign_key="recordings.id")
    tag_id: int = Field(foreign_key="tags.id")
    
    recording: "Recording" = Relationship(back_populates="tags")
    tag: "Tag" = Relationship(back_populates="recordings")

class Tag(BaseDBModel, table=True):
    __tablename__ = "tags"
    name: str = Field(unique=True, index=True)
    
    recordings: List["RecordingTag"] = Relationship(back_populates="tag")
