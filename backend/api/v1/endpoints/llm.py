import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.api.deps import get_current_user, get_db
from backend.api.error_handling import sanitized_http_exception
from backend.processing.llm_services import get_llm_backend
from backend.utils.config_manager import config_manager
from backend.utils.ollama_url_policy import validate_ollama_api_url

router = APIRouter()
logger = logging.getLogger(__name__)


class VisionSupportRead(BaseModel):
    """Whether a provider/model pair can accept images.

    ``supported`` is deliberately tri-state. ``None`` means the provider offers
    no way to ask, which is the case for every hosted API -- the answer is only
    learned by making a call. Only Ollama returns a definite answer, and that is
    exactly where it is needed: a self-hosted user picks their own model and
    would otherwise discover the gap only after a document had been parsed
    without visual analysis.
    """

    provider: str
    model: Optional[str] = None
    supported: Optional[bool] = None


@router.get("/models", response_model=List[str])
async def list_models(
    provider: str = Query(..., description="LLM provider"),
    api_url: Optional[str] = Query(None, description="API URL for local providers"),
    api_key: Optional[str] = Query(None, description="API Key"),
    current_user=Depends(get_current_user),
    db=Depends(get_db),  # We need the DB to fetch system keys
):
    try:
        if provider == "ollama":
            configured_api_url = validate_ollama_api_url(
                config_manager.get("ollama_api_url"),
                allow_private=True,
            )
            if api_url:
                requested_api_url = validate_ollama_api_url(
                    api_url,
                    trusted_url=configured_api_url,
                )
                if requested_api_url != configured_api_url:
                    raise ValueError("Ollama API URL is managed installation-wide.")
            api_url = configured_api_url

        if not api_key:
            from backend.utils.config_manager import async_get_system_api_keys

            system_keys = await async_get_system_api_keys(db)
            api_key = system_keys.get(f"{provider}_api_key")

        backend = get_llm_backend(provider=provider, api_key=api_key, api_url=api_url)
        models = backend.list_models()
        return models
    except ValueError as e:
        raise sanitized_http_exception(
            logger=logger,
            status_code=400,
            client_message="Invalid AI provider configuration.",
            log_message=f"Rejected LLM model listing request for provider '{provider}'.",
            exc=e,
        )
    except Exception as e:  # noqa: BLE001
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Unable to load models for this AI provider.",
            log_message=f"Unexpected error listing models for provider '{provider}'.",
            exc=e,
        )


@router.get("/vision-support", response_model=VisionSupportRead)
async def get_vision_support(
    provider: str = Query(..., description="LLM provider"),
    model: Optional[str] = Query(None, description="Model name to check"),
    api_url: Optional[str] = Query(None, description="API URL for local providers"),
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    """Report whether the given model can accept images.

    Used ahead of a document upload so the deep-parse option can warn before a
    document is parsed and silently downgraded, rather than after. A probe
    failure is not an error: it resolves to ``supported: null``, which the UI
    treats as "proceed and find out".
    """
    try:
        if provider == "ollama":
            api_url = validate_ollama_api_url(
                config_manager.get("ollama_api_url"),
                allow_private=True,
            )

        from backend.utils.config_manager import async_get_system_api_keys

        system_keys = await async_get_system_api_keys(db)
        api_key = system_keys.get(f"{provider}_api_key")

        backend = get_llm_backend(
            provider=provider, api_key=api_key, api_url=api_url, model=model
        )
        return VisionSupportRead(
            provider=provider, model=model, supported=backend.supports_vision()
        )
    except ValueError as e:
        raise sanitized_http_exception(
            logger=logger,
            status_code=400,
            client_message="Invalid AI provider configuration.",
            log_message=f"Rejected vision-support probe for provider '{provider}'.",
            exc=e,
        )
    except Exception as e:  # noqa: BLE001
        # An unreachable provider is indistinguishable from one that simply
        # cannot answer, and neither should block an upload.
        logger.debug(f"Vision-support probe failed for provider '{provider}': {e}")
        return VisionSupportRead(provider=provider, model=model, supported=None)
