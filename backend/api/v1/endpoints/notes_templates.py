"""CRUD and preview for user-editable meeting-notes structures (issue #137).

Two tiers share one table. Install templates are visible to everyone and
writable only by the owner/admins; personal templates belong to one user and are
invisible to the rest. Every write path re-checks that, because a template id is
guessable and the settings endpoint is not the only way in.
"""

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.core.task_dispatch import dispatch_task
from backend.models.notes_template import (
    NotesTemplate,
    NotesTemplateCreate,
    NotesTemplateScope,
    NotesTemplateUpdate,
)
from backend.models.user import User
from backend.processing.llm_backends.base import LLMBackend
from backend.services.notes_structure_jobs import (
    STATUS_ERROR,
    STATUS_PENDING,
    new_job_id,
    publish_job_async,
    read_job,
)
from backend.utils.llm_config import resolve_llm_config_async
from backend.utils.meeting_notes import (
    DEFAULT_NOTES_SECTIONS,
    NOTES_SECTIONS_VERSION,
    MeetingMetadata,
    NotesPromptContext,
)
from backend.utils.notes_structure_generator import validate_generator_brief
from backend.utils.notes_templates import (
    MAX_GLOSSARY_LENGTH,
    MAX_NOTES_SECTIONS_LENGTH,
    MAX_NOTES_TEMPLATE_DESCRIPTION_LENGTH,
    MAX_NOTES_TEMPLATE_NAME_LENGTH,
    MAX_NOTES_TEMPLATES_PER_SCOPE,
    NotesTemplateError,
    is_admin_role,
    is_template_stale,
    resolve_glossary,
    user_can_edit_template,
    user_can_read_template,
    validate_notes_sections,
    validate_notes_template_description,
    validate_notes_template_name,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# A short, obviously fake transcript: the preview exists to show which parts of
# the prompt the user controls, not to demonstrate note quality, and a realistic
# sample would only make the rendered prompt harder to scan.
PREVIEW_TRANSCRIPT = (
    "[00:00 - 00:12] Sam: Right, the migration is the only thing left before we ship.\n"
    "[00:12 - 00:31] Priya: I can take it, but I need the staging database back first.\n"
    "[00:31 - 00:44] Sam: Agreed. Let us call that decided and review on Thursday."
)


class NotesTemplatePreviewRequest(BaseModel):
    sections: Optional[str] = None
    glossary: Optional[str] = None


class NotesTemplateGenerateRequest(BaseModel):
    """The user's description of the notes structure they want."""

    brief: str


def _serialise(
    template: NotesTemplate,
    *,
    user: User,
    install_default_id: Optional[int],
    user_default_id: Optional[int],
) -> dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "sections": template.sections,
        "scope": template.scope,
        "user_id": template.user_id,
        "builtin_version": template.builtin_version,
        "is_editable": user_can_edit_template(
            template, user_id=user.id, is_admin=is_admin_role(user)
        ),
        "is_stale": is_template_stale(template),
        "is_install_default": template.id == install_default_id,
        "is_user_default": template.id == user_default_id,
    }


def _coerce_template_id(value: Any) -> Optional[int]:
    """Read a stored template id defensively.

    Both values come from JSON that a restore or a hand-edited config could have
    left in another shape. A bad value must degrade to "no default", never 500 a
    settings page.
    """
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_default_ids(user: User) -> tuple[Optional[int], Optional[int]]:
    from backend.utils.config_manager import config_manager

    settings = user.settings or {}
    return (
        _coerce_template_id(config_manager.get("install_notes_template_id", None)),
        _coerce_template_id(settings.get("notes_template_id")),
    )


async def _get_template_for_user(
    db: AsyncSession,
    template_id: int,
    user: User,
    *,
    require_edit: bool = False,
) -> NotesTemplate:
    template = await db.get(NotesTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Notes template not found")

    is_admin = is_admin_role(user)
    if not user_can_read_template(template, user_id=user.id, is_admin=is_admin):
        # 404 rather than 403: another user's personal template should not be
        # discoverable by probing ids.
        raise HTTPException(status_code=404, detail="Notes template not found")
    if require_edit and not user_can_edit_template(
        template, user_id=user.id, is_admin=is_admin
    ):
        raise HTTPException(
            status_code=403,
            detail="Install templates can only be changed by an administrator.",
        )
    return template


@router.get("")
async def list_notes_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Every template the user may use: all install templates plus their own."""
    result = await db.execute(
        select(NotesTemplate).where(
            (NotesTemplate.scope == NotesTemplateScope.INSTALL.value)
            | (NotesTemplate.user_id == current_user.id)
        )
    )
    templates: List[NotesTemplate] = list(result.scalars().all())
    install_default_id, user_default_id = _resolve_default_ids(current_user)

    templates.sort(key=lambda item: (item.scope != "install", item.name.lower()))
    return {
        "templates": [
            _serialise(
                template,
                user=current_user,
                install_default_id=install_default_id,
                user_default_id=user_default_id,
            )
            for template in templates
        ],
        "builtin": {
            "name": "Nojoin default",
            "description": (
                "Summary, decisions, action items, detailed notes. Suits project "
                "and status meetings."
            ),
            "sections": DEFAULT_NOTES_SECTIONS,
            "version": NOTES_SECTIONS_VERSION,
        },
        "limits": {
            "max_sections_length": MAX_NOTES_SECTIONS_LENGTH,
            "max_description_length": MAX_NOTES_TEMPLATE_DESCRIPTION_LENGTH,
            "max_glossary_length": MAX_GLOSSARY_LENGTH,
            "max_templates_per_scope": MAX_NOTES_TEMPLATES_PER_SCOPE,
        },
        "is_admin": is_admin_role(current_user),
    }


@router.post("")
async def create_notes_template(
    payload: NotesTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    is_admin = is_admin_role(current_user)
    scope = (payload.scope or NotesTemplateScope.PERSONAL.value).strip()
    if scope not in {item.value for item in NotesTemplateScope}:
        raise HTTPException(status_code=400, detail="Invalid template scope.")
    if scope == NotesTemplateScope.INSTALL.value and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only an administrator can create install templates.",
        )

    try:
        name = validate_notes_template_name(payload.name)
        description = validate_notes_template_description(payload.description)
        sections = validate_notes_sections(payload.sections)
    except NotesTemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    owner_id = None if scope == NotesTemplateScope.INSTALL.value else current_user.id
    count_stmt = select(NotesTemplate).where(NotesTemplate.scope == scope)
    if owner_id is not None:
        count_stmt = count_stmt.where(NotesTemplate.user_id == owner_id)
    existing = (await db.execute(count_stmt)).scalars().all()
    if len(existing) >= MAX_NOTES_TEMPLATES_PER_SCOPE:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_NOTES_TEMPLATES_PER_SCOPE} templates are allowed.",
        )

    template = NotesTemplate(
        name=name,
        description=description,
        sections=sections,
        scope=scope,
        user_id=owner_id,
        # Only stamped when the structure was forked from the shipped one, so a
        # from-scratch structure is never reported as stale later.
        builtin_version=(
            NOTES_SECTIONS_VERSION
            if payload.builtin_version is not None
            or sections.strip() == DEFAULT_NOTES_SECTIONS.strip()
            else None
        ),
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    install_default_id, user_default_id = _resolve_default_ids(current_user)
    return _serialise(
        template,
        user=current_user,
        install_default_id=install_default_id,
        user_default_id=user_default_id,
    )


@router.put("/{template_id}")
async def update_notes_template(
    template_id: int,
    payload: NotesTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    template = await _get_template_for_user(
        db, template_id, current_user, require_edit=True
    )

    try:
        if payload.name is not None:
            template.name = validate_notes_template_name(payload.name)
        if payload.description is not None:
            template.description = validate_notes_template_description(
                payload.description
            )
        if payload.sections is not None:
            sections = validate_notes_sections(payload.sections)
            if sections.strip() != template.sections.strip():
                template.sections = sections
                # Editing away from the shipped structure clears the fork stamp
                # unless the user has landed exactly back on the current default.
                template.builtin_version = (
                    NOTES_SECTIONS_VERSION
                    if sections.strip() == DEFAULT_NOTES_SECTIONS.strip()
                    else None
                )
    except NotesTemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(template)
    await db.commit()
    await db.refresh(template)

    install_default_id, user_default_id = _resolve_default_ids(current_user)
    return _serialise(
        template,
        user=current_user,
        install_default_id=install_default_id,
        user_default_id=user_default_id,
    )


@router.post("/{template_id}/reset")
async def reset_notes_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Replace a template's structure with the current shipped default."""
    template = await _get_template_for_user(
        db, template_id, current_user, require_edit=True
    )
    template.sections = DEFAULT_NOTES_SECTIONS
    template.builtin_version = NOTES_SECTIONS_VERSION
    db.add(template)
    await db.commit()
    await db.refresh(template)

    install_default_id, user_default_id = _resolve_default_ids(current_user)
    return _serialise(
        template,
        user=current_user,
        install_default_id=install_default_id,
        user_default_id=user_default_id,
    )


@router.post("/{template_id}/copy")
async def copy_notes_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Copy any visible template into the user's own library.

    This is the escape hatch that makes read-only install templates workable: a
    regular user who wants a variation copies it rather than editing shared text.
    """
    source = await _get_template_for_user(db, template_id, current_user)
    copy = NotesTemplate(
        # Truncated to the same ceiling the validator enforces: copying a
        # maximum-length name would otherwise produce one that can never be saved
        # again through the edit endpoint.
        name=f"{source.name} (copy)"[:MAX_NOTES_TEMPLATE_NAME_LENGTH],
        description=source.description,
        sections=source.sections,
        scope=NotesTemplateScope.PERSONAL.value,
        user_id=current_user.id,
        builtin_version=source.builtin_version,
    )
    db.add(copy)
    await db.commit()
    await db.refresh(copy)

    install_default_id, user_default_id = _resolve_default_ids(current_user)
    return _serialise(
        copy,
        user=current_user,
        install_default_id=install_default_id,
        user_default_id=user_default_id,
    )


@router.delete("/{template_id}")
async def delete_notes_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    template = await _get_template_for_user(
        db, template_id, current_user, require_edit=True
    )
    await db.delete(template)
    await db.commit()
    # Transcripts keep their snapshot; the FK is ON DELETE SET NULL so past notes
    # are never removed along with the template that produced them.
    return {"status": "success"}


@router.post("/generate")
async def generate_notes_structure(
    payload: NotesTemplateGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Start a structure-generation job and return its id to poll.

    Dispatched to the worker rather than run here: this is an LLM call, and the
    repo keeps inference off the API request path. The 400 for an unconfigured
    provider is raised here, though, so the user is told immediately instead of
    polling a job that was never going to succeed.
    """
    try:
        brief = validate_generator_brief(payload.brief)
    except NotesTemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    llm_config = await resolve_llm_config_async(
        db, current_user.settings or {}, user_id=current_user.id
    )
    missing_llm_config = llm_config.missing_configuration_message()
    if missing_llm_config:
        raise HTTPException(
            status_code=400,
            detail=f"{missing_llm_config}. Configure an AI provider and model in Settings.",
        )

    job_id = new_job_id()
    await publish_job_async(job_id, {"status": STATUS_PENDING})
    task = await dispatch_task(
        "backend.worker.tasks.generate_notes_structure_task",
        args=[job_id, current_user.id, brief],
    )
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, current_user.id)

    return {"job_id": job_id, "status": STATUS_PENDING}


@router.get("/generate/{job_id}")
async def get_generated_notes_structure(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Poll a generation job.

    Returns ``pending`` until the worker publishes a result. An expired or
    unknown id reports as an error rather than pending, so a browser that polls
    a job from a previous session stops rather than spinning forever.
    """
    job = await read_job(job_id)
    if job is None:
        return {
            "status": STATUS_ERROR,
            "error": "That generation request expired. Try again.",
        }
    return job


@router.post("/preview")
async def preview_notes_prompt(
    payload: NotesTemplatePreviewRequest,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Render the exact prompt a structure would produce, with no LLM call.

    The point is legibility: it shows the user which parts of the prompt they
    control and which are fixed, using a sample transcript so nothing about a
    real meeting is involved.
    """
    sections = payload.sections
    if sections is not None:
        try:
            sections = validate_notes_sections(sections)
        except NotesTemplateError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    glossary = payload.glossary
    if glossary is None:
        glossary = resolve_glossary(current_user.settings or {})

    prompt = LLMBackend.build_notes_prompt(
        None,
        PREVIEW_TRANSCRIPT,
        {"SPEAKER_00": "Sam", "SPEAKER_01": "Priya"},
        None,
        None,
        None,
        NotesPromptContext(
            notes_sections=sections,
            glossary=glossary or None,
            metadata=MeetingMetadata(
                title="Release readiness check",
                recorded_on="Thursday 26 March 2026, 14:00",
                duration="18 minutes",
                participants=["Sam", "Priya"],
            ),
        ),
    )
    return {"prompt": prompt, "editable_sections": sections or DEFAULT_NOTES_SECTIONS}
