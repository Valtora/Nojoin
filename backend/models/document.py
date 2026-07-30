from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Column, ForeignKey, Text
from sqlmodel import Field, Relationship

from backend.utils.time import utc_now

from .base import BaseDBModel

# How long a PROCESSING document may go without a write before it is treated as
# abandoned. Generous on purpose: the longest legitimate gap is a single batch
# of vision calls, and a false positive only permits a redundant re-parse while
# a false negative wedges the document permanently.
STALLED_PARSE_AFTER = timedelta(minutes=10)

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


def parse_looks_stalled(document: Document) -> bool:
    """Whether a PROCESSING document's worker appears to have died.

    Computed on the server and sent to the client as a boolean, never derived
    in the browser. ``updated_at`` is stored and serialised without a timezone,
    so JavaScript parses it as local time and any client outside UTC gets an age
    wrong by its whole offset -- which showed every in-flight parse as stalled.
    Server-side, this also cannot disagree with the re-parse endpoint, because
    that endpoint gates on this same function.
    """
    if document.status != DocumentStatus.PROCESSING:
        return False
    last_write = document.updated_at or document.created_at
    if last_write is None:
        return True
    return (utc_now() - last_write) > STALLED_PARSE_AFTER
