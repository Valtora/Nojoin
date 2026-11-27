"""Authenticated model-preparation endpoints backed by a real temporary database."""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_db
from backend.api.v1.endpoints import system
from backend.core.security import API_ACCESS_SCOPE, API_TOKEN_TYPE, create_access_token
from backend.models.revoked_jwt import RevokedJwt
from backend.models.user import User
from backend.tests.sqlite_schemas import USERS_SCHEMA

importlib.import_module("backend.models.registry")


@asynccontextmanager
async def settings_client() -> AsyncIterator[AsyncClient]:
    """Use real authentication, a persisted owner and the production router."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(USERS_SCHEMA))
            await connection.run_sync(RevokedJwt.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            owner = User(username="model-owner", hashed_password="unused", role="owner")
            session.add(owner)
            await session.commit()
            token = create_access_token(
                owner.username,
                token_type=API_TOKEN_TYPE,
                scopes=[API_ACCESS_SCOPE],
                token_version=owner.token_version,
            )
        app = FastAPI()
        app.include_router(system.router, prefix="/system")

        async def database() -> AsyncIterator[AsyncSession]:
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_db] = database
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            yield client
    finally:
        await engine.dispose()
