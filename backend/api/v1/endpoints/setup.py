import asyncio
import hmac
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
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
from backend.utils.deployment_warnings import FIRST_RUN_PASSWORD_PLACEHOLDER
from backend.utils.ollama_url_policy import (
    OllamaURLValidationError,
    validate_ollama_api_url,
)
from backend.utils.rate_limit import enforce_rate_limit

logger = logging.getLogger(__name__)

FIRST_RUN_PASSWORD_AUTH_SCHEME = "Bootstrap"
FIRST_RUN_PASSWORD_ENV_KEY = "FIRST_RUN_PASSWORD"
# Every setup denial but the one below uses this detail, so anonymous responses
# are identical whether the system is initialised, the bootstrap password is
# wrong, or FIRST_RUN_PASSWORD is not configured. The specific reason is
# server-log only.
FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL = "First-run setup access denied."
# The one other setup denial: the password sent is the example value from
# .env.example. It depends only on what was sent, never on the system's state
# or on what FIRST_RUN_PASSWORD holds, so it discloses nothing the sender did
# not already know, and the wizard can say what to change.
FIRST_RUN_PASSWORD_EXAMPLE_DETAIL = (
    "Setup does not accept the example FIRST_RUN_PASSWORD from .env.example."
)
SETUP_RATE_LIMIT = 60
SETUP_RATE_LIMIT_WINDOW_SECONDS = 10 * 60
SETUP_RATE_LIMIT_DETAIL = "Too many setup requests. Please try again later."
PUBLIC_LLM_VALIDATION_ERROR_DETAIL = "Unable to validate the AI provider configuration."
PUBLIC_MODEL_LIST_ERROR_DETAIL = "Unable to list AI provider models."
LLM_PROVIDER_ENV_KEY = "LLM_PROVIDER"
# Outside the int4 range on purpose: the hashtext() keys other routes lock on
# can never take this value.
FIRST_RUN_SETUP_ADVISORY_LOCK_ID = 724_118_305_264_909_173


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


async def lock_first_run_setup(db: AsyncSession) -> None:
    """Hold first-run setup to one request at a time until this transaction ends.

    Setup checks that no user exists and then creates the owner. Two requests
    that both checked before either committed would each create one. Taken
    before the check, this lock makes the second wait for the first to commit
    or roll back, and its check then sees the first's owner. It relies on READ
    COMMITTED, PostgreSQL's default, so the check after the lock reads what the
    first request committed. Other databases run only in tests, one request at
    a time, and take no lock.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": FIRST_RUN_SETUP_ADVISORY_LOCK_ID},
    )


def get_first_run_password(request: Request) -> Optional[str]:
    authorization_header = request.headers.get("Authorization")
    if not authorization_header:
        return None

    scheme, _, credential = authorization_header.partition(" ")
    if scheme.lower() != FIRST_RUN_PASSWORD_AUTH_SCHEME.lower() or not credential:
        return None

    # ASCII whitespace only: the header is read as Latin-1, so a bare strip()
    # would also cut 0x85 and 0xA0, which end the UTF-8 of letters such as "à".
    return credential.strip(" \t")


def _bootstrap_password_matches(provided: str, configured: str) -> bool:
    """Compare the header's password with FIRST_RUN_PASSWORD, as bytes.

    hmac.compare_digest refuses str with any non-ASCII character, so a
    FIRST_RUN_PASSWORD such as one holding a pound sign failed every unlock
    with a 500. Starlette decodes header bytes as Latin-1, so encoding back
    recovers the bytes sent. The web app and curl in a UTF-8 terminal send
    UTF-8, while Python's http.client sends a character up to U+00FF as one
    Latin-1 byte, so either form of the configured password is accepted.
    """
    sent = provided.encode("latin-1", errors="replace")
    candidates = [configured.encode("utf-8")]
    try:
        candidates.append(configured.encode("latin-1"))
    except UnicodeEncodeError:
        pass
    # Every candidate is compared, so the time taken does not say which matched.
    matches = [hmac.compare_digest(sent, candidate) for candidate in candidates]
    return any(matches)


def refuse_example_first_run_password(request: Request) -> None:
    """Refuse the example bootstrap password from .env.example.

    Callers run this before any state-dependent check, so the refusal is the
    same on an initialised system as on a new one.
    """
    provided_password = get_first_run_password(request)
    if not provided_password or not _bootstrap_password_matches(
        provided_password, FIRST_RUN_PASSWORD_PLACEHOLDER
    ):
        return
    logger.warning(
        "First-run setup request denied: the bootstrap password sent is the "
        "example value from .env.example. Set your own FIRST_RUN_PASSWORD in "
        ".env and restart or redeploy Nojoin."
    )
    raise HTTPException(status_code=403, detail=FIRST_RUN_PASSWORD_EXAMPLE_DETAIL)


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

    # Never unlocks, even for a caller that skipped the check above: the only
    # password that would match is the example value everyone can read.
    if configured_password.strip() == FIRST_RUN_PASSWORD_PLACEHOLDER:
        logger.warning(
            "First-run setup request denied: FIRST_RUN_PASSWORD is still the "
            "example value from .env.example. Set your own in .env and restart "
            "or redeploy Nojoin before initialising."
        )
        raise HTTPException(
            status_code=403,
            detail=FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL,
        )

    provided_password = get_first_run_password(request)
    if not provided_password or not _bootstrap_password_matches(
        provided_password, configured_password
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
    refuse_example_first_run_password(request)

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
    from backend.utils import telemetry
    from backend.utils.config_manager import async_get_system_api_keys

    system_keys = await async_get_system_api_keys(db)
    llm_provider = config_manager.get("llm_provider", "gemini")
    # `llm_provider` always resolves to something (an empty LLM_PROVIDER means
    # the provider of the only API key set, or else gemini), so on its own it
    # cannot tell an operator who chose gemini from one who set nothing.
    # .env.example ships LLM_PROVIDER empty and compose passes it through
    # empty, so the raw variable is what says whether a provider was chosen.
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
        # What NOJOIN_TELEMETRY_ENABLED pins telemetry to, or None when it
        # leaves the choice to the wizard. The wizard shows a pinned value
        # read-only, as Settings > Privacy does, since the box could not
        # change what is sent.
        "telemetry_pinned": telemetry.env_override(),
    }


def _build_setup_llm_backend(provider: str, model: Optional[str] = None):
    from backend.utils.config_manager import get_system_api_keys

    api_key = get_system_api_keys().get(f"{provider}_api_key")

    api_url = None
    if provider == "ollama":
        api_url = config_manager.get("ollama_api_url")
        api_url = _validate_setup_ollama_api_url(api_url)

    return get_llm_backend(
        provider,
        api_key=api_key,
        model=model,
        api_url=api_url,
        allow_private_api_url=provider == "ollama",
    )


def _validate_llm_provider(provider: str, model: Optional[str]) -> dict:
    llm = _build_setup_llm_backend(provider, model)
    llm.validate_api_key()

    if provider == "ollama":
        return {
            "valid": True,
            "message": "Connected to Ollama successfully.",
            "models": llm.list_models(),
        }

    provider_name = provider.capitalize() if provider else "LLM"
    return {"valid": True, "message": f"{provider_name} API key is valid."}


def _list_llm_provider_models(provider: str) -> dict:
    return {"models": _build_setup_llm_backend(provider).list_models()}


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
        # The provider work runs on a worker thread. The Ollama URL check
        # resolves DNS, and a provider call blocks for its whole timeout when
        # the provider is unreachable. On the event loop that stalls every
        # other request the API is serving.
        return await asyncio.to_thread(
            _validate_llm_provider, request.provider, request.model
        )
    except HTTPException:
        if is_public_request:
            logger.warning(
                "Public setup LLM validation failed for provider %s.", request.provider
            )
            raise HTTPException(
                status_code=400, detail=PUBLIC_LLM_VALIDATION_ERROR_DETAIL
            )
        raise
    except Exception as exc:  # noqa: BLE001 -- boundary: provider SDK errors share no base class; all become one sanitised 400
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
        # Off the event loop for the reason given in validate_llm.
        return await asyncio.to_thread(_list_llm_provider_models, request.provider)
    except HTTPException:
        if is_public_request:
            logger.warning(
                "Public setup model listing failed for provider %s.", request.provider
            )
            raise HTTPException(status_code=400, detail=PUBLIC_MODEL_LIST_ERROR_DETAIL)
        raise
    except Exception as exc:  # noqa: BLE001 -- boundary: provider SDK errors share no base class; all become one sanitised 400
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
