"""Naming acts on a recording's speakers, anchored to the time they were given for.

Each row says who named someone (a self-introduction, the LLM, a voiceprint
match or the user), how much that source counts, and the span of the meeting
the name was given for. Rows are only ever added: a later rename, Confirm or
Dismiss writes a row that supersedes the earlier one, so the table is also the
audit trail of how each speaker got its name.

The final pass reads the rows still standing and gives each span to the final
speaker holding most of it (``backend/utils/speaker_names/resolve.py``), so a
name follows the voice it was given for even when re-clustering changes the
labels. The name a speaker shows is still the one on ``recording_speakers``.
"""

from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from .base import BaseDBModel


def generate_speaker_name_event_public_id() -> str:
    return str(uuid4())


class SpeakerNameEvent(BaseDBModel, table=True):
    __tablename__ = "speaker_name_events"
    __table_args__ = (
        CheckConstraint(
            "anchor_end_ms > anchor_start_ms",
            name="ck_speaker_name_events_anchor_span",
        ),
        Index(
            "ix_speaker_name_events_recording_anchor",
            "recording_id",
            "anchor_start_ms",
        ),
    )

    public_id: str = Field(
        default_factory=generate_speaker_name_event_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            nullable=False,
            default=generate_speaker_name_event_public_id,
        ),
    )
    recording_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("recordings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    # "name", or "dismiss" for a name the user said was wrong. A dismissal keeps
    # the name it dismissed, so the audit and the check that keeps it from being
    # suggested again survive the machine row it superseded being re-derived.
    kind: str = Field(sa_column=Column(String(16), nullable=False))
    name: str = Field(sa_column=Column(String(255), nullable=False))
    global_speaker_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger, ForeignKey("global_speakers.id", ondelete="SET NULL")
        ),
    )
    # heuristic, llm, voiceprint, auto (confirmed by the setting) or user.
    source: str = Field(sa_column=Column(String(16), nullable=False))
    # The source's rank when the row was written (backend/utils/speaker_names).
    authority: int = Field(sa_column=Column(SmallInteger, nullable=False))
    # api, mcp or worker.
    surface: Optional[str] = Field(default=None, sa_column=Column(String(16)))
    anchor_start_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    anchor_end_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    # The label holding the span when the row was written; audit only.
    anchor_label: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    recording_speaker_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("recording_speakers.id", ondelete="SET NULL"),
            index=True,
        ),
    )
    supersedes_event_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("speaker_name_events.id", ondelete="SET NULL"),
            index=True,
        ),
    )
    confidence: Optional[float] = None
    evidence: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    created_by_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL")),
    )
