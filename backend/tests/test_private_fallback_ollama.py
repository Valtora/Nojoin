"""A fallback Ollama on a private address runs where it is used (#951).

The install-wide fallback address is the administrator's own, so it is trusted
as the primary's is. Only that stored value is: any other address a config
carries, and any address a request supplies, still meets the outbound policy.
"""

from __future__ import annotations

import logging

import pytest
import requests

from backend.api.services import health_service
from backend.api.v1.endpoints import llm as llm_endpoint
from backend.processing.llm_backends.factory import (
    SecondaryLLMBackend,
    get_llm_backend_with_secondary,
)
from backend.processing.llm_backends.ollama import OllamaLLMBackend
from backend.utils.config_manager import config_manager
from backend.utils.llm_config import (
    LLM_PURPOSE_DEFAULT,
    LLM_PURPOSE_MEETING_EDGE,
    ResolvedLLMConfig,
    _merge_llm_config,
    install_wide_ollama_url,
)
from backend.utils.ollama_url_policy import OllamaURLValidationError

PRIMARY_URL = "http://192.168.1.20:11434"
FALLBACK_URL = "http://192.168.1.30:11434"
OTHER_PRIVATE_URL = "http://10.0.0.9:11434"
FACTORY_LOGGER = "backend.processing.llm_backends.factory"


@pytest.fixture
def stored_addresses(monkeypatch):
    """Patch the install-wide addresses config.json (or .env) holds."""
    stored = {
        "ollama_api_url": PRIMARY_URL,
        "secondary_ollama_api_url": FALLBACK_URL,
    }
    real_get = config_manager.get

    def _get(key, default=None):
        if key in stored:
            return stored[key]
        return real_get(key, default)

    monkeypatch.setattr(config_manager, "get", _get)
    return stored


def _config(
    *,
    primary_url: str = PRIMARY_URL,
    fallback_url: str | None = FALLBACK_URL,
    **overrides,
) -> ResolvedLLMConfig:
    values = {
        "provider": "ollama",
        "api_key": None,
        "model": "primary-model",
        "api_url": primary_url,
        "merged_config": {},
        "think": False,
        "secondary_provider": "ollama",
        "secondary_model": "fallback-model",
        "secondary_api_url": fallback_url,
        "secondary_think": False,
    }
    values.update(overrides)
    return ResolvedLLMConfig(**values)


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def ollama_servers(monkeypatch):
    """The primary refuses every connection; the fallback answers."""
    calls: list[str] = []

    def _post(url, **_kwargs):
        calls.append(url)
        if url.startswith(PRIMARY_URL):
            raise requests.ConnectionError("primary is down")
        if url.endswith("/api/show"):
            return _Response({"capabilities": ["completion"], "model_info": {}})
        return _Response(
            {
                "message": {
                    "role": "assistant",
                    "content": "Answer from the fallback.",
                },
                "done": True,
                "done_reason": "stop",
            }
        )

    monkeypatch.setattr(requests, "post", _post)
    return calls


def test_private_fallback_at_another_private_address_answers(
    stored_addresses, ollama_servers
) -> None:
    backend = get_llm_backend_with_secondary(_config())

    assert isinstance(backend, SecondaryLLMBackend)
    assert backend._primary.api_url == PRIMARY_URL
    assert backend._secondary.api_url == FALLBACK_URL

    assert backend.generate_text("Summarise the meeting.") == (
        "Answer from the fallback."
    )
    assert f"{PRIMARY_URL}/api/chat" in ollama_servers
    assert f"{FALLBACK_URL}/api/chat" in ollama_servers


@pytest.mark.parametrize("purpose", [LLM_PURPOSE_DEFAULT, LLM_PURPOSE_MEETING_EDGE])
def test_resolved_install_config_builds_the_private_fallback(
    stored_addresses, purpose
) -> None:
    """Notes, chat and Meeting Edge all resolve through _merge_llm_config."""
    base = {
        "llm_provider": "ollama",
        "ollama_model": "primary-model",
        "ollama_live_model": "primary-live",
        "secondary_llm_provider": "ollama",
        "secondary_ollama_model": "fallback-model",
        "secondary_ollama_live_model": "fallback-live",
        **stored_addresses,
    }
    config = _merge_llm_config(
        base_config=base,
        system_keys={},
        owner_settings=None,
        user_settings=None,
        purpose=purpose,
    )

    backend = get_llm_backend_with_secondary(config, purpose)

    assert isinstance(backend, SecondaryLLMBackend)
    assert backend._secondary.api_url == FALLBACK_URL


def test_host_docker_internal_fallback_with_the_primary_elsewhere(
    stored_addresses,
) -> None:
    stored_addresses["secondary_ollama_api_url"] = "http://host.docker.internal:11434"

    backend = get_llm_backend_with_secondary(
        _config(fallback_url="http://host.docker.internal:11434")
    )

    assert isinstance(backend, SecondaryLLMBackend)
    assert backend._secondary.api_url == "http://host.docker.internal:11434"


def test_a_fallback_address_other_than_the_stored_one_is_still_refused(
    stored_addresses, caplog
) -> None:
    with caplog.at_level(logging.WARNING, logger=FACTORY_LOGGER):
        backend = get_llm_backend_with_secondary(
            _config(fallback_url=OTHER_PRIVATE_URL)
        )

    assert isinstance(backend, OllamaLLMBackend)
    assert backend.api_url == PRIMARY_URL
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == FACTORY_LOGGER and record.levelno == logging.WARNING
    ]
    assert any(
        "SECONDARY_OLLAMA_API_URL" in message
        and "Private or reserved Ollama addresses are not allowed." in message
        for message in messages
    ), messages


def test_each_role_trusts_only_its_own_stored_address(stored_addresses) -> None:
    config = _config()
    assert install_wide_ollama_url(config) == PRIMARY_URL
    assert install_wide_ollama_url(config.secondary_config()) == FALLBACK_URL

    # The fallback's address in the primary's place is not exempt there.
    with pytest.raises(OllamaURLValidationError):
        get_llm_backend_with_secondary(
            _config(primary_url=FALLBACK_URL, secondary_provider=None)
        )


def test_user_settings_cannot_bring_their_own_fallback_address(
    stored_addresses,
) -> None:
    base = {
        "llm_provider": "ollama",
        "ollama_model": "primary-model",
        "secondary_llm_provider": "ollama",
        "secondary_ollama_model": "fallback-model",
        **stored_addresses,
    }
    config = _merge_llm_config(
        base_config=base,
        system_keys={},
        owner_settings=None,
        user_settings={
            "secondary_ollama_api_url": OTHER_PRIVATE_URL,
            "ollama_api_url": OTHER_PRIVATE_URL,
        },
    )

    backend = get_llm_backend_with_secondary(config)

    assert isinstance(backend, SecondaryLLMBackend)
    assert backend._primary.api_url == PRIMARY_URL
    assert backend._secondary.api_url == FALLBACK_URL


def test_a_request_supplied_fallback_address_is_not_trusted(
    stored_addresses,
) -> None:
    """The models route trusts the primary's address alone."""
    with pytest.raises(ValueError):
        llm_endpoint._list_provider_models("ollama", FALLBACK_URL, None)


def test_cli_chain_carries_the_private_fallback(stored_addresses) -> None:
    cli = ResolvedLLMConfig(
        provider="cli",
        api_key=None,
        model="claude-async",
        api_url=None,
        merged_config={},
        cli_user_id=1,
        cli_provider="claude_code",
        secondary_chain=_config(),
    )

    backend = get_llm_backend_with_secondary(cli)

    assert isinstance(backend, SecondaryLLMBackend)
    inner = backend._secondary
    assert isinstance(inner, SecondaryLLMBackend)
    assert inner._primary.api_url == PRIMARY_URL
    assert inner._secondary.api_url == FALLBACK_URL


async def _optional_ai_with(monkeypatch, config) -> dict:
    async def _resolve(_db):
        return config

    monkeypatch.setattr(health_service, "resolve_llm_config_async", _resolve)
    return await health_service._get_optional_ai_component(db=None)


@pytest.mark.anyio
async def test_health_reports_a_private_fallback_as_configured(
    monkeypatch, stored_addresses
) -> None:
    component = await _optional_ai_with(monkeypatch, _config())

    assert component["status"] == "ok"
    assert component["label"] == "Configured"


@pytest.mark.anyio
async def test_health_reports_a_fallback_address_that_is_refused(
    monkeypatch, stored_addresses
) -> None:
    # An .env value with no scheme is stored as is and refused where it is used.
    stored_addresses["secondary_ollama_api_url"] = "192.168.1.30:11434"

    component = await _optional_ai_with(
        monkeypatch, _config(fallback_url="192.168.1.30:11434")
    )

    assert component["status"] == "warning"
    assert component["label"] == "Fallback off"
    assert "Invalid Ollama API URL format." in component["detail"]
    assert "Ollama (primary-model) alone" in component["detail"]
    assert "SECONDARY_OLLAMA_API_URL" in component["action"]
    assert component["configured"] is True
