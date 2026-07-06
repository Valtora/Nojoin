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
from backend.services.cli_oauth.persistence import get_credential

_TOKEN = "sk-ant-oat01-" + "x" * 40

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


def test_connect_status_disconnect_roundtrip():
    async def _run():
        engine, maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                # Initially not connected.
                r = await ac.get("/cli-oauth/status")
                assert r.status_code == 200
                body = r.json()
                assert body["connected"] is False
                assert body["status"] == "not_connected"
                assert "token" not in body

                # Connect: stores the token, reports active. Token never echoed.
                r = await ac.put("/cli-oauth/token", json={"token": _TOKEN})
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["connected"] is True
                assert body["status"] == "active"
                assert "token" not in body and "access_token" not in body
                assert _TOKEN not in r.text

                # Status reflects the connection.
                r = await ac.get("/cli-oauth/status")
                assert r.json()["connected"] is True

                # Stored encrypted at rest, decrypts to the original.
                async with maker() as session:
                    cred = await get_credential(session, 1)
                    assert cred is not None
                    assert cred.access_token_encrypted not in (None, _TOKEN)
                    assert decrypt_secret(cred.access_token_encrypted) == _TOKEN

                # Disconnect removes the row.
                r = await ac.delete("/cli-oauth/token")
                assert r.status_code == 200
                assert r.json()["connected"] is False
                async with maker() as session:
                    assert await get_credential(session, 1) is None
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_short_token_rejected():
    async def _run():
        engine, _maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                r = await ac.put("/cli-oauth/token", json={"token": "   "})
                assert r.status_code == 422
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_disconnect_when_absent_is_noop():
    async def _run():
        engine, _maker, app = await _build_app()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                r = await ac.delete("/cli-oauth/token")
                assert r.status_code == 200
                assert r.json()["connected"] is False
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_credential_is_scoped_per_user():
    async def _run():
        engine, maker, app = await _build_app(user_id=1)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                await ac.put("/cli-oauth/token", json={"token": _TOKEN})

            # A different user sees no connection against the same store.
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
                id=2, username="bob"
            )
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                r = await ac.get("/cli-oauth/status")
                assert r.json()["connected"] is False
        finally:
            await engine.dispose()

    asyncio.run(_run())
