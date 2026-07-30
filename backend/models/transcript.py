from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import BigInteger, Column, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording


class Transcript(BaseDBModel, table=True):
    __tablename__ = "transcripts"

    recording_id: int = Field(
        sa_column=Column(
            BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"), unique=True
        )
    )

    text: Optional[str] = None
    segments: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSONB))
    notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    user_notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    meeting_edge_focus: Optional[str] = Field(default=None, sa_column=Column(Text))
    meeting_edge_payload: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JSONB)
    )
    meeting_edge_status: str = Field(default="idle")
    meeting_edge_error_message: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )
    meeting_edge_source_signature: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )
    speaker_name_suggestions: List[Dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB)
    )
    # Provenance for the notes above: which template produced them, plus a
    # verbatim snapshot of the structure text used. The snapshot is what makes
    # the record honest -- a template can be edited or deleted after the fact,
    # so the id alone would eventually describe text that never ran.
    notes_template_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("notes_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    notes_template_sections: Optional[str] = Field(default=None, sa_column=Column(Text))
    notes_status: str = Field(
        default="pending"
    )  # pending, generating, completed, error
    # Set when a document finishes parsing after the notes were generated, so
    # the notes no longer reflect everything attached to the meeting. Only a
    # prompt to regenerate: regenerating costs a real LLM call on the user's
    # own quota and overwrites any hand edits, so it is never automatic.
    notes_stale_documents: bool = Field(default=False)
    transcript_status: str = Field(
        default="pending"
    )  # pending, processing, completed, error
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))

    recording: "Recording" = Relationship(back_populates="transcript")
