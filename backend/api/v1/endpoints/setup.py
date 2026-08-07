import hmac
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import (
    STANDARD_USER_SCOPE_REQUIREMENTS,
    STANDARD_USER_TOKEN_TYPES,
    enforce_password_change_policy,
    enforce_trusted_browser_origin,
    get_authenticated_user_from_token,
    get_db,
)
from backend.api.error_handling import sanitized_http_exception
from backend.models.user import User
from backend.processing.llm_services import get_llm_backend
from backend.utils.config_manager import config_manager
from backend.utils.ollama_url_policy import (
    OllamaURLValidationError,
    validate_ollama_api_url,
)
from backend.utils.rate_limit import enforce_rate_limit

logger = logging.getLogger(__name__)

FIRST_RUN_PASSWORD_AUTH_SCHEME = "Bootstrap"
FIRST_RUN_PASSWORD_ENV_KEY = "FIRST_RUN_PASSWORD"
# Every setup denial uses this one detail so anonymous responses are identical
# whether the system is initialised, the bootstrap password is wrong, or
# FIRST_RUN_PASSWORD is not configured. The specific reason is server-log only.
FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL = "First-run setup access denied."
SETUP_RATE_LIMIT = 60
SETUP_RATE_LIMIT_WINDOW_SECONDS = 10 * 60
SETUP_RATE_LIMIT_DETAIL = "Too many setup requests. Please try again later."
PUBLIC_LLM_VALIDATION_ERROR_DETAIL = "Unable to validate the AI provider configuration."
PUBLIC_MODEL_LIST_ERROR_DETAIL = "Unable to list AI provider models."
LLM_PROVIDER_ENV_KEY = "LLM_PROVIDER"


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
        logger.warning(
            "First-run setup request denied: FIRST_RUN_PASSWORD is not set. "
            "Set the env var and restart or redeploy Nojoin before initialising."
        )
        raise HTTPException(
            status_code=403,
            detail=FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL,
        )

    provided_password = get_first_run_password(request)
    if not provided_password or not hmac.compare_digest(
        provided_password,
        configured_password,
    ):
        logger.warning(
            "First-run setup request denied: missing or incorrect bootstrap password."
        )
        raise HTTPException(
            status_code=403,
            detail=FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL,
        )


async def enforce_setup_rate_limit(request: Request) -> None:
    """
    Shared per-client limit for the setup surface. Applied uniformly before any
    state-dependent work so throttling behaviour cannot disclose whether the
    system is initialised.
    """
    await enforce_rate_limit(
        request,
        namespace="setup",
        limit=SETUP_RATE_LIMIT,
        window_seconds=SETUP_RATE_LIMIT_WINDOW_SECONDS,
        detail=SETUP_RATE_LIMIT_DETAIL,
    )


async def check_setup_permission(db: AsyncSession, request: Request):
    """
    Check if the endpoint is allowed.
    Allowed if:
    1. System is NOT initialized (no users exist).
    2. OR User is authenticated as Admin/Owner (JWT token in header).
    """
    await enforce_setup_rate_limit(request)

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
        raise HTTPException(
            status_code=403, detail=FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL
        )

    try:
        user = await get_authenticated_user_from_token(
            db,
            token,
            allowed_token_types=STANDARD_USER_TOKEN_TYPES,
            required_scopes_by_type=STANDARD_USER_SCOPE_REQUIREMENTS,
        )
    except HTTPException as exc:
        if exc.status_code in {401, 403}:
            raise HTTPException(
                status_code=403, detail=FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL
            )
        raise

    enforce_password_change_policy(user, path=request.url.path, method=request.method)

    if user.role not in ["owner", "admin"] and not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")

    return user


@router.get("/initial-config")
async def get_initial_config(req: Request, db: AsyncSession = Depends(get_db)):
    """
    Get initial configuration from environment variables relative to the config manager.
    Returns masked values for security.
    """
    await check_setup_permission(db, req)

    def mask_key(key):
        if not key or len(key) < 8:
            return None
        return f"{key[:3]}...{key[-4:]}"

    from backend.preload_models import check_model_status
    from backend.utils.config_manager import async_get_system_api_keys

    system_keys = await async_get_system_api_keys(db)
    llm_provider = config_manager.get("llm_provider", "gemini")
    # `llm_provider` always resolves to something (gemini is the default), so on
    # its own it cannot tell the wizard apart from an operator who chose gemini
    # and one who set nothing at all. .env.example ships LLM_PROVIDER empty and
    # empty env values are ignored, which made the wizard report a missing
    # gemini key to operators who never picked gemini.
    llm_provider_selected = bool(os.getenv(LLM_PROVIDER_ENV_KEY, "").strip())
    secondary_llm_provider = config_manager.get("secondary_llm_provider") or None
    selected_model_key = (
        "ollama_model" if llm_provider == "ollama" else f"{llm_provider}_model"
    )
    model_status = check_model_status()
    pyannote_models_ready = bool(
        model_status.get("pyannote", {}).get("downloaded")
    ) and bool(model_status.get("embedding", {}).get("downloaded"))
    bundled_pyannote_models_ready = pyannote_models_ready and all(
        model_status.get(key, {}).get("source") == "bundled"
        for key in ("pyannote", "embedding")
    )

    return {
        "llm_provider": llm_provider,
        "llm_provider_selected": llm_provider_selected,
        "gemini_api_key": mask_key(system_keys.get("gemini_api_key")),
        "openai_api_key": mask_key(system_keys.get("openai_api_key")),
        "anthropic_api_key": mask_key(system_keys.get("anthropic_api_key")),
        "ollama_api_url": config_manager.get("ollama_api_url"),
        "secondary_llm_provider": secondary_llm_provider,
        "secondary_api_key": mask_key(
            system_keys.get(f"secondary_{secondary_llm_provider}_api_key")
            if secondary_llm_provider
            else None
        ),
        "hf_token": mask_key(system_keys.get("hf_token")),
        "selected_model": config_manager.get(selected_model_key),
        "pyannote_models_ready": pyannote_models_ready,
        "bundled_pyannote_models_ready": bundled_pyannote_models_ready,
    }


@router.post("/validate-llm")
async def validate_llm(
    request: ValidateLLMRequest, req: Request, db: AsyncSession = Depends(get_db)
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
            return {
                "valid": True,
                "message": "Connected to Ollama successfully.",
                "models": models,
            }

        provider_name = provider.capitalize() if provider else "LLM"
        return {"valid": True, "message": f"{provider_name} API key is valid."}
    except HTTPException:
        if is_public_request:
            logger.warning(
                "Public setup LLM validation failed for provider %s.", request.provider
            )
            raise HTTPException(
                status_code=400, detail=PUBLIC_LLM_VALIDATION_ERROR_DETAIL
            )
        raise
    except Exception as exc:  # noqa: BLE001
        _raise_setup_validation_error(
            client_detail=PUBLIC_LLM_VALIDATION_ERROR_DETAIL,
            log_message=(
                f"Public setup LLM validation failed for provider '{request.provider}'."
                if is_public_request
                else "Authenticated setup LLM validation failed for provider "
                f"'{request.provider}'."
            ),
            exc=exc,
        )


@router.post("/list-models")
async def list_models(
    request: ListModelsRequest, req: Request, db: AsyncSession = Depends(get_db)
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
            logger.warning(
                "Public setup model listing failed for provider %s.", request.provider
            )
            raise HTTPException(status_code=400, detail=PUBLIC_MODEL_LIST_ERROR_DETAIL)
        raise
    except Exception as exc:  # noqa: BLE001
        _raise_setup_validation_error(
            client_detail=PUBLIC_MODEL_LIST_ERROR_DETAIL,
            log_message=(
                f"Public setup model listing failed for provider '{request.provider}'."
                if is_public_request
                else "Authenticated setup model listing failed for provider "
                f"'{request.provider}'."
            ),
            exc=exc,
        )
