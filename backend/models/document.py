from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Column, ForeignKey, Text
from sqlmodel import Field, Relationship

from .base import BaseDBModel

if TYPE_CHECKING:
    from .context_chunk import ContextChunk
    from .recording import Recording


class DocumentStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    ERROR = "ERROR"


class Document(BaseDBModel, table=True):
    __tablename__ = "documents"

    recording_id: int = Field(
        sa_column=Column(
            BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), index=True
        )
    )

    title: str = Field(index=True)
    file_path: str = Field(unique=True)  # Path to the file on disk
    file_type: str = Field(default="text/plain")  # mime type

    status: DocumentStatus = Field(default=DocumentStatus.PENDING)
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Relationships
    recording: "Recording" = Relationship(back_populates="documents")
    chunks: List["ContextChunk"] = Relationship(
        back_populates="document",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
