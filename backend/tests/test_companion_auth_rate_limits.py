"""Integration tests for rate limits on the Companion auth endpoints.

These tests exercise the real ``enforce_rate_limit`` implementation. Redis is
forced to be unavailable so the in-process fallback bucket is used, and the
process-wide ``_fallback_windows`` map is reset between tests to keep them
isolated. Each test additionally uses a unique ``X-Real-IP`` value so a state
leak between tests can never silently pass.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api import deps
from backend.api.deps import (
    get_current_pairing_management_user,
    get_current_user,
    get_db,
)
from backend.api.v1.endpoints import login
from backend.main import create_app
from backend.services.companion_pairing_service import (
    ActiveCompanionPairingAuth,
    CompanionCredentialExchangeResult,
    CompanionExchangeUser,
    PreparedCompanionPairingPayload,
)
from backend.utils import rate_limit

TRUSTED_ORIGIN = "https://nojoin.example.com"


@pytest.fixture
def fake_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        username="alice",
        role="user",
        is_superuser=False,
        force_password_change=False,
        is_active=True,
        token_version=0,
    )


@pytest.fixture(autouse=True)
def _force_in_process_rate_limiter(monkeypatch):
    """Force the rate limiter onto the in-process fallback and isolate state."""

    async def _no_redis() -> None:
        return None

    monkeypatch.setattr(rate_limit, "_get_redis", _no_redis)
    rate_limit._fallback_windows.clear()
    yield
    rate_limit._fallback_windows.clear()


@pytest.fixture
def app(monkeypatch, fake_user):
    monkeypatch.setenv("WEB_APP_URL", TRUSTED_ORIGIN)

    async def override_get_db():
        yield SimpleNamespace()

    async def override_current_user():
        return fake_user

    async def override_pairing_user():
        return fake_user

    async def fake_get_authenticated_user_from_token(*args, **kwargs):
        return fake_user

    monkeypatch.setattr(
        deps,
        "get_authenticated_user_from_token",
        fake_get_authenticated_user_from_token,
    )

    # Stub out the pairing service so endpoints exercise the full request path
    # but never touch the database or remote services.
    async def fake_prepare_companion_pairing(db, *, current_user, pairing_code, paired_web_origin, tls_fingerprint):
        return PreparedCompanionPairingPayload(
            pairing_code=pairing_code,
            companion_credential_secret="secret",
            api_protocol="https",
            api_host="localhost",
            api_port=8443,
            tls_fingerprint=None,
            local_control_secret="local-secret",
            local_control_secret_version=1,
            backend_pairing_id="pair-1",
        )

    async def fake_exchange_companion_credential(db, *, pairing_session_id, companion_credential_secret):
        return CompanionCredentialExchangeResult(
            user=CompanionExchangeUser(id=fake_user.id, username=fake_user.username, is_active=True),
            pairing_session_id=pairing_session_id,
            activated=True,
        )

    async def fake_get_active_companion_pairing_auth(db, *, current_user):
        return ActiveCompanionPairingAuth(
            pairing_session_id="session-1",
            paired_web_origin=TRUSTED_ORIGIN,
            local_control_secret="local-secret",
            local_control_secret_version=1,
        )

    async def fake_revoke_companion_pairings(db, *, current_user):
        return 1

    async def fake_cancel_pending_companion_pairings(db, *, current_user):
        return 1

    async def fake_publish_explicit_disconnect(user_id):
        return None

    def fake_resolve_tls_fingerprint():
        return None

    def fake_create_local_control_token(**kwargs):
        return "fake-local-control-token"

    def fake_create_access_token(*args, **kwargs):
        return "fake-access-token"

    monkeypatch.setattr(login, "prepare_companion_pairing", fake_prepare_companion_pairing)
    monkeypatch.setattr(login, "exchange_companion_credential", fake_exchange_companion_credential)
    monkeypatch.setattr(login, "get_active_companion_pairing_auth", fake_get_active_companion_pairing_auth)
    monkeypatch.setattr(login, "revoke_companion_pairings", fake_revoke_companion_pairings)
    monkeypatch.setattr(login, "cancel_pending_companion_pairings", fake_cancel_pending_companion_pairings)
    monkeypatch.setattr(
        login.companion_frontend_events,
        "publish_explicit_disconnect",
        fake_publish_explicit_disconnect,
    )
    monkeypatch.setattr(login, "resolve_tls_fingerprint", fake_resolve_tls_fingerprint)
    monkeypatch.setattr(login.security, "create_local_control_token", fake_create_local_control_token)
    monkeypatch.setattr(login.security, "create_access_token", fake_create_access_token)

    # Replace ``StreamingResponse`` inside the login module with a non-streaming
    # response so the SSE endpoint returns synchronously after the rate-limit
    # decision. The ``event_generator`` coroutine factory is then never
    # iterated, which keeps the test deterministic and fast.
    from fastapi.responses import Response

    class _ImmediateResponse(Response):
        def __init__(self, content=None, media_type=None, headers=None, **_kwargs):
            super().__init__(content=b"", media_type=media_type, headers=headers)

    monkeypatch.setattr(login, "StreamingResponse", _ImmediateResponse)

    application = create_app(app_lifespan=None)
    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_current_user] = override_current_user
    application.dependency_overrides[get_current_pairing_management_user] = override_pairing_user
    return application


@pytest.fixture
async def client(app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url=TRUSTED_ORIGIN,
    ) as async_client:
        yield async_client


def _ip() -> str:
    # Use a unique RFC5737 documentation-range address per call so test buckets
    # are completely independent even if state leaks somehow.
    return f"203.0.113.{uuid4().int % 254 + 1}"


def _headers(ip: str, **extra: str) -> dict[str, str]:
    headers = {"X-Real-IP": ip, "Origin": TRUSTED_ORIGIN}
    headers.update(extra)
    return headers


# ---------------------------------------------------------------------------
# /login/companion-pairing  (prepare)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_companion_pairing_prepare_is_rate_limited(client: AsyncClient) -> None:
    ip = _ip()
    body: dict[str, Any] = {"pairing_code": "ABC123"}
    limit = login.COMPANION_PAIRING_PREPARE_RATE_LIMIT

    for _ in range(limit):
        response = await client.post(
            "/api/v1/login/companion-pairing",
            json=body,
            headers=_headers(ip),
        )
        assert response.status_code == 200, response.text

    blocked = await client.post(
        "/api/v1/login/companion-pairing",
        json=body,
        headers=_headers(ip),
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


# ---------------------------------------------------------------------------
# /login/companion-pairing  (revoke / disconnect / cancel-pending share a bucket)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_companion_pairing_mutation_endpoints_share_a_bucket(client: AsyncClient) -> None:
    ip = _ip()
    limit = login.COMPANION_PAIRING_MUTATION_RATE_LIMIT

    # Spend the budget across all three mutation endpoints.
    spent = 0
    while spent < limit:
        if spent % 3 == 0:
            response = await client.delete(
                "/api/v1/login/companion-pairing",
                headers=_headers(ip),
            )
        elif spent % 3 == 1:
            response = await client.post(
                "/api/v1/login/companion-pairing/disconnect",
                headers=_headers(ip),
            )
        else:
            response = await client.delete(
                "/api/v1/login/companion-pairing/pending",
                headers=_headers(ip),
            )
        assert response.status_code == 200, response.text
        spent += 1

    # Hitting any of the three after exhaustion must 429.
    blocked = await client.post(
        "/api/v1/login/companion-pairing/disconnect",
        headers=_headers(ip),
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


# ---------------------------------------------------------------------------
# /login/companion-token/exchange  (per-session and per-IP buckets)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_companion_token_exchange_per_session_bucket(client: AsyncClient) -> None:
    ip = _ip()
    session_id = "stable-session"
    body = {"pairing_session_id": session_id, "companion_credential_secret": "secret"}
    limit = login.COMPANION_EXCHANGE_PER_SESSION_RATE_LIMIT

    for _ in range(limit):
        response = await client.post(
            "/api/v1/login/companion-token/exchange",
            json=body,
            headers=_headers(ip),
        )
        assert response.status_code == 200, response.text

    blocked = await client.post(
        "/api/v1/login/companion-token/exchange",
        json=body,
        headers=_headers(ip),
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


@pytest.mark.anyio
async def test_companion_token_exchange_per_ip_bucket_catches_rotated_sessions(client: AsyncClient) -> None:
    ip = _ip()
    ip_limit = login.COMPANION_EXCHANGE_PER_IP_RATE_LIMIT
    per_session_limit = login.COMPANION_EXCHANGE_PER_SESSION_RATE_LIMIT
    assert ip_limit > per_session_limit, "test assumes IP bucket is larger than per-session bucket"

    # Rotate the session id so the per-session bucket never trips. The per-IP
    # bucket should still drain and 429 once the IP-wide budget is gone.
    for index in range(ip_limit):
        body = {
            "pairing_session_id": f"rotating-session-{index}",
            "companion_credential_secret": "secret",
        }
        response = await client.post(
            "/api/v1/login/companion-token/exchange",
            json=body,
            headers=_headers(ip),
        )
        assert response.status_code == 200, response.text

    blocked_body = {
        "pairing_session_id": "rotating-session-final",
        "companion_credential_secret": "secret",
    }
    blocked = await client.post(
        "/api/v1/login/companion-token/exchange",
        json=blocked_body,
        headers=_headers(ip),
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


# ---------------------------------------------------------------------------
# /login/companion-local-token  (GET and POST share a bucket)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_companion_local_token_is_rate_limited(client: AsyncClient) -> None:
    ip = _ip()
    limit = login.COMPANION_LOCAL_TOKEN_RATE_LIMIT

    for index in range(limit):
        if index % 2 == 0:
            response = await client.get(
                "/api/v1/login/companion-local-token",
                params={"actions": "status:read"},
                headers=_headers(ip),
            )
        else:
            response = await client.post(
                "/api/v1/login/companion-local-token",
                json={"actions": ["status:read"]},
                headers=_headers(ip),
            )
        assert response.status_code == 200, response.text

    blocked = await client.post(
        "/api/v1/login/companion-local-token",
        json={"actions": ["status:read"]},
        headers=_headers(ip),
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


# ---------------------------------------------------------------------------
# /login/companion-events  (SSE connection setup)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_companion_events_connect_is_rate_limited(client: AsyncClient) -> None:
    ip = _ip()
    limit = login.COMPANION_EVENTS_CONNECT_RATE_LIMIT

    for _ in range(limit):
        response = await client.get(
            "/api/v1/login/companion-events",
            headers=_headers(ip),
        )
        assert response.status_code == 200, response.text

    blocked = await client.get(
        "/api/v1/login/companion-events",
        headers=_headers(ip),
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
