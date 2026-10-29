"""Additional notes; Transcript.notes remains the meeting's primary output."""

from sqlalchemy import BigInteger, Column, ForeignKey, Text
from sqlmodel import Field

from .base import BaseDBModel


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
    name: str
    notes: str | None = Field(default=None, sa_column=Column(Text))
    notes_template_id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("notes_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    notes_template_sections: str = Field(sa_column=Column(Text, nullable=False))
    notes_status: str = "pending"
    error_message: str | None = Field(default=None, sa_column=Column(Text))

    def set_notes_error_message(self, message: str | None) -> None:
        self.error_message = message


class NotesOutputRead(BaseDBModel):
    name: str
    notes: str | None = None
    notes_template_id: int | None = None
    notes_template_sections: str
    notes_status: str
    error_message: str | None = None
