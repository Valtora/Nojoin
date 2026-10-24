"""The captures migration keeps every document a document, and back.

Runs the real Alembic chain on a throwaway database. Skips unless
NOJOIN_TEST_POSTGRES_URL names a server; CI sets it for the backend suite.
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
BEFORE = "0f3b502561a6"
CAPTURES = "c4d81e6a2f90"


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
    admin_url = _psycopg_url(postgres_test_url)
    name = f"nojoin_captures_{uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield (
            make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        )
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _document(conn: psycopg.Connection, document_id: int, title: str, **extra) -> None:
    columns = ", ".join(extra)
    values = ", ".join(["%s"] * len(extra))
    conn.execute(
        "INSERT INTO documents (id, created_at, updated_at, recording_id, title,"
        " file_path, file_type, status, parse_mode, pages_parsed"
        + (f", {columns}" if extra else "")
        + ") VALUES (%s, now(), now(), 1, %s, %s, 'image/png', 'READY', 'VISUAL', 1"
        + (f", {values}" if extra else "")
        + ")",
        (document_id, title, f"/data/documents/{document_id}.png", *extra.values()),
    )


def test_documents_stay_documents_and_captures_need_a_time(
    scratch_database: str,
) -> None:
    migrate_empty_database(scratch_database, BEFORE)
    with psycopg.connect(scratch_database) as conn:
        conn.execute(
            "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
            " is_active, is_superuser, role, force_password_change)"
            " VALUES (7, now(), now(), 'alice', 'x', true, false, 'user', false)"
        )
        conn.execute(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
            " meeting_uid, audio_path, status, upload_progress, processing_progress,"
            " is_archived, is_deleted, user_id) VALUES (1, now(), now(), 'm', 'pub-1',"
            " 'uid-1', '/r1.wav', 'PROCESSED', 0, 0, false, false, 7)"
        )
        _document(conn, 1, "Agenda.png")

    _alembic(scratch_database, "upgrade", CAPTURES)
    with psycopg.connect(scratch_database) as conn:
        assert conn.execute(
            "SELECT kind, capture_ms, use_in_notes FROM documents WHERE id = 1"
        ).fetchone() == ("document", None, False)

        _document(conn, 2, "Capture at 01:05", kind="capture", capture_ms=65_000)
        _document(
            conn,
            3,
            "Capture at 02:00",
            kind="capture",
            capture_ms=120_000,
            use_in_notes=True,
        )
        for bad in (
            {"kind": "capture"},
            {"kind": "capture", "capture_ms": -1},
            {"kind": "document", "capture_ms": 5},
            {"kind": "document", "use_in_notes": True},
            {"kind": "slide", "capture_ms": 5},
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    _document(conn, 9, "Bad", **bad)

    _alembic(scratch_database, "downgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        kept = conn.execute("SELECT id, title FROM documents ORDER BY id").fetchall()
        column = conn.execute(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name = 'documents' AND column_name = 'kind'"
        ).fetchone()
    # Captures stay on their meeting as ordinary documents.
    assert kept == [(1, "Agenda.png"), (2, "Capture at 01:05"), (3, "Capture at 02:00")]
    assert column == (0,)

    _alembic(scratch_database, "upgrade", CAPTURES)
    with psycopg.connect(scratch_database) as conn:
        assert conn.execute("SELECT DISTINCT kind FROM documents").fetchall() == [
            ("document",)
        ]
