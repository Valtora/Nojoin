"""The companion table drop needs the opt-in only when the tables hold rows.

Revision 4a8e2f5b6c71 drops the retired Companion app's pairing tables, behind
NOJOIN_ALLOW_COMPANION_DROP so an install's pairings are never lost unasked.
Every fresh database creates those tables empty a few revisions earlier, so the
guard used to log its warning on every first start with nothing to protect.
Runs the revision's own upgrade() on SQLite through Alembic's operations.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "4a8e2f5b6c71_drop_companion_pairing_tables.py"
)
COMPANION_TABLES = {"companion_pairings", "companion_pairing_requests"}


def _migration():
    spec = importlib.util.spec_from_file_location("companion_drop_revision", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        for table in sorted(COMPANION_TABLES):
            connection.execute(
                sa.text(
                    f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, user_id INTEGER)"
                )
            )
            connection.execute(
                sa.text(f"CREATE INDEX ix_{table}_user_id ON {table} (user_id)")
            )
    try:
        yield engine
    finally:
        engine.dispose()


def _upgrade(engine) -> set[str]:
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            _migration().upgrade()
    return set(sa.inspect(engine).get_table_names())


def test_empty_tables_are_dropped_without_the_opt_in(engine, monkeypatch, caplog):
    monkeypatch.delenv("NOJOIN_ALLOW_COMPANION_DROP", raising=False)

    with caplog.at_level(logging.INFO):
        tables = _upgrade(engine)

    assert not tables & COMPANION_TABLES
    assert "companion" not in caplog.text


def test_tables_with_pairings_are_kept_without_the_opt_in(engine, monkeypatch, caplog):
    monkeypatch.delenv("NOJOIN_ALLOW_COMPANION_DROP", raising=False)
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO companion_pairing_requests (user_id) VALUES (1)")
        )

    with caplog.at_level(logging.WARNING):
        tables = _upgrade(engine)

    assert tables & COMPANION_TABLES == COMPANION_TABLES
    assert "Skipping companion table drop" in caplog.text
    assert "NOJOIN_ALLOW_COMPANION_DROP=1" in caplog.text


def test_tables_with_pairings_are_dropped_with_the_opt_in(engine, monkeypatch):
    monkeypatch.setenv("NOJOIN_ALLOW_COMPANION_DROP", "1")
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO companion_pairings (user_id) VALUES (1)")
        )

    assert not _upgrade(engine) & COMPANION_TABLES
