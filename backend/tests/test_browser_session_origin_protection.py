from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from backend.api import deps
from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import calendar, login
from backend.main import create_app

TRUSTED_ORIGIN = "https://nojoin.example.com"
SECURE_TEST_BASE_URL = TRUSTED_ORIGIN


def set_session_cookie(client: AsyncClient) -> None:
    client.cookies.set(
        "access_token",
        "session-token",
        domain="nojoin.example.com",
        path="/",
    )


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


@pytest.fixture
def app(monkeypatch, fake_user):
    monkeypatch.setenv("WEB_APP_URL", TRUSTED_ORIGIN)

    async def override_get_db():
        yield SimpleNamespace()

    async def fake_allow_rate_limit(*args, **kwargs):
        return None

    async def fake_get_authenticated_user_from_token(*args, **kwargs):
        return fake_user

    async def fake_authenticate_user_credentials(*args, **kwargs):
        return fake_user

    async def fake_handle_calendar_callback(*args, **kwargs):
        return None

    monkeypatch.setattr(deps, "get_authenticated_user_from_token", fake_get_authenticated_user_from_token)
    monkeypatch.setattr(login, "enforce_rate_limit", fake_allow_rate_limit)
    monkeypatch.setattr(login, "_authenticate_user_credentials", fake_authenticate_user_credentials)
    monkeypatch.setattr(calendar, "enforce_rate_limit", fake_allow_rate_limit)
    monkeypatch.setattr(calendar, "handle_callback", fake_handle_calendar_callback)

    app = create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = override_get_db

    @app.post("/api/v1/test/write")
    async def protected_write(current_user=Depends(get_current_user)):
        return {"username": current_user.username}

    @app.get("/api/v1/test/read")
    async def protected_read(current_user=Depends(get_current_user)):
        return {"username": current_user.username}

    return app


@pytest.fixture
async def client(app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url=SECURE_TEST_BASE_URL,
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_cookie_authenticated_write_allows_trusted_origin(client: AsyncClient) -> None:
    set_session_cookie(client)
    response = await client.post(
        "/api/v1/test/write",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert response.status_code == 200
    assert response.json() == {"username": "alice"}


@pytest.mark.anyio
async def test_cookie_authenticated_write_allows_trusted_referer(client: AsyncClient) -> None:
    set_session_cookie(client)
    response = await client.post(
        "/api/v1/test/write",
        headers={"Referer": f"{TRUSTED_ORIGIN}/settings?tab=account"},
    )

    assert response.status_code == 200
    assert response.json() == {"username": "alice"}


@pytest.mark.anyio
async def test_cookie_authenticated_write_rejects_untrusted_origin(client: AsyncClient) -> None:
    set_session_cookie(client)
    response = await client.post(
        "/api/v1/test/write",
        headers={"Origin": "https://evil.example.com"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == deps.BROWSER_SESSION_TRUST_ERROR_DETAIL


@pytest.mark.anyio
async def test_cookie_authenticated_write_rejects_missing_origin_and_referer(client: AsyncClient) -> None:
    set_session_cookie(client)
    response = await client.post(
        "/api/v1/test/write",
    )

    assert response.status_code == 403
    assert response.json()["detail"] == deps.BROWSER_SESSION_TRUST_ERROR_DETAIL


@pytest.mark.anyio
async def test_bearer_authenticated_write_is_not_subject_to_browser_origin_check(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/test/write",
        headers={
            "Authorization": "Bearer api-token",
            "Origin": "https://evil.example.com",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"username": "alice"}


@pytest.mark.anyio
async def test_cookie_authenticated_safe_get_does_not_require_origin_headers(client: AsyncClient) -> None:
    set_session_cookie(client)
    response = await client.get(
        "/api/v1/test/read",
    )

    assert response.status_code == 200
    assert response.json() == {"username": "alice"}


@pytest.mark.anyio
async def test_session_login_rejects_untrusted_origin(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/login/session",
        data={"username": "alice", "password": "password123"},
        headers={"Origin": "https://evil.example.com"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == deps.BROWSER_SESSION_TRUST_ERROR_DETAIL


@pytest.mark.anyio
async def test_session_login_allows_trusted_origin(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/login/session",
        data={"username": "alice", "password": "password123"},
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert response.status_code == 200
    assert "access_token=" in response.headers["set-cookie"]


@pytest.mark.anyio
async def test_logout_rejects_untrusted_origin(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/login/logout",
        headers={"Origin": "https://evil.example.com"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == deps.BROWSER_SESSION_TRUST_ERROR_DETAIL


@pytest.mark.anyio
async def test_logout_allows_trusted_origin(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/login/logout",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully"}


@pytest.mark.anyio
async def test_calendar_oauth_callback_still_allows_cookie_authenticated_get_without_origin(
    client: AsyncClient,
) -> None:
    set_session_cookie(client)
    response = await client.get(
        "/api/v1/calendar/oauth/google/callback?code=oauth-code&state=opaque-state",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "calendar=success" in response.headers["location"]