from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Column, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from backend.processing.text_embedding_version import (
    TEXT_EMBEDDING_DIMENSIONS,
    TEXT_EMBEDDING_VERSION,
)

from .base import BaseDBModel

if TYPE_CHECKING:
    from .document import Document
    from .recording import Recording


class ContextChunk(BaseDBModel, table=True):
    __tablename__ = "context_chunks"

    recording_id: int = Field(
        sa_column=Column(
            BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), index=True
        )
    )
    document_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), index=True
        ),
    )
    # The page this chunk was cut from, when the source is a document. A page
    # that fits the embedding window is a single chunk; an oversized one (a
    # large spreadsheet sheet) becomes several, and they all point back here so
    # retrieval can return the whole page rather than the fragment that matched.
    document_page_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger, ForeignKey("document_pages.id", ondelete="CASCADE"), index=True
        ),
    )

    content: str = Field(sa_column=Column(Text))
    embedding: List[float] = Field(sa_column=Column(Vector(TEXT_EMBEDDING_DIMENSIONS)))

    # Which model produced `embedding`. Cosine distance is only meaningful
    # between vectors of the same version, so every search filters on this and
    # a model change becomes an explicit rebuild rather than silent nonsense.
    embedding_version: int = Field(default=TEXT_EMBEDDING_VERSION, index=True)

    # Metadata for filter/context (start_time, end_time, page_number, speaker, etc.)
    meta: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))

    # Relationships
    recording: "Recording" = Relationship(back_populates="context_chunks")
    document: Optional["Document"] = Relationship(back_populates="chunks")
