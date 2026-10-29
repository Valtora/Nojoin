"""PCM geometry remains nullable for existing chunks on both database dialects."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from backend.core.db import driver_urls


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request: pytest.FixtureRequest) -> Iterator[sa.Engine]:
    schema = None
    if request.param == "sqlite":
        engine = sa.create_engine("sqlite://")
    else:
        schema = f"capture_pcm_{uuid4().hex[:12]}"
        engine = sa.create_engine(
            driver_urls(request.getfixturevalue("postgres_test_url"))[1],
            connect_args={"options": f"-csearch_path={schema},public"},
        )
    try:
        with engine.begin() as connection:
            if schema:
                connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(
                sa.text(
                    "CREATE TABLE recording_audio_chunks (id INTEGER PRIMARY KEY, storage_path TEXT)"
                )
            )
            connection.execute(
                sa.text("INSERT INTO recording_audio_chunks VALUES (1, 'retained.wav')")
            )
        yield engine
    finally:
        if schema:
            with engine.begin() as connection:
                connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_pcm_geometry_upgrade_and_downgrade_preserve_existing_chunks(
    engine: sa.Engine,
) -> None:
    revision = importlib.import_module(
        "backend.alembic.versions.c8f26d3a105b_add_capture_pcm_metadata"
    )
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()
        assert connection.execute(
            sa.text(
                "SELECT storage_path, pcm_data_offset, pcm_byte_size FROM recording_audio_chunks"
            )
        ).one() == ("retained.wav", None, None)
        connection.execute(
            sa.text(
                "UPDATE recording_audio_chunks SET pcm_data_offset=78, pcm_byte_size=:size"
            ),
            {"size": 2**40},
        )
        assert (
            connection.execute(
                sa.text("SELECT pcm_byte_size FROM recording_audio_chunks")
            ).scalar_one()
            == 2**40
        )
        with Operations.context(MigrationContext.configure(connection)):
            revision.downgrade()
        assert (
            connection.execute(
                sa.text("SELECT storage_path FROM recording_audio_chunks")
            ).scalar_one()
            == "retained.wav"
        )
        assert {
            column["name"]
            for column in sa.inspect(connection).get_columns("recording_audio_chunks")
        } == {"id", "storage_path"}
