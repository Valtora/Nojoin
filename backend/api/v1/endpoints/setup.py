import hmac
import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.error_handling import sanitized_http_exception
from backend.processing.llm_services import get_llm_backend
from backend.api.deps import (
    STANDARD_USER_SCOPE_REQUIREMENTS,
    STANDARD_USER_TOKEN_TYPES,
    enforce_trusted_browser_origin,
    enforce_password_change_policy,
    get_authenticated_user_from_token,
    get_db,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from backend.models.user import User
from backend.utils.config_manager import config_manager
from backend.utils.ollama_url_policy import (
    OllamaURLValidationError,
    validate_ollama_api_url,
)

logger = logging.getLogger(__name__)

FIRST_RUN_PASSWORD_AUTH_SCHEME = "Bootstrap"
FIRST_RUN_PASSWORD_ENV_KEY = "FIRST_RUN_PASSWORD"
FIRST_RUN_PASSWORD_REQUIRED_DETAIL = "Bootstrap password required for first-run setup."
FIRST_RUN_PASSWORD_NOT_CONFIGURED_DETAIL = (
    "First-run setup is disabled until FIRST_RUN_PASSWORD is set. "
    "Set the env var and restart or redeploy Nojoin before initialising the system."
)
FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL = "First-run setup access denied."
PUBLIC_LLM_VALIDATION_ERROR_DETAIL = "Unable to validate the AI provider configuration."
PUBLIC_HF_VALIDATION_ERROR_DETAIL = "Unable to validate the Hugging Face token."
PUBLIC_MODEL_LIST_ERROR_DETAIL = "Unable to list AI provider models."


def _raise_setup_validation_error(
    *,
    client_detail: str,
    log_message: str,
    exc: Exception,
) -> None:
    raise sanitized_http_exception(
        logger=logger,
        status_code=400,
        client_message=client_detail,
        log_message=log_message,
        exc=exc,
    )


def _validate_setup_ollama_api_url(url: str | None) -> str | None:
    if not url:
        return url

    try:
        return validate_ollama_api_url(
            url,
            allow_private=True,
            trusted_url=config_manager.get("ollama_api_url"),
        )
    except OllamaURLValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


async def is_system_initialized(db: AsyncSession) -> bool:
    query = select(User).limit(1)
    result = await db.execute(query)
    return result.scalar_one_or_none() is not None


def get_first_run_password(request: Request) -> Optional[str]:
    authorization_header = request.headers.get("Authorization")
    if not authorization_header:
        return None

    scheme, _, credential = authorization_header.partition(" ")
    if scheme.lower() != FIRST_RUN_PASSWORD_AUTH_SCHEME.lower() or not credential:
        return None

    return credential.strip()


def require_first_run_password(request: Request) -> None:
    configured_password = os.getenv(FIRST_RUN_PASSWORD_ENV_KEY)
    if not configured_password:
        raise HTTPException(
            status_code=503,
            detail=FIRST_RUN_PASSWORD_NOT_CONFIGURED_DETAIL,
        )

    provided_password = get_first_run_password(request)
    if not provided_password or not hmac.compare_digest(
        provided_password,
        configured_password,
    ):
        raise HTTPException(
            status_code=403,
            detail=FIRST_RUN_PASSWORD_REQUIRED_DETAIL,
        )

async def check_setup_permission(db: AsyncSession, request: Request):
    """
    Check if the endpoint is allowed.
    Allowed if:
    1. System is NOT initialized (no users exist).
    2. OR User is authenticated as Admin/Owner (JWT token in header).
    """
    is_initialized = await is_system_initialized(db)

    if not is_initialized:
        require_first_run_password(request)
        return None

    # Initialised system: authenticate manually. Depends(get_current_admin_user)
    # cannot be used at the router level as it would block the unauthenticated
    # (pre-initialisation) case.
    
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header:
        scheme, _, auth_token = auth_header.partition(" ")
        if scheme.lower() == "bearer" and auth_token:
            token = auth_token.strip()
    if not token:
        token = request.cookies.get("access_token")
        if token:
            enforce_trusted_browser_origin(request)

    if not token:
        raise HTTPException(status_code=403, detail=FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL)

    try:
        user = await get_authenticated_user_from_token(
            db,
            token,
            allowed_token_types=STANDARD_USER_TOKEN_TYPES,
            required_scopes_by_type=STANDARD_USER_SCOPE_REQUIREMENTS,
        )
    except HTTPException as exc:
        if exc.status_code in {401, 403}:
            raise HTTPException(status_code=403, detail=FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL)
        raise

    enforce_password_change_policy(user, path=request.url.path, method=request.method)

    if user.role not in ["owner", "admin"] and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")

    return user

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
    llm_provider = config_manager.get("llm_provider", "gemini")
    selected_model_key = "ollama_model" if llm_provider == "ollama" else f"{llm_provider}_model"

    return {
        "llm_provider": llm_provider,
        "gemini_api_key": mask_key(system_keys.get("gemini_api_key")),
        "openai_api_key": mask_key(system_keys.get("openai_api_key")),
        "anthropic_api_key": mask_key(system_keys.get("anthropic_api_key")),
        "ollama_api_url": config_manager.get("ollama_api_url"),
        "hf_token": mask_key(system_keys.get("hf_token")),
        "selected_model": config_manager.get(selected_model_key),
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
    is_public_request = user is None
    
    try:
        provider = request.provider
        from backend.utils.config_manager import get_system_api_keys
        system_keys = get_system_api_keys()
        api_key = system_keys.get(f"{provider}_api_key")
             
        api_url = None
        if provider == "ollama":
            api_url = config_manager.get("ollama_api_url")
            api_url = _validate_setup_ollama_api_url(api_url)

        llm = get_llm_backend(
            provider,
            api_key=api_key,
            model=request.model,
            api_url=api_url,
            allow_private_api_url=provider == "ollama",
        )
        llm.validate_api_key()
        
        models = []
        if provider == "ollama":
            models = llm.list_models()
            return {"valid": True, "message": "Connected to Ollama successfully.", "models": models}
            
        provider_name = provider.capitalize() if provider else "LLM"
        return {"valid": True, "message": f"{provider_name} API key is valid."}
    except HTTPException:
        if is_public_request:
            logger.warning("Public setup LLM validation failed for provider %s.", request.provider)
            raise HTTPException(status_code=400, detail=PUBLIC_LLM_VALIDATION_ERROR_DETAIL)
        raise
    except Exception as exc:
        _raise_setup_validation_error(
            client_detail=PUBLIC_LLM_VALIDATION_ERROR_DETAIL,
            log_message=(
                "Public setup LLM validation failed for provider "
                f"'{request.provider}'."
                if is_public_request
                else "Authenticated setup LLM validation failed for provider "
                f"'{request.provider}'."
            ),
            exc=exc,
        )

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
    is_public_request = user is None

    try:
        from backend.utils.config_manager import get_system_api_keys
        system_keys = get_system_api_keys()
        token = system_keys.get("hf_token")
        if not token:
            raise ValueError("Hugging Face token is not set.")
            
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://huggingface.co/api/whoami-v2",
                headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            response.json()
            return {"valid": True, "message": "Hugging Face token is valid."}
    except HTTPException:
        if is_public_request:
            logger.warning("Public setup Hugging Face validation failed.")
            raise HTTPException(status_code=400, detail=PUBLIC_HF_VALIDATION_ERROR_DETAIL)
        raise
    except Exception as exc:
        _raise_setup_validation_error(
            client_detail=PUBLIC_HF_VALIDATION_ERROR_DETAIL,
            log_message=(
                "Public setup Hugging Face validation failed."
                if is_public_request
                else "Authenticated setup Hugging Face validation failed."
            ),
            exc=exc,
        )

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
    is_public_request = user is None

    try:
        provider = request.provider
        from backend.utils.config_manager import get_system_api_keys
        system_keys = get_system_api_keys()
        api_key = system_keys.get(f"{provider}_api_key")
             
        api_url = None
        if provider == "ollama":
            api_url = config_manager.get("ollama_api_url")
            api_url = _validate_setup_ollama_api_url(api_url)

        llm = get_llm_backend(
            provider,
            api_key=api_key,
            api_url=api_url,
            allow_private_api_url=provider == "ollama",
        )
        models = llm.list_models()
        return {"models": models}
    except HTTPException:
        if is_public_request:
            logger.warning("Public setup model listing failed for provider %s.", request.provider)
            raise HTTPException(status_code=400, detail=PUBLIC_MODEL_LIST_ERROR_DETAIL)
        raise
    except Exception as exc:
        _raise_setup_validation_error(
            client_detail=PUBLIC_MODEL_LIST_ERROR_DETAIL,
            log_message=(
                "Public setup model listing failed for provider "
                f"'{request.provider}'."
                if is_public_request
                else "Authenticated setup model listing failed for provider "
                f"'{request.provider}'."
            ),
            exc=exc,
        )
