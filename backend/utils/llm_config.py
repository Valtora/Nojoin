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
    strip_legacy_automatic_ai_settings,
)

SYSTEM_LLM_FIELDS = (
    "llm_provider",
    "gemini_model",
    "gemini_live_model",
    "openai_model",
    "openai_live_model",
    "anthropic_model",
    "anthropic_live_model",
    "ollama_model",
    "ollama_live_model",
    "ollama_api_url",
    "ollama_context_window",
    "secondary_llm_provider",
    "secondary_gemini_model",
    "secondary_gemini_live_model",
    "secondary_openai_model",
    "secondary_openai_live_model",
    "secondary_anthropic_model",
    "secondary_anthropic_live_model",
    "secondary_ollama_model",
    "secondary_ollama_live_model",
    "secondary_ollama_api_url",
    "secondary_ollama_context_window",
    "secondary_gemini_api_key",
    "secondary_openai_api_key",
    "secondary_anthropic_api_key",
)

LLM_PURPOSE_DEFAULT = "default"
LLM_PURPOSE_MEETING_EDGE = "meeting_edge"

LIVE_MODEL_FIELDS_BY_PROVIDER = {
    "gemini": "gemini_live_model",
    "openai": "openai_live_model",
    "anthropic": "anthropic_live_model",
    "ollama": "ollama_live_model",
}

SECONDARY_LIVE_MODEL_FIELDS_BY_PROVIDER = {
    "gemini": "secondary_gemini_live_model",
    "openai": "secondary_openai_live_model",
    "anthropic": "secondary_anthropic_live_model",
    "ollama": "secondary_ollama_live_model",
}

INSTALL_WIDE_ONLY_USER_LLM_FIELDS = frozenset(
    field
    for field in SYSTEM_LLM_FIELDS
    if field
    not in {
        *LIVE_MODEL_FIELDS_BY_PROVIDER.values(),
        *SECONDARY_LIVE_MODEL_FIELDS_BY_PROVIDER.values(),
    }
)


@dataclass(frozen=True)
class ResolvedLLMConfig:
    provider: str
    api_key: str | None
    model: str | None
    api_url: str | None
    merged_config: dict[str, Any]
    context_window: int | None = None
    secondary_provider: str | None = None
    secondary_api_key: str | None = None
    secondary_model: str | None = None
    secondary_api_url: str | None = None
    secondary_context_window: int | None = None
    secondary_live_model: str | None = None

    def missing_configuration_message(self) -> str | None:
        if self.has_secondary:
            return None

        if self.provider != "ollama" and not self.api_key:
            return f"No API key configured for {self.provider}"

        if not self.model:
            return f"No model selected for {self.provider}"

        return None

    @property
    def has_secondary(self) -> bool:
        if not self.secondary_provider:
            return False
        if self.secondary_provider != "ollama" and not self.secondary_api_key:
            return False
        if not self.secondary_model:
            return False
        return True

    def secondary_config(self) -> "ResolvedLLMConfig | None":
        if not self.has_secondary:
            return None
        return ResolvedLLMConfig(
            provider=self.secondary_provider,
            api_key=self.secondary_api_key,
            model=self.secondary_model,
            api_url=self.secondary_api_url,
            context_window=self.secondary_context_window,
            merged_config=self.merged_config,
        )


def _merge_llm_config(
    *,
    base_config: Mapping[str, Any],
    system_keys: Mapping[str, str],
    owner_settings: Mapping[str, Any] | None,
    user_settings: Mapping[str, Any] | None,
    purpose: str = LLM_PURPOSE_DEFAULT,
) -> ResolvedLLMConfig:
    merged: dict[str, Any] = dict(base_config)
    sanitized_user_settings = strip_legacy_automatic_ai_settings(
        dict(user_settings) if user_settings else {}
    )
    for field in INSTALL_WIDE_ONLY_USER_LLM_FIELDS:
        sanitized_user_settings.pop(field, None)
    merged.update({key: value for key, value in system_keys.items() if value})

    if owner_settings:
        for field in SYSTEM_LLM_FIELDS:
            value = owner_settings.get(field)
            current_value = merged.get(field)
            if (
                value is not None
                and value != ""
                and (current_value is None or current_value == "")
            ):
                merged[field] = value

    if sanitized_user_settings:
        merged.update(
            {
                key: value
                for key, value in sanitized_user_settings.items()
                if value is not None
            }
        )

    for key, value in system_keys.items():
        if value:
            merged[key] = value

    provider = str(merged.get("llm_provider") or "gemini")
    api_key = merged.get(f"{provider}_api_key")
    model = _resolve_model_for_purpose(merged, provider, purpose)
    api_url = merged.get("ollama_api_url")
    context_window = (
        _normalise_context_window(merged.get("ollama_context_window"))
        if provider == "ollama"
        else None
    )

    # Resolve secondary provider
    secondary_provider = merged.get("secondary_llm_provider") or None
    secondary_api_key = None
    secondary_model = None
    secondary_api_url = None
    secondary_context_window = None
    secondary_live_model = None

    if secondary_provider:
        secondary_api_key = merged.get(f"secondary_{secondary_provider}_api_key")
        secondary_model = _resolve_secondary_model_for_purpose(
            merged, secondary_provider, purpose
        )
        secondary_api_url = (
            merged.get("secondary_ollama_api_url")
            if secondary_provider == "ollama"
            else None
        )
        secondary_context_window = (
            _normalise_context_window(merged.get("secondary_ollama_context_window"))
            if secondary_provider == "ollama"
            else None
        )
        secondary_live_model_field = SECONDARY_LIVE_MODEL_FIELDS_BY_PROVIDER.get(
            secondary_provider
        )
        if secondary_live_model_field:
            secondary_live_model = merged.get(secondary_live_model_field)

    return ResolvedLLMConfig(
        provider=provider,
        api_key=str(api_key) if api_key else None,
        model=str(model) if model else None,
        api_url=str(api_url) if api_url else None,
        context_window=context_window,
        merged_config=merged,
        secondary_provider=secondary_provider,
        secondary_api_key=str(secondary_api_key) if secondary_api_key else None,
        secondary_model=str(secondary_model) if secondary_model else None,
        secondary_api_url=str(secondary_api_url) if secondary_api_url else None,
        secondary_context_window=secondary_context_window,
        secondary_live_model=str(secondary_live_model)
        if secondary_live_model
        else None,
    )


def _resolve_model_for_purpose(
    merged: Mapping[str, Any],
    provider: str,
    purpose: str,
) -> Any:
    if purpose == LLM_PURPOSE_MEETING_EDGE:
        live_field = LIVE_MODEL_FIELDS_BY_PROVIDER.get(provider)
        if live_field:
            live_model = merged.get(live_field)
            if live_model:
                return live_model

    return merged.get(f"{provider}_model")


def _resolve_secondary_model_for_purpose(
    merged: Mapping[str, Any],
    secondary_provider: str,
    purpose: str,
) -> Any:
    if purpose == LLM_PURPOSE_MEETING_EDGE:
        live_field = SECONDARY_LIVE_MODEL_FIELDS_BY_PROVIDER.get(secondary_provider)
        if live_field:
            live_model = merged.get(live_field)
            if live_model:
                return live_model

    main_model_field = f"secondary_{secondary_provider}_model"
    return merged.get(main_model_field)


def _normalise_context_window(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        context_window = int(value)
    except (TypeError, ValueError):
        return None
    if context_window < 1024:
        return None
    return context_window


def _apply_owner_provider_override(
    config: ResolvedLLMConfig,
    owner_settings: Mapping[str, Any] | None,
    purpose: str,
) -> ResolvedLLMConfig:
    """Fill missing provider selections from owner settings without overriding install-wide config."""
    if not owner_settings:
        return config
    merged = dict(config.merged_config)
    overridden = False
    for field in ("llm_provider", "secondary_llm_provider"):
        if merged.get(field) not in (None, ""):
            continue
        val = owner_settings.get(field)
        if val is not None and val != "":
            merged[field] = val
            overridden = True
    if not overridden:
        return config

    provider = str(merged.get("llm_provider") or "gemini")
    api_key = merged.get(f"{provider}_api_key")
    model = _resolve_model_for_purpose(merged, provider, purpose)
    api_url = merged.get("ollama_api_url")
    context_window = (
        _normalise_context_window(merged.get("ollama_context_window"))
        if provider == "ollama"
        else None
    )

    secondary_provider = merged.get("secondary_llm_provider") or None
    secondary_api_key = None
    secondary_model = None
    secondary_api_url = None
    secondary_context_window = None
    secondary_live_model = None

    if secondary_provider:
        secondary_api_key = merged.get(f"secondary_{secondary_provider}_api_key")
        secondary_model = _resolve_secondary_model_for_purpose(
            merged, secondary_provider, purpose
        )
        secondary_api_url = (
            merged.get("secondary_ollama_api_url")
            if secondary_provider == "ollama"
            else None
        )
        secondary_context_window = (
            _normalise_context_window(merged.get("secondary_ollama_context_window"))
            if secondary_provider == "ollama"
            else None
        )
        secondary_live_model_field = SECONDARY_LIVE_MODEL_FIELDS_BY_PROVIDER.get(
            secondary_provider
        )
        if secondary_live_model_field:
            secondary_live_model = merged.get(secondary_live_model_field)

    return ResolvedLLMConfig(
        provider=provider,
        api_key=str(api_key) if api_key else None,
        model=str(model) if model else None,
        api_url=str(api_url) if api_url else None,
        context_window=context_window,
        merged_config=merged,
        secondary_provider=secondary_provider,
        secondary_api_key=str(secondary_api_key) if secondary_api_key else None,
        secondary_model=str(secondary_model) if secondary_model else None,
        secondary_api_url=str(secondary_api_url) if secondary_api_url else None,
        secondary_context_window=secondary_context_window,
        secondary_live_model=str(secondary_live_model)
        if secondary_live_model
        else None,
    )


def resolve_llm_config(
    session: Session,
    user_settings: Mapping[str, Any] | None = None,
    purpose: str = LLM_PURPOSE_DEFAULT,
) -> ResolvedLLMConfig:
    system_keys = get_system_api_keys(session)
    owner = session.exec(select(User).where(User.role == "owner")).first()
    owner_settings = getattr(owner, "settings", {}) if owner else {}

    merged = _merge_llm_config(
        base_config=config_manager.get_all(),
        system_keys=system_keys,
        owner_settings=owner_settings,
        user_settings=user_settings,
        purpose=purpose,
    )
    return _apply_owner_provider_override(merged, owner_settings, purpose)


async def resolve_llm_config_async(
    db: AsyncSession,
    user_settings: Mapping[str, Any] | None = None,
    purpose: str = LLM_PURPOSE_DEFAULT,
) -> ResolvedLLMConfig:
    system_keys = await async_get_system_api_keys(db)
    result = await db.execute(select(User).where(User.role == "owner"))
    owner = result.scalar_one_or_none()
    owner_settings = getattr(owner, "settings", {}) if owner else {}

    merged = _merge_llm_config(
        base_config=config_manager.get_all(),
        system_keys=system_keys,
        owner_settings=owner_settings,
        user_settings=user_settings,
        purpose=purpose,
    )
    return _apply_owner_provider_override(merged, owner_settings, purpose)
