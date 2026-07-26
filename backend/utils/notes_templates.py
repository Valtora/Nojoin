"""Resolution, merging and validation for user-editable notes structures.

The prompt-assembly half of the feature lives in ``backend.utils.meeting_notes``
(what a template *is*); this module owns the surrounding policy: which template a
given run should use, who may see or edit one, how the two glossary tiers
combine, and what text is accepted in the first place.

Resolution order for a notes run, highest priority first:

1. an explicit template chosen for this run (Regenerate Notes)
2. the user's default (``notes_template_id`` in their settings)
3. the install default (``install_notes_template_id``, admin-managed)
4. the shipped built-in structure

Every tier degrades to the next rather than failing: a template that was deleted,
or that the user may no longer see, must never stop a meeting's notes from being
generated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, select

from backend.models.notes_template import NotesTemplate, NotesTemplateScope
from backend.utils.meeting_notes import (
    DEFAULT_NOTES_SECTIONS,
    NOTES_SECTIONS_VERSION,
    MeetingMetadata,
    NotesPromptContext,
    format_duration_for_prompt,
    is_placeholder_speaker_name,
    resolve_recording_speaker_name,
)
from backend.utils.timezones import utc_naive_to_timezone

MAX_NOTES_TEMPLATE_NAME_LENGTH = 80
MAX_NOTES_TEMPLATE_DESCRIPTION_LENGTH = 200
MAX_NOTES_SECTIONS_LENGTH = 8000
MAX_GLOSSARY_LENGTH = 8000
MAX_NOTES_TEMPLATES_PER_SCOPE = 50

BUILTIN_TEMPLATE_NAME = "Nojoin default"

USER_TEMPLATE_SETTING_KEY = "notes_template_id"
INSTALL_TEMPLATE_SETTING_KEY = "install_notes_template_id"
USER_GLOSSARY_SETTING_KEY = "glossary_terms"
INSTALL_GLOSSARY_SETTING_KEY = "install_glossary_terms"

_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
# "Term: definition", "Term - definition", "Term = definition". The term before
# the separator is the merge key; a line with no separator is its own key.
_GLOSSARY_ENTRY_PATTERN = re.compile(r"^\s*(?:[-*+]\s+)?([^:=]{1,80}?)\s*[:=]\s*(.+)$")


class NotesTemplateError(ValueError):
    """Raised when a template or glossary value is not acceptable."""


@dataclass(frozen=True)
class ResolvedNotesTemplate:
    """The structure a notes run will actually use."""

    sections: str
    name: str
    template_id: Optional[int] = None

    @property
    def is_builtin(self) -> bool:
        return self.template_id is None

    @property
    def is_custom(self) -> bool:
        """Whether the prompt deviates from the shipped structure.

        Drives the relaxed output contract: the strict "notes must open with a
        ``##`` heading" rule only ever encoded the built-in structure's own first
        line, so it cannot be applied to a structure the user wrote.
        """
        return self.sections.strip() != DEFAULT_NOTES_SECTIONS.strip()


def builtin_notes_template() -> ResolvedNotesTemplate:
    return ResolvedNotesTemplate(
        sections=DEFAULT_NOTES_SECTIONS,
        name=BUILTIN_TEMPLATE_NAME,
        template_id=None,
    )


def validate_notes_template_name(value: Optional[str]) -> str:
    name = str(value or "").strip()
    if not name:
        raise NotesTemplateError("Template name is required.")
    if len(name) > MAX_NOTES_TEMPLATE_NAME_LENGTH:
        raise NotesTemplateError(
            f"Template name must be at most {MAX_NOTES_TEMPLATE_NAME_LENGTH} characters."
        )
    if _CONTROL_CHARACTER_PATTERN.search(name):
        raise NotesTemplateError("Template name must not contain control characters.")
    return name


def validate_notes_template_description(value: Optional[str]) -> Optional[str]:
    """Validate the one-line description. Blank is allowed and stored as NULL."""
    description = str(value or "").strip()
    if not description:
        return None
    if len(description) > MAX_NOTES_TEMPLATE_DESCRIPTION_LENGTH:
        raise NotesTemplateError(
            "Description must be at most "
            f"{MAX_NOTES_TEMPLATE_DESCRIPTION_LENGTH} characters."
        )
    if _CONTROL_CHARACTER_PATTERN.search(description):
        raise NotesTemplateError("Description must not contain control characters.")
    return description


def validate_notes_sections(value: Optional[str]) -> str:
    """Validate a user-authored notes structure.

    Deliberately shallow: it rejects what is mechanically broken (empty, oversized,
    control characters, no headings at all) and nothing else. Whether a structure
    produces *good* notes is the user's call, and a validator that guessed at that
    would block the workflows this feature exists to enable.
    """
    sections = str(value or "").strip()
    if not sections:
        raise NotesTemplateError("Template structure is required.")
    if len(sections) > MAX_NOTES_SECTIONS_LENGTH:
        raise NotesTemplateError(
            f"Template structure must be at most {MAX_NOTES_SECTIONS_LENGTH} characters."
        )
    if _CONTROL_CHARACTER_PATTERN.search(sections):
        raise NotesTemplateError(
            "Template structure must not contain control characters."
        )
    if not _HEADING_PATTERN.search(sections):
        raise NotesTemplateError(
            "Template structure must contain at least one Markdown heading, "
            "for example '## Summary'."
        )
    return sections


def validate_glossary(value: Optional[str]) -> str:
    glossary = str(value or "").strip()
    if len(glossary) > MAX_GLOSSARY_LENGTH:
        raise NotesTemplateError(
            f"Glossary must be at most {MAX_GLOSSARY_LENGTH} characters."
        )
    if _CONTROL_CHARACTER_PATTERN.search(glossary):
        raise NotesTemplateError("Glossary must not contain control characters.")
    return glossary


def _glossary_entry_key(line: str) -> str:
    match = _GLOSSARY_ENTRY_PATTERN.match(line)
    if match:
        return match.group(1).strip().casefold()
    return line.strip().casefold()


def merge_glossaries(
    install_glossary: Optional[str],
    personal_glossary: Optional[str],
) -> str:
    """Merge the install-wide and personal glossaries, personal winning ties.

    A term list is additive by nature, so a user adding one personal acronym must
    not lose the organisation's vocabulary. Where both tiers define the same term
    the personal definition replaces the install one *in place*, keeping the
    organisation's ordering stable.
    """
    install_lines = [
        line.strip()
        for line in str(install_glossary or "").splitlines()
        if line.strip()
    ]
    personal_lines = [
        line.strip()
        for line in str(personal_glossary or "").splitlines()
        if line.strip()
    ]

    personal_by_key = {_glossary_entry_key(line): line for line in personal_lines}
    merged: list[str] = []
    used_keys: set[str] = set()

    for line in install_lines:
        key = _glossary_entry_key(line)
        if key in used_keys:
            continue
        used_keys.add(key)
        merged.append(personal_by_key.get(key, line))

    for line in personal_lines:
        key = _glossary_entry_key(line)
        if key in used_keys:
            continue
        used_keys.add(key)
        merged.append(line)

    return "\n".join(merged)


def resolve_glossary(settings: Optional[Mapping[str, Any]]) -> str:
    """Merge both glossary tiers out of an already-merged settings mapping.

    ``settings`` is the merged view (install config under user settings), which is
    what both the API and the worker hold, so the install tier is present even for
    a user who has never opened Settings.
    """
    resolved = settings or {}
    return merge_glossaries(
        resolved.get(INSTALL_GLOSSARY_SETTING_KEY),
        resolved.get(USER_GLOSSARY_SETTING_KEY),
    )


def user_can_read_template(
    template: NotesTemplate,
    *,
    user_id: Optional[int],
    is_admin: bool = False,  # noqa: ARG001 - mirrors user_can_edit_template
) -> bool:
    """Whether the template is visible: install templates are, to everyone.

    Admin status is accepted but unused: reading is not the privileged operation,
    editing is. The parameter keeps the two call sites symmetrical so a caller
    cannot pass it to one and forget it on the other.
    """
    del is_admin
    if template.scope == NotesTemplateScope.INSTALL.value:
        return True
    return template.user_id is not None and template.user_id == user_id


def user_can_edit_template(
    template: NotesTemplate,
    *,
    user_id: Optional[int],
    is_admin: bool = False,
) -> bool:
    if template.scope == NotesTemplateScope.INSTALL.value:
        return bool(is_admin)
    return template.user_id is not None and template.user_id == user_id


def is_admin_role(user: Any) -> bool:
    """Whether a user may manage install-tier templates and the install glossary."""
    role = str(getattr(user, "role", "") or "")
    return role in {"owner", "admin"} or bool(getattr(user, "is_superuser", False))


def is_template_stale(template: NotesTemplate) -> bool:
    """Whether the shipped structure has moved on since this template forked."""
    version = getattr(template, "builtin_version", None)
    if version is None:
        return False
    try:
        return int(version) < NOTES_SECTIONS_VERSION
    except (TypeError, ValueError):
        return False


def _template_to_resolved(template: NotesTemplate) -> ResolvedNotesTemplate:
    return ResolvedNotesTemplate(
        sections=template.sections,
        name=template.name,
        template_id=template.id,
    )


def _candidate_template_ids(
    settings: Optional[Mapping[str, Any]],
    explicit_template_id: Optional[int],
) -> list[int]:
    resolved = settings or {}
    candidates = [
        explicit_template_id,
        resolved.get(USER_TEMPLATE_SETTING_KEY),
        resolved.get(INSTALL_TEMPLATE_SETTING_KEY),
    ]
    ordered: list[int] = []
    for candidate in candidates:
        if candidate in (None, ""):
            continue
        try:
            template_id = int(candidate)
        except (TypeError, ValueError):
            continue
        if template_id not in ordered:
            ordered.append(template_id)
    return ordered


def _select_visible_template(
    templates: Iterable[NotesTemplate],
    candidate_ids: Sequence[int],
    *,
    user_id: Optional[int],
) -> Optional[NotesTemplate]:
    by_id = {template.id: template for template in templates}
    for template_id in candidate_ids:
        template = by_id.get(template_id)
        if template is None:
            continue
        if not user_can_read_template(template, user_id=user_id):
            continue
        return template
    return None


def resolve_notes_template(
    session: Session,
    settings: Optional[Mapping[str, Any]],
    *,
    user_id: Optional[int],
    explicit_template_id: Optional[int] = None,
) -> ResolvedNotesTemplate:
    """Synchronous resolver, used by the worker."""
    candidate_ids = _candidate_template_ids(settings, explicit_template_id)
    if not candidate_ids:
        return builtin_notes_template()

    templates = session.exec(
        select(NotesTemplate).where(NotesTemplate.id.in_(candidate_ids))
    ).all()
    template = _select_visible_template(templates, candidate_ids, user_id=user_id)
    if template is None:
        return builtin_notes_template()
    return _template_to_resolved(template)


def build_meeting_metadata(
    recording: Any,
    speakers: Optional[Iterable[Any]] = None,
    *,
    timezone_name: Optional[str] = None,
) -> MeetingMetadata:
    """Collect the recording facts the prompt always carries.

    Placeholder speaker names (``SPEAKER_00``, ``Unknown``) are dropped rather
    than listed: telling the model that a participant is called "SPEAKER_01"
    invites it to write that into the notes.
    """
    recorded_at = getattr(recording, "created_at", None)
    if recorded_at is not None and timezone_name:
        recorded_at = utc_naive_to_timezone(recorded_at, timezone_name)

    participants: list[str] = []
    for speaker in speakers or []:
        name = resolve_recording_speaker_name(speaker)
        if name and not is_placeholder_speaker_name(name):
            if name not in participants:
                participants.append(name)

    return MeetingMetadata(
        title=getattr(recording, "name", None),
        recorded_on=(
            recorded_at.strftime("%A %d %B %Y, %H:%M") if recorded_at else None
        ),
        duration=format_duration_for_prompt(
            getattr(recording, "duration_seconds", None)
        ),
        participants=participants,
    )


def build_notes_prompt_context(  # noqa: PLR0913 - one argument per prompt input
    session: Session,
    *,
    recording: Any,
    speakers: Optional[Iterable[Any]],
    settings: Optional[Mapping[str, Any]],
    user_id: Optional[int],
    explicit_template_id: Optional[int] = None,
) -> tuple[NotesPromptContext, ResolvedNotesTemplate]:
    """One call for the worker: resolve the template, glossary and metadata.

    Returns the prompt context to hand to the LLM backend and the resolved
    template itself, which the caller records as provenance on the transcript.
    """
    template = resolve_notes_template(
        session,
        settings,
        user_id=user_id,
        explicit_template_id=explicit_template_id,
    )
    context = NotesPromptContext(
        # Only a genuinely custom structure is sent; the built-in one is left as
        # None so the assembled prompt stays byte-identical to the shipped one.
        notes_sections=template.sections if template.is_custom else None,
        glossary=resolve_glossary(settings) or None,
        metadata=build_meeting_metadata(
            recording,
            speakers,
            timezone_name=(settings or {}).get("timezone"),
        ),
    )
    return context, template


async def resolve_notes_template_async(
    db: AsyncSession,
    settings: Optional[Mapping[str, Any]],
    *,
    user_id: Optional[int],
    explicit_template_id: Optional[int] = None,
) -> ResolvedNotesTemplate:
    """Asynchronous twin of :func:`resolve_notes_template`, used by the API."""
    candidate_ids = _candidate_template_ids(settings, explicit_template_id)
    if not candidate_ids:
        return builtin_notes_template()

    result = await db.execute(
        select(NotesTemplate).where(NotesTemplate.id.in_(candidate_ids))
    )
    templates = result.scalars().all()
    template = _select_visible_template(templates, candidate_ids, user_id=user_id)
    if template is None:
        return builtin_notes_template()
    return _template_to_resolved(template)
