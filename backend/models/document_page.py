from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Column, ForeignKey, Text, UniqueConstraint
from sqlmodel import Field, Relationship

from .base import BaseDBModel

if TYPE_CHECKING:
    from .document import Document


class PageParseMode(str, Enum):
    """How a single page's content was actually produced.

    Recorded per page rather than per document because escalation is decided
    page by page: a deck can have twelve structurally-parsed slides and three
    that went to the vision model, and the document card reports both.
    """

    STRUCTURAL = "STRUCTURAL"
    VISUAL = "VISUAL"


class DocumentPage(BaseDBModel, table=True):
    """One page, slide, sheet or section of an uploaded document.

    Rows are written as each page completes rather than in one batch at the
    end, so a parse interrupted by a worker restart resumes from the first
    missing page instead of repeating vision calls that were already paid for.
    """

    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_page_number"),
    )

    document_id: int = Field(
        sa_column=Column(
            BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), index=True
        )
    )

    # 1-based, matching what a user sees in a PDF viewer or slide sorter.
    page_number: int = Field(index=True)

    # The page's own heading where the format exposes one (a slide title, a
    # sheet name). Used to label retrieved context and citations.
    title: Optional[str] = Field(default=None)

    # Markdown. Empty for a page that genuinely holds no extractable content,
    # which is different from a page that failed -- that one carries an error.
    content: str = Field(default="", sa_column=Column(Text))

    parse_mode: PageParseMode = Field(default=PageParseMode.STRUCTURAL)

    # Set when this page alone failed. The parse continues; the document stays
    # usable and the card reports which pages are missing.
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))

    document: "Document" = Relationship(back_populates="pages")
