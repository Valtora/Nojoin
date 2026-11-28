"""The calendar write support migration on a real database.

Upgrades a database seeded at the revision before it, then checks the range
backfill (timed events keep their times; all-day events get midnight UTC at
their dates, the exclusive end defaulting to the next day), the new column
defaults, the cleared sync cursors that make the next sync a full one, and the
downgrade, which drops Proton connections an older build cannot read.

Skips unless NOJOIN_TEST_POSTGRES_URL names a server; CI sets it.
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

from backend.tests.postgres_setup import ALEMBIC_INI, migrate_empty_database

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "7c1d2e9a4b60"
WRITE_SUPPORT = "ac4dec3c4483"


def _alembic(database_url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def scratch_database(postgres_test_url: str) -> Iterator[str]:
    admin_url = (
        make_url(postgres_test_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    name = f"nojoin_calendar_writes_{uuid4().hex[:12]}"
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
        " VALUES (1, now(), now(), 'alice', 'x', true, false, 'user', false)"
    )
    conn.execute(
        "INSERT INTO calendar_connections (id, created_at, updated_at, user_id,"
        " provider, provider_account_id, granted_scopes, sync_status)"
        " VALUES (1, now(), now(), 1, 'google', 'acct-1', '[]', 'success')"
    )
    conn.execute(
        "INSERT INTO calendar_sources (id, created_at, updated_at, connection_id,"
        " provider_calendar_id, name, is_primary, is_read_only, is_selected,"
        " sync_cursor) VALUES (1, now(), now(), 1, 'cal-1', 'Personal', true,"
        " false, true, 'cursor-1')"
    )
    rows = [
        (1, "evt-timed", False, "2026-12-01 10:00", "2026-12-01 11:00", None, None),
        (2, "evt-day", True, None, None, "2026-12-25", "2026-12-27"),
        (3, "evt-day-open", True, None, None, "2026-12-31", None),
        (4, "evt-instant", False, "2026-12-02 09:00", None, None, None),
    ]
    for row in rows:
        conn.execute(
            "INSERT INTO calendar_events (id, created_at, updated_at, calendar_id,"
            " provider_event_id, title, status, is_all_day, starts_at, ends_at,"
            " start_date, end_date) VALUES (%s, now(), now(), 1, %s, 'Event',"
            " 'confirmed', %s, %s, %s, %s, %s)",
            row,
        )


def test_upgrade_backfills_ranges_and_downgrade_drops_proton(
    scratch_database: str,
) -> None:
    migrate_empty_database(scratch_database, BEFORE)
    with psycopg.connect(scratch_database) as conn:
        _seed(conn)

    _alembic(scratch_database, "upgrade", WRITE_SUPPORT)

    with psycopg.connect(scratch_database) as conn:
        ranges = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT id, range_start, range_end, show_as, occurrence_type"
                " FROM calendar_events ORDER BY id"
            ).fetchall()
        }
        cursor = conn.execute(
            "SELECT sync_cursor FROM calendar_sources WHERE id = 1"
        ).fetchone()
        conn.execute(
            "INSERT INTO calendar_connections (id, created_at, updated_at, user_id,"
            " provider, provider_account_id, granted_scopes, sync_status,"
            " provider_secrets_encrypted) VALUES (2, now(), now(), 1, 'proton',"
            " 'proton-user', '[]', 'idle', 'sealed')"
        )

    assert ranges == {
        1: (datetime(2026, 12, 1, 10), datetime(2026, 12, 1, 11), "busy", "single"),
        2: (datetime(2026, 12, 25), datetime(2026, 12, 27), "busy", "single"),
        3: (datetime(2026, 12, 31), datetime(2027, 1, 1), "busy", "single"),
        4: (datetime(2026, 12, 2, 9), datetime(2026, 12, 2, 9), "busy", "single"),
    }
    assert cursor == (None,)

    _alembic(scratch_database, "downgrade", BEFORE)
    with psycopg.connect(scratch_database) as conn:
        providers = [
            row[0]
            for row in conn.execute(
                "SELECT provider FROM calendar_connections ORDER BY id"
            ).fetchall()
        ]
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'calendar_events'"
            ).fetchall()
        }
    assert providers == ["google"]
    assert "range_start" not in columns

    _alembic(scratch_database, "upgrade", WRITE_SUPPORT)
