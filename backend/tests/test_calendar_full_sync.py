"""A full calendar sync must leave recordings linked to their events.

The full path runs whenever a sync cannot go incremental: the monthly shift of
the sync window, a missing cursor, a Google cursor reset, and the Microsoft
partial-occurrence repair. It used to delete every stored event and insert the
listing again, and ``recordings.calendar_event_id`` is ``ON DELETE SET NULL``,
so each run unlinked every recording. These tests enforce SQLite foreign keys
so that the production ``SET NULL`` fires here too.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.services.calendar_service import sync as calendar_sync
from backend.services.calendar_service.models_dto import (
    ProviderCalendarRecord,
    ProviderEventRecord,
    ProviderEventSyncResult,
    ProviderRuntimeConfig,
)
from backend.services.calendar_service.persistence import _reconcile_calendar_events
from backend.tests.sqlite_schemas import (
    CALENDAR_CONNECTIONS_SCHEMA,
    CALENDAR_EVENTS_SCHEMA,
    CALENDAR_SOURCES_SCHEMA,
)

# The window a sync in November 2026 builds, and the one October's built.
WINDOW_START = datetime(2025, 11, 1)
WINDOW_END = datetime(2027, 12, 1)
PREVIOUS_WINDOW_START = datetime(2025, 10, 1)
PREVIOUS_WINDOW_END = datetime(2027, 11, 1)

CALENDAR_ID = 1
CONNECTION_ID = 1
REMOTE_CALENDAR_ID = "primary"

# The sync reads only the link column, so the table carries little else. Its
# foreign key is the production one.
RECORDINGS_LINK_SCHEMA = """
CREATE TABLE recordings (
    id INTEGER PRIMARY KEY,
    calendar_event_id INTEGER REFERENCES calendar_events(id) ON DELETE SET NULL
)
"""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'calendar-full-sync.sqlite3'}",
        future=True,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    yield engine
    await engine.dispose()


async def _seed_calendar(
    engine,
    *,
    provider: str = "google",
    sync_cursor: str | None = None,
    window: tuple[datetime, datetime] | None = None,
) -> None:
    now = datetime(2026, 10, 1)
    async with engine.begin() as connection:
        for schema in (
            CALENDAR_CONNECTIONS_SCHEMA,
            CALENDAR_SOURCES_SCHEMA,
            CALENDAR_EVENTS_SCHEMA,
            RECORDINGS_LINK_SCHEMA,
        ):
            await connection.execute(text(schema))
        await connection.execute(
            text(
                """
                INSERT INTO calendar_connections (
                    id, created_at, updated_at, user_id, provider,
                    provider_account_id, granted_scopes, sync_status
                ) VALUES (:id, :now, :now, 1, :provider, 'acct', '[]', 'idle')
                """
            ),
            {"id": CONNECTION_ID, "now": now, "provider": provider},
        )
        await connection.execute(
            text(
                """
                INSERT INTO calendar_sources (
                    id, created_at, updated_at, connection_id,
                    provider_calendar_id, name, is_primary, is_read_only,
                    is_selected, sync_cursor, sync_window_start, sync_window_end
                ) VALUES (
                    :id, :now, :now, :connection_id, :remote_id, 'Work', 1, 0,
                    1, :sync_cursor, :window_start, :window_end
                )
                """
            ),
            {
                "id": CALENDAR_ID,
                "now": now,
                "connection_id": CONNECTION_ID,
                "remote_id": REMOTE_CALENDAR_ID,
                "sync_cursor": sync_cursor,
                "window_start": window[0] if window else None,
                "window_end": window[1] if window else None,
            },
        )


async def _insert_event(  # noqa: PLR0913 - one keyword per stored column
    engine,
    *,
    event_id: int,
    remote_id: str,
    title: str = "Standup",
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO calendar_events (
                    id, created_at, updated_at, calendar_id, provider_event_id,
                    title, status, is_all_day, starts_at, ends_at, start_date,
                    end_date
                ) VALUES (
                    :id, :now, :now, :calendar_id, :remote_id, :title,
                    'confirmed', :is_all_day, :starts_at, :ends_at, :start_date,
                    :end_date
                )
                """
            ),
            {
                "id": event_id,
                "now": datetime(2026, 10, 1),
                "calendar_id": CALENDAR_ID,
                "remote_id": remote_id,
                "title": title,
                "is_all_day": start_date is not None,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "start_date": start_date,
                "end_date": end_date,
            },
        )


async def _link_recording(engine, *, recording_id: int, event_id: int) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO recordings (id, calendar_event_id) VALUES (:id, :event_id)"
            ),
            {"id": recording_id, "event_id": event_id},
        )


async def _recording_links(engine) -> dict[int, int | None]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT id, calendar_event_id FROM recordings")
        )
        return {row.id: row.calendar_event_id for row in rows}


async def _stored_events(engine) -> dict[str, tuple[int, str]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT id, provider_event_id, title FROM calendar_events")
        )
        return {row.provider_event_id: (row.id, row.title) for row in rows}


def _timed_event(
    remote_id: str, starts_at: datetime, *, title: str = "Standup"
) -> ProviderEventRecord:
    return ProviderEventRecord(
        remote_id=remote_id,
        title=title,
        status="confirmed",
        is_all_day=False,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        start_date=None,
        end_date=None,
    )


async def _reconcile(engine, provider_events: list[ProviderEventRecord]) -> None:
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await _reconcile_calendar_events(
            session, CALENDAR_ID, provider_events, WINDOW_START
        )
        await session.commit()


@pytest.mark.anyio
async def test_full_sync_updates_events_in_place_and_keeps_links(engine) -> None:
    await _seed_calendar(engine)
    standup_at = datetime(2026, 3, 2, 9, 0)
    await _insert_event(
        engine,
        event_id=7,
        remote_id="evt-standup",
        starts_at=standup_at,
        ends_at=standup_at + timedelta(minutes=30),
    )
    await _link_recording(engine, recording_id=1, event_id=7)

    await _reconcile(
        engine,
        [
            _timed_event("evt-standup", standup_at, title="Standup (moved room)"),
            _timed_event("evt-planning", datetime(2026, 3, 3, 14, 0), title="Planning"),
        ],
    )

    events = await _stored_events(engine)
    assert events["evt-standup"] == (7, "Standup (moved room)")
    assert events["evt-planning"][1] == "Planning"
    assert await _recording_links(engine) == {1: 7}


@pytest.mark.anyio
async def test_full_sync_deletes_an_event_the_provider_no_longer_returns(
    engine,
) -> None:
    """Inside the window a missing event was deleted upstream.

    The incremental path deletes it, unlinking any recording, and the full path
    does the same so that the two agree.
    """
    await _seed_calendar(engine)
    for event_id, starts_at in (
        (7, datetime(2026, 3, 2, 9)),
        (8, datetime(2026, 3, 3, 9)),
    ):
        await _insert_event(
            engine,
            event_id=event_id,
            remote_id=f"evt-{event_id}",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
        )
    await _link_recording(engine, recording_id=1, event_id=7)

    await _reconcile(engine, [])

    assert await _stored_events(engine) == {}
    assert await _recording_links(engine) == {1: None}


@pytest.mark.anyio
async def test_full_sync_keeps_a_linked_event_the_window_moved_past(engine) -> None:
    await _seed_calendar(engine)
    # Linked, and ended before the window: kept.
    await _insert_event(
        engine,
        event_id=7,
        remote_id="evt-aged-linked",
        starts_at=datetime(2025, 10, 15, 9),
        ends_at=datetime(2025, 10, 15, 10),
    )
    # Unlinked, and ended before the window: pruned.
    await _insert_event(
        engine,
        event_id=8,
        remote_id="evt-aged-unlinked",
        starts_at=datetime(2025, 10, 20, 9),
        ends_at=datetime(2025, 10, 20, 10),
    )
    # All day on the window's eve. The end date is exclusive, so it ended as
    # the window began: kept.
    await _insert_event(
        engine,
        event_id=9,
        remote_id="evt-aged-all-day",
        start_date=date(2025, 10, 31),
        end_date=date(2025, 11, 1),
    )
    # Linked, but running past the window start, so the provider would have
    # returned it had it still existed: deleted.
    await _insert_event(
        engine,
        event_id=10,
        remote_id="evt-straddling",
        starts_at=datetime(2025, 10, 31, 23, 30),
        ends_at=datetime(2025, 11, 1, 0, 30),
    )
    await _link_recording(engine, recording_id=1, event_id=7)
    await _link_recording(engine, recording_id=2, event_id=9)
    await _link_recording(engine, recording_id=3, event_id=10)

    await _reconcile(engine, [])

    assert set(await _stored_events(engine)) == {"evt-aged-linked", "evt-aged-all-day"}
    assert await _recording_links(engine) == {1: 7, 2: 9, 3: None}


@pytest.mark.anyio
async def test_full_sync_batches_the_ids_it_checks_and_deletes(
    engine, monkeypatch
) -> None:
    """Every stored event can go stale at once, so both lists are unbounded.

    The budget is shrunk rather than seeding past the real one. Counting the
    statements is what shows the batching, as in
    test_bind_parameter_batching_call_sites.py.
    """
    monkeypatch.setattr("backend.utils.db_batching.MAX_BIND_PARAMS", 2)
    await _seed_calendar(engine)
    for event_id in range(1, 6):
        starts_at = datetime(2025, 10, event_id, 9)
        await _insert_event(
            engine,
            event_id=event_id,
            remote_id=f"evt-{event_id}",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
        )

    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(_conn, _cursor, statement, *_):
        statements.append(statement)

    await _reconcile(engine, [])

    assert await _stored_events(engine) == {}
    link_lookups = [s for s in statements if "FROM recordings" in s]
    deletes = [s for s in statements if s.startswith("DELETE FROM calendar_events")]
    assert len(link_lookups) == 3
    assert len(deletes) == 3


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_window_shift_keeps_recording_links(
    engine, monkeypatch, provider
) -> None:
    """The monthly window shift sends every calendar down the full path."""
    await _seed_calendar(
        engine,
        provider=provider,
        sync_cursor="cursor-october",
        window=(PREVIOUS_WINDOW_START, PREVIOUS_WINDOW_END),
    )
    standup_at = datetime(2026, 11, 2, 9, 0)
    await _insert_event(
        engine,
        event_id=7,
        remote_id="evt-standup",
        starts_at=standup_at,
        ends_at=standup_at + timedelta(minutes=30),
    )
    await _link_recording(engine, recording_id=1, event_id=7)

    provider_events = [_timed_event("evt-standup", standup_at)]
    full_listings: list[str] = []

    async def _runtime_config(_db, provider_name):
        return ProviderRuntimeConfig(
            provider=provider_name,
            client_id="client",
            client_secret="secret",
            tenant_id=None,
            enabled=True,
            source="test",
        )

    async def _access_token(*_args):
        return "token"

    async def _calendars(_access_token):
        return [
            ProviderCalendarRecord(
                remote_id=REMOTE_CALENDAR_ID,
                name="Work",
                description=None,
                time_zone="UTC",
                colour=None,
                is_primary=True,
                is_read_only=False,
            )
        ]

    async def _google_events(*_args, sync_cursor):
        assert sync_cursor is None, "a shifted window must not go incremental"
        full_listings.append("google")
        return ProviderEventSyncResult(
            events=provider_events, deleted_remote_ids=[], cursor="cursor-november"
        )

    async def _microsoft_events(*_args):
        full_listings.append("microsoft")
        return provider_events

    async def _microsoft_cursor(*_args):
        return "cursor-november"

    monkeypatch.setattr(calendar_sync, "get_provider_runtime_config", _runtime_config)
    monkeypatch.setattr(
        calendar_sync, "_get_access_token_for_connection", _access_token
    )
    monkeypatch.setattr(calendar_sync, "_list_google_calendars", _calendars)
    monkeypatch.setattr(calendar_sync, "_list_microsoft_calendars", _calendars)
    monkeypatch.setattr(calendar_sync, "_sync_google_events", _google_events)
    monkeypatch.setattr(calendar_sync, "_list_microsoft_events", _microsoft_events)
    monkeypatch.setattr(calendar_sync, "_get_microsoft_delta_cursor", _microsoft_cursor)
    monkeypatch.setattr(
        calendar_sync, "_build_sync_window", lambda: (WINDOW_START, WINDOW_END)
    )

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await calendar_sync.sync_connection_in_session(session, CONNECTION_ID)

    assert full_listings == [provider]
    async with engine.connect() as connection:
        status = (
            await connection.execute(
                text("SELECT sync_status FROM calendar_connections")
            )
        ).scalar_one()
        cursor = (
            await connection.execute(text("SELECT sync_cursor FROM calendar_sources"))
        ).scalar_one()
    assert status == "success"
    assert cursor == "cursor-november"
    assert (await _stored_events(engine))["evt-standup"][0] == 7
    assert await _recording_links(engine) == {1: 7}
