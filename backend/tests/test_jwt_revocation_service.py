from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core import security
from backend.models.invitation import Invitation  # noqa: F401 - register relationship target
from backend.models.revoked_jwt import RevokedJwt
from backend.models.user import User
from backend.services.jwt_revocation_service import (
    bump_user_token_version,
    prune_expired_revoked_jwts,
    revoke_jwt_by_payload,
)
from backend.utils.time import utc_now


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
]


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
def isolated_keyring(monkeypatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    class _StubPathManager:
        user_data_directory = tmp_path

    monkeypatch.setattr(security, "path_manager", _StubPathManager())
    yield tmp_path


async def _seed_user(session_maker, *, username: str = "alice") -> User:
    async with session_maker() as session:
        now = utc_now()
        await session.execute(
            text(
                "INSERT INTO users (id, created_at, updated_at, username, hashed_password)"
                " VALUES (1, :ts, :ts, :u, '')"
            ),
            {"ts": now, "u": username},
        )
        await session.commit()
        result = await session.execute(select(User).where(User.username == username))
        return result.scalar_one()


@pytest.mark.anyio
async def test_bump_user_token_version_increments_and_persists(session_maker):
    user = await _seed_user(session_maker)
    assert user.token_version == 0

    async with session_maker() as session:
        managed = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        new_value = await bump_user_token_version(session, managed)

    assert new_value == 1

    async with session_maker() as session:
        refreshed = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        assert refreshed.token_version == 1


@pytest.mark.anyio
async def test_revoke_jwt_by_payload_inserts_jti_and_is_idempotent(
    session_maker, isolated_keyring
):
    user = await _seed_user(session_maker)
    token = security.create_access_token(
        user.username,
        token_type=security.SESSION_TOKEN_TYPE,
        scopes=[security.WEB_SESSION_SCOPE],
        expires_delta=timedelta(minutes=10),
        token_version=user.token_version,
    )
    payload = security.decode_access_token(token)

    async with session_maker() as session:
        managed = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        first = await revoke_jwt_by_payload(session, payload, managed, reason="logout")
        # Repeat in a fresh session to exercise the lookup branch.
    async with session_maker() as session:
        managed = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        second = await revoke_jwt_by_payload(session, payload, managed, reason="logout")

    assert first is True
    assert second is False

    async with session_maker() as session:
        rows = (await session.execute(select(RevokedJwt))).scalars().all()
        assert len(rows) == 1
        assert rows[0].jti == payload["jti"]
        assert rows[0].reason == "logout"


@pytest.mark.anyio
async def test_revoke_jwt_by_payload_skips_companion_tokens(
    session_maker, isolated_keyring
):
    user = await _seed_user(session_maker)
    token = security.create_access_token(
        user.username,
        token_type=security.COMPANION_TOKEN_TYPE,
        scopes=[security.COMPANION_BOOTSTRAP_SCOPE],
        expires_delta=timedelta(minutes=10),
    )
    payload = security.decode_access_token(token)

    async with session_maker() as session:
        managed = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        result = await revoke_jwt_by_payload(session, payload, managed)

    assert result is False


@pytest.mark.anyio
async def test_prune_expired_revoked_jwts_deletes_only_past_entries(session_maker):
    user = await _seed_user(session_maker)

    async with session_maker() as session:
        session.add(
            RevokedJwt(
                jti="expired",
                user_id=user.id,
                token_type=security.SESSION_TOKEN_TYPE,
                expires_at=utc_now() - timedelta(seconds=1),
                revoked_at=utc_now(),
            )
        )
        session.add(
            RevokedJwt(
                jti="active",
                user_id=user.id,
                token_type=security.SESSION_TOKEN_TYPE,
                expires_at=utc_now() + timedelta(minutes=5),
                revoked_at=utc_now(),
            )
        )
        await session.commit()

    async with session_maker() as session:
        deleted = await prune_expired_revoked_jwts(session)

    assert deleted == 1

    async with session_maker() as session:
        remaining = (await session.execute(select(RevokedJwt))).scalars().all()
        assert [row.jti for row in remaining] == ["active"]
