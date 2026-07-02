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


async def obtain_code(
    client: AsyncClient, client_id: str, challenge: str, state: str = "xyz"
) -> str:
    response = await client.post(
        "/api/v1/oauth/authorize/decision",
        json={
            "approve": True,
            "client_id": client_id,
            "redirect_uri": CLAUDE_CALLBACK,
            "response_type": "code",
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
    assert body["scopes_supported"] == ["mcp:read"]

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
    assert ok.json()["scope"] == "mcp:read"

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
    assert tokens["scope"] == "mcp:read"
    assert tokens["refresh_token"]

    payload = security.decode_access_token(tokens["access_token"])
    assert payload["token_type"] == security.MCP_TOKEN_TYPE
    assert payload["scopes"] == [security.MCP_READ_SCOPE]
    assert payload["sub"] == test_user.username
    assert payload["res"] == f"{TEST_ORIGIN}/mcp"
    assert payload["tv"] == test_user.token_version

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
    assert grants[0]["scope"] == "mcp:read"

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
async def test_mcp_protocol_tools_list_end_to_end(
    monkeypatch, fixed_origin, isolated_keyring, session_maker, test_user: User
):
    """Full stack: auth middleware -> MCP SDK streamable HTTP -> tools/list."""
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

    assert response.status_code == 200, response.text
    body = response.json()
    tool_names = {tool["name"] for tool in body["result"]["tools"]}
    assert tool_names == {
        "list_recordings",
        "get_transcript",
        "get_meeting_notes",
        "list_tags",
    }

    assert init.status_code == 200, init.text
    server_info = init.json()["result"]["serverInfo"]
    assert server_info["name"] == "Nojoin"
    icons = server_info.get("icons")
    assert icons and icons[0]["src"].endswith("/assets/NojoinLogo.png")
