from typing import Optional, List, Any, Dict, TYPE_CHECKING
from sqlmodel import Field, Relationship
from sqlalchemy import BigInteger, Column, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector
from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording
    from .document import Document

class ContextChunk(BaseDBModel, table=True):
    __tablename__ = "context_chunks"
    
    recording_id: int = Field(sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), index=True))
    document_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), index=True))
    
    content: str = Field(sa_column=Column(Text))
    embedding: List[float] = Field(sa_column=Column(Vector(384))) # 384 dim for all-MiniLM-L6-v2
    
    # Metadata for filter/context (start_time, end_time, page_number, speaker, etc.)
    meta: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    
    # Relationships
    recording: "Recording" = Relationship(back_populates="context_chunks")
    document: Optional["Document"] = Relationship(back_populates="chunks")
