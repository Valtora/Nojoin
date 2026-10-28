"""The settling-state revision adds nullable JSON (JSONB on PostgreSQL) and reverses."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from backend.core.db import driver_urls

REVISION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/f2a6e1d9c735_add_transcript_publication_state.py"
)


def _revision() -> ModuleType:
    spec = importlib.util.spec_from_file_location("publication_revision", REVISION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request: pytest.FixtureRequest) -> Iterator[sa.Engine]:
    if request.param == "sqlite":
        engine = sa.create_engine("sqlite://")
        schema = None
    else:
        url = request.getfixturevalue("postgres_test_url")
        schema = f"publication_migration_{uuid4().hex[:12]}"
        engine = sa.create_engine(
            driver_urls(url)[1],
            connect_args={"options": f"-csearch_path={schema},public"},
        )
    try:
        with engine.begin() as connection:
            if schema:
                connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(
                sa.text("CREATE TABLE transcripts (id INTEGER PRIMARY KEY, text TEXT)")
            )
            connection.execute(
                sa.text("INSERT INTO transcripts (id, text) VALUES (1, 'old text')")
            )
        yield engine
    finally:
        if schema:
            with engine.begin() as connection:
                connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_revision_adds_settling_json_and_downgrades(engine: sa.Engine) -> None:
    revision = _revision()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()
        assert connection.execute(
            sa.text("SELECT text, settling_state FROM transcripts")
        ).one() == ("old text", None)
        table = sa.Table("transcripts", sa.MetaData(), autoload_with=connection)
        connection.execute(
            table.update()
            .where(table.c.id == 1)
            .values(settling_state={"eligible": True, "policy": {"k": 3}})
        )
    with engine.connect() as connection:
        row = connection.execute(sa.select(table).where(table.c.id == 1)).one()
        assert row.settling_state["policy"] == {"k": 3}
        if engine.dialect.name == "postgresql":
            assert (
                connection.execute(
                    sa.text(
                        "SELECT pg_typeof(settling_state)::text FROM transcripts LIMIT 1"
                    )
                ).scalar_one()
                == "jsonb"
            )
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            revision.downgrade()
        assert {
            column["name"]
            for column in sa.inspect(connection).get_columns("transcripts")
        } == {"id", "text"}
