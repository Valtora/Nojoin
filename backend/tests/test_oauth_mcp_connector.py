"""Tests for the MCP connector's OAuth 2.1 authorization server and the
bearer-token middleware guarding the /mcp mount."""

from __future__ import annotations

import hashlib
from base64 import urlsafe_b64encode

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.services import oauth_service
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.oauth import well_known_router
from backend.core import security
from backend.mcp_server import auth as mcp_auth
from backend.mcp_server.auth import MCPAuthMiddleware, current_mcp_user
from backend.models.invitation import (
    Invitation,  # noqa: F401 - register relationship target
)
from backend.models.user import User

TEST_ORIGIN = "https://nojoin.example.com"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        username VARCHAR(255) NOT NULL,
        hashed_password VARCHAR(255) NOT NULL DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT 1,
        is_superuser BOOLEAN NOT NULL DEFAULT 0,
        force_password_change BOOLEAN NOT NULL DEFAULT 0,
        role VARCHAR(32) NOT NULL DEFAULT 'user',
        token_version INTEGER NOT NULL DEFAULT 0,
        settings JSON,
        has_seen_demo_recording BOOLEAN NOT NULL DEFAULT 0,
        invitation_id INTEGER
    )
    """,
    """
    CREATE TABLE revoked_jwts (
        jti VARCHAR(64) PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_type VARCHAR(32) NOT NULL,
        expires_at DATETIME NOT NULL,
        revoked_at DATETIME NOT NULL,
        reason VARCHAR(64)
    )
    """,
    """
    CREATE TABLE oauth_clients (
        client_id VARCHAR(64) PRIMARY KEY,
        client_name VARCHAR(256),
        redirect_uris TEXT NOT NULL,
        token_endpoint_auth_method VARCHAR(32) NOT NULL DEFAULT 'none',
        created_at DATETIME NOT NULL,
        last_used_at DATETIME
    )
    """,
    """
    CREATE TABLE oauth_authorization_codes (
        code_hash VARCHAR(64) PRIMARY KEY,
        client_id VARCHAR(64) NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        redirect_uri TEXT NOT NULL,
        scope VARCHAR(256) NOT NULL,
        code_challenge VARCHAR(128) NOT NULL,
        code_challenge_method VARCHAR(16) NOT NULL DEFAULT 'S256',
        resource TEXT,
        expires_at DATETIME NOT NULL,
        used_at DATETIME,
        created_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE oauth_refresh_tokens (
        token_hash VARCHAR(64) PRIMARY KEY,
        grant_id VARCHAR(64) NOT NULL,
        client_id VARCHAR(64) NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        scope VARCHAR(256) NOT NULL,
        resource TEXT,
        expires_at DATETIME NOT NULL,
        created_at DATETIME NOT NULL,
        last_used_at DATETIME,
        revoked_at DATETIME
    )
    """,
]


def make_pkce_pair() -> tuple[str, str]:
    verifier = "v" * 43
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def isolated_keyring(monkeypatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    class _StubPathManager:
        user_data_directory = tmp_path

    monkeypatch.setattr(security, "path_manager", _StubPathManager())
    yield tmp_path


@pytest.fixture
def fixed_origin(monkeypatch):
    monkeypatch.setattr(oauth_service, "get_trusted_web_origin", lambda: TEST_ORIGIN)


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    async with engine.begin() as conn:
        for stmt in SCHEMA_STATEMENTS:
            await conn.execute(text(stmt))
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def test_user(session_maker) -> User:
    async with session_maker() as session:
        user = User(
            username="alice",
            hashed_password="hashed",
            role="user",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.fixture
def api_app(session_maker, test_user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(well_known_router)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: test_user
    return app


@pytest.fixture
async def client(api_app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=api_app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
        yield async_client


async def register_claude_client(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/oauth/register",
        json={
            "client_name": "Claude",
            "redirect_uris": [CLAUDE_CALLBACK],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["token_endpoint_auth_method"] == "none"
    return payload["client_id"]


async def obtain_code(  # noqa: PLR0913 - one keyword per consent-form field
    client: AsyncClient,
    client_id: str,
    challenge: str,
    state: str = "xyz",
    scope: str | None = None,
) -> str:
    response = await client.post(
        "/api/v1/oauth/authorize/decision",
        json={
            "approve": True,
            "client_id": client_id,
            "redirect_uri": CLAUDE_CALLBACK,
            "response_type": "code",
            "scope": scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 200, response.text
    redirect_to = response.json()["redirect_to"]
    assert redirect_to.startswith(f"{CLAUDE_CALLBACK}?")
    assert f"state={state}" in redirect_to
    query = redirect_to.split("?", 1)[1]
    params = dict(pair.split("=", 1) for pair in query.split("&"))
    return params["code"]


@pytest.mark.anyio
async def test_connector_disabled_hides_all_oauth_surface(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setenv("MCP_ENABLED", "false")
    for path in (
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
        "/api/v1/oauth/grants",
    ):
        assert (await client.get(path)).status_code == 404, path
    register = await client.post(
        "/api/v1/oauth/register", json={"redirect_uris": [CLAUDE_CALLBACK]}
    )
    assert register.status_code == 404


@pytest.mark.anyio
async def test_discovery_documents(client: AsyncClient, fixed_origin):
    resource = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert resource.status_code == 200
    body = resource.json()
    assert body["resource"] == f"{TEST_ORIGIN}/mcp"
    assert body["authorization_servers"] == [TEST_ORIGIN]
    assert body["scopes_supported"] == ["mcp:read", "mcp:write"]

    server = await client.get("/.well-known/oauth-authorization-server")
    assert server.status_code == 200
    metadata = server.json()
    assert metadata["issuer"] == TEST_ORIGIN
    assert metadata["authorization_endpoint"] == f"{TEST_ORIGIN}/oauth/authorize"
    assert metadata["token_endpoint"] == f"{TEST_ORIGIN}/api/v1/oauth/token"
    assert metadata["registration_endpoint"] == f"{TEST_ORIGIN}/api/v1/oauth/register"
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]


@pytest.mark.anyio
async def test_registration_rejects_non_https_redirect(client: AsyncClient):
    response = await client.post(
        "/api/v1/oauth/register",
        json={"redirect_uris": ["http://attacker.example.com/callback"]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


@pytest.mark.anyio
async def test_registration_rejects_confidential_clients(client: AsyncClient):
    response = await client.post(
        "/api/v1/oauth/register",
        json={
            "redirect_uris": [CLAUDE_CALLBACK],
            "token_endpoint_auth_method": "client_secret_basic",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


@pytest.mark.anyio
async def test_authorize_info_validates_request(client: AsyncClient):
    client_id = await register_claude_client(client)
    _, challenge = make_pkce_pair()

    ok = await client.get(
        "/api/v1/oauth/authorize/info",
        params={
            "client_id": client_id,
            "redirect_uri": CLAUDE_CALLBACK,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["client_name"] == "Claude"
    # Scope-less requests (what MCP clients send) get the full default grant.
    assert ok.json()["scope"] == "mcp:read mcp:write"
    assert ok.json()["scope_items"] == ["mcp:read", "mcp:write"]

    bad_redirect = await client.get(
        "/api/v1/oauth/authorize/info",
        params={
            "client_id": client_id,
            "redirect_uri": "https://evil.example.com/cb",
            "code_challenge": challenge,
        },
    )
    assert bad_redirect.status_code == 400
    assert bad_redirect.json()["error"] == "invalid_request"

    missing_pkce = await client.get(
        "/api/v1/oauth/authorize/info",
        params={"client_id": client_id, "redirect_uri": CLAUDE_CALLBACK},
    )
    assert missing_pkce.status_code == 400


@pytest.mark.anyio
async def test_full_authorization_code_flow(
    client: AsyncClient, fixed_origin, isolated_keyring, test_user: User
):
    client_id = await register_claude_client(client)
    verifier, challenge = make_pkce_pair()
    code = await obtain_code(client, client_id, challenge)

    token_response = await client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": CLAUDE_CALLBACK,
            "code_verifier": verifier,
        },
    )
    assert token_response.status_code == 200, token_response.text
    tokens = token_response.json()
    assert tokens["token_type"] == "Bearer"
    assert tokens["scope"] == "mcp:read mcp:write"
    assert tokens["refresh_token"]

    payload = security.decode_access_token(tokens["access_token"])
    assert payload["token_type"] == security.MCP_TOKEN_TYPE
    assert payload["scopes"] == [security.MCP_READ_SCOPE, security.MCP_WRITE_SCOPE]
    assert payload["sub"] == test_user.username
    assert payload["res"] == f"{TEST_ORIGIN}/mcp"
    assert payload["tv"] == test_user.token_version
    # The access token names its consent grant so /mcp requests can stamp
    # the grant's last-used time for the Connected Apps view.
    assert payload["grant_id"]

    # Codes are single-use.
    replay = await client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": CLAUDE_CALLBACK,
            "code_verifier": verifier,
        },
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


@pytest.mark.anyio
async def test_unknown_scopes_are_rejected(
    client: AsyncClient, fixed_origin, isolated_keyring, test_user: User
):
    """Any scope outside the supported set is refused outright, including
    the never-released mcp:destroy: a client whose stored grant names one
    must be removed and re-added, not silently narrowed."""
    client_id = await register_claude_client(client)
    _, challenge = make_pkce_pair()
    response = await client.post(
        "/api/v1/oauth/authorize/decision",
        json={
            "approve": True,
            "client_id": client_id,
            "redirect_uri": CLAUDE_CALLBACK,
            "response_type": "code",
            "scope": "mcp:destroy mcp:read mcp:write",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"


@pytest.mark.anyio
async def test_pkce_mismatch_rejected(
    client: AsyncClient, fixed_origin, isolated_keyring
):
    client_id = await register_claude_client(client)
    _, challenge = make_pkce_pair()
    code = await obtain_code(client, client_id, challenge)

    response = await client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": CLAUDE_CALLBACK,
            "code_verifier": "w" * 43,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


@pytest.mark.anyio
async def test_refresh_rotation_and_reuse_revocation(
    client: AsyncClient, fixed_origin, isolated_keyring
):
    client_id = await register_claude_client(client)
    verifier, challenge = make_pkce_pair()
    code = await obtain_code(client, client_id, challenge)
    first = (
        await client.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": CLAUDE_CALLBACK,
                "code_verifier": verifier,
            },
        )
    ).json()

    refreshed = await client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": first["refresh_token"],
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    second = refreshed.json()
    assert second["refresh_token"] != first["refresh_token"]

    # Re-using the rotated (now revoked) token revokes the whole family.
    reuse = await client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": first["refresh_token"],
        },
    )
    assert reuse.status_code == 400
    assert reuse.json()["error"] == "invalid_grant"

    family_member = await client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": second["refresh_token"],
        },
    )
    assert family_member.status_code == 400


@pytest.mark.anyio
async def test_grants_listing_and_revocation(
    client: AsyncClient, fixed_origin, isolated_keyring
):
    client_id = await register_claude_client(client)
    verifier, challenge = make_pkce_pair()
    code = await obtain_code(client, client_id, challenge)
    tokens = (
        await client.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": CLAUDE_CALLBACK,
                "code_verifier": verifier,
            },
        )
    ).json()

    grants = (await client.get("/api/v1/oauth/grants")).json()
    assert len(grants) == 1
    assert grants[0]["client_name"] == "Claude"
    assert grants[0]["scope"] == "mcp:read mcp:write"

    revoke = await client.delete(f"/api/v1/oauth/grants/{grants[0]['grant_id']}")
    assert revoke.status_code == 204

    assert (await client.get("/api/v1/oauth/grants")).json() == []

    refused = await client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert refused.status_code == 400


@pytest.mark.anyio
async def test_denied_consent_redirects_with_error(client: AsyncClient):
    client_id = await register_claude_client(client)
    _, challenge = make_pkce_pair()

    response = await client.post(
        "/api/v1/oauth/authorize/decision",
        json={
            "approve": False,
            "client_id": client_id,
            "redirect_uri": CLAUDE_CALLBACK,
            "response_type": "code",
            "state": "abc",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 200
    redirect_to = response.json()["redirect_to"]
    assert "error=access_denied" in redirect_to
    assert "state=abc" in redirect_to
    assert "code=" not in redirect_to


def _build_mcp_test_app(inner_seen: dict):
    async def inner_app(scope, receive, send):
        inner_seen["user"] = current_mcp_user.get()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    return MCPAuthMiddleware(inner_app)


@pytest.mark.anyio
async def test_mcp_auth_rejects_missing_token(fixed_origin):
    seen: dict = {}
    transport = ASGITransport(app=_build_mcp_test_app(seen))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.post("/", json={})
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert (
        f'resource_metadata="{TEST_ORIGIN}/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )
    assert "user" not in seen


@pytest.mark.anyio
async def test_mcp_auth_accepts_valid_token(
    monkeypatch, fixed_origin, isolated_keyring, session_maker, test_user: User
):
    import backend.core.db as core_db

    monkeypatch.setattr(core_db, "async_session_maker", session_maker)

    token = security.create_access_token(
        test_user.username,
        token_type=security.MCP_TOKEN_TYPE,
        scopes=[security.MCP_READ_SCOPE],
        token_version=test_user.token_version,
        extra_claims={"client_id": "abc", "res": f"{TEST_ORIGIN}/mcp"},
    )

    seen: dict = {}
    transport = ASGITransport(app=_build_mcp_test_app(seen))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.post(
            "/", json={}, headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    assert seen["user"].username == test_user.username


@pytest.mark.anyio
async def test_mcp_auth_rejects_session_tokens_and_wrong_resource(
    monkeypatch, fixed_origin, isolated_keyring, session_maker, test_user: User
):
    import backend.core.db as core_db

    monkeypatch.setattr(core_db, "async_session_maker", session_maker)

    session_token = security.create_access_token(
        test_user.username,
        token_type=security.SESSION_TOKEN_TYPE,
        scopes=[security.WEB_SESSION_SCOPE],
        token_version=test_user.token_version,
    )
    wrong_resource_token = security.create_access_token(
        test_user.username,
        token_type=security.MCP_TOKEN_TYPE,
        scopes=[security.MCP_READ_SCOPE],
        token_version=test_user.token_version,
        extra_claims={"client_id": "abc", "res": "https://other.example.com/mcp"},
    )

    transport = ASGITransport(app=_build_mcp_test_app({}))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        for bad_token in (session_token, wrong_resource_token):
            response = await c.post(
                "/", json={}, headers={"Authorization": f"Bearer {bad_token}"}
            )
            assert response.status_code == 401


@pytest.mark.anyio
async def test_mcp_token_dies_on_token_version_bump(
    monkeypatch, fixed_origin, isolated_keyring, session_maker, test_user: User
):
    import backend.core.db as core_db

    monkeypatch.setattr(core_db, "async_session_maker", session_maker)

    token = security.create_access_token(
        test_user.username,
        token_type=security.MCP_TOKEN_TYPE,
        scopes=[security.MCP_READ_SCOPE],
        token_version=test_user.token_version,
        extra_claims={"client_id": "abc", "res": f"{TEST_ORIGIN}/mcp"},
    )

    async with session_maker() as session:
        db_user = await session.get(User, test_user.id)
        db_user.token_version += 1
        session.add(db_user)
        await session.commit()

    transport = ASGITransport(app=_build_mcp_test_app({}))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.post(
            "/", json={}, headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_authenticated_requests_stamp_grant_last_used(
    monkeypatch, fixed_origin, isolated_keyring, session_maker, test_user: User
):
    """An authenticated /mcp request stamps the grant's active refresh row.

    Rotation only ever writes last_used_at onto rows it simultaneously
    revokes, which the Connected Apps query excludes, so without this stamp
    the view shows "Never" forever. The stamp is throttled, and tokens
    issued before the grant_id claim existed skip it without failing.
    """
    from datetime import timedelta

    import backend.core.db as core_db
    from backend.models.oauth import OAuthClient, OAuthRefreshToken
    from backend.utils.time import utc_now

    monkeypatch.setattr(core_db, "async_session_maker", session_maker)

    async with session_maker() as session:
        session.add(
            OAuthClient(client_id="abc", client_name="Codex", redirect_uris="[]")
        )
        session.add(
            OAuthRefreshToken(
                token_hash="h1",
                grant_id="grant-1",
                client_id="abc",
                user_id=test_user.id,
                scope="mcp:read mcp:write",
                resource=f"{TEST_ORIGIN}/mcp",
                expires_at=utc_now() + timedelta(days=1),
            )
        )
        await session.commit()

    def make_token(extra_claims: dict) -> str:
        return security.create_access_token(
            test_user.username,
            token_type=security.MCP_TOKEN_TYPE,
            scopes=[security.MCP_READ_SCOPE],
            token_version=test_user.token_version,
            extra_claims={"client_id": "abc", "res": f"{TEST_ORIGIN}/mcp"}
            | extra_claims,
        )

    token = make_token({"grant_id": "grant-1"})
    legacy_token = make_token({})

    async def read_stamp():
        async with session_maker() as session:
            row = await session.get(OAuthRefreshToken, "h1")
            return row.last_used_at

    transport = ASGITransport(app=_build_mcp_test_app({}))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        first = await c.post("/", json={}, headers={"Authorization": f"Bearer {token}"})
        assert first.status_code == 200
        stamped = await read_stamp()
        assert stamped is not None

        # Within the throttle window a second request leaves the stamp alone.
        second = await c.post(
            "/", json={}, headers={"Authorization": f"Bearer {token}"}
        )
        assert second.status_code == 200
        assert await read_stamp() == stamped

        # Outside the window (throttle forced to zero) the stamp advances.
        monkeypatch.setattr(oauth_service, "GRANT_LAST_USED_THROTTLE_SECONDS", 0)
        third = await c.post("/", json={}, headers={"Authorization": f"Bearer {token}"})
        assert third.status_code == 200
        assert await read_stamp() >= stamped

        # Tokens issued before the claim existed still authenticate.
        legacy = await c.post(
            "/", json={}, headers={"Authorization": f"Bearer {legacy_token}"}
        )
        assert legacy.status_code == 200

    # The Connected Apps listing now surfaces the usage.
    async with session_maker() as session:
        grants = await oauth_service.list_active_grants(session, user_id=test_user.id)
    assert [g["grant_id"] for g in grants] == ["grant-1"]
    assert grants[0]["last_used_at"] is not None


@pytest.fixture
def anonymous_rate_limit_fallback(monkeypatch):
    """Route anonymous rate limiting to a clean in-memory window.

    Stops the limiter probing Redis (absent in tests) and isolates each
    test from windows consumed by earlier anonymous requests in the same
    process — the fallback store is module-global.
    """
    from backend.utils import rate_limit as rate_limit_utils

    async def _no_redis():
        return None

    monkeypatch.setattr(rate_limit_utils, "_get_redis", _no_redis)
    rate_limit_utils._fallback_windows.clear()
    yield
    rate_limit_utils._fallback_windows.clear()


def _jsonrpc(method: str, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}}


@pytest.mark.anyio
async def test_anonymous_allowlist_passes_bootstrap_methods(
    fixed_origin, anonymous_rate_limit_fallback
):
    """The five bootstrap methods reach the app anonymously, as no user."""
    for method in (
        "initialize",
        "notifications/initialized",
        "ping",
        "tools/list",
        "tools/call",
    ):
        seen: dict = {}
        transport = ASGITransport(app=_build_mcp_test_app(seen))
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.post("/", json=_jsonrpc(method))
        assert response.status_code == 200, method
        assert "user" in seen and seen["user"] is None, method


@pytest.mark.anyio
async def test_anonymous_unclassifiable_requests_stay_401(fixed_origin):
    """Anything the allowlist cannot positively classify keeps the strict 401,
    and every challenge now carries the scope hint."""
    transport = ASGITransport(app=_build_mcp_test_app({}))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        rejected = [
            await c.post("/", json=_jsonrpc("resources/list")),
            await c.post("/", json=_jsonrpc("tools/call/extra")),
            await c.post("/", json={}),
            await c.post(
                "/", content=b"{not json", headers={"Content-Type": "application/json"}
            ),
            await c.post("/", json=[_jsonrpc("ping")]),
            await c.post(
                "/", content=b"x" * (mcp_auth._ANONYMOUS_BODY_LIMIT_BYTES + 1)
            ),
            await c.get("/"),
            await c.post(
                "/", json=_jsonrpc("ping"), headers={"Authorization": "Bearer garbage"}
            ),
        ]
    for response in rejected:
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert (
            f'resource_metadata="{TEST_ORIGIN}/.well-known/oauth-protected-resource/mcp"'
            in challenge
        )
        assert 'scope="mcp:read mcp:write"' in challenge


@pytest.mark.anyio
async def test_anonymous_requests_are_rate_limited(
    monkeypatch, fixed_origin, anonymous_rate_limit_fallback
):
    monkeypatch.setattr(mcp_auth, "_ANONYMOUS_RATE_LIMIT", 2)
    transport = ASGITransport(app=_build_mcp_test_app({}))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        first = await c.post("/", json=_jsonrpc("ping"))
        second = await c.post("/", json=_jsonrpc("ping"))
        third = await c.post("/", json=_jsonrpc("ping"))
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "retry-after" in third.headers
    assert "www-authenticate" in third.headers


@pytest.mark.anyio
async def test_anonymous_discovery_disabled_restores_strict_401(
    monkeypatch, fixed_origin
):
    """Flag off: today's exact pre-feature behaviour, scope hint included."""
    monkeypatch.setenv("MCP_ANONYMOUS_DISCOVERY", "false")
    transport = ASGITransport(app=_build_mcp_test_app({}))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.post("/", json=_jsonrpc("initialize"))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        f'Bearer resource_metadata="{TEST_ORIGIN}'
        '/.well-known/oauth-protected-resource/mcp"'
    )


def test_tool_scope_declarations_match_write_guards():
    """The scope a tool declares to mcp_tool() must match its body's guard.

    The declaration feeds securitySchemes and anonymous challenges; the
    _require_write_scope call in the body is what actually enforces the
    write scope. This sweep runs over every registered tool dynamically, so
    a future tool that declares one and forgets the other fails here.
    """
    import inspect

    from backend.mcp_server.server import _TOOL_SCOPES, mcp

    tools = mcp._tool_manager.list_tools()
    assert tools, "tool registry is empty - registration imports moved?"
    for tool in tools:
        source = inspect.getsource(inspect.unwrap(tool.fn))
        declares_write = _TOOL_SCOPES[tool.name] == security.MCP_WRITE_SCOPE
        guards_write = "_require_write_scope(" in source
        assert declares_write == guards_write, (
            f"{tool.name}: declared scope {_TOOL_SCOPES[tool.name]!r} does not "
            "match its _require_write_scope guard"
        )


def _assert_anonymous_discovery_results(
    anon_init, anon_list, anon_calls: dict, anon_disabled, tool_names: set
) -> None:
    """Assertions for the anonymous phases of the end-to-end test."""
    from backend.mcp_server.server import _TOOL_SCOPES

    # Anonymous bootstrap: the handshake and listing succeed with no token,
    # and the listing names each tool's true scope.
    assert anon_init.status_code == 200, anon_init.text
    assert anon_init.json()["result"]["serverInfo"]["name"] == "Nojoin"

    assert anon_list.status_code == 200, anon_list.text
    anon_tools = anon_list.json()["result"]["tools"]
    assert {tool["name"] for tool in anon_tools} == tool_names
    for tool in anon_tools:
        declared_scope = _TOOL_SCOPES[tool["name"]]
        assert tool["securitySchemes"] == [
            {"type": "oauth2", "scopes": [declared_scope]}
        ], tool["name"]

    # Every anonymous call was answered by the in-band challenge naming that
    # tool's scope - never by the tool itself.
    assert set(anon_calls) == tool_names
    for name, call_result in anon_calls.items():
        assert call_result["isError"] is True, name
        challenge = call_result["_meta"]["mcp/www_authenticate"][0]
        assert (
            f'resource_metadata="{TEST_ORIGIN}'
            '/.well-known/oauth-protected-resource/mcp"' in challenge
        ), name
        assert 'error="invalid_token"' in challenge, name
        assert f'scope="{_TOOL_SCOPES[name]}"' in challenge, name
        challenge_text = " ".join(
            block["text"] for block in call_result["content"] if block["type"] == "text"
        )
        assert "Authentication required" in challenge_text, name

    # With the compatibility flag off, the same anonymous request gets the
    # strict transport-level 401 again.
    assert anon_disabled.status_code == 401


@pytest.mark.anyio
async def test_mcp_protocol_tools_list_end_to_end(  # noqa: PLR0913 - one fixture per protocol concern
    monkeypatch,
    fixed_origin,
    isolated_keyring,
    session_maker,
    test_user: User,
    anonymous_rate_limit_fallback,
):
    """Full stack: auth middleware -> MCP SDK streamable HTTP -> tools/list.

    Also hosts the anonymous-discovery protocol phases: the SDK allows one
    session-manager .run() per FastMCP instance and therefore one such
    context per test process, so every phase that needs the real MCP app
    shares this test's context. The anonymous sweep below makes one
    tools/call per registered tool, so lift the per-IP anonymous limit out
    of the way.
    """
    monkeypatch.setattr(mcp_auth, "_ANONYMOUS_RATE_LIMIT", 1000)
    import backend.core.db as core_db
    from backend.mcp_server import (
        NormaliseMcpMountPathMiddleware,
        build_mcp_asgi_app,
        mcp_session_manager_context,
    )

    monkeypatch.setattr(core_db, "async_session_maker", session_maker)

    token = security.create_access_token(
        test_user.username,
        token_type=security.MCP_TOKEN_TYPE,
        scopes=[security.MCP_READ_SCOPE],
        token_version=test_user.token_version,
        extra_claims={"client_id": "abc", "res": f"{TEST_ORIGIN}/mcp"},
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    # Mount at /mcp exactly as backend.main.create_app does, and request
    # /mcp WITHOUT a trailing slash — what real MCP clients send. This must
    # be served directly, not answered with a 307 slash-redirect.
    app = FastAPI()
    app.add_middleware(NormaliseMcpMountPathMiddleware)
    app.mount("/mcp", build_mcp_asgi_app())

    async with mcp_session_manager_context():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {},
                },
            )
            # The initialize response must advertise the server icon so MCP
            # clients (e.g. Claude's connector list) can render the Nojoin
            # logo.
            init = await c.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )
            # The bearer token above carries only mcp:read, standing in for
            # a grant issued before the write scope existed.
            import_refusal = await c.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "import_people",
                        "arguments": {"people": [{"name": "Dana"}]},
                    },
                },
            )

            # Anonymous-discovery phases: same requests with no Authorization
            # header at all (Codex Desktop's position before its first OAuth).
            anon_headers = {k: v for k, v in headers.items() if k != "Authorization"}
            anon_init = await c.post(
                "/mcp",
                headers=anon_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "anon", "version": "0"},
                    },
                },
            )
            anon_list = await c.post(
                "/mcp",
                headers=anon_headers,
                json={"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
            )
            # Dynamic sweep: one anonymous call per listed tool, so a future
            # tool cannot ship without the in-band challenge.
            anon_calls: dict[str, dict] = {}
            for request_id, tool in enumerate(
                anon_list.json()["result"]["tools"], start=6
            ):
                call = await c.post(
                    "/mcp",
                    headers=anon_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": tool["name"], "arguments": {}},
                    },
                )
                assert call.status_code == 200, (tool["name"], call.text)
                anon_calls[tool["name"]] = call.json()["result"]

            monkeypatch.setenv("MCP_ANONYMOUS_DISCOVERY", "false")
            anon_disabled = await c.post(
                "/mcp",
                headers=anon_headers,
                json={"jsonrpc": "2.0", "id": 999, "method": "ping"},
            )
            monkeypatch.delenv("MCP_ANONYMOUS_DISCOVERY")

    assert response.status_code == 200, response.text
    body = response.json()
    tool_names = {tool["name"] for tool in body["result"]["tools"]}
    assert tool_names == {
        "list_recordings",
        "get_transcript",
        "get_transcript_utterances",
        "get_meeting_notes",
        "get_meeting_analytics",
        "analyse_meeting",
        "list_tags",
        "get_speakers",
        "list_people",
        "get_documents",
        "get_person",
        "import_people",
        "set_speaker_name",
        "append_meeting_notes",
        "search_context",
        "rename_recording",
        "tag_recording",
        "untag_recording",
        "archive_recording",
        "restore_recording",
        "trash_recording",
        "reprocess_recording",
        "regenerate_notes",
        "attach_document",
        "correct_utterance_text",
        "correct_utterance_speaker",
        "unlock_utterance",
        "list_calendar_events",
        "link_calendar_event",
        "list_tasks",
        "create_task",
        "update_task",
    }

    assert init.status_code == 200, init.text
    server_info = init.json()["result"]["serverInfo"]
    assert server_info["name"] == "Nojoin"
    icons = server_info.get("icons")
    assert icons and icons[0]["src"].endswith("/assets/NojoinLogo.png")

    # A grant issued before mcp:write existed passes the endpoint gate but
    # the write tool refuses it with an instruction to reconnect. This must
    # share the session-manager context above: the SDK allows only one
    # .run() per FastMCP instance and therefore one such context per test
    # process.
    assert import_refusal.status_code == 200, import_refusal.text
    result = import_refusal.json()["result"]
    assert result["isError"] is True
    text_blocks = " ".join(
        block["text"] for block in result["content"] if block["type"] == "text"
    )
    assert "read-only" in text_blocks
    assert "reconnect" in text_blocks.lower()

    _assert_anonymous_discovery_results(
        anon_init, anon_list, anon_calls, anon_disabled, tool_names
    )
