"""The tag name migration merges each user's repeated names into the oldest tag.

Runs the real Alembic chain on a throwaway database, from the revision before
it, with a row for each case the migration decides. Skips unless
NOJOIN_TEST_POSTGRES_URL names a server; CI sets it for the backend suite.

Users: Alice (1) and Bob (2). Every tag and link is Alice's unless it says
otherwise.
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
BEFORE = "f704bbd87f98"
NAMES_ONCE = "edb5059e5d68"

# id, name, owner, parent
RECORDING_TAGS = [
    (1, "Board", 1, None),  # kept: the oldest of three
    (2, "Projects", 1, None),
    (3, "Board", 1, 2),
    (4, "Minutes", 1, 3),  # under a copy, so it moves under the kept tag
    (5, "Board", 1, None),
    (6, "Board", 2, None),  # Bob's
    (7, "board", 1, None),  # another name: names compare exactly
    (8, "Client", 1, 9),  # kept, but beneath its own copy
    (9, "Client", 1, 10),
    (10, "Accounts", 1, None),
    (11, "Renewals", 1, 9),
    (12, "Ops", 1, 13),  # kept, two levels beneath its copy
    (13, "Team", 1, 14),
    (14, "Ops", 1, None),
    (15, "Loop", 1, 16),  # a loop saved before #547, of two copies
    (16, "Loop", 1, 15),
    (17, "Old", None, None),  # no owner, left alone
    (18, "Old", None, None),
]
# link id, recording, tag
RECORDING_LINKS = [
    (1, 10, 1),
    (2, 10, 3),  # the kept tag holds meeting 10 already
    (3, 11, 3),
    (4, 11, 5),  # held once link 3 has moved
    (5, 10, 5),
    (6, 11, 9),
    (7, 11, 9),  # the copy's second link to the same meeting
    (8, 10, 16),
]
# link id, task, tag
TASK_LINKS = [(1, 1, 1), (2, 1, 5), (3, 2, 3)]
PEOPLE_TAGS = [
    (1, "Client", 1, None),
    (2, "Client", 1, None),
    (3, "Key accounts", 1, 2),
    (4, "Client", 2, None),  # Bob's
]
# link id, person, tag
PEOPLE_LINKS = [(1, 101, 1), (2, 101, 2), (3, 102, 2)]


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
    name = f"nojoin_tag_names_{uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield (
            make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        )
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _insert_tags(conn: psycopg.Connection, table: str, rows: list[tuple]) -> None:
    for tag_id, name, owner, _ in rows:
        conn.execute(
            f"INSERT INTO {table} (id, created_at, updated_at, name, color, user_id)"
            " VALUES (%s, now(), now(), %s, 'teal', %s)",
            (tag_id, name, owner),
        )
    # Parents once every row exists, as the foreign key needs them.
    for tag_id, _, _, parent in rows:
        conn.execute(
            f"UPDATE {table} SET parent_id = %s WHERE id = %s", (parent, tag_id)
        )


def _insert_links(
    conn: psycopg.Connection, table: str, column: str, rows: list[tuple]
) -> None:
    for link_id, owner_id, tag_id in rows:
        conn.execute(
            f"INSERT INTO {table} (id, created_at, updated_at, {column}, tag_id)"
            " VALUES (%s, now(), now(), %s, %s)",
            (link_id, owner_id, tag_id),
        )


def _seed_before(conn: psycopg.Connection) -> None:
    for user_id, username in ((1, "alice"), (2, "bob")):
        conn.execute(
            "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
            " is_active, is_superuser, role, force_password_change)"
            " VALUES (%s, now(), now(), %s, 'x', true, false, 'user', false)",
            (user_id, username),
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
    for task_id in (1, 2):
        conn.execute(
            "INSERT INTO user_tasks (id, created_at, updated_at, title, user_id)"
            " VALUES (%s, now(), now(), 'task', 1)",
            (task_id,),
        )
    for person_id in (101, 102):
        conn.execute(
            "INSERT INTO global_speakers (id, created_at, updated_at, name, user_id,"
            " is_voiceprint_locked) VALUES (%s, now(), now(), %s, 1, false)",
            (person_id, f"Person {person_id}"),
        )
    _insert_tags(conn, "tags", RECORDING_TAGS)
    _insert_tags(conn, "p_tags", PEOPLE_TAGS)
    _insert_links(conn, "recording_tags", "recording_id", RECORDING_LINKS)
    _insert_links(conn, "user_task_tags", "task_id", TASK_LINKS)
    _insert_links(conn, "people_tags", "global_speaker_id", PEOPLE_LINKS)


def _state(conn: psycopg.Connection) -> dict[str, dict]:
    def tags(table: str) -> dict:
        return {
            tag_id: (name, owner, parent)
            for tag_id, name, owner, parent in conn.execute(
                f"SELECT id, name, user_id, parent_id FROM {table}"
            ).fetchall()
        }

    def links(table: str, column: str) -> dict:
        return {
            link_id: (owner_id, tag_id)
            for link_id, owner_id, tag_id in conn.execute(
                f"SELECT id, {column}, tag_id FROM {table}"
            ).fetchall()
        }

    return {
        "tags": tags("tags"),
        "p_tags": tags("p_tags"),
        "recording_tags": links("recording_tags", "recording_id"),
        "user_task_tags": links("user_task_tags", "task_id"),
        "people_tags": links("people_tags", "global_speaker_id"),
    }


EXPECTED = {
    "tags": {
        1: ("Board", 1, None),
        2: ("Projects", 1, None),
        4: ("Minutes", 1, 1),
        6: ("Board", 2, None),
        7: ("board", 1, None),
        8: ("Client", 1, 10),  # took its copy's place under Accounts
        10: ("Accounts", 1, None),
        11: ("Renewals", 1, 8),
        12: ("Ops", 1, None),  # took its copy's place at the top level
        13: ("Team", 1, 12),
        15: ("Loop", 1, None),  # its copy's place was itself: the top level
        17: ("Old", None, None),
        18: ("Old", None, None),
    },
    "p_tags": {
        1: ("Client", 1, None),
        3: ("Key accounts", 1, 1),
        4: ("Client", 2, None),
    },
    "recording_tags": {1: (10, 1), 3: (11, 1), 6: (11, 8), 8: (10, 15)},
    "user_task_tags": {1: (1, 1), 3: (2, 1)},
    "people_tags": {1: (101, 1), 3: (102, 1)},
}


def test_repeated_names_merge_into_the_oldest_tag(scratch_database: str) -> None:
    _alembic(scratch_database, "upgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        _seed_before(conn)

    log = _alembic(scratch_database, "upgrade", NAMES_ONCE)

    with psycopg.connect(scratch_database) as conn:
        assert _state(conn) == EXPECTED
    assert "tags: 4 repeated names merged, 5 copies removed" in log
    assert "p_tags: 1 repeated names merged, 1 copies removed" in log
    # Counts and ids only: no tag name reaches the log.
    assert "Board" not in log and "Client" not in log


@pytest.mark.parametrize("table", ["tags", "p_tags"])
def test_the_index_refuses_a_second_copy_for_one_user_only(
    scratch_database: str, table: str
) -> None:
    _alembic(scratch_database, "upgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        _seed_before(conn)
    _alembic(scratch_database, "upgrade", NAMES_ONCE)

    insert = (
        f"INSERT INTO {table} (created_at, updated_at, name, user_id)"
        " VALUES (now(), now(), %s, %s)"
    )
    with psycopg.connect(scratch_database, autocommit=True) as conn:
        conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 999)")
        with pytest.raises(psycopg.errors.UniqueViolation) as refused:
            conn.execute(insert, ("Client", 1))
        assert refused.value.diag.constraint_name == f"uq_{table}_user_name"
        conn.execute(insert, ("Client", None))
        conn.execute(insert, ("client", 1))
        conn.execute(insert, ("New", 2))


def test_downgrade_keeps_the_merge_and_a_second_upgrade_merges_again(
    scratch_database: str,
) -> None:
    _alembic(scratch_database, "upgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        _seed_before(conn)
    _alembic(scratch_database, "upgrade", NAMES_ONCE)

    _alembic(scratch_database, "downgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        assert _state(conn) == EXPECTED
        # Without the index a copy can be saved again.
        conn.execute(
            "INSERT INTO tags (id, created_at, updated_at, name, user_id)"
            " VALUES (100, now(), now(), 'Board', 1)"
        )
        conn.execute(
            "INSERT INTO recording_tags (id, created_at, updated_at, recording_id,"
            " tag_id) VALUES (100, now(), now(), 10, 100)"
        )
    _alembic(scratch_database, "upgrade", NAMES_ONCE)

    with psycopg.connect(scratch_database) as conn:
        assert _state(conn) == EXPECTED
