"""Notes a user writes on a recording, anchored to its audio timeline.

A note hangs off the recording, never the transcript or an utterance: the final
pass rebuilds utterances and a reprocess deletes the transcript row, and a note
must outlive both. Its anchor is in milliseconds on the recording's audio
timeline, the clock ``TranscriptUtterance.start_ms`` runs on, so placing it
among the lines (``backend.utils.timeline_notes``) is a pure function of the
note and whatever transcript exists at the time.

Four kinds, distinguished by which anchor columns are set:

* ``point``: one moment, ``anchor_ms``.
* ``selection``: a span of words, ``start_ms``/``end_ms``, with
  ``quoted_text`` the words as the user saw them. The quote is a snapshot and is
  never re-resolved against a later transcript.
* ``head``: about the meeting as a whole, before the transcript. The textarea
  shown while a meeting records or processes edits the earliest head note.
* ``tail``: added after the transcript, such as by an assistant over MCP.

``kind`` is a string checked by a constraint rather than a native enum, so a
new kind is a constraint swap rather than an ``ALTER TYPE``.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlmodel import Field, SQLModel

from backend.models.base import BaseDBModel
from backend.models.pipeline import generate_pipeline_public_id
from backend.utils.time import utc_now


class TimelineNoteKind(str, Enum):
    POINT = "point"
    SELECTION = "selection"
    HEAD = "head"
    TAIL = "tail"


TIMELINE_NOTE_KIND_CHECK = "kind IN ('point', 'selection', 'head', 'tail')"

# The anchor columns each kind sets, and only those. The API validates the same
# shape (backend.models.timeline_note_public) so a bad write is a 422, not a
# constraint error.
TIMELINE_NOTE_SHAPE_CHECK = (
    "(kind = 'point' AND anchor_ms IS NOT NULL AND anchor_ms >= 0"
    " AND start_ms IS NULL AND end_ms IS NULL AND quoted_text IS NULL)"
    " OR (kind = 'selection' AND anchor_ms IS NULL"
    " AND start_ms IS NOT NULL AND end_ms IS NOT NULL"
    " AND start_ms >= 0 AND start_ms < end_ms AND quoted_text IS NOT NULL)"
    " OR (kind IN ('head', 'tail') AND anchor_ms IS NULL"
    " AND start_ms IS NULL AND end_ms IS NULL AND quoted_text IS NULL)"
)


class TimelineNote(BaseDBModel, table=True):
    __tablename__ = "timeline_notes"
    __table_args__ = (
        CheckConstraint(TIMELINE_NOTE_KIND_CHECK, name="ck_timeline_notes_kind"),
        CheckConstraint(TIMELINE_NOTE_SHAPE_CHECK, name="ck_timeline_notes_shape"),
    )

    public_id: str = Field(
        default_factory=generate_pipeline_public_id,
        sa_column=Column(
            String(36),
            unique=True,
            index=True,
            nullable=False,
            default=generate_pipeline_public_id,
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
    # Attribution only: every read is scoped by the recording's owner. Kept
    # through a deleted account so the note itself survives.
    created_by_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    kind: str = Field(sa_column=Column(String(16), nullable=False))
    body: str = Field(sa_column=Column(Text, nullable=False))
    anchor_ms: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    start_ms: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    end_ms: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    quoted_text: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    # Bumped by every edit; a write naming an older revision is refused (409),
    # so two devices editing one note cannot silently overwrite each other.
    revision: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, default=1, server_default="1"),
    )
    # The client-chosen token of the write that made this revision. A client
    # that finds its own token here knows the note is as its write left it.
    # Empty for a write that named none: the migration, the MCP tool.
    last_write_token: Optional[str] = Field(
        default=None, sa_column=Column(String(36), nullable=True)
    )


class TimelineNoteTombstone(SQLModel, table=True):
    """A deleted note's id, so a write still in flight for it learns its fate.

    A delete removes the note and adds its tombstone in one transaction. A
    create retried under the id, or an edit or delete of it, then hears
    whether the delete was made with its own write token or someone else's,
    instead of re-creating the note or reading as a plain 404. Readers of
    notes never see tombstones: they are a table of their own. A delete with a
    token of an id no note has held leaves one too (``never_held``), so a
    create whose answer was lost cannot land after it. Those are kept only as
    long as such a create can still be in transit, and a recording holds only
    a few of them at a time (``routes_timeline_notes``). A public id names at
    most one note ever while its recording exists, so a tombstone's id is
    unique.
    """

    __tablename__ = "timeline_note_tombstones"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=BigInteger)
    public_id: str = Field(
        sa_column=Column(String(36), unique=True, index=True, nullable=False)
    )
    recording_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("recordings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    deleted_write_token: Optional[str] = Field(
        default=None, sa_column=Column(String(36), nullable=True)
    )
    deleted_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(DateTime, nullable=False)
    )
    # Left by a delete of an id no note held: it only refuses a create still
    # in transit, so it is purged once none can be.
    never_held: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
