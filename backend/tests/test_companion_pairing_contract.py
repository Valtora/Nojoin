from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import select

from backend.api.deps import get_current_pairing_management_user, get_current_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints import login
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.companion_pairing import CompanionPairing
from backend.core import security
from backend.services.companion_frontend_events import companion_frontend_events
from jose import jwt
import backend.services.companion_pairing_service as pairing_service

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username VARCHAR(255) NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE companion_pairings (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        user_id INTEGER NOT NULL,
        pairing_session_id VARCHAR(64) NOT NULL UNIQUE,
        status VARCHAR(16) NOT NULL,
        api_protocol VARCHAR(16) NOT NULL,
        api_host VARCHAR(255) NOT NULL,
        api_port INTEGER NOT NULL,
        paired_web_origin VARCHAR(2048) NOT NULL,
        tls_fingerprint VARCHAR(255),
        companion_credential_hash VARCHAR(128),
        local_control_secret_encrypted TEXT,
        local_control_secret_version INTEGER NOT NULL,
        supersedes_pairing_session_id VARCHAR(64),
        revoked_at DATETIME,
        revocation_reason VARCHAR(32)
    )
    """,
    "CREATE INDEX ix_companion_pairings_user_id ON companion_pairings (user_id)",
    "CREATE INDEX ix_companion_pairings_status ON companion_pairings (status)",
    "CREATE INDEX ix_companion_pairings_supersedes_pairing_session_id ON companion_pairings (supersedes_pairing_session_id)",
]


def build_test_user(user_id: int = 1, username: str = "alice"):
    return SimpleNamespace(
        id=user_id,
        username=username,
        role="user",
        is_superuser=False,
        force_password_change=False,
    )


async def seed_user(
    session_maker: sessionmaker,
    user_id: int = 1,
    username: str = "alice",
    is_active: bool = True,
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, username, is_active) VALUES (:id, :username, :is_active)"
            ),
            {"id": user_id, "username": username, "is_active": is_active},
        )
        await session.commit()


async def fetch_pairings(session_maker: sessionmaker) -> list[CompanionPairing]:
    async with session_maker() as session:
        result = await session.execute(
            select(CompanionPairing).order_by(CompanionPairing.local_control_secret_version)
        )
        return list(result.scalars().all())


async def prepare_pairing(client: AsyncClient, override_current_user) -> dict[str, object]:
    override_current_user(1, "alice")
    response = await client.post(
        "/api/v1/login/companion-pairing",
        headers={"Origin": "http://localhost:14141"},
        json={"pairing_code": "ABCD-EFGH"},
    )
    assert response.status_code == 200
    return response.json()


async def exchange_pairing_credential(
    client: AsyncClient,
    payload: dict[str, object],
) -> Any:
    return await client.post(
        "/api/v1/login/companion-token/exchange",
        json={
            "pairing_session_id": payload["backend_pairing_id"],
            "companion_credential_secret": payload["companion_credential_secret"],
        },
    )


@pytest.fixture
async def api_app(monkeypatch) -> FastAPI:
    monkeypatch.setattr(login, "resolve_tls_fingerprint", lambda: "AA:BB:CC")
    monkeypatch.setattr(
        pairing_service,
        "get_trusted_web_origin",
        lambda: "https://localhost:14443",
    )

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return app


@pytest.fixture
async def test_session_maker() -> sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as connection:
        for statement in SCHEMA_STATEMENTS:
            await connection.execute(text(statement))

    await seed_user(session_maker)

    try:
        yield session_maker
    finally:
        await engine.dispose()


@pytest.fixture
async def client(api_app: FastAPI, test_session_maker: sessionmaker) -> AsyncClient:
    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    api_app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

    api_app.dependency_overrides.clear()


@pytest.fixture
def override_current_user(api_app: FastAPI):
    def _override(user_id: int = 1, username: str = "alice") -> None:
        api_app.dependency_overrides[get_current_user] = lambda: build_test_user(
            user_id,
            username,
        )

    return _override


@pytest.fixture
def override_pairing_management_user(api_app: FastAPI):
    def _override(user_id: int = 1, username: str = "alice") -> None:
        api_app.dependency_overrides[get_current_pairing_management_user] = lambda: (
            build_test_user(user_id, username)
        )

    return _override


@pytest.mark.anyio
async def test_first_pair_persists_secret_and_activates_on_validate(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    payload = await prepare_pairing(client, override_current_user)

    assert payload["api_protocol"] == "https"
    assert payload["api_host"] == "localhost"
    assert payload["api_port"] == 14443
    assert payload["tls_fingerprint"] == "AA:BB:CC"
    assert payload["local_control_secret_version"] == 1
    assert payload["local_control_secret"]
    assert payload["companion_credential_secret"]

    exchange = await exchange_pairing_credential(client, payload)

    assert exchange.status_code == 200
    body = exchange.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == security.COMPANION_ACCESS_TOKEN_EXPIRE_SECONDS

    decoded = jwt.decode(
        body["access_token"],
        security.SECRET_KEY,
        algorithms=[security.ALGORITHM],
    )
    assert decoded["token_type"] == security.COMPANION_TOKEN_TYPE
    assert decoded["sub"] == "alice"
    assert decoded[security.COMPANION_PAIRING_ID_CLAIM] == payload["backend_pairing_id"]
    assert security.COMPANION_BOOTSTRAP_SCOPE in decoded["scopes"]

    rows = await fetch_pairings(test_session_maker)
    assert len(rows) == 1
    assert rows[0].status == "active"
    assert rows[0].local_control_secret_version == 1
    assert rows[0].paired_web_origin == "http://localhost:14141"
    assert rows[0].companion_credential_hash is not None
    assert rows[0].local_control_secret_encrypted != payload["local_control_secret"]
    assert security.verify_companion_credential_secret(
        payload["companion_credential_secret"],
        rows[0].companion_credential_hash,
    )
    assert decrypt_secret(rows[0].local_control_secret_encrypted) == payload["local_control_secret"]


@pytest.mark.anyio
async def test_exchange_rejects_invalid_companion_credential(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    payload = await prepare_pairing(client, override_current_user)

    response = await client.post(
        "/api/v1/login/companion-token/exchange",
        json={
            "pairing_session_id": payload["backend_pairing_id"],
            "companion_credential_secret": "wrong-secret",
        },
    )

    assert response.status_code == 401
    assert (
        response.json()["detail"]
        == "Companion pairing credential is invalid. Pair again from Nojoin."
    )

    rows = await fetch_pairings(test_session_maker)
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].companion_credential_hash is not None


@pytest.mark.anyio
async def test_repair_rotates_secret_and_revokes_previous_pairing(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    first_payload = await prepare_pairing(client, override_current_user)
    first_exchange = await exchange_pairing_credential(client, first_payload)
    assert first_exchange.status_code == 200

    second_payload = await prepare_pairing(client, override_current_user)
    assert second_payload["local_control_secret_version"] == 2

    rows_before_validate = await fetch_pairings(test_session_maker)
    assert [row.status for row in rows_before_validate] == ["active", "pending"]

    second_exchange = await exchange_pairing_credential(client, second_payload)
    assert second_exchange.status_code == 200

    rows = await fetch_pairings(test_session_maker)
    assert len(rows) == 2
    assert rows[0].status == "revoked"
    assert rows[0].companion_credential_hash is None
    assert rows[0].local_control_secret_encrypted is None
    assert rows[0].revocation_reason == "replaced"
    assert rows[1].status == "active"
    assert rows[1].local_control_secret_version == 2
    assert security.verify_companion_credential_secret(
        second_payload["companion_credential_secret"],
        rows[1].companion_credential_hash,
    )
    assert decrypt_secret(rows[1].local_control_secret_encrypted) == second_payload["local_control_secret"]


@pytest.mark.anyio
async def test_manual_unpair_revokes_pairing_and_blocks_stale_validate(
    client: AsyncClient,
    override_current_user,
    override_pairing_management_user,
    test_session_maker: sessionmaker,
) -> None:
    payload = await prepare_pairing(client, override_current_user)
    exchange = await exchange_pairing_credential(client, payload)
    assert exchange.status_code == 200

    override_pairing_management_user(1, "alice")
    revoke = await client.delete("/api/v1/login/companion-pairing")

    assert revoke.status_code == 200
    assert revoke.json() == {"revoked": True, "revoked_count": 1}

    rows = await fetch_pairings(test_session_maker)
    assert len(rows) == 1
    assert rows[0].status == "revoked"
    assert rows[0].companion_credential_hash is None
    assert rows[0].local_control_secret_encrypted is None
    assert rows[0].revocation_reason == "manual_unpair"

    stale_validate = await exchange_pairing_credential(client, payload)

    assert stale_validate.status_code == 409
    assert stale_validate.json()["detail"] == "Companion pairing was revoked. Pair again from Nojoin."


@pytest.mark.anyio
async def test_explicit_disconnect_revokes_pairing_and_emits_frontend_signal(
    client: AsyncClient,
    override_current_user,
    override_pairing_management_user,
    test_session_maker: sessionmaker,
) -> None:
    payload = await prepare_pairing(client, override_current_user)
    exchange = await exchange_pairing_credential(client, payload)
    assert exchange.status_code == 200

    queue = await companion_frontend_events.subscribe(1)
    try:
        override_pairing_management_user(1, "alice")
        disconnect = await client.post("/api/v1/login/companion-pairing/disconnect")

        assert disconnect.status_code == 200
        assert disconnect.json() == {
            "disconnected": True,
            "revoked_count": 1,
            "signal_type": "companion-explicit-disconnect",
        }

        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event["type"] == "companion-explicit-disconnect"
        assert event["reason"] == "manual_disconnect"
        assert event["source"] == "companion_app"

        rows = await fetch_pairings(test_session_maker)
        assert len(rows) == 1
        assert rows[0].status == "revoked"
        assert rows[0].companion_credential_hash is None
        assert rows[0].local_control_secret_encrypted is None
        assert rows[0].revocation_reason == "manual_unpair"
    finally:
        await companion_frontend_events.unsubscribe(1, queue)


@pytest.mark.anyio
async def test_cancel_pending_pairing_preserves_active_pairing(
    client: AsyncClient,
    override_current_user,
    override_pairing_management_user,
    test_session_maker: sessionmaker,
) -> None:
    first_payload = await prepare_pairing(client, override_current_user)
    first_exchange = await exchange_pairing_credential(client, first_payload)
    assert first_exchange.status_code == 200

    second_payload = await prepare_pairing(client, override_current_user)
    assert second_payload["local_control_secret_version"] == 2

    override_pairing_management_user(1, "alice")
    cancel = await client.delete("/api/v1/login/companion-pairing/pending")

    assert cancel.status_code == 200
    assert cancel.json() == {"cancelled": True, "cancelled_count": 1}

    rows = await fetch_pairings(test_session_maker)
    assert len(rows) == 2
    assert rows[0].status == "active"
    assert rows[0].revocation_reason is None
    assert rows[1].status == "revoked"
    assert rows[1].companion_credential_hash is None
    assert rows[1].local_control_secret_encrypted is None
    assert rows[1].revocation_reason == "pending_cancelled"


@pytest.mark.anyio
async def test_cancel_pending_pairing_allows_new_prepare_and_active_validate(
    client: AsyncClient,
    override_current_user,
    override_pairing_management_user,
) -> None:
    first_payload = await prepare_pairing(client, override_current_user)
    first_exchange = await exchange_pairing_credential(client, first_payload)
    assert first_exchange.status_code == 200

    second_payload = await prepare_pairing(client, override_current_user)
    assert second_payload["local_control_secret_version"] == 2

    override_pairing_management_user(1, "alice")
    cancel = await client.delete("/api/v1/login/companion-pairing/pending")
    assert cancel.status_code == 200

    active_exchange = await exchange_pairing_credential(client, first_payload)
    assert active_exchange.status_code == 200

    third_payload = await prepare_pairing(client, override_current_user)
    assert third_payload["local_control_secret_version"] == 3


@pytest.mark.anyio
async def test_old_pairing_validate_fails_closed_when_newer_secret_is_pending(
    client: AsyncClient,
    override_current_user,
) -> None:
    first_payload = await prepare_pairing(client, override_current_user)
    first_exchange = await exchange_pairing_credential(client, first_payload)
    assert first_exchange.status_code == 200

    second_payload = await prepare_pairing(client, override_current_user)
    assert second_payload["local_control_secret_version"] == 2

    stale_validate = await exchange_pairing_credential(client, first_payload)

    assert stale_validate.status_code == 409
    assert stale_validate.json()["detail"] == "Companion pairing state is stale or rotated. Pair again from Nojoin."


@pytest.mark.anyio
async def test_prepare_pair_fails_closed_on_incomplete_cleanup(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO companion_pairings (
                    id,
                    created_at,
                    updated_at,
                    user_id,
                    pairing_session_id,
                    status,
                    api_protocol,
                    api_host,
                    api_port,
                    paired_web_origin,
                    tls_fingerprint,
                    companion_credential_hash,
                    local_control_secret_encrypted,
                    local_control_secret_version,
                    supersedes_pairing_session_id,
                    revoked_at,
                    revocation_reason
                ) VALUES (
                    1,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    1,
                    'stale-revoked',
                    'revoked',
                    'https',
                    'localhost',
                    14443,
                    'http://localhost:14141',
                    'AA:BB:CC',
                    NULL,
                    :secret,
                    2,
                    NULL,
                    CURRENT_TIMESTAMP,
                    'manual_unpair'
                )
                """
            ),
            {"secret": encrypt_secret("stale-secret")},
        )
        await session.commit()

    override_current_user(1, "alice")
    response = await client.post(
        "/api/v1/login/companion-pairing",
        headers={"Origin": "http://localhost:14141"},
        json={"pairing_code": "WXYZ-9876"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Companion pairing cleanup is incomplete. Revoke the pairing and pair again."
    )


@pytest.mark.anyio
async def test_issue_local_control_token_for_active_pairing(
    client: AsyncClient,
    override_current_user,
) -> None:
    payload = await prepare_pairing(client, override_current_user)
    exchange = await exchange_pairing_credential(client, payload)
    assert exchange.status_code == 200

    override_current_user(1, "alice")
    response = await client.post(
        "/api/v1/login/companion-local-token",
        headers={"Origin": "http://localhost:14141"},
        json={"actions": [security.LOCAL_CONTROL_STATUS_READ_ACTION]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == security.COMPANION_LOCAL_CONTROL_TOKEN_EXPIRE_SECONDS

    decoded = jwt.decode(
        body["token"],
        payload["local_control_secret"],
        algorithms=[security.ALGORITHM],
        audience=security.COMPANION_LOCAL_CONTROL_AUDIENCE,
    )
    assert decoded["token_type"] == security.COMPANION_LOCAL_CONTROL_TOKEN_TYPE
    assert decoded["origin"] == "http://localhost:14141"
    assert decoded["actions"] == [security.LOCAL_CONTROL_STATUS_READ_ACTION]
    assert decoded[security.COMPANION_PAIRING_ID_CLAIM] == payload["backend_pairing_id"]
    assert decoded["secret_version"] == payload["local_control_secret_version"]


@pytest.mark.anyio
async def test_issue_local_control_token_rejects_wrong_origin(
    client: AsyncClient,
    override_current_user,
) -> None:
    payload = await prepare_pairing(client, override_current_user)
    exchange = await exchange_pairing_credential(client, payload)
    assert exchange.status_code == 200

    override_current_user(1, "alice")
    response = await client.post(
        "/api/v1/login/companion-local-token",
        headers={"Origin": "http://localhost:3000"},
        json={"actions": [security.LOCAL_CONTROL_STATUS_READ_ACTION]},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Local control tokens may only be issued to the paired web origin."
    )


@pytest.mark.anyio
async def test_issue_local_control_token_fails_closed_without_active_pairing(
    client: AsyncClient,
    override_current_user,
) -> None:
    override_current_user(1, "alice")
    response = await client.post(
        "/api/v1/login/companion-local-token",
        headers={"Origin": "http://localhost:14141"},
        json={"actions": [security.LOCAL_CONTROL_STATUS_READ_ACTION]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Companion pairing is not active. Pair again from Nojoin."


@pytest.mark.anyio
async def test_issue_local_control_token_still_supports_get_requests(
    client: AsyncClient,
    override_current_user,
) -> None:
    payload = await prepare_pairing(client, override_current_user)
    exchange = await exchange_pairing_credential(client, payload)
    assert exchange.status_code == 200

    override_current_user(1, "alice")
    response = await client.get(
        "/api/v1/login/companion-local-token",
        headers={"Origin": "http://localhost:14141"},
        params={"actions": security.LOCAL_CONTROL_STATUS_READ_ACTION},
    )

    assert response.status_code == 200