from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from celery.result import AsyncResult

from backend.api.deps import get_db
from backend.core.security import get_password_hash
from backend.models.user import User, UserCreate
from backend.worker.tasks import download_models_task
from backend.utils.config_manager import config_manager
from backend.preload_models import check_model_status
from backend.utils.download_progress import get_download_progress, is_download_in_progress

router = APIRouter()

class SetupRequest(UserCreate):
    llm_provider: str = "gemini"
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    ollama_api_url: Optional[str] = None
    hf_token: Optional[str] = None
    whisper_model_size: Optional[str] = "turbo"
    selected_model: Optional[str] = None

@router.get("/status")
async def get_system_status(
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Check if the system is initialized (has at least one user).
    """
    query = select(User).limit(1)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    # Get web_app_url from system config
    system_config = config_manager.config
    web_app_url = system_config.get("web_app_url", "https://localhost:14443")
    
    return {
        "initialized": user is not None,
        "web_app_url": web_app_url
    }

@router.post("/setup")
async def setup_system(
    *,
    db: AsyncSession = Depends(get_db),
    setup_in: SetupRequest,
) -> Any:
    """
    Initialize the system with the first admin user and initial configuration.
    Only works if no users exist.
    """
    query = select(User).limit(1)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="System is already initialized.",
        )
    
    # Construct settings dict
    settings = {
        "llm_provider": setup_in.llm_provider,
        "hf_token": setup_in.hf_token,
        "whisper_model_size": setup_in.whisper_model_size
    }
    if setup_in.gemini_api_key:
        settings["gemini_api_key"] = setup_in.gemini_api_key
    if setup_in.openai_api_key:
        settings["openai_api_key"] = setup_in.openai_api_key
    if setup_in.anthropic_api_key:
        settings["anthropic_api_key"] = setup_in.anthropic_api_key
    if setup_in.ollama_api_url:
        settings["ollama_api_url"] = setup_in.ollama_api_url
    
    if setup_in.selected_model:
        if setup_in.llm_provider == "ollama":
            settings["ollama_model"] = setup_in.selected_model
        else:
            settings[f"{setup_in.llm_provider}_model"] = setup_in.selected_model

    user = User(
        username=setup_in.username,
        email=setup_in.email,
        hashed_password=get_password_hash(setup_in.password),
        is_superuser=True,
        force_password_change=False, # First user sets their own password, so no need to force change
        settings=settings
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    # Persist key system-wide settings so workers and other processes can access them
    try:
        # Only persist a whitelist of system-level keys to avoid overwriting user-specific preferences
        system_keys = [
            "llm_provider",
            "gemini_api_key",
            "openai_api_key",
            "anthropic_api_key",
            "hf_token",
            "whisper_model_size",
        ]
        for k, v in settings.items():
            if k in system_keys or k.endswith("_model"):
                if v is not None:
                    config_manager.set(k, v)
    except Exception as e:
        # Log but do not fail the setup if persisting to disk fails
        import logging
        logging.getLogger(__name__).warning(f"Failed to persist system settings: {e}")
    return {"message": "System initialized successfully"}

@router.post("/download-models")
async def trigger_model_download(
    hf_token: Optional[str] = None,
    whisper_model_size: Optional[str] = None,
    # No auth dependency here because this is called during setup flow where user might not be fully logged in yet
    # But we should probably protect it or ensure it's only callable if system is just initialized.
    # For simplicity in this setup flow, we'll allow it.
) -> Any:
    """
    Trigger the background task to download models.
    """
    task = download_models_task.delay(hf_token=hf_token, whisper_model_size=whisper_model_size) # type: ignore
    return {"task_id": task.id}

@router.get("/download-progress")
async def get_current_download_progress() -> Any:
    """
    Get the current model download progress from shared state.
    This allows the frontend to see progress from preload_models.py or any active download task.
    
    Returns:
        - progress: percentage (0-100)
        - message: current status message
        - speed: download speed (if available)
        - eta: estimated time remaining (if available)
        - status: "downloading", "complete", "error", or null if no active download
        - in_progress: boolean indicating if a download is currently active
    """
    progress = get_download_progress()
    
    if progress is None:
        return {
            "in_progress": False,
            "progress": None,
            "message": None,
            "speed": None,
            "eta": None,
            "status": None
        }
    
    return {
        "in_progress": is_download_in_progress(),
        "progress": min(progress.get("progress", 0), 100),
        "message": progress.get("message", ""),
        "speed": progress.get("speed"),
        "eta": progress.get("eta"),
        "status": progress.get("status", "downloading"),
        "stage": progress.get("stage")
    }

@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str) -> Any:
    """
    Get the status of a background task.
    """
    task_result = AsyncResult(task_id)
    
    # Handle potential serialization errors if result contains exceptions
    result_data = None
    if task_result.status == 'FAILURE':
        result_data = str(task_result.result)
    elif task_result.status == 'SUCCESS':
        result_data = task_result.result
    else:
        # For PENDING, STARTED, RETRY etc.
        # If it's an exception object, convert to string
        if isinstance(task_result.result, Exception):
             result_data = str(task_result.result)
        else:
             result_data = task_result.result

    response = {
        "task_id": task_id,
        "status": task_result.status,
        "result": result_data,
    }
    
    # If the task is in a custom state (like PROCESSING), result might be the meta dict
    # Celery stores meta info in .info for custom states
    if task_result.status == 'PROCESSING':
         # Ensure we're accessing a dict
         info = task_result.info
         if isinstance(info, dict):
            response["progress"] = info.get('progress', 0)
            response["message"] = info.get('message', '')
            # Pass through other meta fields like speed/eta if present
            response["result"] = info 
         else:
            response["message"] = str(info)
    
    return response

@router.get("/models/status")
async def get_models_status(whisper_model_size: Optional[str] = None) -> Any:
    """
    Get the status of all models.
    """
    return check_model_status(whisper_model_size=whisper_model_size)

@router.delete("/models/{model_name}")
async def delete_model_endpoint(
    model_name: str,
    # db: AsyncSession = Depends(get_db), # Add auth if needed later
) -> Any:
    """
    Delete a specific model from the cache.
    """
    from backend.preload_models import delete_model
    
    if model_name not in ["whisper", "pyannote", "embedding"]:
        raise HTTPException(status_code=400, detail="Invalid model name")
        
    try:
        success = delete_model(model_name)
        if success:
            return {"message": f"Model {model_name} deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail=f"Model {model_name} not found or could not be deleted")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
