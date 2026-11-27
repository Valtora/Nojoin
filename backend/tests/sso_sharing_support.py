"""An API harness for the SSO and meeting-sharing tests.

Builds every ORM table on an in-memory SQLite database, mounts the real
``api_router`` on a FastAPI app with ``get_db`` pointed at it, and lets tests
authenticate as seeded users with real API tokens. Nothing about access control
is stubbed: requests go through ``get_current_user`` and the route's own checks.

Postgres-only column types (JSONB, pgvector) and ``::`` casts in server defaults
are swapped for SQLite equivalents for the CREATE and then restored, the same
technique test_calendar_and_task_api.py uses for its subset of tables.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, BigInteger, Integer, event
from sqlalchemy.dialects.sqlite.base import SQLiteDialect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import backend.models
from backend.api.deps import get_db
from backend.api.v1.api import api_router
from backend.core import security
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.models.user_identity import UserIdentity

for _module in pkgutil.iter_modules(backend.models.__path__):
    importlib.import_module(f"backend.models.{_module.name}")


def _create_all_on_sqlite(sync_connection) -> None:
    dialect = SQLiteDialect()
    restore: list[tuple[Any, str, Any]] = []
    for table in SQLModel.metadata.tables.values():
        for column in table.columns:
            server_default = column.server_default
            if server_default is not None and "::" in str(
                getattr(server_default, "arg", "")
            ):
                restore.append((column, "server_default", server_default))
                column.server_default = None
            if isinstance(column.type, BigInteger) and column.primary_key:
                restore.append((column, "type", column.type))
                column.type = Integer()
                continue
            try:
                column.type.compile(dialect=dialect)
            except Exception:  # noqa: BLE001 -- boundary: Postgres-only type on SQLite
                restore.append((column, "type", column.type))
                column.type = JSON()
    try:
        SQLModel.metadata.create_all(sync_connection)
    finally:
        for column, attribute, value in restore:
            setattr(column, attribute, value)


@dataclass
class Harness:
    app: FastAPI
    session_maker: async_sessionmaker[AsyncSession]
    tokens: dict[int, str] = field(default_factory=dict)

    async def add_user(
        self,
        username: str,
        *,
        role: str = "user",
        groups: Optional[list[str]] = None,
        display_name: Optional[str] = None,
        sso_created: bool = False,
        **extra: Any,
    ) -> User:
        """A user; with ``groups``, ``display_name`` or ``sso_created`` also a
        linked identity at the provider."""
        async with self.session_maker() as session:
            user = User(
                username=username,
                hashed_password=security.get_password_hash("correct horse battery"),
                role=role,
                legal_accepted_at=None if extra.pop("needs_legal", False) else _now(),
                **extra,
            )
            session.add(user)
            await session.flush()
            if groups is not None or display_name is not None or sso_created:
                session.add(
                    UserIdentity(
                        user_id=user.id,
                        issuer="https://idp.test/",
                        subject=f"sub-{username}",
                        display_name=display_name,
                        groups=groups or [],
                        created_account=sso_created,
                    )
                )
            await session.commit()
            await session.refresh(user)
            return user

    async def add_recording(
        self, owner: User, *, name: str = "Weekly sync"
    ) -> Recording:
        async with self.session_maker() as session:
            recording = Recording(
                name=name,
                audio_path=f"/tmp/nojoin-test-{owner.id}-{name}.wav",
                user_id=owner.id,
                status=RecordingStatus.PROCESSED,
                duration_seconds=60.0,
            )
            session.add(recording)
            await session.flush()
            session.add(Transcript(recording_id=recording.id, notes="# Notes\n"))
            await session.commit()
            await session.refresh(recording)
            return recording

    def token_for(self, user: User) -> str:
        return security.create_access_token(
            user.username,
            token_type=security.API_TOKEN_TYPE,
            scopes=[security.API_ACCESS_SCOPE],
            token_version=user.token_version,
        )

    def client(self, user: Optional[User] = None) -> AsyncClient:
        headers = {"Authorization": f"Bearer {self.token_for(user)}"} if user else {}
        return AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="https://localhost:14443",
            headers=headers,
        )


def _now():
    from backend.utils.time import utc_now

    return utc_now()


async def build_harness() -> AsyncIterator[Harness]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(_create_all_on_sqlite)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _get_db
    try:
        yield Harness(app=app, session_maker=session_maker)
    finally:
        await engine.dispose()


def api_routes_with_paths(routes, prefix: str = ""):
    """Every APIRoute under ``routes`` with its full path, through included routers."""
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            inner_prefix = prefix + (getattr(context, "prefix", "") or "")
            yield from api_routes_with_paths(included.routes, inner_prefix)


def recording_routes():
    """(method, path, route) for every route whose path names a recording."""
    rows = []
    for path, route in api_routes_with_paths(api_router.routes):
        if "{recording_id}" not in path:
            continue
        for method in sorted(route.methods or ()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            rows.append((method, path, route))
    return sorted(rows, key=lambda row: (row[1], row[0]))
