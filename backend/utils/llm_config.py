from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, select

from backend.models.user import User
from backend.utils.config_manager import (
    async_get_system_api_keys,
    config_manager,
    get_system_api_keys,
)


SYSTEM_LLM_FIELDS = (
    "llm_provider",
    "gemini_model",
    "openai_model",
    "anthropic_model",
    "ollama_model",
    "ollama_api_url",
)


@dataclass(frozen=True)
class ResolvedLLMConfig:
    provider: str
    api_key: str | None
    model: str | None
    api_url: str | None
    merged_config: dict[str, Any]

    def missing_configuration_message(self) -> str | None:
        if self.provider != "ollama" and not self.api_key:
            return f"No API key configured for {self.provider}"

        if not self.model:
            return f"No model selected for {self.provider}"

        return None


def _merge_llm_config(
    *,
    base_config: Mapping[str, Any],
    system_keys: Mapping[str, str],
    owner_settings: Mapping[str, Any] | None,
    user_settings: Mapping[str, Any] | None,
) -> ResolvedLLMConfig:
    merged: dict[str, Any] = dict(base_config)
    merged.update({key: value for key, value in system_keys.items() if value})

    if owner_settings:
        for field in SYSTEM_LLM_FIELDS:
            value = owner_settings.get(field)
            if value:
                merged[field] = value

    if user_settings:
        merged.update({key: value for key, value in user_settings.items() if value is not None})

    for key, value in system_keys.items():
        if value:
            merged[key] = value

    provider = str(merged.get("llm_provider") or "gemini")
    api_key = merged.get(f"{provider}_api_key")
    model = merged.get(f"{provider}_model")
    api_url = merged.get("ollama_api_url")

    return ResolvedLLMConfig(
        provider=provider,
        api_key=str(api_key) if api_key else None,
        model=str(model) if model else None,
        api_url=str(api_url) if api_url else None,
        merged_config=merged,
    )


def resolve_llm_config(
    session: Session,
    user_settings: Mapping[str, Any] | None = None,
) -> ResolvedLLMConfig:
    system_keys = get_system_api_keys(session)
    owner = session.exec(select(User).where(User.role == "owner")).first()
    owner_settings = getattr(owner, "settings", {}) if owner else {}

    return _merge_llm_config(
        base_config=config_manager.get_all(),
        system_keys=system_keys,
        owner_settings=owner_settings,
        user_settings=user_settings,
    )


async def resolve_llm_config_async(
    db: AsyncSession,
    user_settings: Mapping[str, Any] | None = None,
) -> ResolvedLLMConfig:
    system_keys = await async_get_system_api_keys(db)
    result = await db.execute(select(User).where(User.role == "owner"))
    owner = result.scalar_one_or_none()
    owner_settings = getattr(owner, "settings", {}) if owner else {}

    return _merge_llm_config(
        base_config=config_manager.get_all(),
        system_keys=system_keys,
        owner_settings=owner_settings,
        user_settings=user_settings,
    )