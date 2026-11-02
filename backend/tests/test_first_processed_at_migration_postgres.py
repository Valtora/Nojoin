"""The first_processed_at backfill stamps every meeting that may have finished.

Discard deletes outright what this column leaves empty, so the backfill errs
towards stamping: it leaves only a capture or import still uploading or
paused, and a first pass whose transcript is the capture's own or missing.

Runs the real Alembic chain on a throwaway database, from the revision before
it. Skips unless NOJOIN_TEST_POSTGRES_URL names a server; CI sets it for the
backend suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.engine import make_url

from backend.tests.postgres_setup import migrate_empty_database

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "f2a6e1d9c735"
FIRST_PROCESSED = "53b4bc17207f"

# id, status, client status, transcript status (None: no transcript), stamped
CASES = [
    (1, "UPLOADING", "RECORDING", "processing", False),  # a live capture
    (2, "PAUSED", "PAUSED", "processing", False),  # a paused capture
    (3, "UPLOADING", None, None, False),  # an import still arriving
    (4, "QUEUED", "IDLE", "processing", False),  # a stopped capture, first pass
    (5, "PROCESSING", "IDLE", None, False),  # an import's first pass
    (6, "PROCESSED", "IDLE", "completed", True),
    (7, "PROCESSING", "IDLE", "completed", True),  # notes regenerating
    (8, "QUEUED", "IDLE", "pending", True),  # a reprocess
    (9, "ERROR", "IDLE", "error", True),
    (10, "ERROR", None, None, True),
    (11, "CANCELLED", None, None, True),
]


def _alembic(database_url: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


@pytest.fixture
def scratch_database(postgres_test_url: str) -> Iterator[str]:
    admin_url = (
        make_url(postgres_test_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    name = f"nojoin_first_processed_{uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield (
            make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        )
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _seed(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
        " is_active, is_superuser, role, force_password_change)"
        " VALUES (1, now(), now(), 'alice', 'x', true, true, 'owner', false)"
    )
    for recording_id, status, client_status, transcript_status, _ in CASES:
        conn.execute(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
            " meeting_uid, audio_path, status, client_status, upload_progress,"
            " processing_progress, is_archived, is_deleted, user_id,"
            " processing_completed_at)"
            " VALUES (%s, now(), '2026-10-01 09:00:00', %s, %s, %s, %s, %s, %s,"
            " 0, 0, false, false, 1, %s)",
            (
                recording_id,
                f"Meeting {recording_id}",
                f"public-{recording_id}",
                f"uid-{recording_id}",
                f"/recordings/{recording_id}.wav",
                status,
                client_status,
                "2026-10-01 10:00:00" if recording_id == 6 else None,
            ),
        )
        if transcript_status is not None:
            conn.execute(
                "INSERT INTO transcripts (created_at, updated_at, recording_id,"
                " segments, speaker_name_suggestions, notes_status,"
                " transcript_status, meeting_edge_status, notes_stale_documents,"
                " analytics_status, analytics_ai_status)"
                " VALUES (now(), now(), %s, '[]'::jsonb, '[]'::jsonb, 'pending', %s,"
                " 'idle', false, 'pending', 'pending')",
                (recording_id, transcript_status),
            )


def test_the_backfill_stamps_every_meeting_that_may_have_finished(
    scratch_database: str,
) -> None:
    migrate_empty_database(scratch_database, BEFORE)
    with psycopg.connect(scratch_database) as conn:
        _seed(conn)

    _alembic(scratch_database, "upgrade", FIRST_PROCESSED)

    with psycopg.connect(scratch_database) as conn:
        stamped = dict(
            conn.execute(
                "SELECT id, first_processed_at FROM recordings ORDER BY id"
            ).fetchall()
        )

    assert {
        recording_id: stamped[recording_id] is not None for recording_id, *_ in CASES
    } == {recording_id: expected for recording_id, *_, expected in CASES}
    # Stamped from the last completion where there is one, else the last update.
    assert str(stamped[6]) == "2026-10-01 10:00:00"
    assert str(stamped[7]) == "2026-10-01 09:00:00"

    _alembic(scratch_database, "downgrade", BEFORE)
    _alembic(scratch_database, "upgrade", FIRST_PROCESSED)
