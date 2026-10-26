"""Which provider an empty ``LLM_PROVIDER`` stands for.

An operator who leaves ``LLM_PROVIDER`` empty and sets one API key, say only
``OPENAI_API_KEY``, has said which provider they mean. Reading that as the
default provider, gemini, sent every AI request to Gemini with no key, so every
AI feature failed, and the setup wizard reported that no provider was set up.
Two or more keys say nothing about which one to use, so they keep the default.

Separate from ``config_manager`` so that module, which applies the rule when
it loads the configuration, stays within the file size limit.
"""

from __future__ import annotations

import os

# The providers an API key alone can select. Ollama is not one of them: the
# compose template gives OLLAMA_API_URL a default, so the variable being set
# says nothing about a choice.
KEYED_LLM_PROVIDERS = ("gemini", "openai", "anthropic")


def provider_of_only_llm_key() -> str | None:
    """The provider whose API key is the only one in the environment, if any."""
    keyed = [
        provider
        for provider in KEYED_LLM_PROVIDERS
        if os.environ.get(f"{provider.upper()}_API_KEY", "").strip()
    ]
    return keyed[0] if len(keyed) == 1 else None
