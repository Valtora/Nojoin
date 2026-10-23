"""An installation with two Owners reads one of them, the first, everywhere (#639).

Each reader of the Owner once ran its own query, and three expected exactly one
row: with a second Owner the API failed at startup, every settings read failed,
and so did every AI request. They now share ``primary_owner``, which takes the
Owner with the lowest id, active or not.
"""

from __future__ import annotations

import contextlib
import importlib
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, select

from backend import main
from backend.api.v1.endpoints import settings as settings_endpoint
from backend.models.user import User
from backend.tests.sqlite_schemas import USERS_SCHEMA
from backend.utils import llm_config
from backend.utils.primary_owner import get_primary_owner, get_primary_owner_async

importlib.import_module("backend.models.registry")

# (role, active, settings) in id order: a member first, so the first Owner is
# not simply the first account, and that Owner inactive, which does not matter.
ACCOUNTS = [
    ("user", True, {"llm_provider": "member-choice"}),
    ("owner", False, {"llm_provider": "first-owner-choice"}),
    ("owner", True, {"llm_provider": "second-owner-choice"}),
]
FIRST_OWNER_ID = 2


def _add_accounts(session) -> None:
    for index, (role, active, settings) in enumerate(ACCOUNTS):
        session.add(
            User(
                username=f"account{index}.example",
                hashed_password="x",
                role=role,
                is_active=active,
                settings=dict(settings),
            )
        )


@contextlib.asynccontextmanager
async def _async_accounts() -> AsyncIterator[sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(text(USERS_SCHEMA))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            _add_accounts(session)
            await session.commit()
        yield maker
    finally:
        await engine.dispose()


@contextlib.contextmanager
def _sync_accounts() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as conn:
        conn.execute(text(USERS_SCHEMA))
    try:
        with Session(engine) as session:
            _add_accounts(session)
            session.commit()
            yield session
    finally:
        engine.dispose()


@pytest.fixture
def owner_settings_seen(monkeypatch) -> list:
    """The Owner settings each AI reader was given."""
    seen: list = []

    def record_ai_fallback(merged, owner_settings):
        seen.append(owner_settings)

    def record_provider_override(config, owner_settings, purpose):
        seen.append(owner_settings)
        return config

    monkeypatch.setattr(
        settings_endpoint, "_apply_install_wide_ai_owner_fallback", record_ai_fallback
    )
    monkeypatch.setattr(
        llm_config, "_apply_owner_provider_override", record_provider_override
    )
    return seen


FIRST_OWNER_SETTINGS = {"llm_provider": "first-owner-choice"}


@pytest.mark.anyio
async def test_the_first_owner_by_id_is_the_primary_owner() -> None:
    async with _async_accounts() as maker, maker() as session:
        owner = await get_primary_owner_async(session)

    assert owner is not None and owner.id == FIRST_OWNER_ID

    with _sync_accounts() as session:
        owner = get_primary_owner(session)
        assert owner is not None and owner.id == FIRST_OWNER_ID


@pytest.mark.anyio
async def test_startup_leaves_two_owners_alone(monkeypatch) -> None:
    async with _async_accounts() as maker:
        monkeypatch.setattr(main, "async_session_maker", maker)

        await main.ensure_owner_exists()

        async with maker() as session:
            roles = (
                await session.execute(select(User.role).order_by(User.id))
            ).scalars()
            assert list(roles) == ["user", "owner", "owner"]


@pytest.mark.anyio
async def test_settings_read_the_first_owner(owner_settings_seen) -> None:
    async with _async_accounts() as maker, maker() as session:
        await settings_endpoint._merge_settings({}, session)

    assert owner_settings_seen == [FIRST_OWNER_SETTINGS]


@pytest.mark.anyio
async def test_both_ai_resolvers_read_the_first_owner(owner_settings_seen) -> None:
    async with _async_accounts() as maker, maker() as session:
        await llm_config.resolve_llm_config_async(session)
    with _sync_accounts() as session:
        llm_config.resolve_llm_config(session)

    assert owner_settings_seen == [FIRST_OWNER_SETTINGS, FIRST_OWNER_SETTINGS]
