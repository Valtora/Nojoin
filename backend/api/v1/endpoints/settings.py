from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.models.user import User
from backend.utils.config_manager import get_default_user_settings, config_manager

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
    # System settings that might be passed but should be ignored or handled separately if we allowed admin to change them
    # For now, we only allow user settings update here.

def _merge_settings(user_settings: dict) -> dict:
    """
    Merges system config, default user settings, and user-specific settings.
    Priority: User Settings > Default User Settings > System Config
    """
    # 1. Start with System Config (read-only for users)
    merged = config_manager.get_all()
    
    # 2. Apply Default User Settings
    default_user_settings = get_default_user_settings()
    merged.update(default_user_settings)
    
    # 3. Apply User Specific Settings
    if user_settings:
        merged.update(user_settings)
        
    return merged

@router.get("", response_model=Any)
async def get_settings_root(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Get current user settings (root path).
    Returns a merged view of system config, defaults, and user overrides.
    """
    return _merge_settings(current_user.settings)

@router.get("/", response_model=Any)
async def get_settings(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Get current user settings.
    Returns a merged view of system config, defaults, and user overrides.
    """
    return _merge_settings(current_user.settings)

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
    
    return _merge_settings(current_user.settings)

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
    
    return _merge_settings(current_user.settings)
