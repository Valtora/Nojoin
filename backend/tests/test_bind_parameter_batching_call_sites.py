"""The call sites the bind-limit audit found, and what keeps them batched.

Each of these builds an ``IN`` clause from a list nothing in the code bounds:
a provider's calendar sync payload, and a client-supplied list of recording
ids. One id is one bind parameter, so an unbatched statement fails on a large
enough list. The batch tag endpoint additionally caps what a client may ask
for, which is a resource bound rather than a correctness one.

SQLite accepts more variables than Postgres does, so these cannot assert the
error. They assert the batching instead, by counting the statements issued.
The real ceiling is pinned in test_bind_parameter_limits_postgres.py.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.api.v1.endpoints.tags import (
    MAX_BATCH_RECORDING_IDS,
    BatchTagOperation,
)
from backend.services.calendar_service.persistence import (
    _apply_incremental_calendar_events,
)
from backend.services.recording_identity_service import get_recordings_by_public_ids
from backend.tests.sqlite_schemas import RECORDINGS_SCHEMA
from backend.utils.db_batching import MAX_BIND_PARAMS

OVERSIZED_ID_COUNT = MAX_BIND_PARAMS + 1_000

CALENDAR_EVENTS_SCHEMA = """
CREATE TABLE calendar_events (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    calendar_id INTEGER NOT NULL,
    provider_event_id VARCHAR(255) NOT NULL,
    title VARCHAR(255),
    description TEXT,
    location VARCHAR(255),
    starts_at DATETIME,
    ends_at DATETIME,
    is_all_day BOOLEAN,
    organiser_email VARCHAR(255),
    attendees JSON,
    join_url VARCHAR(1024),
    status VARCHAR(32),
    raw_payload JSON
)
"""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _count_selects(engine, table_name: str) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, *_):
        if table_name in statement:
            statements.append(statement)

    return statements


@pytest.fixture
async def session_for(tmp_path):
    engines = []

    async def _make(schema_sql: str) -> tuple[AsyncSession, object]:
        db_path = tmp_path / f"bind-batching-{len(engines)}.sqlite3"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
        engines.append(engine)
        async with engine.begin() as connection:
            for statement in schema_sql.strip().split(";\n"):
                if statement.strip():
                    await connection.execute(text(statement))
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        return maker(), engine

    yield _make

    for engine in engines:
        await engine.dispose()


@pytest.mark.anyio
async def test_recording_lookup_batches_a_client_supplied_id_list(session_for):
    """A batch tag or MCP search scope is whatever the client sent."""
    session, engine = await session_for(RECORDINGS_SCHEMA)
    statements = _count_selects(engine, "recordings")

    public_ids = [f"rec-{index:06d}" for index in range(OVERSIZED_ID_COUNT)]
    found = await get_recordings_by_public_ids(session, public_ids, user_id=1)

    assert found == []
    assert len(statements) > 1, "expected the lookup to be split across statements"
    await session.close()


@pytest.mark.anyio
async def test_recording_lookup_issues_one_statement_when_it_fits(session_for):
    session, engine = await session_for(RECORDINGS_SCHEMA)
    statements = _count_selects(engine, "recordings")

    await get_recordings_by_public_ids(session, ["rec-1", "rec-2"], user_id=1)

    assert len(statements) == 1
    await session.close()


@pytest.mark.anyio
async def test_calendar_sync_batches_provider_supplied_deletions(session_for):
    """`deleted_remote_ids` is sized by the provider, over a 25-month window."""
    session, engine = await session_for(CALENDAR_EVENTS_SCHEMA)
    statements = _count_selects(engine, "calendar_events")

    deleted_remote_ids = [f"evt-{index:06d}" for index in range(OVERSIZED_ID_COUNT)]
    await _apply_incremental_calendar_events(
        session,
        calendar_id=1,
        provider_events=[],
        deleted_remote_ids=deleted_remote_ids,
    )

    assert len(statements) > 1, "expected the delete to be split across statements"
    await session.close()


def test_batch_tag_operation_accepts_a_list_at_the_cap():
    payload = BatchTagOperation(
        recording_ids=[f"rec-{index}" for index in range(MAX_BATCH_RECORDING_IDS)],
        tag_name="quarterly",
    )

    assert len(payload.recording_ids) == MAX_BATCH_RECORDING_IDS


def test_batch_tag_operation_rejects_a_list_over_the_cap():
    """An oversized batch is a 422, not an unbounded pile of work.

    Batching the lookup made the large case correct rather than a 500, so this
    is a resource bound rather than a correctness fix: every id costs a row
    load and a link write, and one request should not be able to ask for an
    unbounded number of them.
    """
    with pytest.raises(ValidationError):
        BatchTagOperation(
            recording_ids=[
                f"rec-{index}" for index in range(MAX_BATCH_RECORDING_IDS + 1)
            ],
            tag_name="quarterly",
        )
