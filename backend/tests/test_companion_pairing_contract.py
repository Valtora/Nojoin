from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import select

from backend.api.deps import get_current_pairing_management_user, get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core import security
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.companion_pairing import CompanionPairing
from backend.models.companion_pairing_request import CompanionPairingRequest
from backend.services.companion_frontend_events import companion_frontend_events
import backend.services.companion_pairing_service as pairing_service

PAIRING_ORIGIN = "http://localhost:14141"
BACKEND_ORIGIN = "https://localhost:14443"
TLS_FINGERPRINT = "AA:BB:CC"
REPLACEMENT_TLS_FINGERPRINT = "DD:EE:FF"

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        username VARCHAR(255) NOT NULL,
        hashed_password VARCHAR(255) NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT 1,
        is_superuser BOOLEAN NOT NULL DEFAULT 0,
        force_password_change BOOLEAN NOT NULL DEFAULT 0,
        role VARCHAR(32) NOT NULL DEFAULT 'user',
        token_version INTEGER NOT NULL DEFAULT 0,
        settings TEXT NOT NULL DEFAULT '{}',
        has_seen_demo_recording BOOLEAN NOT NULL DEFAULT 0,
        invitation_id INTEGER
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
    """
    CREATE TABLE companion_pairing_requests (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        user_id INTEGER NOT NULL,
        request_id VARCHAR(64) NOT NULL UNIQUE,
        request_secret_hash VARCHAR(128) NOT NULL,
        status VARCHAR(16) NOT NULL,
        api_protocol VARCHAR(16) NOT NULL,
        api_host VARCHAR(255) NOT NULL,
        api_port INTEGER NOT NULL,
        paired_web_origin VARCHAR(2048) NOT NULL,
        replacement_pairing_session_id VARCHAR(64),
        expires_at DATETIME NOT NULL,
        opened_at DATETIME,
        completed_at DATETIME,
        status_detail TEXT,
        failure_reason VARCHAR(64),
        completed_pairing_session_id VARCHAR(64)
    )
    """,
    "CREATE INDEX ix_companion_pairing_requests_user_id ON companion_pairing_requests (user_id)",
    "CREATE INDEX ix_companion_pairing_requests_status ON companion_pairing_requests (status)",
    "CREATE INDEX ix_companion_pairing_requests_replacement_pairing_session_id ON companion_pairing_requests (replacement_pairing_session_id)",
    "CREATE INDEX ix_companion_pairing_requests_expires_at ON companion_pairing_requests (expires_at)",
    "CREATE INDEX ix_companion_pairing_requests_completed_pairing_session_id ON companion_pairing_requests (completed_pairing_session_id)",
]


def build_test_user(user_id: int = 1, username: str = "alice"):
    return SimpleNamespace(
        id=user_id,
        username=username,
        role="user",
        is_superuser=False,
        force_password_change=False,
        is_active=True,
        token_version=0,
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
                """
                INSERT INTO users (
                    id,
                    created_at,
                    updated_at,
                    username,
                    hashed_password,
                    is_active,
                    is_superuser,
                    force_password_change,
                    role,
                    token_version,
                    settings,
                    has_seen_demo_recording,
                    invitation_id
                ) VALUES (
                    :id,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    :username,
                    :hashed_password,
                    :is_active,
                    0,
                    0,
                    'user',
                    0,
                    '{}',
                    0,
                    NULL
                )
                """
            ),
            {
                "id": user_id,
                "username": username,
                "hashed_password": "test-hash",
                "is_active": is_active,
            },
        )
        await session.commit()


async def fetch_pairings(session_maker: sessionmaker) -> list[CompanionPairing]:
    async with session_maker() as session:
        result = await session.execute(
            select(CompanionPairing).order_by(CompanionPairing.local_control_secret_version)
        )
        return list(result.scalars().all())


async def fetch_pairing_requests(session_maker: sessionmaker) -> list[CompanionPairingRequest]:
    async with session_maker() as session:
        result = await session.execute(
            select(CompanionPairingRequest).order_by(CompanionPairingRequest.id)
        )
        return list(result.scalars().all())


def parse_launch_request(payload: dict[str, object]) -> dict[str, str]:
    parsed = urlparse(str(payload["launch_url"]))
    assert parsed.scheme == "nojoin"
    assert parsed.netloc == "pair"
    query = {
        key: values[0]
        for key, values in parse_qs(parsed.query).items()
    }
    assert query["request_id"] == str(payload["request_id"])
    return query


async def create_pairing_request(
    client: AsyncClient,
    override_current_user,
    *,
    origin: str = PAIRING_ORIGIN,
) -> dict[str, object]:
    override_current_user(1, "alice")
    response = await client.post(
        "/api/v1/login/companion-pairing",
        headers={"Origin": origin},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def get_pairing_request_status(
    client: AsyncClient,
    request_id: str,
) -> dict[str, object]:
    response = await client.get(
        f"/api/v1/login/companion-pairing/requests/{request_id}",
    )
    assert response.status_code == 200, response.text
    return response.json()


async def mark_pairing_request_opened(
    client: AsyncClient,
    launch_fields: dict[str, str],
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/login/companion-pairing/request/opened",
        json={
            "request_id": launch_fields["request_id"],
            "request_secret": launch_fields["request_secret"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def reject_pairing_request(
    client: AsyncClient,
    launch_fields: dict[str, str],
    *,
    status_value: str = "declined",
    detail: str = "Declined in Companion.",
    failure_reason: str = "user_declined",
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/login/companion-pairing/request/reject",
        json={
            "request_id": launch_fields["request_id"],
            "request_secret": launch_fields["request_secret"],
            "status": status_value,
            "detail": detail,
            "failure_reason": failure_reason,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def complete_pairing_request(
    client: AsyncClient,
    launch_fields: dict[str, str],
    *,
    tls_fingerprint: str = TLS_FINGERPRINT,
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/login/companion-pairing/request/complete",
        json={
            "request_id": launch_fields["request_id"],
            "request_secret": launch_fields["request_secret"],
            "tls_fingerprint": tls_fingerprint,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def exchange_pairing_credential(
    client: AsyncClient,
    completion_payload: dict[str, object],
):
    return await client.post(
        "/api/v1/login/companion-token/exchange",
        json={
            "pairing_session_id": completion_payload["backend_pairing_id"],
            "companion_credential_secret": completion_payload["companion_credential_secret"],
        },
    )


@pytest.fixture
async def api_app(monkeypatch) -> FastAPI:
    monkeypatch.setattr(
        pairing_service,
        "get_trusted_web_origin",
        lambda: BACKEND_ORIGIN,
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
async def test_pairing_request_lifecycle_completes_and_exchange_mints_token(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    request_payload = await create_pairing_request(client, override_current_user)

    assert request_payload["status"] == "pending"
    assert request_payload["backend_origin"] == BACKEND_ORIGIN
    assert request_payload["replacement"] is False
    assert request_payload["request_id"]
    assert request_payload["expires_at"]

    launch_fields = parse_launch_request(request_payload)
    assert launch_fields["backend_origin"] == BACKEND_ORIGIN
    assert launch_fields["request_id"] == request_payload["request_id"]
    assert launch_fields["replacement"] == "0"
    assert launch_fields["username"] == "alice"
    assert launch_fields["request_secret"]
    assert launch_fields["key_id"]
    assert launch_fields["public_key"]
    assert launch_fields["signature"]

    request_status = await get_pairing_request_status(client, str(request_payload["request_id"]))
    assert request_status["status"] == "pending"
    assert request_status["backend_origin"] == BACKEND_ORIGIN
    assert request_status["replacement"] is False
    assert request_status["opened_at"] is None
    assert request_status["completed_at"] is None

    opened = await mark_pairing_request_opened(client, launch_fields)
    assert opened["status"] == "opened"
    assert opened["opened_at"] is not None

    completion = await complete_pairing_request(client, launch_fields)
    assert completion["api_protocol"] == "https"
    assert completion["api_host"] == "localhost"
    assert completion["api_port"] == 14443
    assert completion["paired_web_origin"] == PAIRING_ORIGIN
    assert completion["companion_credential_secret"]
    assert completion["local_control_secret"]
    assert completion["local_control_secret_version"] == 1
    assert completion["backend_pairing_id"]
    assert completion["backend_identity_key_id"]
    assert completion["backend_identity_public_key"]

    completed_status = await get_pairing_request_status(client, str(request_payload["request_id"]))
    assert completed_status["status"] == "completed"
    assert completed_status["opened_at"] is not None
    assert completed_status["completed_at"] is not None
    assert completed_status["detail"] == "Pairing completed successfully."

    exchange = await exchange_pairing_credential(client, completion)
    assert exchange.status_code == 200
    body = exchange.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == security.COMPANION_ACCESS_TOKEN_EXPIRE_SECONDS

    decoded = security.decode_access_token(body["access_token"])
    assert decoded["token_type"] == security.COMPANION_TOKEN_TYPE
    assert decoded["sub"] == "alice"
    assert decoded[security.COMPANION_PAIRING_ID_CLAIM] == completion["backend_pairing_id"]
    assert security.COMPANION_BOOTSTRAP_SCOPE in decoded["scopes"]

    pairings = await fetch_pairings(test_session_maker)
    assert len(pairings) == 1
    assert pairings[0].status == "active"
    assert pairings[0].tls_fingerprint == TLS_FINGERPRINT
    assert pairings[0].local_control_secret_version == 1
    assert pairings[0].paired_web_origin == PAIRING_ORIGIN
    assert pairings[0].companion_credential_hash is not None
    assert pairings[0].local_control_secret_encrypted != completion["local_control_secret"]
    assert security.verify_companion_credential_secret(
        completion["companion_credential_secret"],
        pairings[0].companion_credential_hash,
    )
    assert decrypt_secret(pairings[0].local_control_secret_encrypted) == completion["local_control_secret"]

    requests = await fetch_pairing_requests(test_session_maker)
    assert len(requests) == 1
    assert requests[0].status == "completed"
    assert requests[0].replacement_pairing_session_id is None
    assert requests[0].completed_pairing_session_id == completion["backend_pairing_id"]
    assert requests[0].failure_reason is None
    assert requests[0].status_detail == "Pairing completed successfully."
    assert requests[0].opened_at is not None
    assert requests[0].completed_at is not None


@pytest.mark.anyio
async def test_exchange_rejects_invalid_companion_credential_after_completion(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    request_payload = await create_pairing_request(client, override_current_user)
    completion = await complete_pairing_request(client, parse_launch_request(request_payload))

    response = await client.post(
        "/api/v1/login/companion-token/exchange",
        json={
            "pairing_session_id": completion["backend_pairing_id"],
            "companion_credential_secret": "wrong-secret",
        },
    )

    assert response.status_code == 401
    assert (
        response.json()["detail"]
        == "Companion pairing credential is invalid. Pair again from Nojoin."
    )

    pairings = await fetch_pairings(test_session_maker)
    assert len(pairings) == 1
    assert pairings[0].status == "active"
    assert security.verify_companion_credential_secret(
        completion["companion_credential_secret"],
        pairings[0].companion_credential_hash,
    )


@pytest.mark.anyio
async def test_repair_completion_rotates_active_pairing_and_tracks_requests(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    first_request = await create_pairing_request(client, override_current_user)
    first_completion = await complete_pairing_request(client, parse_launch_request(first_request))
    first_exchange = await exchange_pairing_credential(client, first_completion)
    assert first_exchange.status_code == 200

    second_request = await create_pairing_request(client, override_current_user)
    second_launch = parse_launch_request(second_request)
    assert second_request["replacement"] is True
    assert second_launch["replacement"] == "1"

    requests_before_complete = await fetch_pairing_requests(test_session_maker)
    assert [row.status for row in requests_before_complete] == ["completed", "pending"]
    assert requests_before_complete[1].replacement_pairing_session_id == first_completion["backend_pairing_id"]

    second_completion = await complete_pairing_request(
        client,
        second_launch,
        tls_fingerprint=REPLACEMENT_TLS_FINGERPRINT,
    )
    assert second_completion["local_control_secret_version"] == 2

    second_exchange = await exchange_pairing_credential(client, second_completion)
    assert second_exchange.status_code == 200

    pairings = await fetch_pairings(test_session_maker)
    assert len(pairings) == 2
    assert pairings[0].status == "revoked"
    assert pairings[0].companion_credential_hash is None
    assert pairings[0].local_control_secret_encrypted is None
    assert pairings[0].revocation_reason == "replaced"
    assert pairings[1].status == "active"
    assert pairings[1].local_control_secret_version == 2
    assert pairings[1].tls_fingerprint == REPLACEMENT_TLS_FINGERPRINT
    assert security.verify_companion_credential_secret(
        second_completion["companion_credential_secret"],
        pairings[1].companion_credential_hash,
    )
    assert decrypt_secret(pairings[1].local_control_secret_encrypted) == second_completion["local_control_secret"]

    requests = await fetch_pairing_requests(test_session_maker)
    assert [row.status for row in requests] == ["completed", "completed"]
    assert requests[1].replacement_pairing_session_id == first_completion["backend_pairing_id"]
    assert requests[1].completed_pairing_session_id == second_completion["backend_pairing_id"]


@pytest.mark.anyio
async def test_manual_unpair_revokes_pairing_and_blocks_stale_exchange(
    client: AsyncClient,
    override_current_user,
    override_pairing_management_user,
    test_session_maker: sessionmaker,
) -> None:
    request_payload = await create_pairing_request(client, override_current_user)
    completion = await complete_pairing_request(client, parse_launch_request(request_payload))
    exchange = await exchange_pairing_credential(client, completion)
    assert exchange.status_code == 200

    override_pairing_management_user(1, "alice")
    revoke = await client.delete("/api/v1/login/companion-pairing")

    assert revoke.status_code == 200
    assert revoke.json() == {"revoked": True, "revoked_count": 1}

    pairings = await fetch_pairings(test_session_maker)
    assert len(pairings) == 1
    assert pairings[0].status == "revoked"
    assert pairings[0].companion_credential_hash is None
    assert pairings[0].local_control_secret_encrypted is None
    assert pairings[0].revocation_reason == "manual_unpair"

    stale_exchange = await exchange_pairing_credential(client, completion)
    assert stale_exchange.status_code == 409
    assert stale_exchange.json()["detail"] == "Companion pairing was revoked. Pair again from Nojoin."


@pytest.mark.anyio
async def test_explicit_disconnect_revokes_pairing_and_emits_frontend_signal(
    client: AsyncClient,
    override_current_user,
    override_pairing_management_user,
    test_session_maker: sessionmaker,
) -> None:
    request_payload = await create_pairing_request(client, override_current_user)
    completion = await complete_pairing_request(client, parse_launch_request(request_payload))
    exchange = await exchange_pairing_credential(client, completion)
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

        pairings = await fetch_pairings(test_session_maker)
        assert len(pairings) == 1
        assert pairings[0].status == "revoked"
        assert pairings[0].companion_credential_hash is None
        assert pairings[0].local_control_secret_encrypted is None
        assert pairings[0].revocation_reason == "manual_unpair"
    finally:
        await companion_frontend_events.unsubscribe(1, queue)


@pytest.mark.anyio
async def test_cancel_pending_request_preserves_active_pairing(
    client: AsyncClient,
    override_current_user,
    override_pairing_management_user,
    test_session_maker: sessionmaker,
) -> None:
    first_request = await create_pairing_request(client, override_current_user)
    first_completion = await complete_pairing_request(client, parse_launch_request(first_request))
    first_exchange = await exchange_pairing_credential(client, first_completion)
    assert first_exchange.status_code == 200

    await create_pairing_request(client, override_current_user)

    override_pairing_management_user(1, "alice")
    cancel = await client.delete("/api/v1/login/companion-pairing/pending")

    assert cancel.status_code == 200
    assert cancel.json() == {"cancelled": True, "cancelled_count": 1}

    pairings = await fetch_pairings(test_session_maker)
    assert len(pairings) == 1
    assert pairings[0].status == "active"
    assert pairings[0].revocation_reason is None

    requests = await fetch_pairing_requests(test_session_maker)
    assert [row.status for row in requests] == ["completed", "cancelled"]
    assert requests[1].failure_reason == "cancelled"
    assert requests[1].status_detail == "Pairing request cancelled before approval."
    assert requests[1].completed_pairing_session_id is None


@pytest.mark.anyio
async def test_cancel_specific_request_marks_terminal_and_allows_new_request(
    client: AsyncClient,
    override_current_user,
    override_pairing_management_user,
    test_session_maker: sessionmaker,
) -> None:
    request_payload = await create_pairing_request(client, override_current_user)

    override_pairing_management_user(1, "alice")
    cancel = await client.delete(
        f"/api/v1/login/companion-pairing/requests/{request_payload['request_id']}"
    )
    assert cancel.status_code == 200
    assert cancel.json() == {"cancelled": True, "cancelled_count": 1}

    request_status = await get_pairing_request_status(client, str(request_payload["request_id"]))
    assert request_status["status"] == "cancelled"
    assert request_status["detail"] == "Pairing request cancelled before approval."

    new_request = await create_pairing_request(client, override_current_user)
    assert new_request["request_id"] != request_payload["request_id"]

    requests = await fetch_pairing_requests(test_session_maker)
    assert [row.status for row in requests] == ["cancelled", "pending"]


@pytest.mark.anyio
async def test_active_pairing_remains_valid_until_replacement_request_completes(
    client: AsyncClient,
    override_current_user,
) -> None:
    first_request = await create_pairing_request(client, override_current_user)
    first_completion = await complete_pairing_request(client, parse_launch_request(first_request))

    second_request = await create_pairing_request(client, override_current_user)
    assert second_request["replacement"] is True

    active_exchange = await exchange_pairing_credential(client, first_completion)
    assert active_exchange.status_code == 200

    await complete_pairing_request(
        client,
        parse_launch_request(second_request),
        tls_fingerprint=REPLACEMENT_TLS_FINGERPRINT,
    )

    stale_exchange = await exchange_pairing_credential(client, first_completion)
    assert stale_exchange.status_code == 409
    assert stale_exchange.json()["detail"] == "Companion pairing was revoked. Pair again from Nojoin."


@pytest.mark.anyio
async def test_reject_request_marks_terminal_state_and_blocks_completion(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    request_payload = await create_pairing_request(client, override_current_user)
    launch_fields = parse_launch_request(request_payload)

    rejected = await reject_pairing_request(client, launch_fields)
    assert rejected["status"] == "declined"
    assert rejected["detail"] == "Declined in Companion."
    assert rejected["completed_at"] is not None

    request_status = await get_pairing_request_status(client, str(request_payload["request_id"]))
    assert request_status["status"] == "declined"
    assert request_status["detail"] == "Declined in Companion."

    completion = await client.post(
        "/api/v1/login/companion-pairing/request/complete",
        json={
            "request_id": launch_fields["request_id"],
            "request_secret": launch_fields["request_secret"],
            "tls_fingerprint": TLS_FINGERPRINT,
        },
    )
    assert completion.status_code == 409
    assert completion.json()["detail"] == "Declined in Companion."

    pairings = await fetch_pairings(test_session_maker)
    assert pairings == []


@pytest.mark.anyio
async def test_create_request_fails_closed_on_incomplete_cleanup(
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
                    :paired_web_origin,
                    :tls_fingerprint,
                    NULL,
                    :secret,
                    2,
                    NULL,
                    CURRENT_TIMESTAMP,
                    'manual_unpair'
                )
                """
            ),
            {
                "paired_web_origin": PAIRING_ORIGIN,
                "tls_fingerprint": TLS_FINGERPRINT,
                "secret": encrypt_secret("stale-secret"),
            },
        )
        await session.commit()

    override_current_user(1, "alice")
    response = await client.post(
        "/api/v1/login/companion-pairing",
        headers={"Origin": PAIRING_ORIGIN},
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
    request_payload = await create_pairing_request(client, override_current_user)
    completion = await complete_pairing_request(client, parse_launch_request(request_payload))

    override_current_user(1, "alice")
    response = await client.post(
        "/api/v1/login/companion-local-token",
        headers={"Origin": PAIRING_ORIGIN},
        json={"actions": [security.LOCAL_CONTROL_STATUS_READ_ACTION]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == security.COMPANION_LOCAL_CONTROL_TOKEN_EXPIRE_SECONDS

    decoded = jwt.decode(
        body["token"],
        completion["local_control_secret"],
        algorithms=[security.ALGORITHM],
        audience=security.COMPANION_LOCAL_CONTROL_AUDIENCE,
    )
    assert decoded["token_type"] == security.COMPANION_LOCAL_CONTROL_TOKEN_TYPE
    assert decoded["origin"] == PAIRING_ORIGIN
    assert decoded["actions"] == [security.LOCAL_CONTROL_STATUS_READ_ACTION]
    assert decoded[security.COMPANION_PAIRING_ID_CLAIM] == completion["backend_pairing_id"]
    assert decoded["secret_version"] == completion["local_control_secret_version"]


@pytest.mark.anyio
async def test_issue_local_control_token_rejects_wrong_origin(
    client: AsyncClient,
    override_current_user,
) -> None:
    request_payload = await create_pairing_request(client, override_current_user)
    await complete_pairing_request(client, parse_launch_request(request_payload))

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
        headers={"Origin": PAIRING_ORIGIN},
        json={"actions": [security.LOCAL_CONTROL_STATUS_READ_ACTION]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Companion pairing is not active. Pair again from Nojoin."


@pytest.mark.anyio
async def test_issue_local_control_token_still_supports_get_requests(
    client: AsyncClient,
    override_current_user,
) -> None:
    request_payload = await create_pairing_request(client, override_current_user)
    completion = await complete_pairing_request(client, parse_launch_request(request_payload))

    override_current_user(1, "alice")
    response = await client.get(
        "/api/v1/login/companion-local-token",
        headers={"Origin": PAIRING_ORIGIN},
        params={"actions": security.LOCAL_CONTROL_STATUS_READ_ACTION},
    )

    assert response.status_code == 200
    body = response.json()
    decoded = jwt.decode(
        body["token"],
        completion["local_control_secret"],
        algorithms=[security.ALGORITHM],
        audience=security.COMPANION_LOCAL_CONTROL_AUDIENCE,
    )
    assert decoded["actions"] == [security.LOCAL_CONTROL_STATUS_READ_ACTION]
