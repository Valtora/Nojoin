"""A token_version bump and an MCP refresh racing on PostgreSQL.

``bump_user_token_version`` revokes every live refresh token of the user, and
``refresh_access_token`` rotates one. Without row locks, a refresh that read
its token before the bump committed would insert a successor the bump never
saw, and the connector would keep its access past a password change. The
refresh therefore locks the users row, then its token, in the order the bump
updates them. SQLite has no row locks, so this runs against a real server.

Skips unless NOJOIN_TEST_POSTGRES_URL names a server. CI sets it for the backend suite.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from backend.api.services import oauth_service
from backend.core import security
from backend.core.db import driver_urls
from backend.models.invitation import Invitation
from backend.models.oauth import OAuthClient, OAuthRefreshToken
from backend.models.user import User
from backend.services.jwt_revocation_service import bump_user_token_version

SCHEMA = "oauth_refresh_lock_test"
CLIENT_ID = "client-under-test"
# Long enough for a blocked statement to show as blocked, short enough to
# keep the suite quick. A statement that is not blocked returns in a few ms.
BLOCK_PROBE_SECONDS = 0.5


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def isolated_keyring(monkeypatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    class _StubPathManager:
        user_data_directory = tmp_path

    monkeypatch.setattr(security, "path_manager", _StubPathManager())
    monkeypatch.setattr(
        oauth_service, "get_trusted_web_origin", lambda: "https://nojoin.example.com"
    )


@pytest.fixture
async def sessions(postgres_test_url: str):
    importlib.import_module("backend.models.registry")
    engine = create_async_engine(
        driver_urls(postgres_test_url)[0],
        connect_args={"server_settings": {"search_path": SCHEMA}},
    )
    async with engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        await connection.run_sync(
            lambda sync: SQLModel.metadata.create_all(
                sync,
                tables=[
                    User.__table__,
                    Invitation.__table__,
                    OAuthClient.__table__,
                    OAuthRefreshToken.__table__,
                ],
            )
        )
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    await engine.dispose()


async def _seed(sessions) -> tuple[int, str]:
    """Create a user with one grant and return the user id and refresh token."""
    async with sessions() as db:
        user = User(username="alice", hashed_password="hashed", role="user")
        db.add(user)
        db.add(
            OAuthClient(
                client_id=CLIENT_ID,
                client_name="Claude",
                redirect_uris='["https://claude.ai/api/mcp/auth_callback"]',
            )
        )
        await db.flush()
        refresh_token = await oauth_service._issue_refresh_token(
            db,
            oauth_service.RefreshGrant(
                grant_id="grant-1",
                client_id=CLIENT_ID,
                user_id=user.id,
                scope="mcp:read mcp:write",
                resource=None,
            ),
        )
        await db.commit()
        return user.id, refresh_token


async def _live_tokens(sessions, user_id: int) -> list[OAuthRefreshToken]:
    async with sessions() as db:
        result = await db.execute(
            select(OAuthRefreshToken)
            .where(OAuthRefreshToken.user_id == user_id)
            .where(OAuthRefreshToken.revoked_at.is_(None))  # type: ignore[union-attr]
        )
        return list(result.scalars())


async def _refresh(sessions, refresh_token: str) -> dict:
    async with sessions() as db:
        return await oauth_service.refresh_access_token(
            db, refresh_token=refresh_token, client_id=CLIENT_ID
        )


@pytest.mark.anyio
async def test_refresh_waits_for_a_bump_in_progress_and_is_refused(
    sessions, isolated_keyring
):
    user_id, refresh_token = await _seed(sessions)

    async with sessions() as bump_db:
        user = await bump_db.get(User, user_id)
        await bump_user_token_version(bump_db, user, commit=False)

        refreshing = asyncio.ensure_future(_refresh(sessions, refresh_token))
        await asyncio.sleep(BLOCK_PROBE_SECONDS)
        assert not refreshing.done(), "refresh did not wait for the bump"

        await bump_db.commit()

    with pytest.raises(oauth_service.OAuthError) as refused:
        await refreshing
    assert refused.value.error == "invalid_grant"
    assert await _live_tokens(sessions, user_id) == []


@pytest.mark.anyio
async def test_bump_during_a_refresh_revokes_the_token_it_issues(
    sessions, isolated_keyring, monkeypatch
):
    user_id, refresh_token = await _seed(sessions)

    # Hold the refresh after it has taken its locks and minted, just before
    # it inserts the successor token.
    reached = asyncio.Event()
    release = asyncio.Event()
    issue = oauth_service._issue_refresh_token

    async def held_issue(db, grant):
        reached.set()
        await release.wait()
        return await issue(db, grant)

    async def bump() -> None:
        async with sessions() as db:
            user = await db.get(User, user_id)
            await bump_user_token_version(db, user)

    monkeypatch.setattr(oauth_service, "_issue_refresh_token", held_issue)
    refreshing = asyncio.ensure_future(_refresh(sessions, refresh_token))
    bumping = None
    try:
        await asyncio.wait_for(reached.wait(), timeout=5)
        bumping = asyncio.ensure_future(bump())
        await asyncio.sleep(BLOCK_PROBE_SECONDS)
        bump_waited = not bumping.done()
    finally:
        # Let both transactions end even when an assertion fails, or the
        # held refresh keeps its locks and the schema teardown never runs.
        release.set()
        issued, _ = await asyncio.gather(refreshing, bumping or asyncio.sleep(0))

    assert bump_waited, "bump did not wait for the refresh in flight"
    assert issued["refresh_token"] != refresh_token
    assert await _live_tokens(sessions, user_id) == []
    with pytest.raises(oauth_service.OAuthError):
        await _refresh(sessions, issued["refresh_token"])
