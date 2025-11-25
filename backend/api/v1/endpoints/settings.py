from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from backend.utils.config_manager import ConfigManager

router = APIRouter()
config_manager = ConfigManager()

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

@router.get("/")
async def get_settings():
    """
    Get current application settings.
    """
    return config_manager.config

@router.post("/")
async def update_settings(settings: SettingsUpdate):
    """
    Update application settings.
    """
    current_config = config_manager.config
    
    # Update only provided fields
    updates = settings.dict(exclude_unset=True)
    for key, value in updates.items():
        current_config[key] = value
        
    config_manager.save_config(current_config)
    return current_config
