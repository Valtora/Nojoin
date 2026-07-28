from __future__ import annotations

import logging
from collections import Counter
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_current_admin_user, get_current_user, get_db
from backend.api.v1.endpoints import invitations, setup, system
from backend.main import create_app

BOOTSTRAP_PASSWORD = "bootstrap-secret"
LEGACY_FIRST_RUN_PASSWORD_HEADER = "X-First-Run-Password"
SECURE_TEST_BASE_URL = "https://test"

# Captured before the autouse bypass fixture patches the module attribute so
# the dedicated rate-limit test can restore the real implementation.
_REAL_ENFORCE_SETUP_RATE_LIMIT = setup.enforce_setup_rate_limit


@pytest.fixture(autouse=True)
def _bypass_setup_rate_limit(monkeypatch):
    async def _allow(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(setup, "enforce_setup_rate_limit", _allow)
    monkeypatch.setattr(system, "enforce_setup_rate_limit", _allow)


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


def _authenticated_setup_headers(
    token: str = "authenticated-setup-token",
) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _authenticated_setup_user(*args, **kwargs):
    return SimpleNamespace(
        id=1,
        role="owner",
        is_superuser=False,
        force_password_change=False,
        settings={},
    )


@pytest.mark.anyio
async def test_system_status_requires_authentication() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = _unauthorized_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.get("/api/v1/system/status")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_system_status_returns_initialized_flag_for_authenticated_user() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="user"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.get("/api/v1/system/status")

    assert response.status_code == 200
    assert response.json() == {"initialized": True}


@pytest.mark.anyio
async def test_public_health_is_minimal() -> None:
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
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
                    role="user",
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

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
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

    monkeypatch.setattr(
        setup, "get_llm_backend", lambda *args, **kwargs: _BrokenBackend()
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
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
async def test_authenticated_setup_validation_hides_provider_errors(
    monkeypatch, caplog
) -> None:
    app, _ = _build_app(initialized=True)
    secret_detail = "secret provider failure with /tmp/cache details"

    class _BrokenBackend:
        def validate_api_key(self):
            raise RuntimeError(secret_detail)

    monkeypatch.setattr(
        setup, "get_authenticated_user_from_token", _authenticated_setup_user
    )
    monkeypatch.setattr(
        setup, "enforce_password_change_policy", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        setup, "get_llm_backend", lambda *args, **kwargs: _BrokenBackend()
    )

    with caplog.at_level(logging.ERROR, logger=setup.logger.name):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/api/v1/setup/validate-llm",
                json={"provider": "openai", "api_key": "test-key"},
                headers=_authenticated_setup_headers(),
            )

    assert response.status_code == 400
    assert response.json() == {
        "detail": setup.PUBLIC_LLM_VALIDATION_ERROR_DETAIL,
    }
    assert secret_detail not in response.text
    assert secret_detail in caplog.text


@pytest.mark.anyio
async def test_authenticated_setup_hf_validation_hides_provider_errors(
    monkeypatch, caplog
) -> None:
    app, _ = _build_app(initialized=True)
    secret_detail = "hf validation failed for account secret-user@example.com"

    class _BrokenHFClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            raise RuntimeError(secret_detail)

    monkeypatch.setattr(
        setup, "get_authenticated_user_from_token", _authenticated_setup_user
    )
    monkeypatch.setattr(
        setup, "enforce_password_change_policy", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(setup.httpx, "AsyncClient", _BrokenHFClient)

    with caplog.at_level(logging.ERROR, logger=setup.logger.name):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/api/v1/setup/validate-hf",
                json={"token": "hf_test_token"},
                headers=_authenticated_setup_headers(),
            )

    assert response.status_code == 400
    assert response.json() == {
        "detail": setup.PUBLIC_HF_VALIDATION_ERROR_DETAIL,
    }
    assert secret_detail not in response.text
    assert secret_detail in caplog.text


@pytest.mark.anyio
async def test_authenticated_setup_model_listing_hides_provider_errors(
    monkeypatch, caplog
) -> None:
    app, _ = _build_app(initialized=True)
    secret_detail = "model listing failure exposed internal provider state"

    class _BrokenBackend:
        def list_models(self):
            raise RuntimeError(secret_detail)

    monkeypatch.setattr(
        setup, "get_authenticated_user_from_token", _authenticated_setup_user
    )
    monkeypatch.setattr(
        setup, "enforce_password_change_policy", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        setup, "get_llm_backend", lambda *args, **kwargs: _BrokenBackend()
    )

    with caplog.at_level(logging.ERROR, logger=setup.logger.name):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/api/v1/setup/list-models",
                json={"provider": "openai", "api_key": "test-key"},
                headers=_authenticated_setup_headers(),
            )

    assert response.status_code == 400
    assert response.json() == {
        "detail": setup.PUBLIC_MODEL_LIST_ERROR_DETAIL,
    }
    assert secret_detail not in response.text
    assert secret_detail in caplog.text


@pytest.mark.anyio
async def test_setup_hf_validation_does_not_disclose_account_identity(
    monkeypatch,
) -> None:
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

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
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

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.post(
            "/api/v1/system/setup",
            json={"username": "owner", "password": "password123"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "First-run setup access denied.",
    }


@pytest.mark.anyio
async def test_first_run_setup_rejects_legacy_bootstrap_header(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)
    monkeypatch.setattr(system, "seed_demo_data", lambda *args, **kwargs: None)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.post(
            "/api/v1/system/setup",
            headers={LEGACY_FIRST_RUN_PASSWORD_HEADER: BOOTSTRAP_PASSWORD},
            json={"username": "owner", "password": "password123"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "First-run setup access denied.",
    }


@pytest.mark.anyio
async def test_first_run_setup_rejects_when_server_password_is_unset(
    monkeypatch,
) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.delenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, raising=False)
    monkeypatch.setattr(system, "seed_demo_data", lambda *args, **kwargs: None)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.post(
            "/api/v1/system/setup",
            headers=_bootstrap_auth_headers(),
            json={"username": "owner", "password": "password123"},
        )

    # Fails closed with the same generic denial used everywhere else so the
    # unset-password state is not disclosed to anonymous clients.
    assert response.status_code == 403
    assert response.json() == {
        "detail": "First-run setup access denied.",
    }


@pytest.mark.anyio
async def test_first_run_setup_accepts_correct_bootstrap_password(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)

    async def _fake_seed_demo_data(*args, **kwargs):
        return None

    monkeypatch.setattr(system, "seed_demo_data", _fake_seed_demo_data)

    async def _fake_enqueue_model_preparation(**kwargs):
        return "model-prep-task"

    monkeypatch.setattr(
        system, "enqueue_model_preparation", _fake_enqueue_model_preparation
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
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
    assert response.json() == {
        "initialized": True,
        "model_preparation_task_id": "model-prep-task",
    }


@pytest.mark.anyio
async def test_initialised_setup_helpers_do_not_disclose_state_without_auth(
    monkeypatch,
) -> None:
    app, _ = _build_app(initialized=True)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
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

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
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
async def test_setup_denials_do_not_disclose_initialisation_state(
    monkeypatch,
) -> None:
    """
    Anonymous setup denials must be byte-identical across every state an
    outside prober could try to distinguish: uninitialised with a wrong
    bootstrap password, uninitialised with FIRST_RUN_PASSWORD unset, and
    initialised without credentials.
    """
    responses: list[tuple[int, dict]] = []

    scenarios = [
        {"initialized": False, "env_password": BOOTSTRAP_PASSWORD},
        {"initialized": False, "env_password": None},
        {"initialized": True, "env_password": BOOTSTRAP_PASSWORD},
    ]

    for scenario in scenarios:
        app, _ = _build_app(initialized=scenario["initialized"])
        if scenario["env_password"] is None:
            monkeypatch.delenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, raising=False)
        else:
            monkeypatch.setenv(
                setup.FIRST_RUN_PASSWORD_ENV_KEY, scenario["env_password"]
            )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
        ) as client:
            get_response = await client.get(
                "/api/v1/setup/initial-config",
                headers=_bootstrap_auth_headers("wrong-guess"),
            )
            post_response = await client.post(
                "/api/v1/system/setup",
                headers=_bootstrap_auth_headers("wrong-guess"),
                json={"username": "owner", "password": "password123"},
            )

        responses.append((get_response.status_code, get_response.json()))
        responses.append((post_response.status_code, post_response.json()))

    assert all(item == responses[0] for item in responses)
    assert responses[0] == (403, {"detail": "First-run setup access denied."})


@pytest.mark.anyio
async def test_setup_requests_are_rate_limited(monkeypatch) -> None:
    from backend.utils import rate_limit as rate_limit_utils

    monkeypatch.setattr(
        setup, "enforce_setup_rate_limit", _REAL_ENFORCE_SETUP_RATE_LIMIT
    )
    monkeypatch.setattr(
        system, "enforce_setup_rate_limit", _REAL_ENFORCE_SETUP_RATE_LIMIT
    )
    monkeypatch.setattr(setup, "SETUP_RATE_LIMIT", 2)

    async def _no_redis():
        return None

    monkeypatch.setattr(rate_limit_utils, "_get_redis", _no_redis)
    rate_limit_utils._fallback_windows.clear()

    app, _ = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        first = await client.get(
            "/api/v1/setup/initial-config",
            headers=_bootstrap_auth_headers("wrong-guess"),
        )
        second = await client.post(
            "/api/v1/system/setup",
            headers=_bootstrap_auth_headers("wrong-guess"),
            json={"username": "owner", "password": "password123"},
        )
        third = await client.get(
            "/api/v1/setup/initial-config",
            headers=_bootstrap_auth_headers("wrong-guess"),
        )

    rate_limit_utils._fallback_windows.clear()

    assert first.status_code == 403
    assert second.status_code == 403
    assert third.status_code == 429
    assert third.json() == {"detail": setup.SETUP_RATE_LIMIT_DETAIL}
    assert "retry-after" in {key.lower() for key in third.headers}


@pytest.mark.anyio
async def test_initial_config_masks_prefilled_secrets(monkeypatch) -> None:
    app, _ = _build_app(initialized=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-value")
    monkeypatch.setenv("HF_TOKEN", "hf_super_secret_value")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
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

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.get("/api/v1/system/health")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_detailed_system_health_returns_component_status_for_authenticated_user(
    monkeypatch,
) -> None:
    app, _ = _build_app(initialized=True)
    # A standard user: the health payload must not carry the admin-only
    # telemetry notice flag for them.
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="user", is_superuser=False
    )

    async def fake_health_status():
        return {
            "status": "warning",
            "version": "2.0.0",
            "deployment_warnings": [
                {
                    "code": "placeholder_first_run_password",
                    "key": "FIRST_RUN_PASSWORD",
                    "title": "Placeholder bootstrap password configured",
                    "message": "Update it.",
                }
            ],
            "components": {
                "db": "connected",
                "worker": "inactive",
            },
        }

    monkeypatch.setattr(system, "get_system_health_status", fake_health_status)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.get("/api/v1/system/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "warning",
        "version": "2.0.0",
        "deployment_warnings": [
            {
                "code": "placeholder_first_run_password",
                "key": "FIRST_RUN_PASSWORD",
                "title": "Placeholder bootstrap password configured",
                "message": "Update it.",
            }
        ],
        "components": {
            "db": "connected",
            "worker": "inactive",
        },
    }


@pytest.mark.anyio
async def test_admin_health_requires_authentication() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_admin_user] = _unauthorized_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.get("/api/v1/system/admin-health")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_admin_health_returns_readiness_payload_for_admin(monkeypatch) -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_admin_user] = lambda: SimpleNamespace(
        id=1,
        role="admin",
        is_superuser=False,
    )

    async def fake_admin_health_status(_db):
        return {
            "status": "warning",
            "version": "2.1.0",
            "summary": {
                "pipeline_status": "degraded",
                "message": "Core transcription is ready, but some processing capabilities are in fallback mode.",
                "blocking_reasons": [],
                "degraded_reasons": [
                    "Speaker diarization will fall back until its prerequisites are ready.",
                ],
            },
            "checks": {
                "database": {"status": "ok", "label": "Connected"},
                "worker": {"status": "ok", "label": "Active"},
                "queue": {"status": "ok", "label": "Reachable"},
                "ffmpeg": {"status": "ok", "label": "Ready"},
                "transcription_model": {"status": "ok", "label": "Ready"},
                "diarization": {"status": "warning", "label": "Fallback active"},
                "device": {"status": "warning", "label": "CPU fallback"},
                "optional_ai": {"status": "info", "label": "Not configured"},
            },
            "download": {
                "in_progress": False,
                "status": None,
                "stage": None,
                "message": None,
                "progress": None,
            },
        }

    monkeypatch.setattr(system, "get_admin_health_status", fake_admin_health_status)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.get("/api/v1/system/admin-health")

    assert response.status_code == 200
    assert response.json()["summary"]["pipeline_status"] == "degraded"
    assert response.json()["checks"]["device"]["label"] == "CPU fallback"
    assert response.json()["checks"]["diarization"]["status"] == "warning"


@pytest.mark.anyio
async def test_task_status_hides_internal_failure_details() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="admin", is_superuser=False
    )

    class _FailedTaskResult:
        status = "FAILURE"
        result = RuntimeError("internal traceback detail")
        info = RuntimeError("internal traceback detail")

    original_async_result = system.AsyncResult
    system.AsyncResult = lambda task_id: _FailedTaskResult()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
        ) as client:
            response = await client.get("/api/v1/system/tasks/test-task")
    finally:
        system.AsyncResult = original_async_result

    assert response.status_code == 200
    assert response.json()["result"] == "Task failed. Check server logs for details."


@pytest.mark.anyio
async def test_cors_preflight_uses_explicit_allowlists() -> None:
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
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
    app.dependency_overrides[get_current_admin_user] = _unauthorized_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        status = await client.get("/api/v1/system/status")
        models_status = await client.get("/api/v1/system/models/status")
        download_progress = await client.get("/api/v1/system/download-progress")
        companion_releases = await client.get("/api/v1/system/companion-releases")
        admin_health = await client.get("/api/v1/system/admin-health")

    assert status.status_code == 401
    assert models_status.status_code == 401
    assert download_progress.status_code == 401
    assert companion_releases.status_code == 401
    assert admin_health.status_code == 401


@pytest.mark.anyio
async def test_companion_releases_endpoint_is_gone_for_authenticated_users() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="user"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        response = await client.get("/api/v1/system/companion-releases")

    assert response.status_code == 410
    assert response.json() == {
        "error": "companion_retired",
        "message": "The Nojoin Companion app has been retired. Please update your installation and use the web app for recording.",
        "see": "https://github.com/Valtora/Nojoin/blob/main/docs/CAPTURE.md",
    }


@pytest.mark.anyio
async def test_openapi_and_docs_require_authentication() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = _unauthorized_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
        openapi_response = await client.get("/api/v1/openapi.json")
        docs_response = await client.get("/api/v1/docs")
        redoc_response = await client.get("/api/v1/redoc")

    assert openapi_response.status_code == 401
    assert docs_response.status_code == 401
    assert redoc_response.status_code == 401


@pytest.mark.anyio
async def test_openapi_and_docs_are_available_to_authenticated_users() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="user"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL
    ) as client:
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
async def test_safe_http_requests_redirect_to_canonical_https_origin(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://nojoin.example.com")
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://nojoin.example.com",
    ) as client:
        # Health paths are exempt from HTTPS enforcement, so use a guarded path
        # to prove plain-HTTP safe requests still redirect to canonical HTTPS.
        response = await client.get("/api/v1/system/health?probe=1")

    assert response.status_code == 307
    assert (
        response.headers["location"]
        == "https://nojoin.example.com/api/v1/system/health?probe=1"
    )


@pytest.mark.anyio
async def test_unsafe_http_requests_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://nojoin.example.com")
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://nojoin.example.com",
    ) as client:
        # A guarded path (health is exempt) proves unsafe plain-HTTP is rejected.
        response = await client.post("/api/v1/system/health")

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Plain HTTP requests are not allowed. Use HTTPS."
    }


@pytest.mark.anyio
async def test_forwarded_https_proxy_requests_are_accepted(monkeypatch) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://nojoin.example.com")
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://nojoin.example.com",
    ) as client:
        # Health paths bypass HTTPS enforcement, so exercise the proxy-scheme
        # gate with a guarded endpoint: a forwarded-https request is accepted
        # (reaches auth and 401s) rather than being redirected to canonical HTTPS.
        response = await client.get(
            "/api/v1/system/health",
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "nojoin.example.com",
            },
        )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_forwarded_https_proxy_requests_accept_host_headers_with_ports(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://localhost:14443")
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://localhost:14443",
    ) as client:
        # Health paths bypass HTTPS enforcement; use a guarded endpoint to prove
        # the host-with-port normalisation still accepts the forwarded request.
        response = await client.get(
            "/api/v1/system/health",
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "localhost:14443",
            },
        )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_liveness_endpoints_bypass_https_enforcement() -> None:
    # Container/orchestrator probes and internal uptime monitors call the
    # liveness endpoints directly over plain HTTP, without forwarded-proto
    # headers. They must answer 200 rather than being redirected to HTTPS.
    app, _ = _build_app(initialized=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/health", "/api/health"):
            response = await client.get(path)
            assert response.status_code == 200, path
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
    assert (
        response.headers["access-control-allow-origin"] == "https://nojoin.example.com"
    )


@pytest.mark.anyio
async def test_models_status_admin() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="admin", is_superuser=False
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url=SECURE_TEST_BASE_URL,
    ) as client:
        response = await client.get("/api/v1/system/models/status")

    assert response.status_code == 200
    res_data = response.json()
    assert "whisper" in res_data
    # Admin users should see the unredacted paths/checked_paths lists
    assert len(res_data["whisper"]["checked_paths"]) > 0


@pytest.mark.anyio
async def test_models_status_non_admin() -> None:
    app, _ = _build_app(initialized=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="user", is_superuser=False
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url=SECURE_TEST_BASE_URL,
    ) as client:
        response = await client.get("/api/v1/system/models/status")

    assert response.status_code == 200
    res_data = response.json()
    assert "whisper" in res_data
    # Non-admin users should get redacted path and checked_paths
    for model in res_data:
        assert res_data[model]["path"] is None
        assert res_data[model]["checked_paths"] == []
