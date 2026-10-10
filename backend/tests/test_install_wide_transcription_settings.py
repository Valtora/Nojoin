"""The transcription engine and model are install-wide.

An administrator's choice under Settings > Transcription goes to config.json and
applies to every user. A value left on a user's row from when the keys were
user-scoped no longer overrides it: not in the processing pipeline or the live
lane, which both resolve through _merge_llm_config, and not on the settings
page.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import settings as settings_ep
from backend.api.v1.endpoints import system
from backend.models.user import User
from backend.tests.sqlite_schemas import NOTES_TEMPLATES_SCHEMA, USERS_SCHEMA
from backend.utils.config_manager import TRANSCRIPTION_SETTING_KEYS, ConfigManager
from backend.utils.llm_config import _merge_llm_config

# The User mapper resolves its relationships by name, so every model has to be
# registered before the first query, as the API's startup does.
importlib.import_module("backend.models.registry")

INSTALL_ENGINE = {
    "transcription_backend": "parakeet",
    "whisper_model_size": "small",
    "parakeet_model": "parakeet-tdt-0.6b-v2",
    "canary_model": "nemo-canary-1b-v2",
}

STALE_ROW = {
    "transcription_backend": "canary",
    "whisper_model_size": "large",
    "parakeet_model": "parakeet-other",
    "canary_model": "canary-other",
}


@pytest.fixture
def install_config(tmp_path: Path, monkeypatch) -> ConfigManager:
    """A real config.json in a temporary directory, used by the settings routes."""
    monkeypatch.setattr(ConfigManager, "_ensure_dirs_exist", lambda self, cfg: None)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(INSTALL_ENGINE), encoding="utf-8")
    manager = ConfigManager(config_path=str(path))
    monkeypatch.setattr(settings_ep, "config_manager", manager)
    return manager


def _config_file(manager: ConfigManager) -> dict:
    return json.loads(Path(manager.config_path).read_text(encoding="utf-8"))


def test_a_user_row_cannot_override_the_install_engine() -> None:
    resolved = _merge_llm_config(
        base_config={"llm_provider": "gemini", **INSTALL_ENGINE},
        system_keys={},
        owner_settings=STALE_ROW,
        user_settings={**STALE_ROW, "prefer_short_titles": False},
    )

    for key in TRANSCRIPTION_SETTING_KEYS:
        assert resolved.merged_config[key] == INSTALL_ENGINE[key]
    # Per-user keys still come from the row.
    assert resolved.merged_config["prefer_short_titles"] is False


async def _call_settings(
    role: str, row: dict, method: str, payload: dict | None = None
) -> tuple[int, dict, dict]:
    """Send one settings request as a user with this role and row.

    Returns the status, the response body and the user's stored row after it.
    """
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
    try:
        session.add(
            User(
                username="caller",
                hashed_password="x",
                role=role,
                is_superuser=False,
                settings=dict(row),
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
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            if method == "POST":
                response = await client.post("/settings", json=payload)
            else:
                response = await client.get("/settings")
        await session.refresh(user)
        return response.status_code, response.json(), dict(user.settings or {})
    finally:
        await session.close()
        await engine.dispose()


def test_an_admin_saves_the_engine_to_config_and_not_to_their_row(install_config):
    status, body, row = asyncio.run(
        _call_settings(
            "admin",
            {},
            "POST",
            {"transcription_backend": "canary", "canary_model": "canary-new"},
        )
    )

    assert status == 200
    saved = _config_file(install_config)
    assert saved["transcription_backend"] == "canary"
    assert saved["canary_model"] == "canary-new"
    assert body["transcription_backend"] == "canary"
    assert not set(TRANSCRIPTION_SETTING_KEYS) & set(row)


def test_a_non_admin_cannot_change_the_engine(install_config):
    status, body, row = asyncio.run(
        _call_settings(
            "user",
            {},
            "POST",
            {"transcription_backend": "canary", "whisper_model_size": "tiny"},
        )
    )

    # Dropped without an error, as the install-wide LLM keys are.
    assert status == 200
    assert _config_file(install_config) == INSTALL_ENGINE
    assert body["transcription_backend"] == "parakeet"
    assert body["whisper_model_size"] == "small"
    assert not set(TRANSCRIPTION_SETTING_KEYS) & set(row)


def test_a_value_left_on_a_users_row_is_not_shown_as_theirs(install_config):
    status, body, _ = asyncio.run(_call_settings("user", STALE_ROW, "GET"))

    assert status == 200
    for key in TRANSCRIPTION_SETTING_KEYS:
        assert body[key] == INSTALL_ENGINE[key]


def test_a_null_engine_is_not_written_over_the_install_engine(install_config):
    status, _, _ = asyncio.run(
        _call_settings("owner", {}, "POST", {"transcription_backend": None})
    )

    assert status == 200
    assert _config_file(install_config)["transcription_backend"] == "parakeet"


class _SetupSession:
    """Just enough session for the first-run setup route."""

    def __init__(self) -> None:
        self.added: list[User] = []

    def add(self, value: User) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        return None

    async def refresh(self, value: User) -> None:
        value.id = 1


@pytest.fixture
def run_setup(install_config, monkeypatch):
    """Post the first-run setup with this Whisper size.

    Returns the response, the users it created and the preparation it queued.
    """

    async def no_op(*args, **kwargs) -> None:
        return None

    async def not_initialized(db) -> bool:
        return False

    monkeypatch.setattr(system, "config_manager", install_config)
    monkeypatch.setattr(system, "enforce_setup_rate_limit", no_op)
    monkeypatch.setattr(system, "is_system_initialized", not_initialized)
    monkeypatch.setattr(system, "require_first_run_password", lambda request: None)
    monkeypatch.setattr(
        importlib.import_module("backend.utils.telemetry"),
        "set_enabled",
        lambda enabled: None,
    )

    def run(whisper_model_size: str):
        session = _SetupSession()
        queued: list[dict] = []

        async def fake_enqueue(**kwargs) -> str:
            queued.append(kwargs)
            return "task-1"

        monkeypatch.setattr(system, "enqueue_model_preparation", fake_enqueue)
        app = FastAPI()
        app.include_router(system.router, prefix="/system")

        async def override_db():
            yield session

        app.dependency_overrides[get_db] = override_db

        async def post():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as client:
                return await client.post(
                    "/system/setup",
                    json={
                        "username": "owner",
                        "password": "a-long-enough-password",
                        "whisper_model_size": whisper_model_size,
                        "include_demo_recording": False,
                    },
                )

        return asyncio.run(post()), session.added, queued

    return run


def test_first_run_setup_saves_the_transcription_model_to_config(
    install_config, run_setup
):
    response, (owner,), queued = run_setup("medium")

    assert response.status_code == 200
    assert _config_file(install_config)["whisper_model_size"] == "medium"
    assert "whisper_model_size" not in owner.settings
    assert queued[0]["whisper_model_size"] == "medium"


def test_the_wizards_default_does_not_replace_a_seeded_size(install_config, run_setup):
    path = Path(install_config.config_path)
    path.write_text(json.dumps({"whisper_model_size": "large"}), encoding="utf-8")

    response, _, queued = run_setup("turbo")

    assert response.status_code == 200
    assert _config_file(install_config)["whisper_model_size"] == "large"
    assert queued[0]["whisper_model_size"] == "large"


def test_setup_finishes_when_config_is_not_an_object(install_config, run_setup):
    Path(install_config.config_path).write_text("[]", encoding="utf-8")

    response, added, _ = run_setup("medium")

    # The owner is already committed, so a 500 would make setup unrepeatable.
    assert response.status_code == 200
    assert len(added) == 1
    assert Path(install_config.config_path).read_text(encoding="utf-8") == "[]"
