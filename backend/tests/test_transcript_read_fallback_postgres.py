"""A failed canonical read must not poison the caller's Postgres transaction.

``build_transcript_segments_for_read`` falls back to the stored projection when
the canonical read raises. On Postgres a failed statement aborts the whole
transaction, so unless the read runs inside a savepoint the caller's next
statement raises ``InFailedSqlTransaction`` and the fallback is never usable.
SQLite does not abort the transaction on error, so only a real server shows it.

Skips unless NOJOIN_TEST_POSTGRES_URL names a server. CI sets it for the
backend suite.
"""

from __future__ import annotations

import json
import logging

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

import backend.utils.canonical_pipeline.core as canonical_core
from backend.tests.test_canonical_transcript_phase1 import TRANSCRIPTS_SCHEMA

SCHEMA = "canonical_read_fallback_test"
RECORDING_ID = 1
PROJECTION = [
    {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "From the projection."}
]


@pytest.fixture
def pg_engine(postgres_test_url: str):
    engine = create_engine(
        postgres_test_url, connect_args={"options": f"-csearch_path={SCHEMA}"}
    )
    try:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
            # The shared schema is written for SQLite; Postgres needs real
            # timestamp types and will not default a BOOLEAN to 0.
            connection.execute(
                text(
                    TRANSCRIPTS_SCHEMA.replace("DATETIME", "TIMESTAMP").replace(
                        "BOOLEAN", "INTEGER"
                    )
                )
            )
            connection.execute(
                text(
                    "INSERT INTO transcripts (id, created_at, updated_at, recording_id, "
                    "segments, notes_status, transcript_status) VALUES (1, now(), now(), "
                    ":recording_id, :segments, 'completed', 'completed')"
                ),
                {"recording_id": RECORDING_ID, "segments": json.dumps(PROJECTION)},
            )
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        engine.dispose()


def test_database_error_in_canonical_read_leaves_the_transaction_usable(
    pg_engine, monkeypatch, caplog
):
    def _failing_read(session, recording_id):
        session.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(canonical_core, "serialize_canonical_utterances", _failing_read)

    with Session(pg_engine) as session:
        # Work the caller did before the read, in the same transaction.
        session.execute(text("UPDATE transcripts SET notes = 'kept' WHERE id = 1"))

        with caplog.at_level(logging.ERROR):
            segments = canonical_core.build_transcript_segments_for_read(
                session, RECORDING_ID
            )

        assert [segment["text"] for segment in segments] == ["From the projection."]
        # The caller can go on using its session and commit its own work.
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        session.commit()

    with pg_engine.connect() as connection:
        notes = connection.execute(text("SELECT notes FROM transcripts")).scalar_one()
    assert notes == "kept"
    assert any(
        "Canonical transcript read failed for recording 1" in record.getMessage()
        and record.exc_info is not None
        for record in caplog.records
    )
