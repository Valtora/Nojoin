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


def _clean_body(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("body must not be empty")
    if len(cleaned) > TIMELINE_NOTE_BODY_MAX_CHARS:
        raise ValueError(
            f"body must be at most {TIMELINE_NOTE_BODY_MAX_CHARS} characters"
        )
    return cleaned


class TimelineNoteAuthorRead(BaseModel):
    id: int
    username: str


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
    created_at: datetime
    updated_at: datetime


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
    def _shape(self) -> "TimelineNoteCreate":
        point = self.kind == TimelineNoteKind.POINT
        selection = self.kind == TimelineNoteKind.SELECTION
        if (self.anchor_ms is not None) != point:
            raise ValueError("anchor_ms is required for a point note and only for one")
        has_range = (
            self.start_ms is not None
            or self.end_ms is not None
            or self.quoted_text is not None
        )
        if has_range != selection:
            raise ValueError(
                "start_ms, end_ms and quoted_text are for a selection note only"
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

    @field_validator("body")
    @classmethod
    def _body(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _clean_body(value)

    @model_validator(mode="after")
    def _changes_something(self) -> "TimelineNoteUpdate":
        if self.body is None and self.anchor_ms is None:
            raise ValueError("an update needs body or anchor_ms")
        return self
