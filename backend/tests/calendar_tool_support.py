"""Shared setup for the MCP calendar tool tests.

Each test module runs on SQLite and, where ``NOJOIN_TEST_POSTGRES_URL`` names
a server, on Postgres too, whose ordering of nulls and dates is the one in
use. Seed helpers write one user's accounts, calendars and events; every name
in them is made up.
"""

from __future__ import annotations

import importlib
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import Session

from backend.core.db import driver_urls
from backend.core.security import MCP_READ_SCOPE, MCP_WRITE_SCOPE
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema
from backend.tests.sqlite_schemas import (
    CALENDAR_CONNECTIONS_SCHEMA,
    CALENDAR_EVENTS_SCHEMA,
    CALENDAR_SOURCES_SCHEMA,
    RECORDINGS_SCHEMA,
    USERS_SCHEMA,
)

ALICE = 1
BOB = 2
ALICE_ZONE = "Europe/London"
# A fixed "now" for the stale checks: tests pass it where it matters.
NOW = datetime(2026, 11, 28, 10, 0)


def users() -> list[User]:
    return [
        User(
            id=ALICE,
            username="alice",
            hashed_password="x",
            role="user",
            settings={"timezone": ALICE_ZONE},
        ),
        User(id=BOB, username="bob", hashed_password="x", role="user"),
    ]


async def sqlite_engine(extra_schemas: tuple[str, ...] = ()) -> AsyncEngine:
    built = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with built.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            CALENDAR_CONNECTIONS_SCHEMA,
            CALENDAR_SOURCES_SCHEMA,
            CALENDAR_EVENTS_SCHEMA,
            RECORDINGS_SCHEMA,
            *extra_schemas,
        ):
            await connection.execute(text(schema))
    async with AsyncSession(built) as session:
        session.add_all(users())
        await session.commit()
    return built


async def postgres_engine(url: str, schema: str) -> AsyncEngine:
    importlib.import_module("backend.models.registry")
    async_url, sync_url = driver_urls(url)
    sync_engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    with sync_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(sync_engine, schema)
    with Session(sync_engine) as session:
        session.add_all(users())
        session.commit()
    sync_engine.dispose()
    return create_async_engine(
        async_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )


def bind_identity(
    user: User,
    scopes: frozenset[str] = frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE}),
) -> None:
    """Set what the auth middleware would; call inside the async test."""
    current_mcp_user.set(user)
    current_mcp_scopes.set(scopes)


def add_account(  # noqa: PLR0913 - one keyword per stored column
    session: AsyncSession,
    *,
    connection_id: int,
    user_id: int = ALICE,
    provider: str = "google",
    email: str = "alice@example.com",
    sync_status: str = "success",
) -> None:
    session.add(
        CalendarConnection(
            id=connection_id,
            user_id=user_id,
            provider=provider,
            provider_account_id=f"{provider}-{user_id}-{connection_id}",
            email=email,
            display_name="Alice Example" if user_id == ALICE else "Bob Example",
            sync_status=sync_status,
            granted_scopes=[],
        )
    )


def add_calendar(  # noqa: PLR0913 - one keyword per stored column
    session: AsyncSession,
    *,
    calendar_id: int,
    connection_id: int,
    name: str = "Personal",
    selected: bool = True,
    primary: bool = False,
    read_only: bool = False,
    time_zone: str | None = None,
    last_synced_at: datetime | None = NOW - timedelta(minutes=5),
    window: tuple[datetime, datetime] | None = (
        datetime(2025, 11, 1),
        datetime(2027, 12, 1),
    ),
) -> None:
    session.add(
        CalendarSource(
            id=calendar_id,
            connection_id=connection_id,
            provider_calendar_id=f"cal-{calendar_id}",
            name=name,
            is_selected=selected,
            is_primary=primary,
            is_read_only=read_only,
            time_zone=time_zone,
            last_synced_at=last_synced_at,
            sync_window_start=window[0] if window else None,
            sync_window_end=window[1] if window else None,
        )
    )


def add_event(  # noqa: PLR0913 - one keyword per stored column
    session: AsyncSession,
    *,
    event_id: int,
    calendar_id: int,
    title: str = "Design review",
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    **fields: Any,
) -> CalendarEvent:
    event = CalendarEvent(
        id=event_id,
        calendar_id=calendar_id,
        provider_event_id=fields.pop("provider_event_id", f"evt-{event_id}"),
        title=title,
        status=fields.pop("status", "confirmed"),
        is_all_day=start_date is not None,
        starts_at=starts_at,
        ends_at=ends_at,
        start_date=start_date,
        end_date=end_date,
        **fields,
    )
    session.add(event)
    return event


def timed(day: date, hour: int, minutes: int = 60) -> dict[str, datetime]:
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
    return {"starts_at": start, "ends_at": start + timedelta(minutes=minutes)}
