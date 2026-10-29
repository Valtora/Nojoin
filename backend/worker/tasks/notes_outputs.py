"""Notes output prompts, persistence and automatic dispatch.

An extra set runs the same prompt and LLM path as the meeting's notes, with
three differences: it always uses the structure saved when it was created, it
keeps its own status and error, and it never touches the recording's progress,
the notes index or the meeting's first notes.
"""

import logging
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from backend.celery_app import celery_app
from backend.models.notes_output import NotesOutput
from backend.models.transcript import Transcript
from backend.utils.meeting_notes import DEFAULT_NOTES_SECTIONS, NotesPromptContext
from backend.utils.notes_generation import (
    claim_notes_generation,
    fail_notes_generation,
)
from backend.utils.notes_templates import ResolvedNotesTemplate
from backend.utils.reprocess_publication import reprocess_replacement_running

logger = logging.getLogger(__name__)


def dispatch_automatic_notes(
    session: Session, transcript: Transcript, request_id: str
) -> bool:
    """A queue failure belongs to notes and must not fail final publication."""
    try:
        celery_app.send_task(
            "backend.worker.tasks.generate_meeting_intelligence_task",
            args=[transcript.recording_id],
            kwargs={"request_id": request_id},
        )
        return True
    except Exception as exc:
        logger.exception(
            "Could not queue meeting intelligence for recording %s",
            transcript.recording_id,
        )
        fail_notes_generation(
            session,
            transcript,
            request_id,
            str(exc) or "Meeting notes could not be queued.",
        )
        return False


def load_output_for_generation(
    session: Any, transcript: Transcript, output_id: int, request_id: str | None
) -> NotesOutput | None:
    """The set to generate, or None when the run must not go ahead."""
    output = session.get(NotesOutput, output_id)
    if output is None or output.recording_id != transcript.recording_id:
        return None
    # The API refuses both; a task queued before either lands here.
    if transcript.transcription_failed() or reprocess_replacement_running(transcript):
        request_id = request_id or claim_notes_generation(session, output)
        fail_notes_generation(
            session, output, request_id, "Transcript is being replaced or has failed."
        )
        return None
    return output


def output_notes_context(
    notes_context: NotesPromptContext, output: NotesOutput
) -> NotesPromptContext:
    """The prompt context with the set's saved structure in place of the default.

    The saved text is used even when its template was later edited or deleted.
    Like ``build_notes_prompt_context``, the built-in structure is sent as None
    so the prompt stays the shipped one.
    """
    template = ResolvedNotesTemplate(
        sections=output.notes_template_sections or DEFAULT_NOTES_SECTIONS,
        name=output.name,
    )
    return replace(
        notes_context,
        notes_sections=template.sections if template.is_custom else None,
    )


def finish_output_generation(session: Any, output: NotesOutput, notes: str) -> None:
    output.notes = notes
    output.notes_status = "completed"
    output.set_notes_error_message(None)
    session.add(output)
    session.commit()
