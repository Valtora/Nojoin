"""Generating an extra notes set (``NotesOutput``) in ``generate_notes_task``.

An extra set runs the same prompt and LLM path as the meeting's notes, with
three differences: it always uses the structure saved when it was created, it
keeps its own status and error, and it never touches the recording's progress,
the notes index or the meeting's first notes.
"""

import logging
from dataclasses import replace
from typing import Any

from sqlmodel import select

from backend.models.notes_output import NotesOutput
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.glossary_suggestions import (
    GLOSSARY_OUTPUT_KIND,
    glossary_extraction_spec,
    parse_glossary_suggestions,
    suggestions_limit,
)
from backend.utils.meeting_notes import DEFAULT_NOTES_SECTIONS, NotesPromptContext
from backend.utils.notes_templates import ResolvedNotesTemplate
from backend.utils.reprocess_publication import reprocess_replacement_running

logger = logging.getLogger(__name__)


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
    notes_context: NotesPromptContext,
    output: NotesOutput,
    *,
    limit: int = 15,
    extract_more: bool = False,
) -> NotesPromptContext:
    """The prompt context with the set's saved structure in place of the default.

    The saved text is used even when its template was later edited or deleted.
    Like ``build_notes_prompt_context``, the built-in structure is sent as None
    so the prompt stays the shipped one.
    """
    if output.kind == GLOSSARY_OUTPUT_KIND:
        excluded = "\n".join(
            filter(
                None, [notes_context.glossary, output.notes if extract_more else None]
            )
        )
        return replace(
            notes_context,
            output_spec_override=glossary_extraction_spec(limit, excluded),
        )
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


def glossary_output_notes(
    answer: str,
    output: NotesOutput,
    context: NotesPromptContext,
    settings: dict[str, Any],
    extract_more: bool,
) -> str:
    """Validate new entries before appending, preserving prior suggestions."""
    prior = (output.notes or "") if extract_more else ""
    notes = parse_glossary_suggestions(
        answer,
        limit=suggestions_limit(settings),
        excluded="\n".join(filter(None, [context.glossary, prior])),
    )
    return "\n".join(filter(None, [prior, notes]))


def fail_output_generation(session: Any, output: NotesOutput, message: str) -> None:
    output.notes_status = "error"
    output.set_notes_error_message(message)
    session.add(output)
    session.commit()


def prepare_automatic_glossary(
    session: Any, recording: Recording
) -> NotesOutput | None:
    """Publish the optional output before primary completion, without waiting on its LLM."""
    try:
        owner = session.get(User, recording.user_id) if recording.user_id else None
        if (
            owner is None
            or (owner.settings or {}).get("glossary_suggestions_enabled") is not True
        ):
            return None
        # Serialise creation against another successful notes run for this meeting.
        session.exec(
            select(Recording).where(Recording.id == recording.id).with_for_update()
        ).first()
        existing = session.exec(
            select(NotesOutput).where(
                NotesOutput.recording_id == recording.id,
                NotesOutput.kind == GLOSSARY_OUTPUT_KIND,
            )
        ).first()
        if existing is not None:
            return None
        output = NotesOutput(
            recording_id=recording.id,
            kind=GLOSSARY_OUTPUT_KIND,
            name="Glossary",
            notes_status="generating",
        )
        session.add(output)
        session.commit()
        return output
    except Exception:
        session.rollback()
        logger.exception(
            "Automatic glossary preparation failed for recording %s", recording.id
        )
        return None


def generate_automatic_glossary(
    session: Any, recording: Recording, output: NotesOutput | None
) -> None:
    """Generate after primary notes are committed; failures belong to the extra output."""
    if output is None:
        return
    try:
        from .intelligence import _generate_notes

        _generate_notes(session, recording.id, output_id=output.id)
    except Exception:
        session.rollback()
        logger.exception(
            "Automatic glossary extraction failed for recording %s", recording.id
        )
