from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import cli_oauth
from backend.core.encryption import decrypt_secret

# Register every ORM model so the User mapper (reached via the credential FK)
# configures cleanly when the endpoint queries the credential table.
from backend.models import registry  # noqa: F401
from backend.services.cli_oauth import oauth
from backend.services.cli_oauth.persistence import get_credential

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
    CONSTRAINT uq_cli_oauth_credential_user_provider UNIQUE (user_id, provider)
)
"""


async def _build_app(user_id: int = 1):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(text(_CREATE_CLI_OAUTH_CREDENTIALS))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    app = FastAPI()
    app.include_router(cli_oauth.router, prefix="/cli-oauth")

    async def override_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=user_id, username="alice"
    )
    return engine, maker, app


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
                body = r.json()
                assert body["connected"] is True and body["status"] == "active"
                # Token never echoed.
                assert "access_token" not in body and "token" not in body

                # Stored encrypted, decrypts to the exchanged access token.
                async with maker() as session:
                    cred = await get_credential(session, 1)
                    assert cred is not None
                    assert cred.access_token_encrypted not in (None, "sk-ant-oat01-REALACCESS")
                    assert decrypt_secret(cred.access_token_encrypted) == "sk-ant-oat01-REALACCESS"
                    assert decrypt_secret(cred.refresh_token_encrypted) == "sk-ant-ort01-REALREFRESH"
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
                assert r.json()["connected"] is False

                await ac.post("/cli-oauth/start")
                await ac.post("/cli-oauth/complete", json={"code": "c"})
                assert (await ac.get("/cli-oauth/status")).json()["connected"] is True

                r = await ac.delete("/cli-oauth/token")
                assert r.status_code == 200 and r.json()["connected"] is False
                assert (await ac.get("/cli-oauth/status")).json()["connected"] is False
        finally:
            await engine.dispose()
            _restore(originals)

    asyncio.run(_run())
