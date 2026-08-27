"""Bind parameter ceiling tests that need a real PostgreSQL.

These are the tests SQLite cannot stand in for. The ceiling that broke finalize
on a two-hour recording belongs to the PostgreSQL wire protocol and to asyncpg's
use of it; SQLite builds routinely accept far more variables than Postgres will,
so a SQLite test passes on exactly the statement that fails in production.

Each test skips unless NOJOIN_TEST_POSTGRES_URL names a server. CI sets it for
the backend suite.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import select

from backend.models.pipeline import RecordingAudioWindowManifest
from backend.tests.test_recording_audio_window_manifest_upsert import (
    RECORDING_ID,
    _payload,
)
from backend.utils.db_batching import MAX_BIND_PARAMS, bind_batches
from backend.utils.recording_audio_sync import (
    _upsert_window_manifests,
    _window_manifest_upsert_statement,
)

# A two-hour meeting at the default 20s window and 5s hop produces roughly 1440
# windows. At 25 bound columns each that is ~36000 parameters, over the 32767
# the protocol allows, which is the exact shape of the production failure.
TWO_HOUR_WINDOW_COUNT = 1600

MANIFEST_TABLE_SCHEMA = """
CREATE TABLE recording_audio_window_manifests (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id BIGINT NOT NULL,
    window_index BIGINT NOT NULL,
    source_kind VARCHAR(32) NOT NULL,
    target_window_ms BIGINT NOT NULL,
    hop_ms BIGINT NOT NULL,
    window_start_ms BIGINT NOT NULL,
    window_end_ms BIGINT NOT NULL,
    chunk_start_sequence BIGINT NOT NULL,
    chunk_end_sequence BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL,
    asr_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    asr_processing_run_id BIGINT,
    asr_last_error TEXT,
    diarization_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    diarization_processing_run_id BIGINT,
    diarization_config_hash VARCHAR(255),
    diarization_window_result_id BIGINT,
    diarization_last_error TEXT,
    is_partial BOOLEAN NOT NULL,
    is_sealed BOOLEAN NOT NULL,
    processing_run_id BIGINT,
    last_error TEXT,
    CONSTRAINT uq_recording_audio_window_manifests_recording_window
        UNIQUE (recording_id, window_index)
)
"""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def pg_session(postgres_test_url: str):
    """An asyncpg-backed session over a throwaway schema.

    asyncpg is the point: it always uses the extended query protocol, so the
    parameter limit applies. The Celery workers reach the same code through
    psycopg2, which interpolates client-side and never hits it, which is why
    the worker path stayed green while the API path failed.
    """
    async_url = postgres_test_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_url, future=True)
    schema = "bind_limit_test"

    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await connection.execute(text(f"SET search_path TO {schema}"))
            await connection.execute(text(MANIFEST_TABLE_SCHEMA))

        async with AsyncSession(engine) as session:
            await session.execute(text(f"SET search_path TO {schema}"))
            yield session

        async with engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    finally:
        await engine.dispose()


def _payloads(count: int) -> list[dict[str, Any]]:
    return [_payload(index) for index in range(count)]


@pytest.mark.anyio
async def test_unbatched_upsert_overflows_the_postgres_bind_limit(pg_session):
    """The ceiling is real, and this is the statement that hit it.

    Pinning the failure keeps the batching honest: if a future change made the
    single-statement form work, the batching above it would be dead weight and
    this test would say so.
    """
    payloads = _payloads(TWO_HOUR_WINDOW_COUNT)
    assert len(payloads) * len(payloads[0]) > 32767

    def _run_unbatched(session):
        session.execute(
            _window_manifest_upsert_statement(
                session,
                RecordingAudioWindowManifest.__table__,
                payloads,
            )
        )

    with pytest.raises(InterfaceError) as excinfo:
        await pg_session.run_sync(_run_unbatched)

    assert "the number of query arguments cannot exceed 32767" in str(excinfo.value)


@pytest.mark.anyio
async def test_upsert_persists_a_two_hour_recording_on_postgres(pg_session):
    """The production finalize path: run_sync over the async session."""
    payloads = _payloads(TWO_HOUR_WINDOW_COUNT)

    # Read the indexes inside run_sync: the returned rows belong to the sync
    # session, and touching their attributes afterwards is IO on the wrong
    # greenlet rather than anything to do with what this test asserts.
    def _upsert_and_read(session) -> list[int]:
        rows = _upsert_window_manifests(
            session,
            recording_id=RECORDING_ID,
            manifest_payloads=payloads,
        )
        return [int(row.window_index) for row in rows]

    window_indexes = await pg_session.run_sync(_upsert_and_read)
    await pg_session.commit()

    assert window_indexes == list(range(TWO_HOUR_WINDOW_COUNT))


@pytest.mark.anyio
async def test_upsert_is_idempotent_across_batches_on_postgres(pg_session):
    """A re-finalize must update in place, not duplicate across batch seams."""
    payloads = _payloads(TWO_HOUR_WINDOW_COUNT)

    for _ in range(2):
        await pg_session.run_sync(
            lambda session: _upsert_window_manifests(
                session,
                recording_id=RECORDING_ID,
                manifest_payloads=payloads,
            )
        )
        await pg_session.commit()

    total = await pg_session.execute(
        select(RecordingAudioWindowManifest.id).where(
            RecordingAudioWindowManifest.recording_id == RECORDING_ID
        )
    )
    assert len(list(total.scalars().all())) == TWO_HOUR_WINDOW_COUNT


@pytest.mark.anyio
async def test_large_in_clause_needs_batching_on_postgres(pg_session):
    """An IN clause binds one parameter per item and hits the same ceiling.

    This is the shape behind the calendar sync and the batch recording lookup,
    so it is worth pinning against a real server rather than reasoning about.
    """
    item_count = MAX_BIND_PARAMS + 5_000
    window_indexes = list(range(item_count))

    unbatched = select(RecordingAudioWindowManifest.id).where(
        RecordingAudioWindowManifest.window_index.in_(window_indexes)
    )
    with pytest.raises(InterfaceError):
        await pg_session.execute(unbatched)
    await pg_session.rollback()

    matched: list[int] = []
    for index_batch in bind_batches(window_indexes):
        result = await pg_session.execute(
            select(RecordingAudioWindowManifest.id).where(
                RecordingAudioWindowManifest.window_index.in_(index_batch)
            )
        )
        matched.extend(result.scalars().all())

    assert matched == []
