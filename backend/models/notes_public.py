"""The canonical lifecycle returned by notes mutations, without another read."""

from uuid import UUID

from pydantic import BaseModel

from backend.models.transcript import Transcript
from backend.utils.generation_claim import (
    GENERATING,
    reported_generation_error,
    reported_generation_status,
)


class NotesRunPublicRead(BaseModel):
    status: str = "success"
    notes_status: str
    notes_run_id: UUID | None = None
    notes_progress: str | None = None
    notes: str | None = None
    error_message: str | None = None


class GenerateNotesResponse(NotesRunPublicRead):
    message: str = "Note generation started"


class CancelNotesRequest(BaseModel):
    notes_run_id: UUID


def notes_run_state(transcript: Transcript) -> NotesRunPublicRead:
    """Hide the identity and step of an expired or terminal run on every response."""
    status = reported_generation_status(transcript.notes_status, transcript.updated_at)
    return NotesRunPublicRead(
        notes_status=status,
        notes_run_id=transcript.notes_run_id if status == GENERATING else None,
        notes_progress=transcript.notes_progress if status == GENERATING else None,
        notes=transcript.notes,
        error_message=(
            transcript.error_message
            if transcript.transcription_failed()
            else reported_generation_error(
                transcript.notes_status, transcript.updated_at, transcript.error_message
            )
        ),
    )
