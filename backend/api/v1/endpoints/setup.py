from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import httpx
from backend.processing.LLM_Services import get_llm_backend
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

class ValidateLLMRequest(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None

class ValidateHFRequest(BaseModel):
    token: str

class ListModelsRequest(BaseModel):
    provider: str
    api_key: str

@router.post("/validate-llm")
async def validate_llm(request: ValidateLLMRequest):
    """
    Validate LLM API Key.
    """
    try:
        llm = get_llm_backend(request.provider, api_key=request.api_key, model=request.model)
        llm.validate_api_key()
        provider_name = request.provider.capitalize() if request.provider else "LLM"
        return {"valid": True, "message": f"{provider_name} API key is valid."}
    except Exception as e:
        logger.error(f"Validation failed for {request.provider}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/validate-hf")
async def validate_hf(request: ValidateHFRequest):
    """
    Validate Hugging Face Token.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://huggingface.co/api/whoami-v2",
                headers={"Authorization": f"Bearer {request.token}"}
            )
            response.raise_for_status()
            user_info = response.json()
            return {"valid": True, "message": f"Token valid for user: {user_info.get('name', 'Unknown')}"}
    except Exception as e:
        logger.error(f"HF Validation failed: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid Hugging Face token: {str(e)}")

@router.post("/list-models")
async def list_models(request: ListModelsRequest):
    """
    List available models for a given provider and API key.
    """
    try:
        llm = get_llm_backend(request.provider, api_key=request.api_key)
        models = llm.list_models()
        return {"models": models}
    except Exception as e:
        logger.error(f"Failed to list models for {request.provider}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
