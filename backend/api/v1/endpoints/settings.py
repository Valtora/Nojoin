from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from backend.api.error_handling import sanitized_http_exception
from backend.api.deps import get_current_user, get_db
from backend.celery_app import celery_app
from backend.models.recording import Recording, RecordingStatus
from backend.models.user import User
from backend.services.model_preparation import enqueue_model_preparation
from backend.utils.config_manager import (
    APP_THEMES,
    INSTALL_WIDE_AI_SETTING_KEYS,
    MEETING_EDGE_CONTEXT_LEVEL_MAX,
    MEETING_EDGE_CONTEXT_LEVEL_MIN,
    SENSITIVE_KEYS,
    TRANSCRIPTION_BACKENDS,
    WHISPER_MODEL_SIZES,
    config_manager,
    get_default_user_settings,
    strip_legacy_automatic_ai_settings,
)
from backend.utils.ollama_url_policy import OllamaURLValidationError, validate_ollama_api_url
from backend.utils.timezones import validate_timezone_name

router = APIRouter()
logger = logging.getLogger(__name__)
INSTALL_WIDE_ONLY_USER_SETTING_KEYS = frozenset(INSTALL_WIDE_AI_SETTING_KEYS)

class SettingsUpdate(BaseModel):
    llm_provider: Optional[str] = None
    enable_meeting_edge: Optional[bool] = None
    meeting_edge_context_level: Optional[int] = None
    whisper_model_size: Optional[str] = None
    transcription_backend: Optional[str] = None
    parakeet_model: Optional[str] = None
    canary_model: Optional[str] = None
    enable_live_transcription: Optional[bool] = None
    theme: Optional[str] = None
    gemini_model: Optional[str] = None
    gemini_live_model: Optional[str] = None
    openai_model: Optional[str] = None
    openai_live_model: Optional[str] = None
    anthropic_model: Optional[str] = None
    anthropic_live_model: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_live_model: Optional[str] = None
    ollama_api_url: Optional[str] = None
    ollama_context_window: Optional[int] = None
    secondary_llm_provider: Optional[str] = None
    secondary_gemini_model: Optional[str] = None
    secondary_gemini_live_model: Optional[str] = None
    secondary_openai_model: Optional[str] = None
    secondary_openai_live_model: Optional[str] = None
    secondary_anthropic_model: Optional[str] = None
    secondary_anthropic_live_model: Optional[str] = None
    secondary_ollama_model: Optional[str] = None
    secondary_ollama_live_model: Optional[str] = None
    secondary_ollama_api_url: Optional[str] = None
    secondary_ollama_context_window: Optional[int] = None
    secondary_gemini_api_key: Optional[str] = None
    secondary_openai_api_key: Optional[str] = None
    secondary_anthropic_api_key: Optional[str] = None
    enable_auto_voiceprints: Optional[bool] = None
    prefer_short_titles: Optional[bool] = None
    enable_vad: Optional[bool] = None
    enable_diarization: Optional[bool] = None
    spellcheck_language: Optional[str] = None
    timezone: Optional[str] = None

    @field_validator('whisper_model_size')
    @classmethod
    def validate_whisper_model_size(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in WHISPER_MODEL_SIZES:
            raise ValueError(f"Invalid whisper_model_size. Must be one of {WHISPER_MODEL_SIZES}")
        return value

    @field_validator('transcription_backend')
    @classmethod
    def validate_transcription_backend(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in TRANSCRIPTION_BACKENDS:
            raise ValueError(f"Invalid transcription_backend. Must be one of {TRANSCRIPTION_BACKENDS}")
        return value

    @field_validator('theme')
    @classmethod
    def validate_theme(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in APP_THEMES:
            raise ValueError(f"Invalid theme. Must be one of {APP_THEMES}")
        return value

    @field_validator('llm_provider')
    @classmethod
    def validate_llm_provider(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in ["gemini", "openai", "anthropic", "ollama"]:
            raise ValueError("Invalid llm_provider. Must be one of ['gemini', 'openai', 'anthropic', 'ollama']")
        return value

    @field_validator('secondary_llm_provider')
    @classmethod
    def validate_secondary_llm_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value != "" and value not in ["gemini", "openai", "anthropic", "ollama"]:
            raise ValueError("Invalid secondary_llm_provider. Must be one of ['gemini', 'openai', 'anthropic', 'ollama'] or empty")
        return value

    @field_validator('ollama_api_url')
    @classmethod
    def validate_ollama_api_url(cls, value: Optional[str]) -> Optional[str]:
        if value:
            try:
                return validate_ollama_api_url(value, allow_private=True)
            except OllamaURLValidationError as exc:
                raise ValueError(str(exc)) from exc
        return value

    @field_validator('secondary_ollama_api_url')
    @classmethod
    def validate_secondary_ollama_api_url(cls, value: Optional[str]) -> Optional[str]:
        if value:
            try:
                return validate_ollama_api_url(value, allow_private=True)
            except OllamaURLValidationError as exc:
                raise ValueError(str(exc)) from exc
        return value

    @field_validator('ollama_context_window', 'secondary_ollama_context_window')
    @classmethod
    def validate_ollama_context_window(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if value < 1024:
            raise ValueError("Ollama context window must be at least 1024 tokens.")
        return value

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value:
            return validate_timezone_name(value)
        return value

    @field_validator('meeting_edge_context_level')
    @classmethod
    def validate_meeting_edge_context_level(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if not MEETING_EDGE_CONTEXT_LEVEL_MIN <= value <= MEETING_EDGE_CONTEXT_LEVEL_MAX:
            raise ValueError(
                f"Invalid meeting_edge_context_level. Must be between {MEETING_EDGE_CONTEXT_LEVEL_MIN} and {MEETING_EDGE_CONTEXT_LEVEL_MAX}"
            )
        return value


def _has_configured_value(value: Any) -> bool:
    return value is not None and value != ""


def _apply_install_wide_ai_owner_fallback(
    merged: dict[str, Any],
    owner_settings: dict[str, Any] | None,
) -> None:
    if not owner_settings:
        return

    for field in INSTALL_WIDE_AI_SETTING_KEYS:
        current_value = merged.get(field)
        owner_value = owner_settings.get(field)
        if _has_configured_value(current_value) or not _has_configured_value(owner_value):
            continue
        merged[field] = owner_value


def _apply_default_user_settings(
    merged: dict[str, Any],
    default_user_settings: dict[str, Any],
) -> None:
    for key, value in default_user_settings.items():
        current_value = merged.get(key)
        if key in INSTALL_WIDE_AI_SETTING_KEYS and _has_configured_value(current_value):
            continue
        merged[key] = value


def _persist_install_wide_ai_settings(update_data: dict[str, Any]) -> None:
    install_wide_updates = {
        key: value
        for key, value in update_data.items()
        if key in INSTALL_WIDE_AI_SETTING_KEYS
    }
    if not install_wide_updates:
        return

    config_data = config_manager.get_all()
    config_data.update(install_wide_updates)
    config_manager.save_config(config_data)
    config_manager.reload()

async def _merge_settings(user_settings: dict, db: AsyncSession) -> dict:
    """
    Merges system config, default user settings, and user-specific settings.
    Priority: User Settings > Default User Settings > System Config
    """
    # 1. Start with System Config (read-only for users)
    merged = config_manager.get_all()

    # 2. Load Default User Settings for missing user-scoped values.
    default_user_settings = get_default_user_settings()

    # 2.5 Apply Owner's System-Wide LLM Configuration Defaults
    from backend.models.user import User
    from sqlmodel import select
    result = await db.execute(select(User).where(User.role == "owner"))
    owner = result.scalar_one_or_none()
    owner_settings = getattr(owner, "settings", {}) if owner else {}

    _apply_install_wide_ai_owner_fallback(merged, owner_settings)
    
    # 3. Apply User Specific Settings
    _apply_default_user_settings(merged, default_user_settings)
    sanitized_user_settings = _get_mutable_user_settings(user_settings)
    if sanitized_user_settings:
        merged.update(
            {k: v for k, v in sanitized_user_settings.items() if v is not None}
        )
        
    # 4. Inject system API keys (from Admin DB or .env) globally
    from backend.utils.config_manager import async_get_system_api_keys
    system_keys = await async_get_system_api_keys(db)
    for sk in SENSITIVE_KEYS:
        val = system_keys.get(sk)
        if val:
            merged[sk] = val
            
    # 5. Mask sensitive keys
    for key in SENSITIVE_KEYS:
        if merged.get(key):
            val = str(merged[key])
            if len(val) > 8:
                merged[key] = f"{val[:3]}...{val[-4:]}"
            else:
                merged[key] = "***"
                
    return merged


def _get_mutable_user_settings(user_settings: dict | None) -> dict[str, Any]:
    sanitized = strip_legacy_automatic_ai_settings(
        dict(user_settings) if user_settings else {}
    )
    for key in INSTALL_WIDE_ONLY_USER_SETTING_KEYS:
        sanitized.pop(key, None)
    return sanitized


def _build_settings_update_data(
    settings: SettingsUpdate,
    *,
    is_admin: bool,
) -> dict[str, Any]:
    update_data = strip_legacy_automatic_ai_settings(
        settings.model_dump(exclude_unset=True)
    )

    for key in list(SENSITIVE_KEYS):
        if key not in update_data:
            continue

        value = update_data[key]
        if not is_admin or (value and ("..." in str(value) or "***" in str(value))):
            del update_data[key]

    if not is_admin:
        for key in INSTALL_WIDE_ONLY_USER_SETTING_KEYS:
            update_data.pop(key, None)

    return update_data


async def _dispatch_meeting_edge_refresh_for_active_recordings(
    db: AsyncSession,
    current_user: User,
) -> None:
    """Best-effort Meeting Edge refresh for the user's in-flight recordings.

    Lets context-level / enable-toggle changes take effect promptly instead of
    waiting for the next transcript-driven refresh.
    """
    from sqlalchemy import select as sa_select

    try:
        result = await db.execute(
            sa_select(Recording.id).where(
                Recording.user_id == current_user.id,
                Recording.status.in_(
                    [
                        RecordingStatus.UPLOADING,
                        RecordingStatus.QUEUED,
                        RecordingStatus.PROCESSING,
                    ]
                ),
            )
        )
        recording_ids = [row[0] for row in result.all()]
        for recording_id in recording_ids:
            celery_app.send_task(
                "backend.worker.tasks.refresh_meeting_edge_task",
                args=[recording_id],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to dispatch Meeting Edge refresh after settings update: %s",
            exc,
        )


async def _save_user_settings(
    settings: SettingsUpdate,
    current_user: User,
    db: AsyncSession,
) -> Any:
    current_settings = _get_mutable_user_settings(current_user.settings)
    is_admin = current_user.role in ["owner", "admin"] or current_user.is_superuser
    update_data = _build_settings_update_data(settings, is_admin=is_admin)

    try:
        if "ollama_api_url" in update_data:
            update_data["ollama_api_url"] = validate_ollama_api_url(
                update_data["ollama_api_url"],
                allow_private=True,
            )
        for key, value in update_data.items():
            config_manager.validate_config_value(key, value)
    except ValueError as e:
        raise sanitized_http_exception(
            logger=logger,
            status_code=400,
            client_message="Invalid settings value.",
            log_message="Rejected settings update due to invalid value.",
            exc=e,
        )

    if is_admin:
        _persist_install_wide_ai_settings(update_data)

    user_scoped_updates = {
        key: value for key, value in update_data.items() if key not in INSTALL_WIDE_AI_SETTING_KEYS
    }
    current_settings.update(user_scoped_updates)
    current_user.settings = current_settings

    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)

    meeting_edge_keys = {"meeting_edge_context_level", "enable_meeting_edge"}
    if meeting_edge_keys.intersection(update_data):
        await _dispatch_meeting_edge_refresh_for_active_recordings(db, current_user)

    model_keys = {"whisper_model_size", "transcription_backend", "parakeet_model", "canary_model"}
    if is_admin and model_keys.intersection(update_data):
        prepared_settings = dict(current_settings)
        try:
            enqueue_model_preparation(
                whisper_model_size=prepared_settings.get("whisper_model_size"),
                transcription_backend=prepared_settings.get("transcription_backend"),
                parakeet_model=prepared_settings.get("parakeet_model"),
                canary_model=prepared_settings.get("canary_model"),
                include_core=(
                    "whisper_model_size" in update_data
                    or update_data.get("transcription_backend") == "whisper"
                ),
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to queue model preparation after settings update: %s", e, exc_info=True)

    return await _merge_settings(current_user.settings, db)

@router.get("", response_model=Any)
async def get_settings_root(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Get current user settings (root path).
    Returns a merged view of system config, defaults, and user overrides.
    """
    return await _merge_settings(current_user.settings, db)

@router.get("/", response_model=Any)
async def get_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Get current user settings.
    Returns a merged view of system config, defaults, and user overrides.
    """
    return await _merge_settings(current_user.settings, db)

@router.post("", response_model=Any)
async def update_settings_root(
    settings: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Update user settings (root path).
    """
    return await _save_user_settings(settings, current_user, db)

@router.post("/", response_model=Any)
async def update_settings(
    settings: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Update user settings.
    """
    return await _save_user_settings(settings, current_user, db)


# --- Personal Dictionary ---

class WordPayload(BaseModel):
    word: str

@router.get("/personal-dictionary", response_model=List[str])
async def get_personal_dictionary(
    current_user: User = Depends(get_current_user),
) -> List[str]:
    settings = current_user.settings or {}
    return settings.get("personal_dictionary", [])

@router.post("/personal-dictionary", response_model=List[str])
async def add_personal_dictionary_word(
    payload: WordPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[str]:
    current_settings = _get_mutable_user_settings(current_user.settings)
    words = current_settings.get("personal_dictionary", [])
    normalised = payload.word.strip()
    if not normalised:
        raise HTTPException(status_code=400, detail="Word cannot be empty.")
    if normalised not in words:
        words.append(normalised)
    current_settings["personal_dictionary"] = words
    current_user.settings = current_settings
    db.add(current_user)
    await db.commit()
    return words

@router.delete("/personal-dictionary/{word}", response_model=List[str])
async def remove_personal_dictionary_word(
    word: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[str]:
    current_settings = _get_mutable_user_settings(current_user.settings)
    words = current_settings.get("personal_dictionary", [])
    words = [w for w in words if w != word]
    current_settings["personal_dictionary"] = words
    current_user.settings = current_settings
    db.add(current_user)
    await db.commit()
    return words


# --- Spellcheck Ignored Words ---

@router.get("/spellcheck-ignored", response_model=List[str])
async def get_spellcheck_ignored(
    current_user: User = Depends(get_current_user),
) -> List[str]:
    settings = current_user.settings or {}
    return settings.get("spellcheck_ignored_words", [])

@router.post("/spellcheck-ignored", response_model=List[str])
async def add_spellcheck_ignored_word(
    payload: WordPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[str]:
    current_settings = _get_mutable_user_settings(current_user.settings)
    words = current_settings.get("spellcheck_ignored_words", [])
    normalised = payload.word.strip()
    if not normalised:
        raise HTTPException(status_code=400, detail="Word cannot be empty.")
    if normalised not in words:
        words.append(normalised)
    current_settings["spellcheck_ignored_words"] = words
    current_user.settings = current_settings
    db.add(current_user)
    await db.commit()
    return words

@router.delete("/spellcheck-ignored/{word}", response_model=List[str])
async def remove_spellcheck_ignored_word(
    word: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[str]:
    current_settings = _get_mutable_user_settings(current_user.settings)
    words = current_settings.get("spellcheck_ignored_words", [])
    words = [w for w in words if w != word]
    current_settings["spellcheck_ignored_words"] = words
    current_user.settings = current_settings
    db.add(current_user)
    await db.commit()
    return words
