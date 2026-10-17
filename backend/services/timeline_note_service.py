"""Reading a recording's timeline notes for the API and the LLM prompts.

Placement and prompt text are pure and live in ``backend.utils.timeline_notes``;
this module only loads the rows and the authors' names, for a sync session (the
workers) or an async one (the API).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, col, select

from backend.models.timeline_note import TimelineNote
from backend.models.timeline_note_public import TimelineNoteAuthorRead, TimelineNoteRead
from backend.models.user import User
from backend.utils.timeline_notes import (
    NotePlacement,
    place_timeline_notes,
    render_notes_as_lines,
    render_notes_list_context,
    render_user_notes_context,
)


def _notes_statement(recording_id: int):
    return (
        select(TimelineNote)
        .where(TimelineNote.recording_id == recording_id)
        .order_by(col(TimelineNote.created_at), col(TimelineNote.id))
    )


def _authors_statement(notes: Iterable[TimelineNote]):
    author_ids = {
        note.created_by_user_id for note in notes if note.created_by_user_id is not None
    }
    if not author_ids:
        return None
    return select(User.id, User.username).where(col(User.id).in_(author_ids))


@dataclass(frozen=True)
class RecordingTimelineNotes:
    """One recording's notes and their authors' names, ready to place."""

    notes: Sequence[TimelineNote] = ()
    author_names: Mapping[int, str] = field(default_factory=dict)

    def place(self, segments: Sequence[Mapping]) -> list[NotePlacement[TimelineNote]]:
        return place_timeline_notes(segments, self.notes)

    def in_timeline_order(self) -> list[NotePlacement[TimelineNote]]:
        # The order of notes does not depend on the transcript, only on their
        # times, so it can be had without loading one.
        return self.place(())

    def user_notes_context(self) -> Optional[str]:
        """The prompt section for a transcript rendered with its body notes."""
        return render_user_notes_context(self.in_timeline_order())

    def user_notes_list(self) -> Optional[str]:
        """The prompt section listing every note, for a transcript without them."""
        return render_notes_list_context(self.in_timeline_order())

    def appendix_lines(self) -> list[str]:
        """The lines of the ``## User Notes`` section added to generated notes."""
        return render_notes_as_lines(self.in_timeline_order())


def load_recording_timeline_notes(
    session: Session, recording_id: int
) -> RecordingTimelineNotes:
    """Load a recording's notes with a sync (worker) session."""
    notes = list(session.exec(_notes_statement(recording_id)).all())
    statement = _authors_statement(notes)
    authors = dict(session.exec(statement).all()) if statement is not None else {}
    return RecordingTimelineNotes(notes=notes, author_names=authors)


async def load_author_names_async(
    db: AsyncSession, notes: Iterable[TimelineNote]
) -> dict[int, str]:
    statement = _authors_statement(notes)
    return dict((await db.execute(statement)).all()) if statement is not None else {}


async def load_recording_timeline_notes_async(
    db: AsyncSession, recording_id: int
) -> RecordingTimelineNotes:
    """Load a recording's notes with an async session."""
    notes = list((await db.execute(_notes_statement(recording_id))).scalars().all())
    return RecordingTimelineNotes(
        notes=notes, author_names=await load_author_names_async(db, notes)
    )


def serialize_timeline_note(
    note: TimelineNote,
    *,
    recording_public_id: str,
    author_names: Mapping[int, str],
) -> TimelineNoteRead:
    author_id = note.created_by_user_id
    author_name = author_names.get(author_id) if author_id is not None else None
    return TimelineNoteRead(
        id=note.public_id,
        recording_id=recording_public_id,
        kind=note.kind,
        body=note.body,
        anchor_ms=note.anchor_ms,
        start_ms=note.start_ms,
        end_ms=note.end_ms,
        quoted_text=note.quoted_text,
        created_by=(
            TimelineNoteAuthorRead(id=author_id, username=author_name)
            if author_id is not None and author_name is not None
            else None
        ),
        revision=note.revision,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def serialize_recording_timeline_notes(
    loaded: RecordingTimelineNotes, *, recording_public_id: str
) -> list[TimelineNoteRead]:
    """Every note of a recording, in timeline order."""
    return [
        serialize_timeline_note(
            placement.note,
            recording_public_id=recording_public_id,
            author_names=loaded.author_names,
        )
        for placement in loaded.in_timeline_order()
    ]
