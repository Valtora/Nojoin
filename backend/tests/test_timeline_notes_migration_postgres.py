"""The timeline notes migration carries free-text notes over, and back.

Runs the real Alembic chain on a throwaway database, because the backfill and
the fold-back are SQL against the production schema. Skips unless
NOJOIN_TEST_POSTGRES_URL names a server; CI sets it for the backend suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "7c3e5a91d2f4"
TIMELINE_NOTES = "63005cac6492"
NOTED_AT = datetime(2026, 3, 2, 11, 0)


def _alembic(database_url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _psycopg_url(url: str) -> str:
    return (
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


@pytest.fixture
def scratch_database(postgres_test_url: str) -> Iterator[str]:
    """A database of its own, so the chain can be walked up and down freely."""
    admin_url = _psycopg_url(postgres_test_url)
    name = f"nojoin_timeline_notes_{uuid4().hex[:12]}"
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
    conn.execute(
        "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
        " is_active, is_superuser, role, force_password_change)"
        " VALUES (7, now(), now(), 'mira', 'x', true, false, 'user', false)"
    )
    for recording_id in (1, 2, 3, 4):
        conn.execute(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
            " meeting_uid, audio_path, status, upload_progress, processing_progress,"
            " is_archived, is_deleted, user_id) VALUES (%s, now(), now(), 'm', %s,"
            " %s, %s, 'PROCESSED', 0, 0, false, false, 7)",
            (
                recording_id,
                f"pub-{recording_id}",
                f"uid-{recording_id}",
                f"/r{recording_id}.wav",
            ),
        )
    for recording_id, user_notes in (
        (1, "\n  Agenda: budget.\nOwner: Dana.  \n"),
        (2, "\n\n   \n"),
        (3, None),
    ):
        conn.execute(
            "INSERT INTO transcripts (created_at, updated_at, recording_id, user_notes,"
            " notes_status, transcript_status, meeting_edge_status,"
            " speaker_name_suggestions)"
            " VALUES (now(), %s, %s, %s, 'pending', 'pending', 'idle', '[]'::jsonb)",
            (NOTED_AT, recording_id, user_notes),
        )


def _add_note(
    conn: psycopg.Connection, recording_id: int, kind: str, body: str, **anchors: object
) -> None:
    columns = ", ".join(["kind", "body", *anchors])
    values = ", ".join(["%s"] * (2 + len(anchors)))
    conn.execute(
        f"INSERT INTO timeline_notes (created_at, updated_at, public_id, recording_id,"
        f" created_by_user_id, {columns}) VALUES (now(), now(), %s, %s, 7, {values})",
        (str(uuid4()), recording_id, kind, body, *anchors.values()),
    )


def test_user_notes_become_a_head_note_and_fold_back_on_downgrade(
    scratch_database: str,
) -> None:
    _alembic(scratch_database, "upgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        _seed_before(conn)

    _alembic(scratch_database, "upgrade", TIMELINE_NOTES)
    with psycopg.connect(scratch_database) as conn:
        notes = conn.execute(
            "SELECT recording_id, kind, body, created_by_user_id, created_at,"
            " anchor_ms, revision FROM timeline_notes ORDER BY recording_id"
        ).fetchall()
        has_column = conn.execute(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name = 'transcripts' AND column_name = 'user_notes'"
        ).fetchone()
        # Blank and missing notes made no note; the real one kept its text.
        assert notes == [
            (1, "head", "Agenda: budget.\nOwner: Dana.", 7, NOTED_AT, None, 1)
        ]
        assert has_column == (0,)

        _add_note(conn, 1, "tail", "Wrap up.")
        _add_note(conn, 1, "point", "Late point.", anchor_ms=90_000)
        _add_note(
            conn,
            1,
            "selection",
            "Early quote.",
            start_ms=1_000,
            end_ms=2_000,
            quoted_text="budget",
        )
        _add_note(conn, 4, "point", "On a recording with no transcript.", anchor_ms=0)
        # Deleting a recording takes its notes with it; a backup restore's
        # overwrite mode relies on this.
        _add_note(conn, 3, "head", "Goes with its recording.")
        conn.execute(
            "INSERT INTO timeline_note_tombstones (public_id, recording_id,"
            " deleted_write_token, deleted_at) VALUES (%s, 3, %s, now())",
            (str(uuid4()), str(uuid4())),
        )
        conn.execute("DELETE FROM recordings WHERE id = 3")
        assert conn.execute(
            "SELECT count(*) FROM timeline_notes WHERE recording_id = 3"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM timeline_note_tombstones"
        ).fetchone() == (0,)
        # A deleted note's id is held once.
        conn.execute(
            "INSERT INTO timeline_note_tombstones (public_id, recording_id,"
            " deleted_at) VALUES ('held', 1, now())"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO timeline_note_tombstones (public_id, recording_id,"
                    " deleted_at) VALUES ('held', 4, now())"
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                _add_note(conn, 1, "selection", "No quote.", start_ms=0, end_ms=1)

    _alembic(scratch_database, "downgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        folded = dict(
            conn.execute(
                "SELECT recording_id, user_notes FROM transcripts ORDER BY recording_id"
            ).fetchall()
        )
        table = conn.execute("SELECT to_regclass('timeline_notes')").fetchone()
        tombstones = conn.execute(
            "SELECT to_regclass('timeline_note_tombstones')"
        ).fetchone()

    assert folded == {
        1: "Agenda: budget.\nOwner: Dana.\n\nEarly quote.\n\nLate point.\n\nWrap up.",
        2: None,
        4: "On a recording with no transcript.",
    }
    assert table == (None,)
    assert tombstones == (None,)

    # And back up again: the folded text is one head note once more.
    _alembic(scratch_database, "upgrade", TIMELINE_NOTES)
    with psycopg.connect(scratch_database) as conn:
        bodies = conn.execute(
            "SELECT recording_id, kind, body FROM timeline_notes ORDER BY recording_id"
        ).fetchall()
    assert bodies == [
        (1, "head", folded[1]),
        (4, "head", "On a recording with no transcript."),
    ]
