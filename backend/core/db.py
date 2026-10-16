import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import Session

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Fallback for local development if not set
    POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
    POSTGRES_SERVER = os.getenv("POSTGRES_SERVER", "localhost")
    POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_DB = os.getenv("POSTGRES_DB", "nojoin")
    DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_SERVER}:{POSTGRES_PORT}/{POSTGRES_DB}"


def driver_urls(database_url: str) -> tuple[str, str]:
    """Return the async and sync URLs for a ``DATABASE_URL``.

    A plain ``postgresql://`` URL gets asyncpg for the API and psycopg 3 for
    the workers, both named. SQLAlchemy's default driver for a bare
    ``postgresql://`` moved from psycopg2 to psycopg 3 in 2.1, so leaving the
    choice to it ties the driver to the SQLAlchemy release. Any other URL, such
    as SQLite in tests, is used as given.
    """
    if database_url.startswith("postgresql://"):
        return (
            database_url.replace("postgresql://", "postgresql+asyncpg://", 1),
            database_url.replace("postgresql://", "postgresql+psycopg://", 1),
        )
    return database_url, database_url


ASYNC_DATABASE_URL, SYNC_DATABASE_URL = driver_urls(DATABASE_URL)

engine = create_async_engine(ASYNC_DATABASE_URL, echo=False, future=True)
sync_engine = create_engine(
    SYNC_DATABASE_URL, echo=False, future=True, pool_pre_ping=True, pool_recycle=3600
)

async_session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


def get_sync_session() -> Session:
    """
    Creates a synchronous session for use in Celery workers.
    """
    return Session(sync_engine)


# Register every ORM model on the SQLModel class registry.
#
# Relationships name their targets by string, so SQLAlchemy resolves them only
# once all the classes exist. An entry point that relies on its own imports can
# pass the tests and still fail to configure its mappers in production with
# "failed to locate a name". This module is the one chokepoint every entry point
# already goes through, and no model imports it back, so there is no cycle.
# Kept at the bottom so the engines above are built first.
from backend.models import registry  # noqa: E402,F401
