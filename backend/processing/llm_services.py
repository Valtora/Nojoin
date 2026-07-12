"""Facade for the LLM backend implementations.

The per-provider backends, shared base class, and factory helpers now live in
``backend.processing.llm_backends.*``. This module is retained as a thin
compatibility facade so the historical import path
``backend.processing.llm_services`` keeps working unchanged for every call site.
This is a pure decomposition: no behaviour, signatures, or prompt text changed.
"""

from backend.processing.llm_backends.anthropic import AnthropicLLMBackend
from backend.processing.llm_backends.base import (
    JSON_CONTRACT_ERRORS,
    LLMBackend,
    build_eligible_speaker_labels_prompt_section,
    get_default_model_for_provider,
    summarize_llm_response_shape,
)
from backend.processing.llm_backends.factory import (
    SecondaryLLMBackend,
    get_llm_backend,
    get_llm_backend_with_secondary,
)
from backend.processing.llm_backends.gemini import GeminiLLMBackend
from backend.processing.llm_backends.ollama import (
    OLLAMA_DEFAULT_NUM_CTX,
    OllamaLLMBackend,
)
from backend.processing.llm_backends.openai import OpenAILLMBackend

# Retained as module attributes for backward compatibility: some call sites and
# tests reach these through this module (e.g. tests monkeypatch
# ``llm_services.config_manager``). They are intentionally not part of the
# public ``__all__`` surface.
from backend.utils.config_manager import config_manager  # noqa: F401
from backend.utils.ollama_url_policy import validate_ollama_api_url  # noqa: F401

__all__ = [
    "JSON_CONTRACT_ERRORS",
    "OLLAMA_DEFAULT_NUM_CTX",
    "AnthropicLLMBackend",
    "GeminiLLMBackend",
    "LLMBackend",
    "OllamaLLMBackend",
    "OpenAILLMBackend",
    "SecondaryLLMBackend",
    "build_eligible_speaker_labels_prompt_section",
    "get_default_model_for_provider",
    "get_llm_backend",
    "get_llm_backend_with_secondary",
    "summarize_llm_response_shape",
]
