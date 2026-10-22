"""The recording tag migration keeps the oldest of each repeated link.

Runs the real Alembic chain on a throwaway database, from the revision before
it. Skips unless NOJOIN_TEST_POSTGRES_URL names a server; CI sets it for the
backend suite. Every row is Alice's.
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

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "8f7a09a9d7f3"
LINKS_ONCE = "fb5a849e7f5b"

# link id, recording, tag (1 Board, 2 Finance)
LINKS = [
    (1, 10, 1),  # kept: the oldest of three
    (2, 10, 1),
    (3, 10, 1),
    (4, 11, 1),
    (5, 11, 2),  # kept
    (6, 11, 2),
    (7, 10, 2),
    (8, None, 1),  # no meeting: left alone, twice
    (9, None, 1),
]

EXPECTED = {
    1: (10, 1),
    4: (11, 1),
    5: (11, 2),
    7: (10, 2),
    8: (None, 1),
    9: (None, 1),
}


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


def _psycopg_url(url: str) -> str:
    return (
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


@pytest.fixture
def scratch_database(postgres_test_url: str) -> Iterator[str]:
    """A database of its own, so the chain can be walked up and down freely."""
    admin_url = _psycopg_url(postgres_test_url)
    name = f"nojoin_tag_links_{uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield (
            make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        )
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _insert_link(
    conn: psycopg.Connection, link_id: int, recording_id: int | None, tag_id: int
) -> None:
    conn.execute(
        "INSERT INTO recording_tags (id, created_at, updated_at, recording_id, tag_id)"
        " VALUES (%s, now(), now(), %s, %s)",
        (link_id, recording_id, tag_id),
    )


def _seed_before(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
        " is_active, is_superuser, role, force_password_change)"
        " VALUES (1, now(), now(), 'alice', 'x', true, false, 'user', false)"
    )
    for recording_id in (10, 11):
        conn.execute(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
            " meeting_uid, audio_path, status, upload_progress, processing_progress,"
            " is_archived, is_deleted, user_id) VALUES (%s, now(), now(), 'm', %s,"
            " %s, %s, 'PROCESSED', 0, 0, false, false, 1)",
            (
                recording_id,
                f"pub-{recording_id}",
                f"uid-{recording_id}",
                f"/r{recording_id}.wav",
            ),
        )
    for tag_id, name in ((1, "Board"), (2, "Finance")):
        conn.execute(
            "INSERT INTO tags (id, created_at, updated_at, name, user_id)"
            " VALUES (%s, now(), now(), %s, 1)",
            (tag_id, name),
        )
    for link in LINKS:
        _insert_link(conn, *link)


def _links(conn: psycopg.Connection) -> dict[int, tuple]:
    return {
        link_id: (recording_id, tag_id)
        for link_id, recording_id, tag_id in conn.execute(
            "SELECT id, recording_id, tag_id FROM recording_tags"
        ).fetchall()
    }


def _upgraded(scratch_database: str) -> str:
    _alembic(scratch_database, "upgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        _seed_before(conn)
    return _alembic(scratch_database, "upgrade", LINKS_ONCE)


def test_repeated_links_keep_the_oldest(scratch_database: str) -> None:
    log = _upgraded(scratch_database)

    with psycopg.connect(scratch_database) as conn:
        assert _links(conn) == EXPECTED
    assert "recording_tags: 3 repeated links deleted" in log


def test_the_constraint_refuses_a_second_link(scratch_database: str) -> None:
    _upgraded(scratch_database)

    with psycopg.connect(scratch_database, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation) as refused:
            _insert_link(conn, 100, 10, 1)
        assert refused.value.diag.constraint_name == "unique_recording_tag"
        # A link with no meeting is not held to it.
        _insert_link(conn, 101, None, 1)
        # Untagging frees the pair for a new link.
        conn.execute("DELETE FROM recording_tags WHERE id = 4")
        _insert_link(conn, 102, 11, 1)


def test_downgrade_keeps_the_deletions_and_a_second_upgrade_deletes_again(
    scratch_database: str,
) -> None:
    _upgraded(scratch_database)

    _alembic(scratch_database, "downgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        assert _links(conn) == EXPECTED
        # Without the constraint a link can be saved twice again.
        _insert_link(conn, 100, 10, 1)
    log = _alembic(scratch_database, "upgrade", LINKS_ONCE)

    with psycopg.connect(scratch_database) as conn:
        assert _links(conn) == EXPECTED
    assert "recording_tags: 1 repeated links deleted" in log
