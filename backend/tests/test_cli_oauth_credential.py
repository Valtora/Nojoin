from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Register every ORM model so SQLAlchemy can resolve User's cross-model
# relationships (e.g. Invitation) when configuring the mapper reached via the
# credential's user_id FK.
from backend.models import registry  # noqa: F401
from backend.models.cli_oauth import CliOAuthCredential, CliOAuthCredentialStatus
from backend.services.cli_oauth.persistence import (
    CliTokenBundle,
    decrypt_credential_tokens,
    delete_credential,
    get_credential,
    upsert_credential,
)

# SQLite only auto-assigns the rowid for the literal type "INTEGER PRIMARY KEY";
# the model's BigInteger PK (a sequence-backed BIGINT in Postgres) would not
# autoincrement here. Declaring the table with raw SQLite-friendly DDL mirrors
# how the calendar tests build their schema and keeps the UNIQUE constraint
# enforceable (so the duplicate test fails for the right reason).
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


async def _make_session_maker():
    """A throwaway in-memory SQLite engine with only the credential table.

    Mirrors the async SQLite setup used by the calendar tests; each test drives
    it through ``asyncio.run`` so no async-pytest configuration is required.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(text(_CREATE_CLI_OAUTH_CREDENTIALS))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, maker


def test_credential_encrypt_roundtrip():
    async def _run():
        engine, maker = await _make_session_maker()
        try:
            async with maker() as db:
                cred = await upsert_credential(
                    db,
                    user_id=1,
                    tokens=CliTokenBundle(
                        access_token="access-123", refresh_token="refresh-abc"
                    ),
                )
                # Stored as ciphertext, never the plaintext token.
                assert cred.access_token_encrypted not in (None, "access-123")
                assert cred.refresh_token_encrypted not in (None, "refresh-abc")
                assert cred.status == CliOAuthCredentialStatus.ACTIVE.value

            async with maker() as db:
                fetched = await get_credential(db, 1)
                assert fetched is not None
                access, refresh = decrypt_credential_tokens(fetched)
                assert (access, refresh) == ("access-123", "refresh-abc")
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_upsert_is_idempotent_per_user_provider():
    async def _run():
        engine, maker = await _make_session_maker()
        try:
            async with maker() as db:
                first = await upsert_credential(
                    db, user_id=7, tokens=CliTokenBundle(access_token="a1")
                )
                second = await upsert_credential(
                    db, user_id=7, tokens=CliTokenBundle(access_token="a2")
                )
                # Same row updated in place, not a duplicate.
                assert first.id == second.id
                access, _ = decrypt_credential_tokens(second)
                assert access == "a2"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_upsert_preserves_refresh_token_when_absent():
    async def _run():
        engine, maker = await _make_session_maker()
        try:
            async with maker() as db:
                await upsert_credential(
                    db,
                    user_id=3,
                    tokens=CliTokenBundle(access_token="a1", refresh_token="r1"),
                )
                # A refresh that returns no new refresh token must keep the old one.
                await upsert_credential(
                    db, user_id=3, tokens=CliTokenBundle(access_token="a2")
                )
                cred = await get_credential(db, 3)
                access, refresh = decrypt_credential_tokens(cred)
                assert access == "a2"
                assert refresh == "r1"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_unique_constraint_blocks_duplicate_user_provider():
    async def _run():
        engine, maker = await _make_session_maker()
        try:
            async with maker() as db:
                db.add(CliOAuthCredential(user_id=9, provider="claude_code"))
                db.add(CliOAuthCredential(user_id=9, provider="claude_code"))
                with pytest.raises(IntegrityError):
                    await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_delete_credential_removes_row():
    async def _run():
        engine, maker = await _make_session_maker()
        try:
            async with maker() as db:
                await upsert_credential(
                    db, user_id=5, tokens=CliTokenBundle(access_token="a")
                )
            async with maker() as db:
                assert await delete_credential(db, 5) is True
            async with maker() as db:
                assert await get_credential(db, 5) is None
                # Deleting an absent row is a no-op, not an error.
                assert await delete_credential(db, 5) is False
        finally:
            await engine.dispose()

    asyncio.run(_run())
