"""The title_user_edited backfill keeps every name but a default one (issue #837).

Nothing recorded which existing names the user chose, so the upgrade counts
each as chosen, and a reprocess keeps it, except a default name such as
"Sunday 1st Nov, Morning Meeting", which only the server gives and which the
AI title may still replace.

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

from backend.models.recording import is_default_meeting_name
from backend.tests.postgres_setup import migrate_empty_database

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "53b4bc17207f"
TITLE_USER_EDITED = "bb518dd1f469"

# id, name, counted as the user's after the upgrade
CASES = [
    (1, "Budget review", True),
    (2, "Sunday 1st Nov, Morning Meeting", False),
    (3, "Thursday 31st Oct, Late Night Meeting", False),
    # The first default format reads as a name someone could type.
    (4, "Monday Morning Meeting", True),
    (5, "Sunday 1st Nov, Morning Meeting (follow-up)", True),
    # Characters a pattern or a quoting slip would trip on.
    (6, "Q3 [draft] (v2) ^$ .* 100% it's \\ done", True),
    (7, "", True),
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
    name = f"nojoin_title_user_edited_{uuid4().hex[:12]}"
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
    for recording_id, name, _ in CASES:
        conn.execute(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
            " meeting_uid, audio_path, status, upload_progress, processing_progress,"
            " is_archived, is_deleted, user_id)"
            " VALUES (%s, now(), now(), %s, %s, %s, %s, 'PROCESSED', 0, 0,"
            " false, false, 1)",
            (
                recording_id,
                name,
                f"public-{recording_id}",
                f"uid-{recording_id}",
                f"/recordings/{recording_id}.wav",
            ),
        )


def _flags(database_url: str) -> dict[int, bool]:
    with psycopg.connect(database_url) as conn:
        return dict(
            conn.execute(
                "SELECT id, title_user_edited FROM recordings ORDER BY id"
            ).fetchall()
        )


def test_the_backfill_keeps_every_name_but_a_default_one(
    scratch_database: str,
) -> None:
    migrate_empty_database(scratch_database, BEFORE)
    with psycopg.connect(scratch_database) as conn:
        _seed(conn)

    _alembic(scratch_database, "upgrade", TITLE_USER_EDITED)

    expected = {recording_id: chosen for recording_id, _, chosen in CASES}
    assert _flags(scratch_database) == expected
    # The migration's pattern and the model's agree on every case, so a restore
    # of an older archive decides as the upgrade did.
    assert {
        recording_id: not is_default_meeting_name(name)
        for recording_id, name, _ in CASES
    } == expected

    # A recording added afterwards starts unchosen, from the column default.
    with psycopg.connect(scratch_database) as conn:
        conn.execute(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
            " meeting_uid, audio_path, status, upload_progress, processing_progress,"
            " is_archived, is_deleted, user_id)"
            " VALUES (8, now(), now(), 'Budget review', 'public-8', 'uid-8',"
            " '/recordings/8.wav', 'PROCESSED', 0, 0, false, false, 1)"
        )
    assert _flags(scratch_database)[8] is False

    _alembic(scratch_database, "downgrade", BEFORE)
    _alembic(scratch_database, "upgrade", TITLE_USER_EDITED)
    assert _flags(scratch_database) == {**expected, 8: True}
