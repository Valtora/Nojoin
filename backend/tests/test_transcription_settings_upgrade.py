"""The owner's transcription choice is carried into config.json on upgrade.

Before the engine and model were install-wide, Settings > Transcription and
first-run setup stored them on the owner's own row. The upgrade step moves the
owner's choice into config.json once, before startup queues model preparation,
and clears it from the row so a later start cannot replay it over a choice
saved since.
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


def test_the_owners_choice_moves_into_config(config_path, caplog):
    with caplog.at_level(logging.WARNING, logger=upgrade.logger.name):
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
        # Each carried value is logged, with the default it replaces.
        assert any(
            key in r.getMessage() and repr(value) in r.getMessage()
            for r in caplog.records
        )
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


def test_the_owners_later_choice_replaces_what_setup_left_in_config(
    config_path, caplog
):
    # Some older releases wrote the wizard's size to config.json.
    _set_config(config_path, whisper_model_size="small")

    with caplog.at_level(logging.WARNING, logger=upgrade.logger.name):
        carried, (owner,) = asyncio.run(
            _carry([("owner", {"whisper_model_size": "large"})])
        )

    assert carried == {"whisper_model_size": "large"}
    assert _config(config_path)["whisper_model_size"] == "large"
    assert owner == {}
    assert any("'small'" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("key", "file_value"),
    [
        ("transcription_backend", "canary"),
        ("whisper_model_size", "large"),
        ("parakeet_model", "parakeet-other"),
        ("canary_model", "canary-other"),
    ],
)
def test_an_owner_default_does_not_replace_a_config_value(
    config_path, caplog, key, file_value
):
    # Setup and the settings page's autosave store defaults on the owner's row.
    _set_config(config_path, **{key: file_value})
    default = DEFAULT_SYSTEM_CONFIG[key]
    other = "whisper_model_size" if key != "whisper_model_size" else "parakeet_model"
    choice = {key: default, other: OWNER_CHOICE.get(other, "parakeet-tdt-0.6b-v2")}

    with caplog.at_level(logging.WARNING, logger=upgrade.logger.name):
        carried, (owner,) = asyncio.run(_carry([("owner", choice)]))

    config = _config(config_path)
    assert config[key] == file_value
    # The owner's other key still carries.
    assert config[other] == choice[other]
    assert key not in carried
    assert owner == {}
    assert any(repr(default) in r.getMessage() for r in caplog.records)


def test_an_empty_owner_value_is_skipped_quietly(config_path, caplog):
    with caplog.at_level(logging.WARNING, logger=upgrade.logger.name):
        carried, (owner,) = asyncio.run(
            _carry([("owner", {"transcription_backend": ""})])
        )

    assert carried == {}
    assert owner == {}
    assert not caplog.records


def test_a_config_that_cannot_be_read_is_left_alone(config_path, caplog):
    config_path.unlink()
    config_path.mkdir()

    with caplog.at_level(logging.WARNING, logger=upgrade.logger.name):
        carried, (owner,) = asyncio.run(_carry([("owner", OWNER_CHOICE)]))

    assert carried == {}
    assert config_path.is_dir()
    assert owner == OWNER_CHOICE
    assert any(str(config_path) in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("key", "value"),
    [("transcription_backend", "nonsense"), ("whisper_model_size", "huge")],
)
def test_an_invalid_owner_value_is_logged_and_not_carried(
    config_path, caplog, key, value
):
    before = _config(config_path)

    with caplog.at_level(logging.WARNING, logger=upgrade.logger.name):
        carried, (owner,) = asyncio.run(_carry([("owner", {key: value})]))

    assert carried == {}
    assert _config(config_path) == before
    assert owner == {}
    assert any(repr(value) in r.getMessage() for r in caplog.records)


def test_the_first_owner_by_id_is_carried(config_path):
    carried, rows = asyncio.run(
        _carry(
            [
                ("user", {"transcription_backend": "canary"}),
                ("owner", {"transcription_backend": "parakeet"}),
                ("owner", {"transcription_backend": "canary"}),
            ]
        )
    )

    assert carried == {"transcription_backend": "parakeet"}
    assert _config(config_path)["transcription_backend"] == "parakeet"
    assert rows == [
        {"transcription_backend": "canary"},
        {},
        {"transcription_backend": "canary"},
    ]


def test_a_missing_config_is_created_with_the_owners_choice(config_path):
    config_path.unlink()

    carried, (owner,) = asyncio.run(_carry([("owner", OWNER_CHOICE)]))

    assert carried == OWNER_CHOICE
    assert _config(config_path) == OWNER_CHOICE
    assert owner == {}


def test_a_config_that_is_not_an_object_is_left_alone(config_path):
    config_path.write_text("[]", encoding="utf-8")

    carried, (owner,) = asyncio.run(_carry([("owner", OWNER_CHOICE)]))

    assert carried == {}
    assert config_path.read_text(encoding="utf-8") == "[]"
    assert owner == OWNER_CHOICE


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


@pytest.mark.parametrize(
    "error", [OSError("read-only file system"), ValueError("not an object")]
)
def test_startup_carries_on_when_the_step_fails(monkeypatch, caplog, error):
    async def fail(session) -> dict:
        raise error

    monkeypatch.setattr(main, "carry_owner_transcription_choice", fail)

    with caplog.at_level(logging.ERROR, logger=main.logger.name):
        asyncio.run(main.carry_owner_transcription_choice_on_startup())

    assert any(
        "Could not carry the owner's transcription choice" in record.getMessage()
        for record in caplog.records
    )


def test_startup_prepares_the_engine_it_has_just_carried(config_path, monkeypatch):
    """The carry-over runs in the lifespan, before preparation is queued."""
    model_preparation = importlib.import_module("backend.services.model_preparation")
    steps: list[str] = []
    dispatched: list[dict] = []

    async def fake_dispatch(name, *, kwargs, **options):
        steps.append("prepare")
        dispatched.append(kwargs)
        return type("Task", (), {"id": "task-1"})()

    async def no_op_async(*args, **kwargs) -> None:
        return None

    def no_op(*args, **kwargs) -> None:
        return None

    real_carry = main.carry_owner_transcription_choice

    async def recorded_carry(session) -> dict:
        steps.append("carry")
        return await real_carry(session)

    for name in (
        "log_signing_keyring_status",
        "run_migrations",
        "log_deployment_warnings",
        "log_trusted_proxy_warnings",
        "log_recordings_storage_warnings",
        "start_stall_watchdog",
    ):
        monkeypatch.setattr(main, name, no_op)
    for name in (
        "ensure_owner_exists",
        "log_first_run_setup_pointer",
        "ensure_recording_public_ids_on_startup",
        "ensure_recording_meeting_uids_on_startup",
        "seed_demo_data",
    ):
        monkeypatch.setattr(main, name, no_op_async)
    monkeypatch.setattr(main, "is_mcp_enabled", lambda: False)
    monkeypatch.setattr(main, "carry_owner_transcription_choice", recorded_carry)
    monkeypatch.setattr(model_preparation, "config_manager", upgrade.config_manager)
    monkeypatch.setattr(model_preparation, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(model_preparation, "set_download_progress", no_op)

    async def run() -> None:
        async with _users([("owner", OWNER_CHOICE)]) as maker:
            monkeypatch.setattr(main, "async_session_maker", maker)
            async with main.lifespan(main.app):
                pass

    asyncio.run(run())

    assert steps == ["carry", "prepare"]
    assert dispatched[0]["transcription_backend"] == "parakeet"
    assert dispatched[0]["parakeet_model"] == "parakeet-tdt-0.6b-v2"
