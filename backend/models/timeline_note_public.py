"""Request and response models for timeline notes.

Mirrored in ``frontend/src/types/index.ts``. Creation validates the same
per-kind shape the database enforces (``TIMELINE_NOTE_SHAPE_CHECK``), so a bad
request is a 422 naming the problem rather than a constraint error.

Every write may carry a client-chosen ``write_token``. The note keeps the token
of the write that made its current revision (``last_write_token``), and a
delete keeps it on the note's tombstone, so a client that lost a response can
tell its own write from another device's by comparing tokens.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.models.timeline_note import TimelineNoteKind

TIMELINE_NOTE_BODY_MAX_CHARS = 50_000
TIMELINE_NOTE_QUOTE_MAX_CHARS = 2_000
# The largest integer a browser holds exactly; the frontend does arithmetic on
# these values.
TIMELINE_MS_MAX = 2**53 - 1


class TimelineNoteErrorCode(str, Enum):
    """The ``code`` of a refused note write, which clients branch on.

    Pinned with the frontend by ``backend/tests/fixtures/timeline_note_error_codes.json``.
    """

    RECORDING_NOT_FOUND = "recording_not_found"
    NOTE_NOT_FOUND = "note_not_found"
    # The note was deleted by a write carrying this request's token.
    NOTE_DELETED_BY_YOU = "note_deleted_by_you"
    # The note was deleted by a write carrying another token, or none.
    NOTE_DELETED_ELSEWHERE = "note_deleted_elsewhere"
    REVISION_CONFLICT = "revision_conflict"
    NOTE_ID_IN_USE = "note_id_in_use"
    # An edit moved a note that is not a point note.
    NOTE_NOT_MOVABLE = "note_not_movable"
    LINE_SETTLING = "line_settling"
    LINE_STALE = "line_stale"


def _clean_body(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("body must not be empty")
    if len(cleaned) > TIMELINE_NOTE_BODY_MAX_CHARS:
        raise ValueError(
            f"body must be at most {TIMELINE_NOTE_BODY_MAX_CHARS} characters"
        )
    return cleaned


def _validate_point_span(
    anchor_ms: int | None, start_ms: int | None, end_ms: int | None
) -> None:
    if start_ms is None and end_ms is None:
        return
    if start_ms is None or end_ms is None or start_ms != anchor_ms or start_ms > end_ms:
        raise ValueError("a point span needs start_ms = anchor_ms <= end_ms")


class TimelineNoteAuthorRead(BaseModel):
    id: int
    username: str


class NoteAttachmentRead(BaseModel):
    """The file a note holds. ``id`` is its document's, for download and preview."""

    id: int
    title: str
    file_type: str
    file_size_bytes: Optional[int] = None
    # The document's parse status: PENDING until it is first switched on.
    status: str
    error_message: Optional[str] = None
    parse_warning: Optional[str] = None
    use_in_notes: bool
    # Whether the parser reads its format, so it can be switched on. Any other
    # file is kept to download only.
    readable: bool
    # Whether GET /documents/{id}/preview draws it.
    is_image: bool


class NoteAttachmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_in_notes: bool


class TimelineNoteRead(BaseModel):
    id: str
    recording_id: str
    kind: TimelineNoteKind
    body: str
    anchor_ms: Optional[int] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    quoted_text: Optional[str] = None
    created_by: Optional[TimelineNoteAuthorRead] = None
    revision: int
    # The token of the write that made this revision, if it named one.
    last_write_token: Optional[str] = None
    attachment: Optional[NoteAttachmentRead] = None
    created_at: datetime
    updated_at: datetime


class NoteFileRemovedRead(BaseModel):
    """What removing a note's file left: the note without it, or null when it went too."""

    note: Optional[TimelineNoteRead] = None


class SourceUtteranceReference(BaseModel):
    """The source line as the person saw it, checked only when acting on it."""

    id: str
    revision: int = Field(ge=1)
    word_ids: Optional[list[str]] = Field(default=None, min_length=1)
    # Complete captured token wording, separate from a possibly partial quote.
    # Required to prove selected words survived an older source revision.
    word_texts: Optional[dict[str, str]] = None


class TimelineNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Chosen by the client so a retried create cannot make a second note: the
    # same id for the same recording returns the note it already made.
    id: Optional[UUID] = None
    write_token: Optional[UUID] = None
    kind: TimelineNoteKind
    body: str
    anchor_ms: Optional[int] = Field(default=None, ge=0, le=TIMELINE_MS_MAX)
    start_ms: Optional[int] = Field(default=None, ge=0, le=TIMELINE_MS_MAX)
    end_ms: Optional[int] = Field(default=None, ge=0, le=TIMELINE_MS_MAX)
    quoted_text: Optional[str] = None
    source_utterances: Optional[list[SourceUtteranceReference]] = None
    target_utterance_id: Optional[str] = None
    target_utterance_revision: Optional[int] = Field(default=None, ge=1)

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        return _clean_body(value)

    @field_validator("quoted_text")
    @classmethod
    def _quote(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("quoted_text must not be empty")
        if len(cleaned) > TIMELINE_NOTE_QUOTE_MAX_CHARS:
            raise ValueError(
                f"quoted_text must be at most {TIMELINE_NOTE_QUOTE_MAX_CHARS} characters"
            )
        return cleaned

    @model_validator(mode="after")
    def _target_reference(self) -> "TimelineNoteCreate":
        if (self.target_utterance_id is None) != (
            self.target_utterance_revision is None
        ):
            raise ValueError(
                "target_utterance_id and target_utterance_revision must be supplied together"
            )
        return self

    @model_validator(mode="after")
    def _shape(self) -> "TimelineNoteCreate":
        point = self.kind == TimelineNoteKind.POINT
        selection = self.kind == TimelineNoteKind.SELECTION
        if self.target_utterance_id is not None and not point:
            raise ValueError("a destination line is for a point note only")
        if self.source_utterances is not None and not selection:
            raise ValueError("source_utterances are for a selection note only")
        if (self.anchor_ms is not None) != point:
            raise ValueError("anchor_ms is required for a point note and only for one")
        has_range = self.start_ms is not None or self.end_ms is not None
        if point:
            _validate_point_span(self.anchor_ms, self.start_ms, self.end_ms)
        if self.quoted_text is not None and not selection:
            raise ValueError("quoted_text is for a selection note only")
        if has_range and not (point or selection):
            raise ValueError(
                "start_ms and end_ms are for a point or selection note only"
            )
        if selection:
            if self.start_ms is None or self.end_ms is None or self.quoted_text is None:
                raise ValueError(
                    "a selection note needs start_ms, end_ms and quoted_text"
                )
            if self.start_ms >= self.end_ms:
                raise ValueError("start_ms must be before end_ms")
        return self


class TimelineNoteUpdate(BaseModel):
    """An edit. The kind, a selection's range and its quote never change."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    write_token: Optional[UUID] = None
    body: Optional[str] = None
    # Moves a point note (B4 drags it); refused for any other kind.
    anchor_ms: Optional[int] = Field(default=None, ge=0, le=TIMELINE_MS_MAX)
    target_utterance_id: Optional[str] = None
    target_utterance_revision: Optional[int] = Field(default=None, ge=1)

    @field_validator("body")
    @classmethod
    def _body(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _clean_body(value)

    @model_validator(mode="after")
    def _changes_something(self) -> "TimelineNoteUpdate":
        if (self.target_utterance_id is None) != (
            self.target_utterance_revision is None
        ):
            raise ValueError(
                "target_utterance_id and target_utterance_revision must be supplied together"
            )
        if self.target_utterance_id is not None and self.anchor_ms is None:
            raise ValueError("a destination line requires anchor_ms")
        if self.body is None and self.anchor_ms is None:
            raise ValueError("an update needs body or anchor_ms")
        return self
