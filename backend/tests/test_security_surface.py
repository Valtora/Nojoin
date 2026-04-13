from __future__ import annotations

from collections.abc import AsyncGenerator
from collections import Counter
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import invitations, setup, system
from backend.main import create_app


class _FakeResult:
    def __init__(self, initialized: bool):
        self._initialized = initialized

    def scalar_one_or_none(self):
        return object() if self._initialized else None


class _FakeSession:
    def __init__(self, initialized: bool):
        self._initialized = initialized

    async def execute(self, statement):
        return _FakeResult(self._initialized)


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _build_app(*, initialized: bool) -> tuple:
    app = create_app(app_lifespan=None)

    async def override_get_db() -> AsyncGenerator[_FakeSession, None]:
        yield _FakeSession(initialized)

    app.dependency_overrides[get_db] = override_get_db
    return app, override_get_db


def _unauthorized_user():
    raise HTTPException(status_code=401, detail="Not authenticated")


@pytest.mark.anyio
async def test_system_status_returns_only_initialized_flag() -> None:
    app, _ = _build_app(initialized=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/system/status")

    assert response.status_code == 200
    assert response.json() == {"initialized": True}


@pytest.mark.anyio
async def test_system_status_reports_uninitialized_without_extra_metadata() -> None:
    app, _ = _build_app(initialized=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/system/status")

    assert response.status_code == 200
    assert response.json() == {"initialized": False}


@pytest.mark.anyio
async def test_public_health_is_minimal() -> None:
    app, _ = _build_app(initialized=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_public_invitation_validation_is_minimal(monkeypatch) -> None:
    app = create_app(app_lifespan=None)

    class _InvitationSession:
        async def execute(self, statement):
            return _FakeScalarResult(
                SimpleNamespace(
                    code="invite123",
                    is_revoked=False,
                    expires_at=None,
                    max_uses=None,
                    used_count=0,
                )
            )

    async def override_get_db() -> AsyncGenerator[_InvitationSession, None]:
        yield _InvitationSession()

    async def _allow_request(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(invitations, "enforce_rate_limit", _allow_request)
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/invitations/validate/invite123")

    assert response.status_code == 200
    assert response.json() == {"valid": True}


@pytest.mark.anyio
async def test_setup_validation_hides_provider_errors_when_public(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)

    class _BrokenBackend:
        def validate_api_key(self):
            raise RuntimeError("secret provider failure with /tmp/cache details")

    monkeypatch.setattr(setup, "get_llm_backend", lambda *args, **kwargs: _BrokenBackend())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/setup/validate-llm",
            json={"provider": "openai", "api_key": "test-key"},
        )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Unable to validate the AI provider configuration.",
    }


@pytest.mark.anyio
async def test_setup_hf_validation_does_not_disclose_account_identity(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)

    class _FakeHFResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"name": "sensitive-user-name"}

    class _FakeHFClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            return _FakeHFResponse()

    monkeypatch.setattr(setup.httpx, "AsyncClient", _FakeHFClient)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/setup/validate-hf",
            json={"token": "hf_test_token"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "message": "Hugging Face token is valid.",
    }


@pytest.mark.anyio
async def test_detailed_system_health_requires_authentication() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = _unauthorized_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/system/health")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_detailed_system_health_returns_component_status_for_authenticated_user(monkeypatch) -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="user")

    async def fake_health_status():
        return {
            "status": "warning",
            "version": "2.0.0",
            "components": {
                "db": "connected",
                "worker": "inactive",
            },
        }

    monkeypatch.setattr(system, "get_system_health_status", fake_health_status)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/system/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "warning",
        "version": "2.0.0",
        "components": {
            "db": "connected",
            "worker": "inactive",
        },
    }


@pytest.mark.anyio
async def test_task_status_hides_internal_failure_details() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="user")

    class _FailedTaskResult:
        status = "FAILURE"
        result = RuntimeError("internal traceback detail")
        info = RuntimeError("internal traceback detail")

    original_async_result = system.AsyncResult
    system.AsyncResult = lambda task_id: _FailedTaskResult()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/system/tasks/test-task")
    finally:
        system.AsyncResult = original_async_result

    assert response.status_code == 200
    assert response.json()["result"] == "Task failed. Check server logs for details."


@pytest.mark.anyio
async def test_cors_preflight_uses_explicit_allowlists() -> None:
    app, _ = _build_app(initialized=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:14141",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-methods"] != "*"
    assert "GET" in response.headers["access-control-allow-methods"]
    assert "POST" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-headers"] != "*"
    assert "Authorization" in response.headers["access-control-allow-headers"]
    assert "Content-Type" in response.headers["access-control-allow-headers"]


@pytest.mark.anyio
async def test_operational_system_endpoints_require_authentication() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = _unauthorized_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        download_progress = await client.get("/api/v1/system/download-progress")
        models_status = await client.get("/api/v1/system/models/status")
        companion_releases = await client.get("/api/v1/system/companion-releases")

    assert download_progress.status_code == 401
    assert models_status.status_code == 401
    assert companion_releases.status_code == 401


@pytest.mark.anyio
async def test_openapi_and_docs_require_authentication() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = _unauthorized_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        openapi_response = await client.get("/api/v1/openapi.json")
        docs_response = await client.get("/api/v1/docs")
        redoc_response = await client.get("/api/v1/redoc")

    assert openapi_response.status_code == 401
    assert docs_response.status_code == 401
    assert redoc_response.status_code == 401


@pytest.mark.anyio
async def test_openapi_and_docs_are_available_to_authenticated_users() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="user")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        openapi_response = await client.get("/api/v1/openapi.json")
        docs_response = await client.get("/api/v1/docs")
        redoc_response = await client.get("/api/v1/redoc")

    assert openapi_response.status_code == 200
    assert openapi_response.json()["info"]["title"] == "Nojoin API"
    assert docs_response.status_code == 200
    assert "Swagger UI" in docs_response.text
    assert redoc_response.status_code == 200
    assert "ReDoc" in redoc_response.text


def test_http_routes_do_not_register_duplicate_path_method_pairs() -> None:
    app, _ = _build_app(initialized=True)

    route_signatures = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            route_signatures.append((path, method))

    duplicates = [
        signature for signature, count in Counter(route_signatures).items() if count > 1
    ]

    assert duplicates == []