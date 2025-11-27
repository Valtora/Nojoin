from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.models.user import User

router = APIRouter()

class SettingsUpdate(BaseModel):
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    llm_provider: Optional[str] = None
    whisper_model_size: Optional[str] = None
    theme: Optional[str] = None
    hf_token: Optional[str] = None
    worker_url: Optional[str] = None
    companion_url: Optional[str] = None
    gemini_model: Optional[str] = None
    openai_model: Optional[str] = None
    anthropic_model: Optional[str] = None
    infer_meeting_title: Optional[bool] = None
    enable_auto_voiceprints: Optional[bool] = None

@router.get("", response_model=Any)
async def get_settings_root(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Get current user settings (root path).
    """
    return current_user.settings or {}

@router.get("/", response_model=Any)
async def get_settings(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Get current user settings.
    """
    return current_user.settings or {}

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
    current_settings = current_user.settings or {}
    update_data = settings.dict(exclude_unset=True)
    
    # Merge new settings
    current_settings.update(update_data)
    current_user.settings = current_settings
    
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user.settings

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
    current_settings = current_user.settings or {}
    update_data = settings.dict(exclude_unset=True)
    
    # Merge new settings
    current_settings.update(update_data)
    current_user.settings = current_settings
    
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user.settings
