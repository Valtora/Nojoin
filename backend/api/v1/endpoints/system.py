from typing import Any, Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from celery.result import AsyncResult

from backend.api.deps import get_db, get_current_user, get_current_admin_user
from backend.core.security import get_password_hash
from backend.models.user import User, UserCreate
from backend.worker.tasks import download_models_task
from backend.utils.config_manager import config_manager
from backend.preload_models import check_model_status
from backend.utils.download_progress import get_download_progress, is_download_in_progress
from backend.seed_demo import seed_demo_data

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
        config_manager.update_config(settings)
    except Exception as e:
        # Log error but don't fail the request since DB is updated
        print(f"Failed to update config file: {e}")

    return user

@router.post("/download-models")
async def trigger_model_download(
    hf_token: Optional[str] = None,
    whisper_model_size: Optional[str] = None,
    current_user: User = Depends(get_current_admin_user),
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
    variant: Optional[str] = None,
    current_user: User = Depends(get_current_admin_user),
) -> Any:

    """
    Delete a specific model from the cache.
    """
    from backend.preload_models import delete_model
    
    if model_name not in ["whisper", "pyannote", "embedding"]:
        raise HTTPException(status_code=400, detail="Invalid model name")
        
    try:
        success = delete_model(model_name, whisper_model_size=variant)
        if success:
            return {"message": f"Model {model_name} deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail=f"Model {model_name} not found or could not be deleted")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/seed-demo")
async def seed_demo(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Re-create the demo meeting for the current user if it doesn't exist.
    """
    if current_user.id:
        await seed_demo_data(user_id=current_user.id, force=True)
    return {"message": "Demo data seeding initiated"}

@router.get("/demo-recording")
async def get_demo_recording(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Get the demo recording for the current user.
    Returns the recording ID if it exists, otherwise returns None.
    """
    from sqlmodel import select
    from backend.models.recording import Recording
    
    query = select(Recording).where(
        Recording.name == "Welcome to Nojoin",
        Recording.user_id == current_user.id
    )
    result = await db.execute(query)
    recording = result.scalar_one_or_none()
    
    if recording:
        return {"id": recording.id}
    return {"id": None}

@router.get("/companion-releases")
async def get_companion_releases() -> Any:
    """
    Fetch the latest companion app release from GitHub.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.github.com/repos/Valtora/Nojoin/releases")
            response.raise_for_status()
            releases = response.json()
            
            for release in releases:
                # Look for assets
                assets = release.get("assets", [])
                windows_asset = None
                
                for asset in assets:
                    name = asset.get("name", "").lower()
                    if name.endswith(".exe") and "portable" not in name:
                        windows_asset = asset
                        break
                
                if windows_asset:
                    return {
                        "version": release.get("tag_name"),
                        "windows_url": windows_asset.get("browser_download_url"),
                        # Add placeholders for other OSs when supported
                        "macos_url": None,
                        "linux_url": None
                    }
            
            # If no suitable release found
            return {
                "version": None,
                "windows_url": None,
                "macos_url": None,
                "linux_url": None
            }
            
    except Exception as e:
        print(f"Error fetching releases: {e}")
        # Fallback to generic releases page
        return {
            "version": None,
            "windows_url": "https://github.com/Valtora/Nojoin/releases",
            "macos_url": "https://github.com/Valtora/Nojoin/releases",
            "linux_url": "https://github.com/Valtora/Nojoin/releases"
        }

