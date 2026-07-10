from backend.utils.llm_config import (
    CLI_PROVIDER,
    LLM_PURPOSE_DEFAULT,
    LLM_PURPOSE_MEETING_EDGE,
    _maybe_cli_config,
    _merge_llm_config,
)


def _merged_with(user_settings, purpose=LLM_PURPOSE_DEFAULT, base_config=None):
    return _merge_llm_config(
        base_config=base_config or {"llm_provider": "gemini", "gemini_model": "g-pro"},
        system_keys={},
        owner_settings=None,
        user_settings=user_settings,
        purpose=purpose,
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


def test_merge_llm_config_falls_back_to_main_model_without_meeting_edge_override() -> (
    None
):
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


def test_merge_llm_config_prefers_config_backed_model_defaults_over_owner_settings() -> (
    None
):
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


def test_cli_oauth_resolves_to_cli_provider_with_async_model() -> None:
    merged = _merged_with(
        {
            "usage_model": "cli_oauth",
            "cli_model": "claude-async",
            "cli_live_model": "claude-live",
        }
    )
    cli = _maybe_cli_config(merged, LLM_PURPOSE_DEFAULT)

    assert cli is not None
    assert cli.provider == CLI_PROVIDER
    assert cli.model == "claude-async"
    # No env credential: the subprocess authenticates with the user's token.
    assert cli.api_key is None
    assert cli.api_url is None


def test_cli_oauth_uses_live_model_for_meeting_edge() -> None:
    merged = _merged_with(
        {
            "usage_model": "cli_oauth",
            "cli_model": "claude-async",
            "cli_live_model": "claude-live",
        },
        purpose=LLM_PURPOSE_MEETING_EDGE,
    )
    cli = _maybe_cli_config(merged, LLM_PURPOSE_MEETING_EDGE)

    assert cli is not None
    assert cli.model == "claude-live"


def test_cli_oauth_meeting_edge_falls_back_to_async_model() -> None:
    merged = _merged_with(
        {"usage_model": "cli_oauth", "cli_model": "claude-async"},
        purpose=LLM_PURPOSE_MEETING_EDGE,
    )
    cli = _maybe_cli_config(merged, LLM_PURPOSE_MEETING_EDGE)

    assert cli is not None
    assert cli.model == "claude-async"


def test_cli_oauth_falls_back_to_server_default_chain() -> None:
    # A cli_oauth user degrades to the ENTIRE server-default chain, not straight
    # to the secondary: user sub -> server primary -> server secondary.
    merged = _merge_llm_config(
        base_config={
            "llm_provider": "openai",
            "openai_model": "gpt-primary",
            "secondary_llm_provider": "gemini",
            "secondary_gemini_model": "g-secondary",
        },
        system_keys={
            "openai_api_key": "sk-primary",
            "secondary_gemini_api_key": "sk-secondary",
        },
        owner_settings=None,
        user_settings={"usage_model": "cli_oauth", "cli_model": "claude-async"},
        purpose=LLM_PURPOSE_DEFAULT,
    )
    cli = _maybe_cli_config(merged, LLM_PURPOSE_DEFAULT)

    assert cli is not None
    assert cli.provider == CLI_PROVIDER
    assert cli.model == "claude-async"
    # The CLI config no longer carries a flat secondary of its own.
    assert not cli.has_secondary
    # Tier 2 is the server primary.
    chain = cli.secondary_chain
    assert chain is not None
    assert chain.provider == "openai"
    assert chain.model == "gpt-primary"
    # Tier 3 is the server secondary, hanging off the server primary.
    server_secondary = chain.secondary_config()
    assert server_secondary is not None
    assert server_secondary.provider == "gemini"
    assert server_secondary.model == "g-secondary"


def test_cli_oauth_carries_user_id_for_credential_lookup() -> None:
    merged = _merged_with({"usage_model": "cli_oauth", "cli_model": "claude-async"})
    cli = _maybe_cli_config(merged, LLM_PURPOSE_DEFAULT, user_id=42)

    assert cli is not None
    assert cli.cli_user_id == 42


def test_cli_config_never_blocks_missing_configuration() -> None:
    # A cli user with no secondary and no explicit model must not be blocked: the
    # subscription token replaces the api_key and the model defaults at call time.
    merged = _merged_with({"usage_model": "cli_oauth"})
    cli = _maybe_cli_config(merged, LLM_PURPOSE_DEFAULT)

    assert cli is not None
    assert cli.provider == CLI_PROVIDER
    assert cli.api_key is None
    assert cli.model is None
    assert cli.missing_configuration_message() is None


def test_non_cli_usage_model_falls_through() -> None:
    for usage_model in (None, "", "byok", "ollama"):
        user_settings = None if usage_model is None else {"usage_model": usage_model}
        merged = _merged_with(user_settings)
        assert _maybe_cli_config(merged, LLM_PURPOSE_DEFAULT) is None


def test_owner_usage_model_does_not_leak_to_user() -> None:
    # usage_model is per-user: an owner on cli_oauth must not force a user onto it.
    merged = _merge_llm_config(
        base_config={"llm_provider": "gemini", "gemini_model": "g-pro"},
        system_keys={},
        owner_settings={"usage_model": "cli_oauth"},
        user_settings=None,
        purpose=LLM_PURPOSE_DEFAULT,
    )
    assert _maybe_cli_config(merged, LLM_PURPOSE_DEFAULT) is None
