"""The owner's transcription choice is carried into config.json on upgrade.

Before the engine and model were install-wide, Settings > Transcription stored
them on the choosing administrator's own row. The upgrade step moves the
owner's choice into config.json once, without overriding a value an operator
set there, and clears it from the row so a later start cannot replay it over a
choice saved since.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import main
from backend.models.user import User
from backend.services import transcription_settings_upgrade as upgrade
from backend.tests.sqlite_schemas import USERS_SCHEMA
from backend.utils.config_manager import DEFAULT_SYSTEM_CONFIG, ConfigManager

# The User mapper resolves its relationships by name, so every model has to be
# registered before the first query, as the API's startup does.
importlib.import_module("backend.models.registry")

OWNER_CHOICE = {
    "transcription_backend": "parakeet",
    "parakeet_model": "parakeet-tdt-0.6b-v2",
    "whisper_model_size": "small",
}


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch) -> Path:
    """A config.json as first start writes it: every shipped default."""
    monkeypatch.setattr(ConfigManager, "_ensure_dirs_exist", lambda self, cfg: None)
    path = tmp_path / "config.json"
    monkeypatch.setattr(upgrade, "config_manager", ConfigManager(config_path=str(path)))
    return path


def _config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _set_config(path: Path, **values) -> None:
    path.write_text(json.dumps({**_config(path), **values}), encoding="utf-8")


@contextlib.asynccontextmanager
async def _users(rows: list[tuple[str, dict]]) -> AsyncIterator[sessionmaker]:
    """A database holding these (role, settings) users, in id order."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(text(USERS_SCHEMA))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            for index, (role, settings) in enumerate(rows):
                session.add(
                    User(
                        username=f"user{index}",
                        hashed_password="x",
                        role=role,
                        settings=dict(settings),
                    )
                )
            await session.commit()
        yield maker
    finally:
        await engine.dispose()


async def _rows(maker: sessionmaker) -> list[dict]:
    async with maker() as session:
        users = (await session.execute(select(User).order_by(User.id))).scalars()
        return [dict(user.settings or {}) for user in users]


async def _carry(rows: list[tuple[str, dict]], runs: int = 1, between=None):
    """Run the upgrade step over these users; return the last result and rows.

    ``between`` runs before each run after the first.
    """
    async with _users(rows) as maker:
        carried: dict = {}
        for run in range(runs):
            if run and between is not None:
                between()
            async with maker() as session:
                carried = await upgrade.carry_owner_transcription_choice(session)
        return carried, await _rows(maker)


def test_the_owners_choice_moves_into_config(config_path):
    carried, (owner, member) = asyncio.run(
        _carry(
            [
                ("owner", {**OWNER_CHOICE, "theme": "light"}),
                ("user", {"transcription_backend": "canary"}),
            ]
        )
    )

    assert carried == OWNER_CHOICE
    config = _config(config_path)
    for key, value in OWNER_CHOICE.items():
        assert config[key] == value
    # Cleared from the owner's row; every other setting stays.
    assert owner == {"theme": "light"}
    # Another user's value is not carried, only ignored from now on.
    assert member == {"transcription_backend": "canary"}


def test_a_second_start_does_not_replay_the_choice_over_a_later_one(config_path):
    def admin_switches_back_to_whisper() -> None:
        upgrade.config_manager.save_values({"transcription_backend": "whisper"})

    carried, (owner,) = asyncio.run(
        _carry(
            [("owner", OWNER_CHOICE)],
            runs=2,
            between=admin_switches_back_to_whisper,
        )
    )

    assert carried == {}
    assert _config(config_path)["transcription_backend"] == "whisper"
    assert owner == {}


def test_a_value_an_operator_set_in_config_wins(config_path):
    _set_config(config_path, transcription_backend="canary")

    carried, (owner,) = asyncio.run(_carry([("owner", OWNER_CHOICE)]))

    config = _config(config_path)
    assert config["transcription_backend"] == "canary"
    # The keys the operator left at their defaults still carry.
    assert config["whisper_model_size"] == "small"
    assert config["parakeet_model"] == "parakeet-tdt-0.6b-v2"
    assert "transcription_backend" not in carried
    assert owner == {}


def test_an_unreadable_config_is_left_alone_and_the_row_kept(config_path):
    config_path.write_text("{not json", encoding="utf-8")

    carried, (owner,) = asyncio.run(_carry([("owner", OWNER_CHOICE)]))

    assert carried == {}
    assert config_path.read_text(encoding="utf-8") == "{not json"
    assert owner == OWNER_CHOICE


def test_a_failed_write_keeps_the_row_for_the_next_start(config_path, monkeypatch):
    def fail(values) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(upgrade.config_manager, "save_values", fail)

    async def run() -> list[dict]:
        async with _users([("owner", OWNER_CHOICE)]) as maker:
            async with maker() as session:
                with pytest.raises(OSError):
                    await upgrade.carry_owner_transcription_choice(session)
            return await _rows(maker)

    assert asyncio.run(run()) == [OWNER_CHOICE]
    assert _config(config_path)["transcription_backend"] == "whisper"


def test_nothing_is_written_without_an_owner_choice(config_path):
    before = config_path.read_text(encoding="utf-8")

    carried, _ = asyncio.run(
        _carry([("owner", {"theme": "light"}), ("admin", OWNER_CHOICE)])
    )

    assert carried == {}
    assert config_path.read_text(encoding="utf-8") == before
    assert (
        _config(config_path)["transcription_backend"]
        == (DEFAULT_SYSTEM_CONFIG["transcription_backend"])
    )


def test_startup_carries_on_when_the_step_fails(monkeypatch, caplog):
    async def fail(session) -> dict:
        raise OSError("read-only file system")

    monkeypatch.setattr(main, "carry_owner_transcription_choice", fail)

    with caplog.at_level(logging.ERROR, logger=main.logger.name):
        asyncio.run(main.carry_owner_transcription_choice_on_startup())

    assert any(
        "Could not carry the owner's transcription choice" in record.getMessage()
        for record in caplog.records
    )
