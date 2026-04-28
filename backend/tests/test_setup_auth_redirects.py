from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import login, setup, system
from backend.main import create_app


BOOTSTRAP_PASSWORD = "bootstrap-secret"
SECURE_TEST_BASE_URL = "https://test"


class _FakeResult:
    def __init__(self, initialized: bool):
        self._initialized = initialized

    def scalar_one_or_none(self):
        return object() if self._initialized else None


class _FakeSession:
    def __init__(self, initialized: bool):
        self._initialized = initialized
        self._added = []

    async def execute(self, statement):
        return _FakeResult(self._initialized)

    def add(self, value):
        self._added.append(value)

    async def commit(self):
        if self._added:
            self._initialized = True

    async def refresh(self, value):
        if getattr(value, "id", None) is None:
            value.id = 1


def _build_app(*, initialized: bool):
    app = create_app(app_lifespan=None)

    async def override_get_db() -> AsyncGenerator[_FakeSession, None]:
        yield _FakeSession(initialized)

    app.dependency_overrides[get_db] = override_get_db
    return app


def _bootstrap_auth_headers(password: str = BOOTSTRAP_PASSWORD) -> dict[str, str]:
    return {
        "Authorization": f"{setup.FIRST_RUN_PASSWORD_AUTH_SCHEME} {password}",
    }


@pytest.mark.anyio
async def test_setup_endpoints_do_not_redirect_or_emit_location_headers(monkeypatch) -> None:
    app = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)

    class _ValidBackend:
        def validate_api_key(self):
            return None

        def list_models(self):
            return ["gemini-2.5-flash"]

    class _FakeHFResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"name": "masked"}

    class _FakeHFClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            return _FakeHFResponse()

    async def _fake_seed_demo_data(*args, **kwargs):
        return None

    monkeypatch.setattr(setup, "get_llm_backend", lambda *args, **kwargs: _ValidBackend())
    monkeypatch.setattr(setup.httpx, "AsyncClient", _FakeHFClient)
    monkeypatch.setattr(system, "seed_demo_data", _fake_seed_demo_data)

    headers = _bootstrap_auth_headers()

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        responses = [
            await client.get("/api/v1/setup/initial-config", headers=headers),
            await client.post(
                "/api/v1/setup/validate-llm",
                json={"provider": "openai", "api_key": "test-key"},
                headers=headers,
            ),
            await client.post(
                "/api/v1/setup/validate-hf",
                json={"token": "hf_test_token"},
                headers=headers,
            ),
            await client.post(
                "/api/v1/setup/list-models",
                json={"provider": "openai", "api_key": "test-key"},
                headers=headers,
            ),
            await client.post(
                "/api/v1/system/setup",
                headers=headers,
                json={"username": "owner", "password": "password123"},
            ),
        ]

    for response in responses:
        assert response.status_code == 200
        assert "location" not in response.headers


@pytest.mark.anyio
async def test_auth_endpoints_do_not_redirect_or_emit_location_headers(monkeypatch) -> None:
    app = _build_app(initialized=True)

    async def _allow_request(*args, **kwargs):
        return None

    async def _fake_authenticate(*args, **kwargs):
        return SimpleNamespace(
            username="owner",
            force_password_change=False,
            is_superuser=True,
            is_active=True,
        )

    monkeypatch.setattr(login, "enforce_rate_limit", _allow_request)
    monkeypatch.setattr(login, "_authenticate_user_credentials", _fake_authenticate)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1,
        username="owner",
        role="owner",
        is_superuser=True,
        force_password_change=False,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        responses = [
            await client.post(
                "/api/v1/login/access-token",
                data={"username": "owner", "password": "password123"},
            ),
            await client.post(
                "/api/v1/login/session",
                data={"username": "owner", "password": "password123"},
            ),
            await client.post("/api/v1/login/logout"),
        ]

    for response in responses:
        assert response.status_code == 200
        assert "location" not in response.headers