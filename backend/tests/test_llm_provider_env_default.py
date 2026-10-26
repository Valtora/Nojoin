"""An empty LLM_PROVIDER means gemini, whatever else holds a provider, unless
exactly one provider key is set, which then names the provider (#724).

docker-compose.example.yml filled in gemini for an unset LLM_PROVIDER until
#477, and now passes the variable through empty so the setup wizard can see
that no provider was chosen. An install that relied on the old default has to
keep gemini. Two other places can hold a provider and would take over if the
empty value were skipped like the other environment overrides: config.json,
which settings saves write the environment's value back into, and an owner row
from before the provider became install-wide.
"""

import json

import pytest

from backend.utils.config_manager import DEFAULT_LLM_PROVIDER, ConfigManager
from backend.utils.llm_config import (
    LLM_PURPOSE_DEFAULT,
    _apply_owner_provider_override,
    _merge_llm_config,
)

PROVIDER_KEY_VARIABLES = ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch):
    """A key in the developer's own environment would choose a provider."""
    for variable in PROVIDER_KEY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def _manager(tmp_path, on_disk: dict) -> ConfigManager:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(on_disk), encoding="utf-8")
    return ConfigManager(config_path=str(path))


@pytest.mark.parametrize("empty", ["", "  "])
def test_an_empty_llm_provider_outranks_config_json(
    tmp_path, monkeypatch, empty: str
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", empty)

    manager = _manager(tmp_path, {"llm_provider": "openai"})

    assert manager.get("llm_provider") == DEFAULT_LLM_PROVIDER == "gemini"


def test_an_empty_llm_provider_is_gemini_with_nothing_in_config_json(
    tmp_path, monkeypatch
) -> None:
    """get() falls back to the raw variable for a key the config lacks, so
    without a value of its own it returned the empty string, and first-run
    setup stored the wizard's model under ``_model``."""
    monkeypatch.setenv("LLM_PROVIDER", "")

    manager = _manager(tmp_path, {})

    assert manager.get_all()["llm_provider"] == "gemini"
    assert manager.get("llm_provider", "gemini") == "gemini"


def test_a_set_llm_provider_still_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    manager = _manager(tmp_path, {"llm_provider": "openai"})

    assert manager.get("llm_provider") == "anthropic"


def test_an_absent_llm_provider_leaves_config_json_in_charge(
    tmp_path, monkeypatch
) -> None:
    """Only a compose file that never declares the variable gets here."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    manager = _manager(tmp_path, {"llm_provider": "openai"})

    assert manager.get("llm_provider") == "openai"


def test_an_empty_llm_provider_is_not_replaced_by_the_owners_legacy_provider(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "")
    manager = _manager(tmp_path, {"gemini_model": "gemini-flash-latest"})
    owner_settings = {"llm_provider": "openai", "openai_model": "gpt-test"}

    merged = _merge_llm_config(
        base_config=manager.get_all(),
        system_keys={"gemini_api_key": "gemini-key", "openai_api_key": "openai-key"},
        owner_settings=owner_settings,
        user_settings=None,
    )
    resolved = _apply_owner_provider_override(
        merged, owner_settings, LLM_PURPOSE_DEFAULT
    )

    assert resolved.provider == "gemini"
    assert resolved.api_key == "gemini-key"
    assert resolved.model == "gemini-flash-latest"


@pytest.mark.parametrize(
    ("variable", "provider"),
    [
        ("OPENAI_API_KEY", "openai"),
        ("ANTHROPIC_API_KEY", "anthropic"),
        ("GEMINI_API_KEY", "gemini"),
    ],
)
def test_an_empty_llm_provider_follows_the_only_key_set(
    tmp_path, monkeypatch, variable: str, provider: str
) -> None:
    """Only OPENAI_API_KEY set, with LLM_PROVIDER empty, read as gemini: every
    AI request went to Gemini with no key, and the setup wizard said no
    provider was configured."""
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv(variable, "sk-test-value")

    manager = _manager(tmp_path, {"llm_provider": "ollama"})

    assert manager.get("llm_provider") == provider


def test_several_keys_keep_the_default_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-value")

    assert _manager(tmp_path, {}).get("llm_provider") == DEFAULT_LLM_PROVIDER


def test_a_blank_key_is_not_a_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("OPENAI_API_KEY", "  ")

    assert _manager(tmp_path, {}).get("llm_provider") == "gemini"


def test_a_set_llm_provider_outranks_a_single_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-value")

    assert _manager(tmp_path, {}).get("llm_provider") == "ollama"


def test_the_inferred_provider_and_its_key_resolve_together(
    tmp_path, monkeypatch
) -> None:
    """What processing runs with: the key's provider, its key and its model."""
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-value")
    manager = _manager(tmp_path, {"openai_model": "gpt-test"})

    resolved = _merge_llm_config(
        base_config=manager.get_all(),
        system_keys={"openai_api_key": "sk-test-value"},
        owner_settings=None,
        user_settings=None,
    )

    assert resolved.provider == "openai"
    assert resolved.api_key == "sk-test-value"
    assert resolved.model == "gpt-test"
