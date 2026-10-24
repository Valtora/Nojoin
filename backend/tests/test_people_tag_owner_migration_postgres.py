"""The people tag owner migration gives every people tag an owner, deleting none.

Runs the real Alembic chain on a throwaway database, from the revision before
it, with a row for each case the migration decides. Skips unless
NOJOIN_TEST_POSTGRES_URL names a server; CI sets it for the backend suite.

Users: Alice (1), Bob (2), Cara (3) and Olive (4), the install's Owner. Tag
ids under 100 are seeded; the migration's new tags come from the sequence, at
1000 and above.
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
BEFORE = "7cee4b4388a7"
TAG_OWNERS = "f704bbd87f98"

# id, name, role
USERS = [
    (1, "alice", "user"),
    (2, "bob", "user"),
    (3, "cara", "user"),
    (4, "olive", "owner"),
]
# person id -> owner
PEOPLE = {101: 1, 102: 1, 103: 1, 201: 2, 301: 3, 401: None}
# id, name, owner, parent
PEOPLE_TAGS = [
    (1, "Shared", None, None),  # three links from Alice's people, one from Bob's
    (2, "Tie", None, None),  # one link each from Bob's and Cara's
    (3, "Parent", None, None),  # no link; its only child goes to Cara
    (4, "Child", None, 3),  # Cara's link
    (5, "Unused", None, None),  # used by nobody
    (6, "Client", None, None),  # Alice's link, and Alice has a Client
    (7, "Client", 1, None),
    (8, "Vendor", 2, None),  # Bob's, linked from Alice's person
    (9, "Team", 1, None),
    (10, "Team", 2, None),  # Bob's, linked from a person holding Alice's Team
    (11, "Nested", 1, 8),  # Alice's, under Bob's Vendor
    (12, "Under shared", 2, 1),  # Bob's, under the tag Alice is given
]
# link id, person, tag
LINKS = [
    (1, 101, 1),
    (2, 102, 1),
    (3, 103, 1),
    (4, 201, 1),
    (5, 201, 2),
    (6, 301, 2),
    (7, 301, 4),
    (8, 101, 6),
    (9, 101, 8),
    (10, 102, 9),
    (11, 102, 10),
    (12, 401, 8),  # a person with no owner keeps their link
]
# id, name, owner, parent
RECORDING_TAGS = [
    (20, "Board", 1, None),
    (21, "Minutes", 1, 20),  # under Alice's own
    (22, "Cross", 1, 30),  # under Bob's
    (23, "Old", None, 30),  # no owner, left alone
    (24, "Under old", 1, 23),  # under a tag with no owner
    (30, "Hiring", 2, None),
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


def _psycopg_url(url: str) -> str:
    return (
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


@pytest.fixture
def scratch_database(postgres_test_url: str) -> Iterator[str]:
    """A database of its own, so the chain can be walked up and down freely."""
    admin_url = _psycopg_url(postgres_test_url)
    name = f"nojoin_tag_owner_{uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield (
            make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        )
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _seed_before(conn: psycopg.Connection) -> None:
    for user_id, username, role in USERS:
        conn.execute(
            "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
            " is_active, is_superuser, role, force_password_change)"
            " VALUES (%s, now(), now(), %s, 'x', true, %s, %s, false)",
            (user_id, username, role == "owner", role),
        )
    for person_id, owner in PEOPLE.items():
        conn.execute(
            "INSERT INTO global_speakers (id, created_at, updated_at, name, user_id,"
            " is_voiceprint_locked) VALUES (%s, now(), now(), %s, %s, false)",
            (person_id, f"person-{person_id}", owner),
        )
    for table, rows in (("p_tags", PEOPLE_TAGS), ("tags", RECORDING_TAGS)):
        for tag_id, name, owner, _ in rows:
            conn.execute(
                f"INSERT INTO {table} (id, created_at, updated_at, name, color,"
                " user_id) VALUES (%s, now(), now(), %s, 'teal', %s)",
                (tag_id, name, owner),
            )
        # Parents once every row exists, as the foreign key needs them.
        for tag_id, _, _, parent in rows:
            conn.execute(
                f"UPDATE {table} SET parent_id = %s WHERE id = %s", (parent, tag_id)
            )
    conn.execute("SELECT setval(pg_get_serial_sequence('p_tags', 'id'), 999)")
    for link_id, person_id, tag_id in LINKS:
        conn.execute(
            "INSERT INTO people_tags (id, created_at, updated_at, global_speaker_id,"
            " tag_id) VALUES (%s, now(), now(), %s, %s)",
            (link_id, person_id, tag_id),
        )


def _state(conn: psycopg.Connection) -> tuple[dict, dict, dict]:
    people_tags = {
        tag_id: (owner, name, color, parent)
        for tag_id, owner, name, color, parent in conn.execute(
            "SELECT id, user_id, name, color, parent_id FROM p_tags"
        ).fetchall()
    }
    links = {
        link_id: (person_id, tag_id)
        for link_id, person_id, tag_id in conn.execute(
            "SELECT id, global_speaker_id, tag_id FROM people_tags"
        ).fetchall()
    }
    recording_tags = {
        tag_id: (owner, parent)
        for tag_id, owner, parent in conn.execute(
            "SELECT id, user_id, parent_id FROM tags"
        ).fetchall()
    }
    return people_tags, links, recording_tags


def test_every_people_tag_gets_the_owner_who_uses_it(scratch_database: str) -> None:
    migrate_empty_database(scratch_database, BEFORE)
    with psycopg.connect(scratch_database) as conn:
        _seed_before(conn)

    log = _alembic(scratch_database, "upgrade", TAG_OWNERS)

    with psycopg.connect(scratch_database) as conn:
        people_tags, links, recording_tags = _state(conn)
    seeded = {tag_id for tag_id, *_ in PEOPLE_TAGS}
    created = sorted(set(people_tags) - seeded)
    created_by_owner_and_name = {people_tags[tag_id][:2]: tag_id for tag_id in created}
    bobs_shared = created_by_owner_and_name[(2, "Shared")]
    caras_tie = created_by_owner_and_name[(3, "Tie")]
    alices_vendor = created_by_owner_and_name[(1, "Vendor")]

    # No tag was deleted, and three were made for links that moved. They carry
    # the name and colour only.
    assert len(created) == 3
    for tag_id in created:
        assert people_tags[tag_id][2:] == ("teal", None)
    assert {
        tag_id: (people_tags[tag_id][0], people_tags[tag_id][1], people_tags[tag_id][3])
        for tag_id in seeded
    } == {
        1: (1, "Shared", None),  # most uses are Alice's
        2: (2, "Tie", None),  # a tie goes to the lowest user id
        3: (3, "Parent", None),  # its child went to Cara
        4: (3, "Child", 3),
        5: (4, "Unused", None),  # the Owner account
        6: (1, "Client (2)", None),  # Alice already had a Client
        7: (1, "Client", None),
        8: (2, "Vendor", None),
        9: (1, "Team", None),
        10: (2, "Team", None),
        11: (1, "Nested", None),  # Bob's Vendor was never Alice's to see
        12: (2, "Under shared", bobs_shared),  # Bob's own Shared, as he saw it
    }
    assert links == {
        1: (101, 1),
        2: (102, 1),
        3: (103, 1),
        4: (201, bobs_shared),
        5: (201, 2),
        6: (301, caras_tie),
        7: (301, 4),
        8: (101, 6),
        9: (101, alices_vendor),
        10: (102, 9),
        # 11 is gone: person 102 already held Alice's own Team.
        12: (401, 8),
    }
    assert recording_tags == {
        20: (1, None),
        21: (1, 20),
        22: (1, None),
        23: (None, 30),
        24: (1, None),
        30: (2, None),
    }
    assert "6 given an owner" in log
    assert "1 of them used by nobody" in log
    assert "1 renamed" in log
    assert "3 moved to the person's owner's own tag" in log
    assert "1 removed because the person already held that tag" in log
    assert "People tag parents: 2 moved" in log
    assert "3 people tags created" in log
    assert "Recording tag parents: 2 moved" in log

    # A second run, after a downgrade, changes nothing.
    state = (people_tags, links, recording_tags)
    _alembic(scratch_database, "downgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        assert _state(conn) == state
    log = _alembic(scratch_database, "upgrade", TAG_OWNERS)
    with psycopg.connect(scratch_database) as conn:
        assert _state(conn) == state
    assert "0 given an owner" in log
    assert "0 people tags created" in log
