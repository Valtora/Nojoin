from __future__ import annotations

import socket
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import llm as llm_endpoint
from backend.main import create_app
from backend.processing import llm_services
from backend.processing.llm_services import OllamaLLMBackend
from backend.utils.ollama_url_policy import (
    OllamaURLValidationError,
    validate_ollama_api_url,
)

SECURE_TEST_BASE_URL = "https://test"


class _FakeSession:
    pass


def _build_app():
    app = create_app(app_lifespan=None)

    async def override_get_db() -> AsyncGenerator[_FakeSession, None]:
        yield _FakeSession()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1,
        role="user",
        is_superuser=False,
    )
    return app


def test_validate_ollama_api_url_rejects_internal_service_hostname() -> None:
    with pytest.raises(OllamaURLValidationError):
        validate_ollama_api_url("http://socket-proxy:11434")


def test_validate_ollama_api_url_rejects_private_resolved_hostname(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("10.0.0.5", 0),
            )
        ],
    )

    with pytest.raises(OllamaURLValidationError):
        validate_ollama_api_url("https://ollama.example.test:11434")


def test_validate_ollama_api_url_allows_public_ipv6_address() -> None:
    assert (
        validate_ollama_api_url("http://[2001:4860:4860::8888]:11434")
        == "http://[2001:4860:4860::8888]:11434"
    )


def test_validate_ollama_api_url_allows_install_wide_private_candidate() -> None:
    assert (
        validate_ollama_api_url(
            "http://192.168.1.20:11434",
            allow_private=True,
        )
        == "http://192.168.1.20:11434"
    )


def test_validate_ollama_api_url_allows_trusted_private_runtime_endpoint() -> None:
    assert (
        validate_ollama_api_url(
            "http://192.168.1.20:11434",
            trusted_url="http://192.168.1.20:11434",
        )
        == "http://192.168.1.20:11434"
    )


def test_ollama_backend_rejects_untrusted_private_runtime_url(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_services.config_manager,
        "get",
        lambda key, default=None: (
            "http://host.docker.internal:11434" if key == "ollama_api_url" else default
        ),
    )

    with pytest.raises(OllamaURLValidationError):
        OllamaLLMBackend(api_url="http://192.168.1.20:11434", model="llama3")


@pytest.mark.anyio
async def test_authenticated_llm_models_route_uses_install_wide_ollama_url(
    monkeypatch,
) -> None:
    app = _build_app()
    captured: dict[str, object] = {}

    class _FakeBackend:
        def list_models(self):
            return ["llama3"]

    monkeypatch.setattr(
        llm_endpoint.config_manager,
        "get",
        lambda key, default=None: (
            "http://host.docker.internal:11434" if key == "ollama_api_url" else default
        ),
    )

    def _fake_get_llm_backend(*, provider, api_key=None, api_url=None, model=None):
        captured["provider"] = provider
        captured["api_url"] = api_url
        return _FakeBackend()

    monkeypatch.setattr(llm_endpoint, "get_llm_backend", _fake_get_llm_backend)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url=SECURE_TEST_BASE_URL,
    ) as client:
        response = await client.get(
            "/api/v1/llm/models",
            params={
                "provider": "ollama",
                "api_key": "test-key",
                "api_url": "http://host.docker.internal:11434",
            },
        )

    assert response.status_code == 200
    assert response.json() == ["llama3"]
    assert captured == {
        "provider": "ollama",
        "api_url": "http://host.docker.internal:11434",
    }


@pytest.mark.anyio
async def test_authenticated_llm_models_route_rejects_ollama_override(
    monkeypatch,
) -> None:
    app = _build_app()

    monkeypatch.setattr(
        llm_endpoint.config_manager,
        "get",
        lambda key, default=None: (
            "http://host.docker.internal:11434" if key == "ollama_api_url" else default
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url=SECURE_TEST_BASE_URL,
    ) as client:
        response = await client.get(
            "/api/v1/llm/models",
            params={
                "provider": "ollama",
                "api_key": "test-key",
                "api_url": "https://example.com",
            },
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid AI provider configuration."}
