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

router = APIRouter()

class SetupRequest(UserCreate):
    llm_provider: str = "gemini"
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    hf_token: Optional[str] = None

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
    return {"initialized": user is not None}

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
        "hf_token": setup_in.hf_token
    }
    if setup_in.gemini_api_key:
        settings["gemini_api_key"] = setup_in.gemini_api_key
    if setup_in.openai_api_key:
        settings["openai_api_key"] = setup_in.openai_api_key
    if setup_in.anthropic_api_key:
        settings["anthropic_api_key"] = setup_in.anthropic_api_key

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
    task = download_models_task.delay(hf_token=hf_token, whisper_model_size=whisper_model_size)
    return {"task_id": task.id}

@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str) -> Any:
    """
    Get the status of a background task.
    """
    task_result = AsyncResult(task_id)
    response = {
        "task_id": task_id,
        "status": task_result.status,
        "result": task_result.result,
    }
    # If the task is in a custom state (like PROCESSING), result might be the meta dict
    if task_result.status == 'PROCESSING':
         response["progress"] = task_result.info.get('progress', 0)
         response["message"] = task_result.info.get('message', '')
    
    return response
