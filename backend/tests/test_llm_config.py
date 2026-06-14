from backend.utils.llm_config import (
    LLM_PURPOSE_DEFAULT,
    LLM_PURPOSE_MEETING_EDGE,
    _merge_llm_config,
)


def test_merge_llm_config_uses_main_model_by_default() -> None:
    resolved = _merge_llm_config(
        base_config={
            "llm_provider": "gemini",
            "gemini_model": "gemini-2.5-pro",
            "gemini_live_model": "gemini-2.5-flash-lite",
        },
        system_keys={"gemini_api_key": "sk-system"},
        owner_settings=None,
        user_settings=None,
        purpose=LLM_PURPOSE_DEFAULT,
    )

    assert resolved.provider == "gemini"
    assert resolved.api_key == "sk-system"
    assert resolved.model == "gemini-2.5-pro"


def test_merge_llm_config_uses_meeting_edge_model_when_present() -> None:
    resolved = _merge_llm_config(
        base_config={
            "llm_provider": "openai",
            "openai_model": "gpt-4.1",
            "openai_live_model": "gpt-4.1-mini",
        },
        system_keys={"openai_api_key": "sk-system"},
        owner_settings=None,
        user_settings=None,
        purpose=LLM_PURPOSE_MEETING_EDGE,
    )

    assert resolved.provider == "openai"
    assert resolved.api_key == "sk-system"
    assert resolved.model == "gpt-4.1-mini"


def test_merge_llm_config_falls_back_to_main_model_without_meeting_edge_override() -> None:
    resolved = _merge_llm_config(
        base_config={
            "llm_provider": "anthropic",
            "anthropic_model": "claude-sonnet-4",
            "anthropic_live_model": None,
        },
        system_keys={"anthropic_api_key": "sk-system"},
        owner_settings=None,
        user_settings=None,
        purpose=LLM_PURPOSE_MEETING_EDGE,
    )

    assert resolved.provider == "anthropic"
    assert resolved.model == "claude-sonnet-4"


def test_merge_llm_config_prefers_user_meeting_edge_override() -> None:
    resolved = _merge_llm_config(
        base_config={
            "llm_provider": "ollama",
            "ollama_model": "llama3.1:70b",
            "ollama_live_model": "llama3.1:8b",
            "ollama_api_url": "http://localhost:11434",
            "ollama_context_window": 131072,
        },
        system_keys={},
        owner_settings={"ollama_live_model": "phi4:mini"},
        user_settings={"ollama_live_model": "qwen2.5:3b"},
        purpose=LLM_PURPOSE_MEETING_EDGE,
    )

    assert resolved.provider == "ollama"
    assert resolved.api_url == "http://localhost:11434"
    assert resolved.model == "qwen2.5:3b"
    assert resolved.context_window == 131072


def test_merge_llm_config_ignores_user_ollama_api_url_override() -> None:
    resolved = _merge_llm_config(
        base_config={
            "llm_provider": "ollama",
            "ollama_model": "llama3.1:70b",
            "ollama_api_url": "http://localhost:11434",
        },
        system_keys={},
        owner_settings=None,
        user_settings={"ollama_api_url": "http://192.168.1.20:11434"},
        purpose=LLM_PURPOSE_DEFAULT,
    )

    assert resolved.provider == "ollama"
    assert resolved.api_url == "http://localhost:11434"


def test_merge_llm_config_resolves_secondary_ollama_context_window() -> None:
    resolved = _merge_llm_config(
        base_config={
            "llm_provider": "openai",
            "openai_model": "gpt-4.1",
            "secondary_llm_provider": "ollama",
            "secondary_ollama_model": "llama3.1:70b",
            "secondary_ollama_api_url": "http://localhost:11434",
            "secondary_ollama_context_window": "65536",
        },
        system_keys={"openai_api_key": "sk-system"},
        owner_settings=None,
        user_settings=None,
        purpose=LLM_PURPOSE_DEFAULT,
    )

    secondary = resolved.secondary_config()

    assert secondary is not None
    assert secondary.provider == "ollama"
    assert secondary.context_window == 65536


def test_merge_llm_config_prefers_config_backed_model_defaults_over_owner_settings() -> None:
    resolved = _merge_llm_config(
        base_config={
            "llm_provider": "openai",
            "openai_model": "gpt-4.1",
            "openai_live_model": "gpt-4.1-mini",
        },
        system_keys={},
        owner_settings={
            "llm_provider": "anthropic",
            "openai_live_model": "gpt-4.1-nano",
            "anthropic_model": "claude-sonnet-4",
        },
        user_settings=None,
        purpose=LLM_PURPOSE_MEETING_EDGE,
    )

    assert resolved.provider == "openai"
    assert resolved.model == "gpt-4.1-mini"
