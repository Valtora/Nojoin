"""Independent notes metadata preserves historical outputs on both dialects."""

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

TABLES = ("transcripts", "notes_outputs")
REVISION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/b3e7c2a9d641_independent_notes_generation.py"
)


def _revision() -> ModuleType:
    spec = importlib.util.spec_from_file_location("notes_generation_revision", REVISION)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


@pytest.fixture(params=["sqlite", "postgresql"])
def migration_engine(request: pytest.FixtureRequest) -> Iterator[sa.Engine]:
    schema = None
    if request.param == "sqlite":
        engine = sa.create_engine("sqlite://")
    else:
        schema = f"notes_migration_{uuid4().hex[:12]}"
        engine = sa.create_engine(
            driver_urls(request.getfixturevalue("postgres_test_url"))[1],
            connect_args={"options": f"-csearch_path={schema},public"},
        )
    try:
        with engine.begin() as connection:
            if schema:
                connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            for name in TABLES:
                connection.execute(
                    sa.text(
                        f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, notes TEXT, "
                        "notes_status VARCHAR NOT NULL, error_message TEXT)"
                    )
                )
                connection.execute(
                    sa.text(
                        f"INSERT INTO {name} (id, notes, notes_status, error_message) "
                        "VALUES (1, :notes, 'error', 'Previous provider failure')"
                    ),
                    {"notes": f"Historical notes from {name}"},
                )
        yield engine
    finally:
        if schema:
            with engine.begin() as connection:
                connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_notes_metadata_upgrade_json_roundtrip_and_downgrade(
    migration_engine: sa.Engine,
) -> None:
    revision = _revision()
    payload = {
        "request_id": "independent-notes-request",
        "event_watermark": 2**40,
        "generated_through_ms": 3_600_000,
    }
    reflected: dict[str, sa.Table] = {}
    with migration_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()
        for name in TABLES:
            table = sa.Table(name, sa.MetaData(), autoload_with=connection)
            reflected[name] = table
            assert connection.execute(sa.select(table)).one()._asdict() == {
                "id": 1,
                "notes": f"Historical notes from {name}",
                "notes_status": "error",
                "error_message": "Previous provider failure",
                "notes_generation_metadata": None,
            }
            connection.execute(
                table.insert().values(id=2, notes="New output", notes_status="pending")
            )
            connection.execute(
                table.update()
                .where(table.c.id == 1)
                .values(notes_generation_metadata=payload)
            )

    with migration_engine.connect() as reader:
        for name, table in reflected.items():
            assert (
                reader.execute(
                    sa.select(table.c.notes_generation_metadata).where(table.c.id == 1)
                ).scalar_one()
                == payload
            )
            assert (
                reader.execute(
                    sa.select(table.c.notes_generation_metadata).where(table.c.id == 2)
                ).scalar_one()
                is None
            )
            if migration_engine.dialect.name == "postgresql":
                assert (
                    reader.execute(
                        sa.text(
                            f"SELECT pg_typeof(notes_generation_metadata)::text "
                            f"FROM {name} WHERE id = 1"
                        )
                    ).scalar_one()
                    == "jsonb"
                )

    with migration_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            revision.downgrade()
        for name in TABLES:
            assert {
                column["name"] for column in sa.inspect(connection).get_columns(name)
            } == {"id", "notes", "notes_status", "error_message"}
            rows = connection.execute(
                sa.text(
                    f"SELECT notes, notes_status, error_message FROM {name} ORDER BY id"
                )
            ).all()
            assert rows == [
                (f"Historical notes from {name}", "error", "Previous provider failure"),
                ("New output", "pending", None),
            ]
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()
        for name in TABLES:
            assert connection.execute(
                sa.text(f"SELECT notes_generation_metadata FROM {name} ORDER BY id")
            ).all() == [(None,), (None,)]
