"""Chunk registration on a real PostgreSQL (issue #417).

SQLite has no advisory locks and no row-level locking, so the property that
fixed the stalled uploads can only be shown here: registering a segment holds a
lock that serialises chunk bookkeeping for the recording without locking the
recording row, so a segment upload writing that row is never queued behind it.

Each test skips unless NOJOIN_TEST_POSTGRES_URL names a server. CI sets it for
the backend suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.tests.test_bind_parameter_limits_postgres import MANIFEST_TABLE_SCHEMA
from backend.tests.test_recording_audio_registration import (  # noqa: F401 -- fixture
    RECORDING_ID,
    _expected_placement,
    _spec_rows,
    _write_wav,
    window_geometry,
)
from backend.utils.recording_audio_registration import (
    ChunkStream,
    register_recording_audio_chunk,
)
from backend.utils.recording_audio_sync import (
    RECORDING_AUDIO_LOCK_NAMESPACE,
    TRANSCODE_FAILED_SUFFIX,
)

SCHEMA = "chunk_cursor_test"

TABLES = [
    """
    CREATE TABLE recordings (
        id BIGINT PRIMARY KEY,
        last_activity_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE recording_audio_chunks (
        id BIGSERIAL PRIMARY KEY,
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        recording_id BIGINT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        sequence_no BIGINT NOT NULL,
        source_kind VARCHAR NOT NULL,
        absolute_start_ms BIGINT NOT NULL,
        absolute_end_ms BIGINT NOT NULL,
        duration_ms BIGINT NOT NULL,
        sample_rate_hz BIGINT NOT NULL,
        channel_count BIGINT NOT NULL,
        byte_size BIGINT NOT NULL,
        pcm_data_offset BIGINT,
        pcm_byte_size BIGINT,
        sha256 VARCHAR(128) NOT NULL,
        storage_path TEXT NOT NULL,
        upload_status VARCHAR NOT NULL,
        idempotency_key VARCHAR(255),
        received_at TIMESTAMP NOT NULL,
        cleanup_eligible_at TIMESTAMP,
        UNIQUE (recording_id, sequence_no),
        UNIQUE (recording_id, idempotency_key)
    )
    """,
    MANIFEST_TABLE_SCHEMA,
    """
    CREATE TABLE recording_audio_chunk_cursors (
        id BIGSERIAL PRIMARY KEY,
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        recording_id BIGINT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        source_kind VARCHAR(32) NOT NULL,
        through_sequence BIGINT NOT NULL,
        end_ms BIGINT NOT NULL,
        group_start_sequence BIGINT NOT NULL,
        group_start_ms BIGINT NOT NULL,
        group_first_window_index BIGINT NOT NULL,
        target_window_ms BIGINT NOT NULL,
        hop_ms BIGINT NOT NULL,
        sealed BOOLEAN NOT NULL,
        UNIQUE (recording_id, source_kind)
    )
    """,
]


@pytest.fixture
def pg_engine(postgres_test_url: str):
    admin = create_engine(postgres_test_url)
    with admin.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    engine = create_engine(
        postgres_test_url, connect_args={"options": f"-csearch_path={SCHEMA}"}
    )
    try:
        with engine.begin() as connection:
            for statement in TABLES:
                connection.execute(text(statement))
            connection.execute(
                text("INSERT INTO recordings (id) VALUES (:id)"), {"id": RECORDING_ID}
            )
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        admin.dispose()


def _register(engine, upload_dir: Path, sequence: int) -> None:
    path = upload_dir / f"{sequence}.wav"
    _write_wav(path, 2_000)
    with Session(engine) as session:
        register_recording_audio_chunk(
            session,
            ChunkStream(RECORDING_ID, "browser", upload_dir),
            sequence=sequence,
            storage_path=path,
        )
        session.commit()


def test_registering_a_segment_does_not_lock_the_recording_row(
    pg_engine, tmp_path: Path
) -> None:
    path = tmp_path / "0.wav"
    _write_wav(path, 2_000)

    with Session(pg_engine) as registering:
        register_recording_audio_chunk(
            registering,
            ChunkStream(RECORDING_ID, "browser", tmp_path),
            sequence=0,
            storage_path=path,
        )

        # A segment upload's activity write goes straight through while the
        # registration transaction is still open.
        with pg_engine.connect() as upload:
            upload.execute(text("SET lock_timeout = '1s'"))
            upload.execute(
                text("UPDATE recordings SET last_activity_at = now() WHERE id = :id"),
                {"id": RECORDING_ID},
            )
            upload.commit()

        # Another registration for the same recording waits its turn.
        with pg_engine.connect() as competing:
            competing.execute(text("SET lock_timeout = '200ms'"))
            with pytest.raises(OperationalError, match="lock timeout"):
                competing.execute(
                    text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
                    {"namespace": RECORDING_AUDIO_LOCK_NAMESPACE, "key": RECORDING_ID},
                )
        registering.commit()


def test_registration_matches_the_full_builder_on_postgres(
    pg_engine, tmp_path: Path
) -> None:
    order = [0, 2, 1, 3, 4, 6, 5, 7, 9, 10, 11, 12, 13, 15, 14, 16, 17, 18, 19]
    (tmp_path / f"8{TRANSCODE_FAILED_SUFFIX}").write_text("boom\n")
    for sequence in order:
        _register(pg_engine, tmp_path, sequence)

    with pg_engine.connect() as connection:
        manifests = [
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                int(row[5]),
                int(row[6]),
            )
            for row in connection.execute(
                text(
                    "SELECT window_index, window_start_ms, window_end_ms,"
                    " chunk_start_sequence, chunk_end_sequence, is_partial,"
                    " is_sealed FROM recording_audio_window_manifests"
                    " ORDER BY window_index"
                )
            )
        ]
    expected = _expected_placement({sequence: 2_000 for sequence in order}, {8})
    assert manifests == _spec_rows(expected)
