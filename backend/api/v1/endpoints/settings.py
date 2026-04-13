from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urlparse

from backend.api.deps import get_current_user, get_db
from backend.models.user import User
from backend.utils.config_manager import get_default_user_settings, config_manager, WHISPER_MODEL_SIZES, APP_THEMES, SENSITIVE_KEYS
from backend.utils.timezones import validate_timezone_name

router = APIRouter()

class SettingsUpdate(BaseModel):
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    llm_provider: Optional[str] = None
    whisper_model_size: Optional[str] = None
    theme: Optional[str] = None
    hf_token: Optional[str] = None
    gemini_model: Optional[str] = None
    openai_model: Optional[str] = None
    anthropic_model: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_api_url: Optional[str] = None
    enable_auto_voiceprints: Optional[bool] = None
    auto_generate_notes: Optional[bool] = None
    auto_generate_title: Optional[bool] = None
    prefer_short_titles: Optional[bool] = None
    auto_infer_speakers: Optional[bool] = None
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

    @field_validator('ollama_api_url')
    @classmethod
    def validate_ollama_api_url(cls, value: Optional[str]) -> Optional[str]:
        if value:
            try:
                result = urlparse(value)
                if not all([result.scheme, result.netloc]):
                    raise ValueError("Invalid URL format")
            except ValueError:
                raise ValueError("Invalid URL format")
        return value

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value:
            return validate_timezone_name(value)
        return value

async def _merge_settings(user_settings: dict, db: AsyncSession) -> dict:
    """
    Merges system config, default user settings, and user-specific settings.
    Priority: User Settings > Default User Settings > System Config
    """
    # 1. Start with System Config (read-only for users)
    merged = config_manager.get_all()
    
    # 2. Apply Default User Settings
    default_user_settings = get_default_user_settings()
    merged.update(default_user_settings)
    
    # 2.5 Apply Owner's System-Wide LLM Configuration Defaults
    from backend.models.user import User
    from sqlmodel import select
    result = await db.execute(select(User).where(User.role == "owner"))
    owner = result.scalar_one_or_none()
    owner_settings = getattr(owner, "settings", {}) if owner else {}
    
    system_fields = ["llm_provider", "gemini_model", "openai_model", "anthropic_model", "ollama_model", "ollama_api_url"]
    for sys_field in system_fields:
        if owner_settings and owner_settings.get(sys_field):
            merged[sys_field] = owner_settings[sys_field]
    
    # 3. Apply User Specific Settings
    if user_settings:
        merged.update({k: v for k, v in user_settings.items() if v is not None})
        
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
    # Update user settings
    # Create a copy to ensure SQLAlchemy detects the change
    current_settings = dict(current_user.settings) if current_user.settings else {}
    update_data = settings.dict(exclude_unset=True)
    
    is_admin = current_user.role in ["owner", "admin"] or current_user.is_superuser
    
    # Ignore any sensitive keys that are masked to avoid overwriting real keys
    # Also drop them entirely if the user is not an admin
    for key in SENSITIVE_KEYS:
        if key in update_data:
            if not is_admin:
                del update_data[key]
            elif update_data[key] and ("..." in update_data[key] or "***" in update_data[key]):
                del update_data[key]
    
    # Validate settings
    try:
        for key, value in update_data.items():
            config_manager.validate_config_value(key, value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Merge new settings
    current_settings.update(update_data)
    current_user.settings = current_settings
    
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    
    return await _merge_settings(current_user.settings, db)

@router.post("/", response_model=Any)
async def update_settings(
    settings: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Update user settings.
    """
    # Update user settings
    # Create a copy to ensure SQLAlchemy detects the change
    current_settings = dict(current_user.settings) if current_user.settings else {}
    update_data = settings.dict(exclude_unset=True)
    
    is_admin = current_user.role in ["owner", "admin"] or current_user.is_superuser
    
    # Ignore any sensitive keys that are masked to avoid overwriting real keys
    # Also drop them entirely if the user is not an admin
    for key in SENSITIVE_KEYS:
        if key in update_data:
            if not is_admin:
                del update_data[key]
            elif update_data[key] and ("..." in update_data[key] or "***" in update_data[key]):
                del update_data[key]
            del update_data[key]
    
    # Validate settings
    try:
        for key, value in update_data.items():
            config_manager.validate_config_value(key, value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Merge new settings
    current_settings.update(update_data)
    current_user.settings = current_settings
    
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    
    return await _merge_settings(current_user.settings, db)


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
    current_settings = dict(current_user.settings) if current_user.settings else {}
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
    current_settings = dict(current_user.settings) if current_user.settings else {}
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
    current_settings = dict(current_user.settings) if current_user.settings else {}
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
    current_settings = dict(current_user.settings) if current_user.settings else {}
    words = current_settings.get("spellcheck_ignored_words", [])
    words = [w for w in words if w != word]
    current_settings["spellcheck_ignored_words"] = words
    current_user.settings = current_settings
    db.add(current_user)
    await db.commit()
    return words
