"""Two requests at once cannot take the last Owner or superuser away, on PostgreSQL.

The users routes lock every active Owner's and superuser's row before they count
them (#634, #640). Here two accounts demote, delete or take the rights of each
other at the same moment through the real routes and the real locks: one change
goes through and the other is refused, so one is left. Skips unless
NOJOIN_TEST_POSTGRES_URL names a server.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Session, select

from backend.api.v1.endpoints import users
from backend.core.db import driver_urls
from backend.models.user import User, UserRole, UserUpdate
from backend.tests.postgres_setup import fresh_schema

SCHEMA = "privileged_accounts_guard_test"


@pytest.fixture
def engine(postgres_test_url: str):
    importlib.import_module("backend.models.registry")
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": f"-csearch_path={SCHEMA},public"},
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(engine, SCHEMA)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def two_owners(engine) -> SimpleNamespace:
    with Session(engine) as session:
        alice = User(username="alice.example", hashed_password="x", role="owner")
        bob = User(username="bob.example", hashed_password="x", role="owner")
        session.add_all([alice, bob])
        session.commit()
        return SimpleNamespace(alice=alice.id, bob=bob.id)


@asynccontextmanager
async def _two_sessions(postgres_test_url: str):
    engine = create_async_engine(
        driver_urls(postgres_test_url)[0],
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    try:
        async with (
            AsyncSession(engine, expire_on_commit=False) as first,
            AsyncSession(engine, expire_on_commit=False) as second,
        ):
            yield first, second
    finally:
        await engine.dispose()


def _wait_for_a_session_to_block(engine, timeout: float = 10.0) -> None:
    """Return once a session of this database is waiting on another's lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # A new transaction each time: pg_stat_activity is a per-transaction
        # snapshot, so one long transaction would never see the wait begin.
        with engine.connect() as connection:
            waiting = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
        if waiting:
            return
        time.sleep(0.05)
    raise AssertionError("the second request never waited for the first")


def _owners(engine) -> list[int]:
    with Session(engine) as session:
        return sorted(
            session.exec(select(User.id).where(User.role == UserRole.OWNER)).all()
        )


async def _as(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    assert user is not None
    return user


def _superusers(engine) -> list[int]:
    with Session(engine) as session:
        return sorted(session.exec(select(User.id).where(User.is_superuser)).all())


def _outcomes(
    results: list[object], detail: str = users.LAST_OWNER_DETAIL
) -> list[str]:
    outcomes = []
    for result in results:
        if isinstance(result, HTTPException):
            assert result.detail == detail
            outcomes.append(f"refused {result.status_code}")
        elif isinstance(result, BaseException):
            raise result
        else:
            outcomes.append("done")
    return sorted(outcomes)


@pytest.mark.anyio
async def test_two_owners_demoting_each_other_leave_one(
    engine, two_owners, postgres_test_url
) -> None:
    async with _two_sessions(postgres_test_url) as (first, second):
        alice = await _as(first, two_owners.alice)
        bob = await _as(second, two_owners.bob)
        await first.commit()
        await second.commit()

        results = await asyncio.gather(
            users.update_user_role(
                db=first, user_id=two_owners.bob, role="admin", current_user=alice
            ),
            users.update_user_role(
                db=second, user_id=two_owners.alice, role="admin", current_user=bob
            ),
            return_exceptions=True,
        )

    assert _outcomes(results) == ["done", "refused 409"]
    assert len(_owners(engine)) == 1


@pytest.mark.anyio
async def test_two_owners_deleting_each_other_leave_one(
    engine, two_owners, postgres_test_url
) -> None:
    async with _two_sessions(postgres_test_url) as (first, second):
        alice = await _as(first, two_owners.alice)
        bob = await _as(second, two_owners.bob)
        await first.commit()
        await second.commit()

        results = await asyncio.gather(
            users.delete_user(db=first, user_id=two_owners.bob, current_user=alice),
            users.delete_user(db=second, user_id=two_owners.alice, current_user=bob),
            return_exceptions=True,
        )

    assert _outcomes(results) == ["done", "refused 409"]
    assert len(_owners(engine)) == 1


@pytest.mark.anyio
async def test_a_demotion_waits_for_one_in_flight(
    engine, two_owners, postgres_test_url
) -> None:
    """The second change waits on the first's locks, then counts what it left."""
    async with _two_sessions(postgres_test_url) as (first, second):
        bob = await _as(second, two_owners.bob)
        await second.commit()

        # The first request holds the Owner locks and has demoted Alice, but
        # has not committed yet.
        accounts = await users.lock_privileged_accounts(first)
        assert accounts.owner_ids == {two_owners.alice, two_owners.bob}
        alice = await _as(first, two_owners.alice)
        alice.role = UserRole.ADMIN
        first.add(alice)
        await first.flush()

        pending = asyncio.ensure_future(
            users.update_user_role(
                db=second, user_id=two_owners.bob, role="user", current_user=bob
            )
        )
        await asyncio.to_thread(_wait_for_a_session_to_block, engine)
        assert not pending.done()

        await first.commit()
        with pytest.raises(HTTPException) as exc_info:
            await asyncio.wait_for(pending, timeout=30)

    assert exc_info.value.status_code == 409
    assert _owners(engine) == [two_owners.bob]


@pytest.mark.anyio
async def test_two_superusers_taking_each_others_rights_leave_one(
    engine, two_owners, postgres_test_url
) -> None:
    with Session(engine) as session:
        for user_id in (two_owners.alice, two_owners.bob):
            account = session.get(User, user_id)
            account.is_superuser = True
            session.add(account)
        session.commit()

    async with _two_sessions(postgres_test_url) as (first, second):
        alice = await _as(first, two_owners.alice)
        bob = await _as(second, two_owners.bob)
        await first.commit()
        await second.commit()

        results = await asyncio.gather(
            users.update_user(
                db=first,
                user_id=two_owners.bob,
                user_in=UserUpdate(is_superuser=False),
                current_user=alice,
            ),
            users.update_user(
                db=second,
                user_id=two_owners.alice,
                user_in=UserUpdate(is_superuser=False),
                current_user=bob,
            ),
            return_exceptions=True,
        )

    assert _outcomes(results, users.LAST_SUPERUSER_DETAIL) == ["done", "refused 409"]
    assert len(_superusers(engine)) == 1
