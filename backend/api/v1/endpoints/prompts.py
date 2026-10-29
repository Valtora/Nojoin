"""Environment-gated, administrator-only edits to shipped prompt blocks."""

import logging
from threading import RLock
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.api.deps import get_current_admin_user
from backend.api.error_handling import sanitized_http_exception
from backend.models.prompt_public import PromptCatalogueEntry, PromptOverrideUpdate
from backend.prompts import (
    REGISTRY,
    default_sha256,
    developer_mode_enabled,
    prompt_catalogue_entry,
    validate_override,
)
from backend.utils.config_manager import config_manager

logger = logging.getLogger(__name__)
_write_lock = RLock()


def require_developer_mode() -> None:
    if not developer_mode_enabled():
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(
    dependencies=[Depends(require_developer_mode), Depends(get_current_admin_user)]
)


def _require_prompt(prompt_id: str) -> None:
    if prompt_id not in REGISTRY:
        raise HTTPException(status_code=404, detail="Prompt not found")


def _overrides(config: dict[str, Any]) -> dict[str, Any]:
    stored = config.get("prompt_overrides")
    return dict(stored) if isinstance(stored, dict) else {}


def _save_override(prompt_id: str, text: str | None) -> dict[str, Any]:
    """Change one entry while preserving the raw file's unrelated settings."""
    try:
        with _write_lock:
            overrides = _overrides(config_manager.read_file())
            if text is None:
                overrides.pop(prompt_id, None)
            else:
                overrides[prompt_id] = {
                    "text": text,
                    "base_sha256": default_sha256(prompt_id),
                }
            config_manager.save_values({"prompt_overrides": overrides})
    except (OSError, ValueError) as exc:
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Could not save prompt overrides.",
            log_message="Failed to persist a developer prompt override.",
            exc=exc,
        ) from exc
    return prompt_catalogue_entry(prompt_id, overrides.get(prompt_id))


@router.get("", response_model=list[PromptCatalogueEntry])
def list_prompts() -> list[dict[str, Any]]:
    config_manager.reload()
    overrides = _overrides(config_manager.get_all())
    return [
        prompt_catalogue_entry(prompt_id, overrides.get(prompt_id))
        for prompt_id in REGISTRY
    ]


@router.put("/{prompt_id}", response_model=PromptCatalogueEntry)
def save_prompt(prompt_id: str, payload: PromptOverrideUpdate) -> dict[str, Any]:
    _require_prompt(prompt_id)
    try:
        validate_override(prompt_id, payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _save_override(prompt_id, payload.text)


@router.delete("/{prompt_id}", response_model=PromptCatalogueEntry)
def reset_prompt(prompt_id: str) -> dict[str, Any]:
    _require_prompt(prompt_id)
    return _save_override(prompt_id, None)
