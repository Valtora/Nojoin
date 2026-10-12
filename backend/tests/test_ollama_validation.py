"""Validating an Ollama server fails when the server cannot list its models.

validate_api_key used to call list_models, which answers [] for any failure,
so an unreachable server validated and the setup endpoint reported "Connected
to Ollama successfully." list_models keeps answering [] for the model pickers.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
import requests
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_db
from backend.api.v1.endpoints import setup
from backend.main import create_app
from backend.processing.llm_backends.ollama import OllamaLLMBackend


class _Response:
    def __init__(self, body: dict, status_code: int = 200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")

    def json(self) -> dict:
        return self._body


class _Unreachable:
    def get(self, url, **kwargs):
        raise requests.ConnectionError("Connection to 10.255.255.1 timed out.")


class _Answers:
    def __init__(self, response: _Response):
        self._response = response

    def get(self, url, **kwargs):
        return self._response


def _backend(fake_requests) -> OllamaLLMBackend:
    backend = object.__new__(OllamaLLMBackend)
    backend.model = "llama3"
    backend.api_url = "http://10.255.255.1:11434"
    backend.context_window = None
    backend.requests = fake_requests
    return backend


def test_validation_fails_when_the_server_is_unreachable() -> None:
    with pytest.raises(ValueError, match="timed out"):
        _backend(_Unreachable()).validate_api_key()


def test_validation_fails_when_the_server_answers_with_an_error() -> None:
    backend = _backend(_Answers(_Response({"error": "boom"}, status_code=500)))

    with pytest.raises(ValueError, match="500"):
        backend.validate_api_key()


def test_validation_passes_when_the_server_lists_its_models() -> None:
    backend = _backend(_Answers(_Response({"models": [{"name": "llama3"}]})))

    assert backend.validate_api_key() is True


def test_listing_models_still_answers_empty_when_the_server_is_unreachable() -> None:
    assert _backend(_Unreachable()).list_models() == []


@pytest.mark.anyio
async def test_setup_validation_reports_an_unreachable_server(monkeypatch) -> None:
    async def _admin(db, request):
        return SimpleNamespace(id=1, role="owner")

    async def _db() -> AsyncGenerator[object, None]:
        yield object()

    monkeypatch.setattr(setup, "check_setup_permission", _admin)
    monkeypatch.setattr(
        setup, "get_llm_backend", lambda *args, **kwargs: _backend(_Unreachable())
    )
    monkeypatch.setattr(
        setup.config_manager,
        "get",
        lambda key, default=None: None if key == "ollama_api_url" else default,
    )
    app = create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = _db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/setup/validate-llm", json={"provider": "ollama"}
        )

    assert response.status_code == 400
    assert response.json() == {"detail": setup.PUBLIC_LLM_VALIDATION_ERROR_DETAIL}
