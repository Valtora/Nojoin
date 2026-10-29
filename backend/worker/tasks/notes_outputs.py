"""Generating an extra notes set (``NotesOutput``) in ``generate_notes_task``.

An extra set runs the same prompt and LLM path as the meeting's notes, with
three differences: it always uses the structure saved when it was created, it
keeps its own status and error, and it never touches the recording's progress,
the notes index or the meeting's first notes.
"""

from dataclasses import replace
from typing import Any

from backend.models.notes_output import NotesOutput
from backend.models.transcript import Transcript
from backend.utils.meeting_notes import DEFAULT_NOTES_SECTIONS, NotesPromptContext
from backend.utils.notes_templates import ResolvedNotesTemplate
from backend.utils.reprocess_publication import reprocess_replacement_running


def load_output_for_generation(
    session: Any, transcript: Transcript, output_id: int
) -> NotesOutput | None:
    """The set to generate, or None when the run must not go ahead."""
    output = session.get(NotesOutput, output_id)
    if output is None or output.recording_id != transcript.recording_id:
        return None
    # The API refuses both; a task queued before either lands here.
    if transcript.transcription_failed() or reprocess_replacement_running(transcript):
        fail_output_generation(
            session, output, "Transcript is being replaced or has failed."
        )
        return None
    return output


def start_output_generation(session: Any, output: NotesOutput) -> None:
    output.notes_status = "generating"
    output.set_notes_error_message(None)
    session.add(output)
    session.commit()


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


def fail_output_generation(session: Any, output: NotesOutput, message: str) -> None:
    output.notes_status = "error"
    output.set_notes_error_message(message)
    session.add(output)
    session.commit()
