"""The people owner migration gives every person an owner, deleting nothing.

Runs the real Alembic chain on a throwaway database, from the revision before
it, with a row for each case the migration decides. Skips unless
NOJOIN_TEST_POSTGRES_URL names a server; CI sets it for the backend suite.

Users: Alice (1), Bob (2), Cara (3). People ids in the 100s are seeded; the
migration's new people come from the sequence, at 1000 and above.
"""

from __future__ import annotations

import json
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
BEFORE = "7ff0bbdadad8"
OWNERS = "7cee4b4388a7"
VOICE = [0.6, 0.8]

# id, name, owner, voiceprint
PEOPLE = [
    (101, "Ann", None, VOICE),  # Alice's recordings only
    (102, "Ben", None, VOICE),  # two links from Alice's, one from Bob's
    (103, "Cy", None, VOICE),  # linked from nothing
    (104, "Dee", 1, VOICE),  # Alice's, linked from Bob's too
    (105, "Dee", 2, None),  # Bob's own Dee
    (106, "Eve", 3, VOICE),  # Cara's, linked from Bob's
    (107, "Fay", None, VOICE),  # Cara's recordings, and Cara has a Fay
    (108, "Fay", 3, None),
    (109, "Gus", None, VOICE),  # one link each from Bob's and Cara's
    (110, "Hal", None, VOICE),  # linked only from a recording with no owner
]
# recording id -> owner
RECORDINGS = {10: 1, 11: 1, 20: 2, 30: 3, 40: None}
# speaker id, recording, person, merged into
SPEAKERS = [
    (1, 10, 101, None),
    (2, 10, 102, None),
    (3, 11, 102, None),
    (4, 20, 102, None),
    (5, 10, 104, None),
    (6, 20, 104, None),
    (7, 20, 104, 6),  # merged into speaker 6, link kept
    (8, 20, 106, None),
    (9, 30, 107, None),
    (10, 20, 109, None),
    (11, 30, 109, None),
    (12, 40, 110, None),
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
    name = f"nojoin_people_owner_{uuid4().hex[:12]}"
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
    for user_id, username in ((1, "alice"), (2, "bob"), (3, "cara")):
        conn.execute(
            "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
            " is_active, is_superuser, role, force_password_change)"
            " VALUES (%s, now(), now(), %s, 'x', true, false, 'user', false)",
            (user_id, username),
        )
    for recording_id, owner in RECORDINGS.items():
        conn.execute(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
            " meeting_uid, audio_path, status, upload_progress, processing_progress,"
            " is_archived, is_deleted, user_id) VALUES (%s, now(), now(), 'm', %s,"
            " %s, %s, 'PROCESSED', 0, 0, false, false, %s)",
            (
                recording_id,
                f"pub-{recording_id}",
                f"uid-{recording_id}",
                f"/r{recording_id}.wav",
                owner,
            ),
        )
    for person_id, name, owner, voiceprint in PEOPLE:
        conn.execute(
            "INSERT INTO global_speakers (id, created_at, updated_at, name, color,"
            " embedding, embedding_version, user_id, is_voiceprint_locked, email)"
            " VALUES (%s, now(), now(), %s, 'teal', %s, 2, %s, false, %s)",
            (
                person_id,
                name,
                json.dumps(voiceprint) if voiceprint else None,
                owner,
                f"{name.lower()}@example.com",
            ),
        )
    conn.execute("SELECT setval('global_speakers_id_seq', 999)")
    for speaker_id, recording_id, person_id, merged_into in SPEAKERS:
        conn.execute(
            "INSERT INTO recording_speakers (id, created_at, updated_at, public_id,"
            " recording_id, diarization_label, global_speaker_id, merged_into_id,"
            " speaker_status, speaker_kind, identity_locked)"
            " VALUES (%s, now(), now(), %s, %s, %s, %s, %s, 'active', 'automated',"
            " false)",
            (
                speaker_id,
                f"rs-{speaker_id}",
                recording_id,
                f"SPEAKER_{speaker_id:02d}",
                person_id,
                merged_into,
            ),
        )
    # A drift guard verdict and a tag on Alice's Dee stay with her.
    conn.execute(
        "INSERT INTO recording_speaker_drift_refusals"
        " (recording_speaker_id, global_speaker_id) VALUES (6, 104)"
    )
    conn.execute(
        "INSERT INTO p_tags (id, created_at, updated_at, name, user_id)"
        " VALUES (1, now(), now(), 'client', 1)"
    )
    conn.execute(
        "INSERT INTO people_tags (created_at, updated_at, global_speaker_id, tag_id)"
        " VALUES (now(), now(), 104, 1)"
    )


def _state(conn: psycopg.Connection) -> tuple[dict, dict, list, list]:
    people = {
        person_id: (owner, name, embedding, color, email)
        for person_id, owner, name, embedding, color, email in conn.execute(
            "SELECT id, user_id, name, embedding, color, email FROM global_speakers"
        ).fetchall()
    }
    links = dict(
        conn.execute("SELECT id, global_speaker_id FROM recording_speakers").fetchall()
    )
    refusals = conn.execute(
        "SELECT recording_speaker_id, global_speaker_id"
        " FROM recording_speaker_drift_refusals"
    ).fetchall()
    tags = conn.execute("SELECT global_speaker_id, tag_id FROM people_tags").fetchall()
    return people, links, refusals, tags


def test_every_person_gets_the_owner_of_the_recordings_that_link_them(
    scratch_database: str,
) -> None:
    _alembic(scratch_database, "upgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        _seed_before(conn)

    log = _alembic(scratch_database, "upgrade", OWNERS)

    with psycopg.connect(scratch_database) as conn:
        people, links, refusals, tags = _state(conn)
    seeded = {person_id: name for person_id, name, _, _ in PEOPLE}
    created = sorted(set(people) - set(seeded))
    created_by_owner_and_name = {
        (people[person_id][0], people[person_id][1]): person_id for person_id in created
    }
    bobs_ben = created_by_owner_and_name[(2, "Ben")]
    bobs_eve = created_by_owner_and_name[(2, "Eve")]
    caras_gus = created_by_owner_and_name[(3, "Gus")]

    # Nobody was deleted, and three people were made for links that moved.
    assert set(seeded) <= set(people)
    assert len(created) == 3
    owners = {person_id: people[person_id][:2] for person_id in seeded}
    assert owners == {
        101: (1, "Ann"),
        102: (1, "Ben"),  # most links are Alice's
        103: (None, "Cy"),
        104: (1, "Dee"),
        105: (2, "Dee"),
        106: (3, "Eve"),
        107: (3, "Fay (2)"),  # Cara already had a Fay
        108: (3, "Fay"),
        109: (2, "Gus"),  # a tie goes to the lowest user id
        110: (None, "Hal"),
    }
    # Every voiceprint and detail stays where it was; the new people carry a
    # name and colour only.
    for person_id, _, _, voiceprint in PEOPLE:
        assert people[person_id][2] == voiceprint
        assert people[person_id][4] == f"{seeded[person_id].lower()}@example.com"
    for person_id in created:
        assert people[person_id][2:] == (None, "teal", None)

    assert links == {
        1: 101,
        2: 102,
        3: 102,
        4: bobs_ben,
        5: 104,
        6: 105,  # Bob's own Dee
        7: 105,
        8: bobs_eve,
        9: 107,
        10: 109,
        11: caras_gus,
        12: 110,  # a recording with no owner keeps its link
    }
    assert refusals == [(6, 104)]
    assert tags == [(104, 1)]
    assert "4 given an owner" in log
    assert "2 with no owner and no link from an owned recording" in log
    assert "5 speaker links moved" in log

    # A second run, after a downgrade, changes nothing.
    _alembic(scratch_database, "downgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        assert _state(conn) == (people, links, refusals, tags)
    _alembic(scratch_database, "upgrade", OWNERS)
    with psycopg.connect(scratch_database) as conn:
        assert _state(conn) == (people, links, refusals, tags)
