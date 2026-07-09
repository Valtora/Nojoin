from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import cli_oauth
from backend.api.v1.endpoints.cli_oauth import _usage_by_user, _usage_row
from backend.core.encryption import decrypt_secret

# Register every ORM model so the User mapper (reached via the credential FK)
# configures cleanly when the endpoint queries the credential table.
from backend.models import registry  # noqa: F401
from backend.models.cli_oauth import CliOAuthCredential, CliUsageDaily
from backend.services.cli_oauth import codex_oauth, oauth
from backend.services.cli_oauth.persistence import get_credential
from backend.utils.time import utc_now

# SQLite-friendly DDL (INTEGER PRIMARY KEY autoincrements; the model's BigInteger
# PK is sequence-backed only on Postgres).
_CREATE_CLI_OAUTH_CREDENTIALS = """
CREATE TABLE cli_oauth_credentials (
    id INTEGER PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    user_id BIGINT NOT NULL,
    provider VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    access_token_encrypted TEXT,
    refresh_token_encrypted TEXT,
    token_expires_at TIMESTAMP,
    oauth_client_id VARCHAR(512),
    last_refreshed_at TIMESTAMP,
    usage_limited_until TIMESTAMP,
    last_utilization FLOAT,
    last_rate_limit_status VARCHAR(32),
    last_rate_limit_type VARCHAR(32),
    last_rate_limit_at TIMESTAMP,
    CONSTRAINT uq_cli_oauth_credential_user_provider UNIQUE (user_id, provider)
)
"""

_CREATE_CLI_USAGE_DAILY = """
CREATE TABLE cli_usage_daily (
    id INTEGER PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    user_id BIGINT NOT NULL,
    provider VARCHAR(32) NOT NULL,
    usage_date DATE NOT NULL,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    cache_read_input_tokens BIGINT NOT NULL DEFAULT 0,
    cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
    request_count INTEGER NOT NULL DEFAULT 0,
    total_cost_usd FLOAT NOT NULL DEFAULT 0,
    CONSTRAINT uq_cli_usage_daily_user_provider_date UNIQUE (user_id, provider, usage_date)
)
"""


async def _build_app(
    user_id: int = 1, *, role: str = "owner", is_superuser: bool = True
):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(text(_CREATE_CLI_OAUTH_CREDENTIALS))
        await conn.execute(text(_CREATE_CLI_USAGE_DAILY))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    app = FastAPI()
    app.include_router(cli_oauth.router, prefix="/cli-oauth")

    async def override_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=user_id, username="alice", role=role, is_superuser=is_superuser
    )
    return engine, maker, app


async def _seed_usage(maker, *, user_id, usage_date, input_tokens, output_tokens):
    async with maker() as session:
        session.add(
            CliUsageDaily(
                user_id=user_id,
                provider="claude_code",
                usage_date=usage_date,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                request_count=1,
            )
        )
        await session.commit()


class _FakeOAuth:
    """In-memory replacement for the Redis pending store + token exchange."""

    def __init__(self):
        self.pending: dict[int, dict] = {}
        self.exchanged_with: dict | None = None
        self.raise_on_exchange: Exception | None = None

    async def store_pending_pkce(self, user_id, verifier, state):
        self.pending[user_id] = {"verifier": verifier, "state": state}

    async def pop_pending_pkce(self, user_id):
        return self.pending.pop(user_id, None)

    async def exchange_code(self, code, verifier, state):
        if self.raise_on_exchange:
            raise self.raise_on_exchange
        self.exchanged_with = {"code": code, "verifier": verifier, "state": state}
        return oauth.OAuthTokens(
            access_token="sk-ant-oat01-REALACCESS",
            refresh_token="sk-ant-ort01-REALREFRESH",
            expires_in=28800,
            scope="user:inference",
        )


def _patch(fake: _FakeOAuth):
    originals = {
        name: getattr(oauth, name)
        for name in ("store_pending_pkce", "pop_pending_pkce", "exchange_code")
    }
    for name in originals:
        setattr(oauth, name, getattr(fake, name))
    return originals


def _restore(originals):
    for name, fn in originals.items():
        setattr(oauth, name, fn)


def _provider_entry(body: dict, provider: str = "claude_code") -> dict:
    """The per-provider status dict from a /status, /complete or /token body."""
    return next(p for p in body["providers"] if p["provider"] == provider)


class _FakeCodexOAuth:
    """In-memory replacement for the Codex device flow + Redis pending store."""

    def __init__(self):
        self.pending: dict[int, dict] = {}
        # Each poll pops one entry: an Exception to raise, or OAuthTokens.
        self.poll_results: list = []

    async def request_device_code(self):
        return codex_oauth.DeviceCodeGrant(
            device_code="dev-123",
            user_code="ABCD-1234",
            verification_uri="https://auth.openai.com/device",
            verification_uri_complete="https://auth.openai.com/device?code=ABCD-1234",
            expires_in=900,
            interval=5,
        )

    async def store_pending_device(self, user_id, grant):
        self.pending[user_id] = {"device_code": grant.device_code}

    async def get_pending_device(self, user_id):
        return self.pending.get(user_id)

    async def clear_pending_device(self, user_id):
        self.pending.pop(user_id, None)

    async def poll_device_token(self, device_code):
        result = self.poll_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


_CODEX_PATCH_NAMES = (
    "request_device_code",
    "store_pending_device",
    "get_pending_device",
    "clear_pending_device",
    "poll_device_token",
)


def _patch_codex(fake: _FakeCodexOAuth):
    originals = {name: getattr(codex_oauth, name) for name in _CODEX_PATCH_NAMES}
    for name in _CODEX_PATCH_NAMES:
        setattr(codex_oauth, name, getattr(fake, name))
    return originals


def _restore_codex(originals):
    for name, fn in originals.items():
        setattr(codex_oauth, name, fn)


def test_start_returns_authorize_url_and_stashes_pending():
    async def _run():
        fake = _FakeOAuth()
        originals = _patch(fake)
        engine, _maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                r = await ac.post("/cli-oauth/start")
                assert r.status_code == 200, r.text
                url = r.json()["authorize_url"]
                assert url.startswith(oauth.AUTHORIZE_URL)
                assert "code_challenge_method=S256" in url
                assert 1 in fake.pending  # verifier + state stashed for this user
        finally:
            await engine.dispose()
            _restore(originals)

    asyncio.run(_run())


def test_complete_exchanges_and_stores_encrypted():
    async def _run():
        fake = _FakeOAuth()
        originals = _patch(fake)
        engine, maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                await ac.post("/cli-oauth/start")
                r = await ac.post("/cli-oauth/complete", json={"code": "authcode123"})
                assert r.status_code == 200, r.text
                claude = _provider_entry(r.json())
                assert claude["connected"] is True and claude["status"] == "active"
                # Token never echoed anywhere in the response.
                assert "REALACCESS" not in r.text and "access_token" not in r.text

                # Stored encrypted, decrypts to the exchanged access token.
                async with maker() as session:
                    cred = await get_credential(session, 1)
                    assert cred is not None
                    assert cred.access_token_encrypted not in (
                        None,
                        "sk-ant-oat01-REALACCESS",
                    )
                    assert (
                        decrypt_secret(cred.access_token_encrypted)
                        == "sk-ant-oat01-REALACCESS"
                    )
                    assert (
                        decrypt_secret(cred.refresh_token_encrypted)
                        == "sk-ant-ort01-REALREFRESH"
                    )
                    assert cred.token_expires_at is not None
        finally:
            await engine.dispose()
            _restore(originals)

    asyncio.run(_run())


def test_complete_without_pending_is_400():
    async def _run():
        fake = _FakeOAuth()
        originals = _patch(fake)
        engine, _maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                r = await ac.post("/cli-oauth/complete", json={"code": "x"})
                assert r.status_code == 400
        finally:
            await engine.dispose()
            _restore(originals)

    asyncio.run(_run())


def test_complete_state_mismatch_is_400():
    async def _run():
        fake = _FakeOAuth()
        originals = _patch(fake)
        engine, _maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                await ac.post("/cli-oauth/start")
                # A pasted code carrying a different state must be rejected.
                r = await ac.post(
                    "/cli-oauth/complete", json={"code": "authcode#WRONGSTATE"}
                )
                assert r.status_code == 400
        finally:
            await engine.dispose()
            _restore(originals)

    asyncio.run(_run())


def test_complete_exchange_failure_is_400():
    async def _run():
        fake = _FakeOAuth()
        fake.raise_on_exchange = oauth.CliOAuthExchangeError("invalid_grant")
        originals = _patch(fake)
        engine, _maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                await ac.post("/cli-oauth/start")
                r = await ac.post("/cli-oauth/complete", json={"code": "stalecode"})
                assert r.status_code == 400
        finally:
            await engine.dispose()
            _restore(originals)

    asyncio.run(_run())


def test_complete_rejects_codex_provider():
    async def _run():
        engine, _maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                # Codex uses device sign-in, so /complete must reject it.
                r = await ac.post(
                    "/cli-oauth/complete", json={"code": "x", "provider": "codex"}
                )
                assert r.status_code == 400
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_start_codex_returns_device_grant():
    async def _run():
        fake = _FakeCodexOAuth()
        originals = _patch_codex(fake)
        engine, _maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                r = await ac.post("/cli-oauth/start", json={"provider": "codex"})
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["provider"] == "codex" and body["kind"] == "device"
                assert body["user_code"] == "ABCD-1234"
                assert body["verification_uri"].startswith("https://")
                assert fake.pending.get(1) == {"device_code": "dev-123"}
        finally:
            await engine.dispose()
            _restore_codex(originals)

    asyncio.run(_run())


def test_poll_codex_pending_then_connects_and_stores():
    async def _run():
        fake = _FakeCodexOAuth()
        fake.poll_results = [
            codex_oauth.CliOAuthAuthorizationPending("authorization_pending"),
            oauth.OAuthTokens(
                access_token="oai-access-REAL",
                refresh_token="oai-refresh-REAL",
                expires_in=3600,
                scope="openid",
            ),
        ]
        originals = _patch_codex(fake)
        engine, maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                await ac.post("/cli-oauth/start", json={"provider": "codex"})

                first = await ac.post("/cli-oauth/poll", json={"provider": "codex"})
                assert first.json()["status"] == "pending"

                second = await ac.post("/cli-oauth/poll", json={"provider": "codex"})
                assert second.json()["status"] == "connected"

                # Stored encrypted under the codex provider, token never echoed.
                assert "oai-access-REAL" not in second.text
                async with maker() as session:
                    cred = await get_credential(session, 1, "codex")
                    assert cred is not None
                    assert decrypt_secret(cred.access_token_encrypted) == "oai-access-REAL"
                    assert (
                        decrypt_secret(cred.refresh_token_encrypted) == "oai-refresh-REAL"
                    )
                # Pending device state cleared after a successful connect.
                assert fake.pending.get(1) is None
        finally:
            await engine.dispose()
            _restore_codex(originals)

    asyncio.run(_run())


def test_status_and_disconnect():
    async def _run():
        fake = _FakeOAuth()
        originals = _patch(fake)
        engine, _maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                r = await ac.get("/cli-oauth/status")
                assert _provider_entry(r.json())["connected"] is False

                await ac.post("/cli-oauth/start")
                await ac.post("/cli-oauth/complete", json={"code": "c"})
                connected = await ac.get("/cli-oauth/status")
                assert _provider_entry(connected.json())["connected"] is True

                r = await ac.delete("/cli-oauth/token")
                assert r.status_code == 200
                assert _provider_entry(r.json())["connected"] is False
                after = await ac.get("/cli-oauth/status")
                assert _provider_entry(after.json())["connected"] is False
        finally:
            await engine.dispose()
            _restore(originals)

    asyncio.run(_run())


def test_status_reports_and_hides_usage_limit():
    from datetime import timedelta

    from backend.api.v1.endpoints.cli_oauth import _provider_status
    from backend.models.cli_oauth import CliOAuthCredential
    from backend.utils.time import utc_now

    future = utc_now() + timedelta(hours=1)
    limited = CliOAuthCredential(
        user_id=1, provider="claude_code", status="active", usage_limited_until=future
    )
    assert _provider_status("claude_code", limited).usage_limited_until == future

    stale = CliOAuthCredential(
        user_id=1,
        provider="claude_code",
        status="active",
        usage_limited_until=utc_now() - timedelta(minutes=1),
    )
    # Past its reset -> not surfaced, so the UI clears itself.
    assert _provider_status("claude_code", stale).usage_limited_until is None


# --- usage accounting (self-view + admin overview) ---


def test_usage_by_user_windows_sum_input_plus_output():
    async def _run():
        engine, maker, _app = await _build_app()
        try:
            today = utc_now().date()
            await _seed_usage(
                maker, user_id=1, usage_date=today, input_tokens=100, output_tokens=40
            )
            await _seed_usage(
                maker,
                user_id=1,
                usage_date=today - timedelta(days=10),
                input_tokens=10,
                output_tokens=5,
            )
            await _seed_usage(
                maker,
                user_id=1,
                usage_date=today - timedelta(days=40),
                input_tokens=1,
                output_tokens=1,
            )
            async with maker() as db:
                agg = (await _usage_by_user(db))[1]
            assert agg["tokens_total"] == 157  # 140 + 15 + 2 (all windows)
            assert agg["tokens_7d"] == 140  # today only
            assert agg["tokens_30d"] == 155  # today + 10 days ago
            assert agg["requests_total"] == 3
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_usage_row_maps_credential_and_quota_fields():
    future = utc_now() + timedelta(hours=1)
    credential = CliOAuthCredential(
        user_id=1,
        provider="claude_code",
        status="active",
        usage_limited_until=future,
        last_utilization=0.72,
        last_rate_limit_status="allowed_warning",
        last_rate_limit_type="five_hour",
    )
    row = _usage_row(
        SimpleNamespace(id=1, username="alice"),
        {
            "tokens_total": 500,
            "tokens_7d": 120,
            "tokens_30d": 300,
            "requests_total": 4,
        },
        credential,
    )
    assert row.connected is True
    assert row.tokens_total == 500 and row.tokens_7d == 120
    assert row.utilization == 0.72
    assert row.rate_limit_status == "allowed_warning"
    assert row.usage_limited_until == future

    # No credential -> not connected and quota fields empty.
    bare = _usage_row(SimpleNamespace(id=2, username="bob"), None, None)
    assert bare.connected is False
    assert bare.tokens_total == 0
    assert bare.utilization is None
    assert bare.usage_limited_until is None


def test_status_includes_self_usage():
    async def _run():
        engine, maker, app = await _build_app()
        try:
            await _seed_usage(
                maker,
                user_id=1,
                usage_date=utc_now().date(),
                input_tokens=90,
                output_tokens=10,
            )
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                body = (await ac.get("/cli-oauth/status")).json()
                assert body["tokens_7d"] == 100
                assert body["tokens_total"] == 100
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_admin_usage_forbidden_for_non_admin():
    async def _run():
        engine, _maker, app = await _build_app(role="user", is_superuser=False)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                r = await ac.get("/cli-oauth/admin/usage")
                assert r.status_code == 403
        finally:
            await engine.dispose()

    asyncio.run(_run())
