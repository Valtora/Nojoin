import logging

import pytest

from backend.processing.llm_backends import factory
from backend.processing.llm_services import get_llm_backend_with_secondary
from backend.utils.llm_config import ResolvedLLMConfig

FACTORY_LOGGER = "backend.processing.llm_backends.factory"


@pytest.fixture(autouse=True)
def _fresh_warning_state(monkeypatch):
    monkeypatch.setattr(factory, "_WARNED_UNUSABLE_SECONDARIES", set())


def _config(**secondary) -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider="gemini",
        api_key="primary-key",
        model="gemini-x",
        api_url=None,
        merged_config={},
        **secondary,
    )


def _warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == FACTORY_LOGGER and record.levelno == logging.WARNING
    ]


def test_secondary_without_a_model_is_reported_once(caplog):
    config = _config(
        secondary_provider="ollama",
        secondary_api_url="http://ollama.invalid:11434",
    )

    with caplog.at_level(logging.WARNING, logger=FACTORY_LOGGER):
        first = get_llm_backend_with_secondary(config)
        get_llm_backend_with_secondary(config)

    assert type(first).__name__ == "GeminiLLMBackend"
    messages = _warnings(caplog)
    assert len(messages) == 1
    assert "Fallback provider ollama has no model" in messages[0]
    assert "Settings > AI providers" in messages[0]


def test_hosted_secondary_without_a_key_names_the_env_variable(caplog):
    config = _config(secondary_provider="openai", secondary_model="gpt-x")

    with caplog.at_level(logging.WARNING, logger=FACTORY_LOGGER):
        get_llm_backend_with_secondary(config)

    messages = _warnings(caplog)
    assert len(messages) == 1
    assert "has no API key" in messages[0]
    assert "SECONDARY_OPENAI_API_KEY" in messages[0]


def test_no_warning_without_a_secondary_or_with_a_usable_one(caplog):
    with caplog.at_level(logging.WARNING, logger=FACTORY_LOGGER):
        get_llm_backend_with_secondary(_config())
        backend = get_llm_backend_with_secondary(
            _config(
                secondary_provider="openai",
                secondary_api_key="secondary-key",
                secondary_model="gpt-x",
            )
        )

    assert type(backend).__name__ == "SecondaryLLMBackend"
    assert _warnings(caplog) == []
