"""The API asks LLM providers about models from a worker thread, never the loop.

Listing models from an unreachable Ollama waits out a 10 second connect
timeout. Made on the event loop, that wait stalled every other request the API
was serving, and validate-llm lists twice. Each test records whether every
blocking step of one endpoint (the Ollama URL check, which resolves DNS, the
backend construction and the provider call) ran with an event loop running in
its thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import llm as llm_endpoint
from backend.api.v1.endpoints import setup
from backend.main import create_app

SECURE_TEST_BASE_URL = "https://test"
OLLAMA_URL = "http://host.docker.internal:11434"


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class _RecordingBackend:
    def __init__(self, calls: list[tuple[str, bool]]):
        self._calls = calls

    def validate_api_key(self) -> bool:
        self._calls.append(("validate_api_key", _on_event_loop()))
        return True

    def list_models(self) -> list[str]:
        self._calls.append(("list_models", _on_event_loop()))
        return ["llama3"]

    def supports_vision(self) -> bool:
        self._calls.append(("supports_vision", _on_event_loop()))
        return True


def _record_blocking_steps(monkeypatch, module) -> list[tuple[str, bool]]:
    calls: list[tuple[str, bool]] = []

    def _fake_get_llm_backend(*args, **kwargs):
        calls.append(("get_llm_backend", _on_event_loop()))
        return _RecordingBackend(calls)

    def _fake_validate_url(url, *args, **kwargs):
        calls.append(("validate_url", _on_event_loop()))
        return url

    monkeypatch.setattr(
        module.config_manager,
        "get",
        lambda key, default=None: OLLAMA_URL if key == "ollama_api_url" else default,
    )
    monkeypatch.setattr(module, "get_llm_backend", _fake_get_llm_backend)
    if module is setup:
        monkeypatch.setattr(setup, "_validate_setup_ollama_api_url", _fake_validate_url)
    else:
        monkeypatch.setattr(module, "validate_ollama_api_url", _fake_validate_url)
    return calls


def _build_app():
    app = create_app(app_lifespan=None)

    async def override_get_db() -> AsyncGenerator[object, None]:
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="owner", is_superuser=True
    )
    return app


@pytest.fixture
def _authenticated_setup(monkeypatch):
    async def _admin(db, request):
        return SimpleNamespace(id=1, role="owner")

    monkeypatch.setattr(setup, "check_setup_permission", _admin)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "body", "expected_steps"),
    [
        (
            "POST",
            "/api/v1/setup/list-models",
            {"provider": "ollama"},
            ["validate_url", "get_llm_backend", "list_models"],
        ),
        (
            "POST",
            "/api/v1/setup/validate-llm",
            {"provider": "ollama", "model": "llama3"},
            ["validate_url", "get_llm_backend", "validate_api_key", "list_models"],
        ),
    ],
)
async def test_setup_provider_calls_run_off_the_event_loop(
    monkeypatch, _authenticated_setup, method, path, body, expected_steps
) -> None:
    calls = _record_blocking_steps(monkeypatch, setup)

    async with AsyncClient(
        transport=ASGITransport(app=_build_app()), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.request(method, path, json=body)

    assert response.status_code == 200
    assert [step for step, _ in calls] == expected_steps
    assert [step for step, on_loop in calls if on_loop] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "params", "expected_steps"),
    [
        (
            "/api/v1/llm/models",
            {"provider": "ollama", "api_key": "test-key"},
            ["validate_url", "get_llm_backend", "list_models"],
        ),
        (
            "/api/v1/llm/vision-support",
            {"provider": "ollama", "model": "llava"},
            ["validate_url", "get_llm_backend", "supports_vision"],
        ),
    ],
)
async def test_llm_provider_calls_run_off_the_event_loop(
    monkeypatch, path, params, expected_steps
) -> None:
    calls = _record_blocking_steps(monkeypatch, llm_endpoint)

    async with AsyncClient(
        transport=ASGITransport(app=_build_app()), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.get(path, params=params)

    assert response.status_code == 200
    assert [step for step, _ in calls] == expected_steps
    assert [step for step, on_loop in calls if on_loop] == []


@pytest.mark.anyio
async def test_setup_url_rejection_still_answers_400_from_the_worker_thread(
    monkeypatch, _authenticated_setup
) -> None:
    def _reject(url, *args, **kwargs):
        raise setup.HTTPException(status_code=400, detail="Ollama URL rejected.")

    _record_blocking_steps(monkeypatch, setup)
    monkeypatch.setattr(setup, "_validate_setup_ollama_api_url", _reject)

    async with AsyncClient(
        transport=ASGITransport(app=_build_app()), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.post(
            "/api/v1/setup/list-models", json={"provider": "ollama"}
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Ollama URL rejected."}
