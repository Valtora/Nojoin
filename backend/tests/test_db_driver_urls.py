"""The drivers backend.core.db picks for a DATABASE_URL.

SQLAlchemy 2.1 changed the driver a bare ``postgresql://`` means from psycopg2
to psycopg 3, and the backend broke on the upgrade because it had relied on
the default. These pin both drivers by name, and building the engines proves
each one is installed.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from backend.core.db import driver_urls

COMPOSE_URL = "postgresql://postgres:secret@db:5432/nojoin"


def test_a_compose_url_names_asyncpg_for_the_api_and_psycopg_for_the_workers():
    async_url, sync_url = driver_urls(COMPOSE_URL)

    assert async_url == "postgresql+asyncpg://postgres:secret@db:5432/nojoin"
    assert sync_url == "postgresql+psycopg://postgres:secret@db:5432/nojoin"


def test_both_drivers_are_installed():
    async_url, sync_url = driver_urls(COMPOSE_URL)

    # Neither engine connects until it is used, so no server is needed.
    async_engine = create_async_engine(async_url)
    sync_engine = create_engine(sync_url)
    try:
        assert async_engine.dialect.driver == "asyncpg"
        assert sync_engine.dialect.driver == "psycopg"
    finally:
        sync_engine.dispose()
        async_engine.sync_engine.dispose()


def test_a_url_that_is_not_bare_postgres_is_used_as_given():
    assert driver_urls("sqlite:///./test.db") == (
        "sqlite:///./test.db",
        "sqlite:///./test.db",
    )
