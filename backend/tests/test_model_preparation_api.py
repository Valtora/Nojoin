"""Explicit model preparation (Settings > AI).

Selecting a transcription model used to queue a download as a side effect of
saving the setting. That download runs on the GPU lane, in front of live work,
with no indication in the UI that it had started. Preparation is now requested
explicitly, so these tests pin both halves: the settings save queues nothing,
and the dedicated endpoint queues the model the admin actually has selected.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models.registry  # noqa: F401
from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import settings as settings_ep
from backend.api.v1.endpoints import system
from backend.models.user import User
from backend.tests.sqlite_schemas import NOTES_TEMPLATES_SCHEMA, USERS_SCHEMA

DOWNLOAD_TASK = "backend.worker.tasks.download_models_task"


class _FakeConfigManager:
    """Install config with none of the transcription keys set.

    Deliberately empty: the transcription keys are user-scoped, so anything the
    endpoint resolves has to come from the user row rather than from here.
    """

    def __init__(self, values: dict | None = None):
        self.config = dict(values or {})

    def get(self, key, default=None):
        return self.config.get(key, default)

    def get_all(self):
        return dict(self.config)

    def save_config(self, config_data):
        self.config = dict(config_data)

    def reload(self):
        return None

    def validate_config_value(self, key, value):
        return True


# --- POST /system/models/prepare -------------------------------------------------


@pytest.fixture()
def prepare_app(monkeypatch):
    """System router with the enqueue call recorded rather than sent."""
    calls: list[dict] = []

    def fake_enqueue(**kwargs):
        calls.append(kwargs)
        return "task-123"

    monkeypatch.setattr(system, "enqueue_model_preparation", fake_enqueue)
    monkeypatch.setattr(system, "is_download_in_progress", lambda: False)
    monkeypatch.setattr(system, "config_manager", _FakeConfigManager())

    app = FastAPI()
    app.include_router(system.router, prefix="/system")
    return app, calls


def _as_user(app, **overrides):
    attrs = {"id": 1, "role": "owner", "is_superuser": True, "settings": {}}
    attrs.update(overrides)
    user = SimpleNamespace(**attrs)
    app.dependency_overrides[get_current_user] = lambda: user
    return user


async def _post_prepare(app, payload=None):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        return await client.post("/system/models/prepare", json=payload or {})


def test_preparation_is_refused_for_non_admins(prepare_app):
    app, calls = prepare_app
    _as_user(app, role="user", is_superuser=False)

    response = asyncio.run(_post_prepare(app))

    assert response.status_code == 403
    assert calls == []


def test_active_target_prepares_the_admins_own_selection(prepare_app):
    app, calls = prepare_app
    _as_user(app, settings={"whisper_model_size": "medium"})

    response = asyncio.run(_post_prepare(app, {"target": "active"}))

    assert response.status_code == 200
    assert response.json()["task_id"] == "task-123"
    # The user row wins over the install config, which has none of these keys.
    assert calls == [
        {
            "whisper_model_size": "medium",
            "transcription_backend": "whisper",
            "parakeet_model": "parakeet-tdt-0.6b-v3",
            "canary_model": "nemo-canary-1b-v2",
            "include_core": True,
        }
    ]


def test_active_target_on_an_onnx_engine_skips_the_core_batch(prepare_app):
    app, calls = prepare_app
    _as_user(app, settings={"transcription_backend": "parakeet"})

    response = asyncio.run(_post_prepare(app, {"target": "active"}))

    assert response.status_code == 200
    assert calls[0]["transcription_backend"] == "parakeet"
    assert calls[0]["include_core"] is False


def test_a_missing_row_can_be_repaired_on_its_own(prepare_app):
    app, calls = prepare_app
    _as_user(app, settings={"transcription_backend": "whisper"})

    response = asyncio.run(_post_prepare(app, {"target": "canary"}))

    assert response.status_code == 200
    assert calls[0]["transcription_backend"] == "canary"
    assert calls[0]["include_core"] is False


def test_the_core_target_prepares_whisper_and_the_pyannote_models(prepare_app):
    app, calls = prepare_app
    _as_user(app, settings={"transcription_backend": "canary"})

    response = asyncio.run(_post_prepare(app, {"target": "core"}))

    assert response.status_code == 200
    assert calls[0]["transcription_backend"] == "whisper"
    assert calls[0]["include_core"] is True


def test_a_second_preparation_is_refused_while_one_is_running(prepare_app, monkeypatch):
    app, calls = prepare_app
    monkeypatch.setattr(system, "is_download_in_progress", lambda: True)
    _as_user(app)

    response = asyncio.run(_post_prepare(app))

    assert response.status_code == 409
    assert calls == []


def test_an_unknown_target_is_rejected(prepare_app):
    app, calls = prepare_app
    _as_user(app)

    response = asyncio.run(_post_prepare(app, {"target": "everything"}))

    assert response.status_code == 422
    assert calls == []


# --- POST /settings ---------------------------------------------------------------


async def _build_settings_app():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(text(USERS_SCHEMA))
        await conn.execute(text(NOTES_TEMPLATES_SCHEMA))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    session = maker()
    session.add(
        User(
            username="owner",
            hashed_password="x",
            role="owner",
            is_superuser=True,
            settings={},
        )
    )
    await session.commit()
    user = (await session.execute(select(User))).scalars().one()

    app = FastAPI()
    app.include_router(settings_ep.router, prefix="/settings")

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return engine, app, session


async def _post_settings(payload: dict):
    engine, app, session = await _build_settings_app()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            return await client.post("/settings", json=payload)
    finally:
        await session.close()
        await engine.dispose()


def test_changing_the_transcription_model_queues_no_download(
    monkeypatch, stub_celery_dispatch
):
    monkeypatch.setattr(settings_ep, "config_manager", _FakeConfigManager())

    response = asyncio.run(
        _post_settings(
            {"whisper_model_size": "medium", "transcription_backend": "whisper"}
        )
    )

    assert response.status_code == 200
    assert response.json()["whisper_model_size"] == "medium"
    assert DOWNLOAD_TASK not in [name for name, _, _ in stub_celery_dispatch]
