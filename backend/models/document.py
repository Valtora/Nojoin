from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Column, ForeignKey, Text
from sqlmodel import Field, Relationship

from .base import BaseDBModel

if TYPE_CHECKING:
    from .context_chunk import ContextChunk
    from .document_page import DocumentPage
    from .recording import Recording


class DocumentStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    ERROR = "ERROR"


class DocumentParseMode(str, Enum):
    """What the uploader asked for, not what happened.

    ``VISUAL`` is the default: pages are rendered and sent to a vision-capable
    model so charts, diagrams and scanned pages survive. ``STRUCTURAL`` is the
    per-upload opt-out, and is also where a visual parse lands when no vision
    model is reachable -- in that case the requested mode stays ``VISUAL`` and
    ``parse_warning`` explains the downgrade, so the user can retry after
    fixing their model rather than being told the document simply failed.
    """

    VISUAL = "VISUAL"
    STRUCTURAL = "STRUCTURAL"


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
    # Recorded at upload rather than stat()-ed on read: the documents list is a
    # hot path and the file may have been removed from disk since.
    file_size_bytes: Optional[int] = Field(default=None, sa_column=Column(BigInteger))

    status: DocumentStatus = Field(default=DocumentStatus.PENDING)
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Requested parse mode. See DocumentParseMode: this is the request, and
    # `pages.parse_mode` records what each page actually got.
    parse_mode: DocumentParseMode = Field(default=DocumentParseMode.VISUAL)

    # Non-fatal degradation, surfaced on the document card. A document with a
    # warning is still READY and still searchable.
    parse_warning: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Progress, so a long parse reports something better than a spinner.
    # `page_count` is None until the format has been opened and counted.
    page_count: Optional[int] = Field(default=None)
    pages_parsed: int = Field(default=0)
    # Which phase the parse is in. Page counts alone are ambiguous: they reach
    # "7 of 7" while indexing is still running, which reads as a hang. Short
    # enough to render verbatim in the UI.
    parse_stage: Optional[str] = Field(default=None)

    # Relationships
    recording: "Recording" = Relationship(back_populates="documents")
    pages: List["DocumentPage"] = Relationship(
        back_populates="document",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "DocumentPage.page_number",
        },
    )
    chunks: List["ContextChunk"] = Relationship(
        back_populates="document",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


# Registers DocumentPage on the SQLModel class registry, which is what lets the
# string reference in the `pages` relationship above resolve.
#
# Not redundant with backend/models/registry.py: that module is imported by
# init_db and the tests, but NOT by the API process, which reaches Document
# through recording_public and would otherwise configure its mappers with
# DocumentPage unknown. Safe from circularity because document_page imports
# this module only under TYPE_CHECKING.
from .document_page import DocumentPage  # noqa: E402,F401
