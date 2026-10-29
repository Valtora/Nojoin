"""The notes-structure generator: a skill the LLM applies on the user's behalf.

The user describes the meetings they run; the model writes a notes structure for
them. It is a narrow, well-bounded job, so the prompt spends most of its length
telling the model what it must *not* produce -- the parts of the notes prompt the
application owns (fidelity rules, table syntax, the transcript, the response
contract). A generated structure that restates those is not wrong so much as
wasted, and one that contradicts them would be silently overridden anyway.

The model returns JSON so the name, description and structure land in the right
fields; the parser is deliberately tolerant of fenced or prose-wrapped output,
matching the other structured paths in this codebase.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from backend.prompts import render_prompt
from backend.utils.meeting_notes import (
    default_notes_sections,
)
from backend.utils.notes_templates import (
    MAX_NOTES_SECTIONS_LENGTH,
    MAX_NOTES_TEMPLATE_DESCRIPTION_LENGTH,
    MAX_NOTES_TEMPLATE_NAME_LENGTH,
    NotesTemplateError,
    validate_notes_sections,
    validate_notes_template_description,
    validate_notes_template_name,
)

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)

MAX_GENERATOR_BRIEF_LENGTH = 1500


@dataclass(frozen=True)
class GeneratedNotesStructure:
    """A validated structure proposal, ready to show the user for review."""

    name: str
    description: str
    sections: str


def build_notes_structure_generator_prompt(brief: str) -> str:
    """Render once, preserving the user's brief as opaque text."""
    return render_prompt(
        "structure.instructions",
        max_notes_template_name_length=str(MAX_NOTES_TEMPLATE_NAME_LENGTH),
        max_notes_template_description_length=str(
            MAX_NOTES_TEMPLATE_DESCRIPTION_LENGTH
        ),
        max_notes_sections_length=str(MAX_NOTES_SECTIONS_LENGTH),
        default_notes_sections=default_notes_sections(),
        brief=brief.strip(),
    )


def validate_generator_brief(value: str | None) -> str:
    brief = str(value or "").strip()
    if not brief:
        raise NotesTemplateError("Describe the notes structure you want.")
    if len(brief) > MAX_GENERATOR_BRIEF_LENGTH:
        raise NotesTemplateError(
            f"Description must be at most {MAX_GENERATOR_BRIEF_LENGTH} characters."
        )
    return brief


def parse_generated_notes_structure(response_text: str) -> GeneratedNotesStructure:
    """Read the model's JSON proposal, validating it like a hand-written one.

    The generated structure goes through exactly the same validators as one the
    user typed: a model that returns something unusable should fail here, not at
    generation time on a real meeting.
    """
    payload = _load_json_object(response_text)

    try:
        name = validate_notes_template_name(payload.get("name"))
        description = validate_notes_template_description(payload.get("description"))
        sections = validate_notes_sections(payload.get("sections"))
    except NotesTemplateError as exc:
        raise NotesTemplateError(
            f"The generated structure was not usable: {exc}"
        ) from exc

    return GeneratedNotesStructure(
        name=name,
        description=description or "",
        sections=sections,
    )


def _load_json_object(response_text: str) -> dict:
    text = (response_text or "").strip()
    if not text:
        raise NotesTemplateError("The model returned an empty response.")

    for candidate in _json_candidates(text):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    raise NotesTemplateError("Could not read a structure from the model's response.")


def _json_candidates(text: str):
    yield text
    for match in JSON_FENCE_PATTERN.finditer(text):
        yield match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        yield text[start : end + 1]
