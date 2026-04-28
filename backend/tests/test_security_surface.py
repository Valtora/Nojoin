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


BOOTSTRAP_PASSWORD = "bootstrap-secret"
LEGACY_FIRST_RUN_PASSWORD_HEADER = "X-First-Run-Password"
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


def _bootstrap_auth_headers(password: str = BOOTSTRAP_PASSWORD) -> dict[str, str]:
    return {
        "Authorization": f"{setup.FIRST_RUN_PASSWORD_AUTH_SCHEME} {password}",
    }


@pytest.mark.anyio
async def test_system_status_requires_authentication() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = _unauthorized_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.get("/api/v1/system/status")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_system_status_returns_initialized_flag_for_authenticated_user() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="user")

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.get("/api/v1/system/status")

    assert response.status_code == 200
    assert response.json() == {"initialized": True}


@pytest.mark.anyio
async def test_public_health_is_minimal() -> None:
    app, _ = _build_app(initialized=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.get("/api/v1/invitations/validate/invite123")

    assert response.status_code == 200
    assert response.json() == {"valid": True}


@pytest.mark.anyio
async def test_setup_validation_hides_provider_errors_when_public(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)

    class _BrokenBackend:
        def validate_api_key(self):
            raise RuntimeError("secret provider failure with /tmp/cache details")

    monkeypatch.setattr(setup, "get_llm_backend", lambda *args, **kwargs: _BrokenBackend())

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.post(
            "/api/v1/setup/validate-llm",
            json={"provider": "openai", "api_key": "test-key"},
            headers=_bootstrap_auth_headers(),
        )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Unable to validate the AI provider configuration.",
    }


@pytest.mark.anyio
async def test_setup_hf_validation_does_not_disclose_account_identity(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)

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

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.post(
            "/api/v1/setup/validate-hf",
            json={"token": "hf_test_token"},
            headers=_bootstrap_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "message": "Hugging Face token is valid.",
    }


@pytest.mark.anyio
async def test_first_run_setup_rejects_missing_bootstrap_password(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)
    monkeypatch.setattr(system, "seed_demo_data", lambda *args, **kwargs: None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.post(
            "/api/v1/system/setup",
            json={"username": "owner", "password": "password123"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Bootstrap password required for first-run setup.",
    }


@pytest.mark.anyio
async def test_first_run_setup_rejects_legacy_bootstrap_header(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)
    monkeypatch.setattr(system, "seed_demo_data", lambda *args, **kwargs: None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.post(
            "/api/v1/system/setup",
            headers={LEGACY_FIRST_RUN_PASSWORD_HEADER: BOOTSTRAP_PASSWORD},
            json={"username": "owner", "password": "password123"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Bootstrap password required for first-run setup.",
    }


@pytest.mark.anyio
async def test_first_run_setup_rejects_when_server_password_is_unset(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.delenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, raising=False)
    monkeypatch.setattr(system, "seed_demo_data", lambda *args, **kwargs: None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.post(
            "/api/v1/system/setup",
            headers=_bootstrap_auth_headers(),
            json={"username": "owner", "password": "password123"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "First-run setup is disabled until FIRST_RUN_PASSWORD is set. "
            "Set the env var and restart or redeploy Nojoin before initialising the system."
        ),
    }


@pytest.mark.anyio
async def test_first_run_setup_accepts_correct_bootstrap_password(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)

    async def _fake_seed_demo_data(*args, **kwargs):
        return None

    monkeypatch.setattr(system, "seed_demo_data", _fake_seed_demo_data)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.post(
            "/api/v1/system/setup",
            headers=_bootstrap_auth_headers(),
            json={
                "username": "owner",
                "password": "password123",
                "llm_provider": "gemini",
                "gemini_api_key": "gem-secret-value",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"initialized": True}


@pytest.mark.anyio
async def test_initialised_setup_helpers_do_not_disclose_state_without_auth(monkeypatch) -> None:
    app, _ = _build_app(initialized=True)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.get(
            "/api/v1/setup/initial-config",
            headers=_bootstrap_auth_headers(),
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "First-run setup access denied.",
    }


@pytest.mark.anyio
async def test_initialised_setup_post_does_not_disclose_state(monkeypatch) -> None:
    app, _ = _build_app(initialized=True)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)
    monkeypatch.setattr(system, "seed_demo_data", lambda *args, **kwargs: None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.post(
            "/api/v1/system/setup",
            headers=_bootstrap_auth_headers(),
            json={"username": "owner", "password": "password123"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "First-run setup access denied.",
    }


@pytest.mark.anyio
async def test_initial_config_masks_prefilled_secrets(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-value")
    monkeypatch.setenv("HF_TOKEN", "hf_super_secret_value")

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.get(
            "/api/v1/setup/initial-config",
            headers=_bootstrap_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json()["gemini_api_key"] == "gem...alue"
    assert response.json()["hf_token"] == "hf_...alue"
    assert response.json()["selected_model"] is not None
    assert "gemini-secret-value" not in response.text
    assert "hf_super_secret_value" not in response.text


@pytest.mark.anyio
async def test_detailed_system_health_requires_authentication() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = _unauthorized_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
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
        async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
            response = await client.get("/api/v1/system/tasks/test-task")
    finally:
        system.AsyncResult = original_async_result

    assert response.status_code == 200
    assert response.json()["result"] == "Task failed. Check server logs for details."


@pytest.mark.anyio
async def test_cors_preflight_uses_explicit_allowlists() -> None:
    app, _ = _build_app(initialized=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        status = await client.get("/api/v1/system/status")
        download_progress = await client.get("/api/v1/system/download-progress")
        models_status = await client.get("/api/v1/system/models/status")
        companion_releases = await client.get("/api/v1/system/companion-releases")

    assert status.status_code == 401
    assert download_progress.status_code == 401
    assert models_status.status_code == 401
    assert companion_releases.status_code == 401


@pytest.mark.anyio
async def test_openapi_and_docs_require_authentication() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = _unauthorized_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
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


@pytest.mark.anyio
async def test_safe_http_requests_redirect_to_canonical_https_origin(monkeypatch) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://nojoin.example.com")
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://nojoin.example.com",
    ) as client:
        response = await client.get("/api/health?probe=1")

    assert response.status_code == 307
    assert response.headers["location"] == "https://nojoin.example.com/api/health?probe=1"


@pytest.mark.anyio
async def test_unsafe_http_requests_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://nojoin.example.com")
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://nojoin.example.com",
    ) as client:
        response = await client.post("/api/health")

    assert response.status_code == 400
    assert response.json() == {"detail": "Plain HTTP requests are not allowed. Use HTTPS."}


@pytest.mark.anyio
async def test_forwarded_https_proxy_requests_are_accepted(monkeypatch) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://nojoin.example.com")
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://nojoin.example.com",
    ) as client:
        response = await client.get(
            "/api/health",
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "nojoin.example.com",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_forwarded_https_proxy_requests_accept_host_headers_with_ports(monkeypatch) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://localhost:14443")
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://localhost:14443",
    ) as client:
        response = await client.get(
            "/api/health",
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "localhost:14443",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_untrusted_hosts_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://nojoin.example.com")
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://unexpected.example.com",
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 400


@pytest.mark.anyio
async def test_cors_preflight_allows_configured_web_app_url(monkeypatch) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://nojoin.example.com")
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url=SECURE_TEST_BASE_URL,
    ) as client:
        response = await client.options(
            "/api/health",
            headers={
                "Origin": "https://nojoin.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://nojoin.example.com"