from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional
import httpx
from backend.processing.llm_services import get_llm_backend
from backend.api.deps import get_db, get_current_admin_user
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from backend.models.user import User
from backend.utils.config_manager import config_manager
import logging
import urllib.parse

logger = logging.getLogger(__name__)

def validate_api_url_for_ssrf(url: Optional[str]):
    if not url:
        return
        
    # If the URL matches the default configured OLLAMA_API_URL, it's safe (admin configured it via env)
    default_ollama_url = config_manager.get("ollama_api_url")
    if url == default_ollama_url:
        return

    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return
            
        hostname = hostname.lower()
        
        # Block obvious internal hostnames and IPs if they differ from the default
        forbidden_hosts = [
            "localhost", "127.0.0.1", "0.0.0.0", "socket-proxy", "db", "redis", "worker", "api", "frontend", "host.docker.internal", "nginx"
        ]
        
        if hostname in forbidden_hosts or hostname.startswith("192.168.") or hostname.startswith("10.") or hostname.startswith("172."):
            raise HTTPException(status_code=400, detail="Invalid API URL: Internal network hostnames are blocked for security reasons.")
            
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail="Invalid API URL format")

router = APIRouter()

class ValidateLLMRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    model: Optional[str] = None
    api_url: Optional[str] = None

class ValidateHFRequest(BaseModel):
    token: str

class ListModelsRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    api_url: Optional[str] = None

async def check_setup_permission(db: AsyncSession, request: Request):
    """
    Check if the endpoint is allowed.
    Allowed if:
    1. System is NOT initialized (no users exist).
    2. OR User is authenticated as Admin/Owner (JWT token in header).
    """
    # Check if system is initialized
    query = select(User).limit(1)
    result = await db.execute(query)
    is_initialized = result.scalar_one_or_none() is not None

    if not is_initialized:
        return True

    # Initialised system: authenticate manually. Depends(get_current_admin_user)
    # cannot be used at the router level as it would block the unauthenticated
    # (pre-initialisation) case.
    
    auth_header = request.headers.get('Authorization')
    if not auth_header:
         raise HTTPException(status_code=401, detail="System is initialized. Authentication required.")

    from backend.api.deps import get_current_user, get_current_admin_user
    from backend.core.security import ALGORITHM, SECRET_KEY
    from jose import jwt, JWTError
    
    token = auth_header.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        token_data = payload.get("sub")
        if not token_data:
             raise HTTPException(status_code=401, detail="Invalid token")
             
        query = select(User).where(User.username == token_data)
        result = await db.execute(query)
        user = result.scalar_one_or_none()
        
        if not user or not user.is_active:
             raise HTTPException(status_code=401, detail="Invalid user")
             
        if user.role not in ["owner", "admin"] and not user.is_superuser:
            raise HTTPException(status_code=403, detail="Not authorized")
            
        return user
        
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

@router.get("/initial-config")
async def get_initial_config(
    req: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Get initial configuration from environment variables relative to the config manager.
    Returns masked values for security.
    """
    await check_setup_permission(db, req)
    
    # helper to mask key
    def mask_key(key):
        if not key or len(key) < 8:
            return None
        return f"{key[:3]}...{key[-4:]}"

    from backend.utils.config_manager import async_get_system_api_keys
    system_keys = await async_get_system_api_keys(db)

    return {
        "llm_provider": config_manager.get("llm_provider"),
        "gemini_api_key": mask_key(system_keys.get("gemini_api_key")),
        "openai_api_key": mask_key(system_keys.get("openai_api_key")),
        "anthropic_api_key": mask_key(system_keys.get("anthropic_api_key")),
        "ollama_api_url": config_manager.get("ollama_api_url"),
        "hf_token": mask_key(system_keys.get("hf_token"))
    }

@router.post("/validate-llm")
async def validate_llm(
    request: ValidateLLMRequest,
    req: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Validate LLM API Key.
    """
    user = await check_setup_permission(db, req)
    
    try:
        # Fallback to database user settings, then config manager if api_key/url is missing or looks masked
        api_key = request.api_key
        if not api_key or "..." in api_key or "***" in api_key:
             from backend.utils.config_manager import async_get_system_api_keys
             system_keys = await async_get_system_api_keys(db)
             db_key = user.settings.get(f"{request.provider}_api_key") if user and hasattr(user, "settings") and user.settings else None
             api_key = db_key if db_key else system_keys.get(f"{request.provider}_api_key")
             
        api_url = request.api_url
        if request.provider == "ollama" and (not api_url or "..." in api_url or "***" in api_url):
             db_url = user.settings.get("ollama_api_url") if user and hasattr(user, "settings") and user.settings else None
             api_url = db_url if db_url else config_manager.get("ollama_api_url")

        validate_api_url_for_ssrf(api_url)

        llm = get_llm_backend(request.provider, api_key=api_key, model=request.model, api_url=api_url)
        llm.validate_api_key()
        
        models = []
        if request.provider == "ollama":
            models = llm.list_models()
            return {"valid": True, "message": "Connected to Ollama successfully.", "models": models}
            
        provider_name = request.provider.capitalize() if request.provider else "LLM"
        return {"valid": True, "message": f"{provider_name} API key is valid."}
    except Exception as e:
        logger.error(f"Validation failed for {request.provider}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/validate-hf")
async def validate_hf(
    request: ValidateHFRequest,
    req: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Validate Hugging Face Token.
    """
    user = await check_setup_permission(db, req)

    try:
        token = request.token
        if not token or "..." in token or "***" in token:
            from backend.utils.config_manager import async_get_system_api_keys
            system_keys = await async_get_system_api_keys(db)
            db_token = user.settings.get("hf_token") if user and hasattr(user, "settings") and user.settings else None
            token = db_token if db_token else system_keys.get("hf_token")
            
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://huggingface.co/api/whoami-v2",
                headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            user_info = response.json()
            return {"valid": True, "message": f"Token valid for user: {user_info.get('name', 'Unknown')}"}
    except Exception as e:
        logger.error(f"HF Validation failed: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid Hugging Face token: {str(e)}")

@router.post("/list-models")
async def list_models(
    request: ListModelsRequest,
    req: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    List available models for a given provider and API key.
    """
    user = await check_setup_permission(db, req)

    try:
        # Fallback logic
        api_key = request.api_key
        if not api_key or "..." in api_key or "***" in api_key:
             from backend.utils.config_manager import async_get_system_api_keys
             system_keys = await async_get_system_api_keys(db)
             db_key = user.settings.get(f"{request.provider}_api_key") if user and hasattr(user, "settings") and user.settings else None
             api_key = db_key if db_key else system_keys.get(f"{request.provider}_api_key")
             
        api_url = request.api_url
        if request.provider == "ollama" and (not api_url or "..." in api_url or "***" in api_url):
             db_url = user.settings.get("ollama_api_url") if user and hasattr(user, "settings") and user.settings else None
             api_url = db_url if db_url else config_manager.get("ollama_api_url")

        validate_api_url_for_ssrf(api_url)

        llm = get_llm_backend(request.provider, api_key=api_key, api_url=api_url)
        models = llm.list_models()
        return {"models": models}
    except Exception as e:
        logger.error(f"Failed to list models for {request.provider}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
