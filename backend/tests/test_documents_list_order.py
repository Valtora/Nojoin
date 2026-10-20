"""Documents list in the order they were queued for upload.

The client uploads a batch two at a time, so a small file can finish before a
larger one queued ahead of it. The row is stamped with the time its upload
started, and the list orders by that stamp, so the panel follows the queue.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import documents as documents_ep
from backend.models.document import Document
from backend.models.recording import Recording
from backend.models.user import User
from backend.tests.sqlite_schemas import (
    DOCUMENTS_SCHEMA,
    RECORDINGS_SCHEMA,
    USERS_SCHEMA,
)

T0 = datetime(2026, 10, 11, 12, 0, 0)


@pytest.fixture
def api():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = FastAPI()
    app.include_router(documents_ep.router)

    async def override_db():
        async with maker() as session:
            yield session

    user = User(id=1, username="alice", hashed_password="x", role="user")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user

    async def setup() -> Recording:
        async with engine.begin() as conn:
            await conn.execute(text(USERS_SCHEMA))
            await conn.execute(text(RECORDINGS_SCHEMA))
            await conn.execute(text(DOCUMENTS_SCHEMA))
        async with maker() as session:
            session.add(user)
            recording = Recording(
                id=1,
                name="Standup",
                audio_path="/tmp/standup.wav",
                status="PROCESSED",
                user_id=1,
                upload_progress=100,
                processing_progress=100,
            )
            session.add(recording)
            await session.commit()
            await session.refresh(recording)
            return recording

    yield SimpleNamespace(app=app, maker=maker, setup=setup)
    app.dependency_overrides.clear()


async def _list(api, recording) -> list[str]:
    async with AsyncClient(
        transport=ASGITransport(app=api.app), base_url="http://t"
    ) as client:
        response = await client.get(f"/recordings/{recording.public_id}/documents")
    assert response.status_code == 200
    return [document["title"] for document in response.json()]


def _document(recording_id: int, title: str, created_at: datetime) -> Document:
    return Document(
        recording_id=recording_id,
        title=title,
        file_path=f"/tmp/{title}",
        file_size_bytes=1,
        created_at=created_at,
        updated_at=created_at,
    )


async def _run_list_follows_the_queue(api):
    recording = await api.setup()
    async with api.maker() as session:
        # Queued first, finished last: the larger file gets the later row id.
        session.add(_document(1, "small.txt", T0 + timedelta(seconds=1)))
        session.add(_document(1, "large.pdf", T0))
        session.add(_document(1, "third.docx", T0 + timedelta(seconds=2)))
        await session.commit()

    return await _list(api, recording)


def test_the_list_follows_upload_start_not_completion(api) -> None:
    import asyncio

    titles = asyncio.run(_run_list_follows_the_queue(api))

    assert titles == ["large.pdf", "small.txt", "third.docx"]


async def _run_upload_stamps_start(api, monkeypatch):
    recording = await api.setup()

    # Time moves on while the bytes stream in; the row keeps the start.
    clock = iter([T0, T0 + timedelta(seconds=30), T0 + timedelta(seconds=31)])
    monkeypatch.setattr(documents_ep, "utc_now", lambda: next(clock))

    @contextlib.asynccontextmanager
    async def no_limit(*_args, **_kwargs):
        yield

    async def stream(*, file, dest_path, max_size):
        return 3

    monkeypatch.setattr(documents_ep, "enforce_upload_concurrency", no_limit)
    monkeypatch.setattr(documents_ep, "ensure_disk_headroom", lambda *_a, **_k: None)
    monkeypatch.setattr(documents_ep, "stream_and_validate_upload", stream)

    async def dispatch(*_args, **_kwargs):
        return SimpleNamespace(id="task-1")

    monkeypatch.setattr(documents_ep, "dispatch_task", dispatch)

    async def register(*_args, **_kwargs):
        return None

    import backend.models.task as task_models

    monkeypatch.setattr(task_models, "register_task_ownership", register)

    async with AsyncClient(
        transport=ASGITransport(app=api.app), base_url="http://t"
    ) as client:
        response = await client.post(
            f"/recordings/{recording.public_id}/documents",
            files={"file": ("notes.txt", b"abc", "text/plain")},
            data={"deep_parse": "false"},
        )
    assert response.status_code == 200, response.text

    async with api.maker() as session:
        document = await session.get(Document, response.json()["id"])
        return document.created_at


def test_an_upload_is_stamped_when_it_starts(api, monkeypatch) -> None:
    import asyncio

    created_at = asyncio.run(_run_upload_stamps_start(api, monkeypatch))

    assert created_at == T0
