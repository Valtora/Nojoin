"""The suite ignores the checkout's .env and every LLM provider variable (#1056).

A developer's ``.env``, or a provider variable exported in their shell, chose
the install-wide LLM provider for the whole test process, which CI never sees.
``suite_env.py`` keeps both out; these tests fail if either gets back in.
"""

import importlib
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from backend.tests import suite_env
from backend.utils.config_defaults import DEFAULT_SYSTEM_CONFIG
from backend.utils.config_manager import (
    ENV_OVERRIDES,
    SENSITIVE_KEYS,
    ConfigManager,
    get_system_api_keys,
)
from backend.utils.llm_config import SYSTEM_LLM_FIELDS
from backend.utils.llm_provider_env import KEYED_LLM_PROVIDERS

PROBE = "NOJOIN_SUITE_ENV_PROBE"


def test_load_dotenv_sets_nothing(tmp_path: Path) -> None:
    """The backend's own calls find the checkout's .env from any directory."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"{PROBE}=from-a-dotenv-file\n", encoding="utf-8")
    try:
        assert load_dotenv(env_file) is False
        assert PROBE not in os.environ
    finally:
        os.environ.pop(PROBE, None)


def test_no_provider_setting_comes_from_the_environment(tmp_path: Path) -> None:
    """config_manager overlays LLM_PROVIDER and the Ollama settings as it loads,
    and reads the API keys whenever it is asked for them."""
    config = ConfigManager(config_path=str(tmp_path / "config.json")).get_all()

    assert {key: config.get(key) for key in ENV_OVERRIDES} == {
        key: DEFAULT_SYSTEM_CONFIG.get(key) for key in ENV_OVERRIDES
    }
    assert [key for key in get_system_api_keys() if key.endswith("_api_key")] == []


def test_importing_suite_env_removes_provider_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("SECONDARY_OPENAI_API_KEY", "sk-developer")
    monkeypatch.setenv("OLLAMA_API_URL", "http://192.0.2.10:11434")
    monkeypatch.setenv(PROBE, "kept")

    importlib.reload(suite_env)

    set_here = {"LLM_PROVIDER", "SECONDARY_OPENAI_API_KEY", "OLLAMA_API_URL"}
    assert set_here & set(os.environ) == set()
    assert os.environ[PROBE] == "kept"
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"


def test_every_provider_variable_the_backend_reads_is_removed() -> None:
    read = {
        *ENV_OVERRIDES.values(),
        # ConfigManager.get falls back to the upper-cased key.
        *(field.upper() for field in SYSTEM_LLM_FIELDS),
        *(key.upper() for key in SENSITIVE_KEYS if key.endswith("_api_key")),
        *(f"{provider.upper()}_API_KEY" for provider in KEYED_LLM_PROVIDERS),
    }

    missed = sorted(name for name in read if not suite_env.is_provider_variable(name))
    assert missed == []
