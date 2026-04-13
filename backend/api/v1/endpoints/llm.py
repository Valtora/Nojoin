from fastapi import APIRouter, HTTPException, Query, Depends
from typing import List, Optional
from backend.api.error_handling import sanitized_http_exception
from backend.processing.llm_services import get_llm_backend
from backend.api.deps import get_current_user, get_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/models", response_model=List[str])
async def list_models(
    provider: str = Query(..., description="LLM provider"),
    api_url: Optional[str] = Query(None, description="API URL for local providers"),
    api_key: Optional[str] = Query(None, description="API Key"),
    current_user = Depends(get_current_user),
    db = Depends(get_db) # We need the DB to fetch system keys
):
    try:
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
    except Exception as e:
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Unable to load models for this AI provider.",
            log_message=f"Unexpected error listing models for provider '{provider}'.",
            exc=e,
        )
