"""The gist migration leaves every existing recording on automatic titles.

Walks the real Alembic revision up, down, and up on a disposable PostgreSQL
database. Skips when NOJOIN_TEST_POSTGRES_URL is not set.
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
GIST = "ca93f15bd027"
OLD_NAMES = {1: "Mike's launch review", 2: "Réunion — planning"}


def _alembic(database_url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def scratch_database(postgres_test_url: str) -> Iterator[str]:
    """Keep migration rollback independent from other tests and their schemas."""
    admin_url = make_url(postgres_test_url).set(drivername="postgresql")
    name = f"nojoin_recording_gist_{uuid4().hex[:12]}"
    with psycopg.connect(
        admin_url.render_as_string(hide_password=False), autocommit=True
    ) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield admin_url.set(database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(
            admin_url.render_as_string(hide_password=False), autocommit=True
        ) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _insert_recording(conn: psycopg.Connection, recording_id: int, name: str) -> None:
    conn.execute(
        "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
        " meeting_uid, audio_path, status, upload_progress, processing_progress,"
        " is_archived, is_deleted) VALUES (%s, now(), now(), %s, %s, %s, %s,"
        " 'PROCESSED', 0, 100, false, false)",
        (
            recording_id,
            name,
            f"pub-{recording_id}",
            f"uid-{recording_id}",
            f"/r{recording_id}.wav",
        ),
    )


def _brief_rows(conn: psycopg.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT id, name, title_user_edited, gist FROM recordings ORDER BY id"
    ).fetchall()


def test_existing_recordings_keep_automatic_titles_and_rollback_preserves_names(
    scratch_database: str,
) -> None:
    migrate_empty_database(scratch_database, BEFORE)
    with psycopg.connect(scratch_database) as conn:
        for recording_id, name in OLD_NAMES.items():
            _insert_recording(conn, recording_id, name)

    _alembic(scratch_database, "upgrade", GIST)
    with psycopg.connect(scratch_database) as conn:
        # Upstream retitled every recording at each final pass, so no existing
        # title is treated as a rename: reprocessing still replaces it.
        assert _brief_rows(conn) == [
            (row_id, name, False, None) for row_id, name in OLD_NAMES.items()
        ]
        conn.execute(
            "UPDATE recordings SET gist = 'The team reviewed the launch.',"
            " title_user_edited = true WHERE id = 1"
        )

    _alembic(scratch_database, "downgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        assert conn.execute(
            "SELECT id, name FROM recordings ORDER BY id"
        ).fetchall() == list(OLD_NAMES.items())

    _alembic(scratch_database, "upgrade", GIST)
    with psycopg.connect(scratch_database) as conn:
        assert _brief_rows(conn) == [
            (row_id, name, False, None) for row_id, name in OLD_NAMES.items()
        ]
