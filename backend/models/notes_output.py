"""Additional notes; Transcript.notes remains the meeting's primary output."""

from typing import Any

from pydantic import model_validator
from sqlalchemy import JSON, BigInteger, Column, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.utils.generation_claim import (
    LOST_GENERATION_MESSAGE,
    public_generation_metadata,
    reported_generation_status,
)

from .base import BaseDBModel

# A set generated from a notes template. Other kinds of output need no template,
# which is why the template columns are nullable.
NOTES_OUTPUT_KIND_NOTES = "notes"


class NotesOutput(BaseDBModel, table=True):
    __tablename__ = "notes_outputs"

    recording_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("recordings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    kind: str = Field(
        default=NOTES_OUTPUT_KIND_NOTES,
        sa_column=Column(
            String, nullable=False, server_default=NOTES_OUTPUT_KIND_NOTES
        ),
    )
    name: str
    notes: str | None = Field(default=None, sa_column=Column(Text))
    notes_generation_metadata: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(
            JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
        ),
    )
    notes_template_id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("notes_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # The structure saved when the set was created, so a later edit or deletion
    # of its template never changes what Regenerate produces.
    notes_template_sections: str | None = Field(default=None, sa_column=Column(Text))
    notes_status: str = "pending"
    error_message: str | None = Field(default=None, sa_column=Column(Text))

    def set_notes_error_message(self, message: str | None) -> None:
        self.error_message = message


class NotesOutputRead(BaseDBModel):
    kind: str
    name: str
    notes: str | None = None
    notes_generation_metadata: dict[str, Any] | None = None
    notes_template_id: int | None = None
    notes_template_sections: str | None = None
    notes_status: str
    error_message: str | None = None

    @model_validator(mode="after")
    def _public_generation_state(self) -> "NotesOutputRead":
        self.notes_generation_metadata = public_generation_metadata(
            self.notes_generation_metadata
        )
        if reported_generation_status(self.notes_status, self.updated_at) == "error":
            self.notes_status = "error"
            self.error_message = self.error_message or LOST_GENERATION_MESSAGE
        return self
