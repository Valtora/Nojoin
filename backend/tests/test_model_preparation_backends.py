"""Which models a preparation run puts on disk.

API startup queues preparation with the core batch on. Only the Whisper engine
loads Whisper, so an install whose users transcribe with Parakeet or Canary must
not download it at every start; the diarisation and voice-embedding models are
needed whatever the engine. The engine is stored per user (Settings >
Transcription writes it to the choosing administrator's row, not config.json),
so startup reads the users.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend import preload_models
from backend.services import model_preparation
from backend.tests.sqlite_schemas import USERS_SCHEMA


@pytest.fixture
def prepared(monkeypatch) -> list[str]:
    """Record each preparation step in place of the real downloads."""
    steps: list[str] = []
    monkeypatch.setattr(preload_models, "clear_download_progress", lambda: None)
    monkeypatch.setattr(
        preload_models, "set_download_progress", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(preload_models, "_release_validation_caches", lambda: None)
    monkeypatch.setattr(
        preload_models,
        "_prepare_whisper_model",
        lambda size: steps.append(f"whisper:{size}"),
    )
    monkeypatch.setattr(
        preload_models,
        "_prepare_pyannote_models",
        lambda token: steps.append("pyannote"),
    )
    monkeypatch.setattr(
        preload_models,
        "_prepare_onnx_asr_model",
        lambda model_id: steps.append(f"onnx:{model_id}"),
    )
    monkeypatch.setattr(
        preload_models,
        "_resolve_onnx_asr_id",
        lambda backend, model_id: f"{backend}/{model_id}",
    )
    return steps


@pytest.mark.parametrize(
    ("backend", "model_kwarg", "model_id"),
    [
        ("parakeet", "parakeet_model", "parakeet-tdt-0.6b-v3"),
        ("canary", "canary_model", "nemo-canary-1b-v2"),
    ],
)
def test_core_batch_on_an_onnx_engine_skips_whisper(
    prepared, backend, model_kwarg, model_id
):
    preload_models.download_models(
        transcription_backend=backend,
        whisper_model_size="turbo",
        include_core=True,
        **{model_kwarg: model_id},
    )

    assert prepared == ["pyannote", f"onnx:{backend}/{model_id}"]


def test_core_batch_on_whisper_prepares_the_configured_whisper_size(prepared):
    preload_models.download_models(
        transcription_backend="whisper", whisper_model_size="small", include_core=True
    )

    assert prepared == ["whisper:small", "pyannote"]


def test_with_no_backend_given_the_install_config_decides(prepared, monkeypatch):
    config = {"transcription_backend": "parakeet"}
    monkeypatch.setattr(
        preload_models.config_manager,
        "get",
        lambda key, default=None: config.get(key, default),
    )

    preload_models.download_models(include_core=True)

    assert prepared == ["pyannote", "onnx:parakeet/parakeet-tdt-0.6b-v3"]


def test_an_explicit_whisper_flag_overrides_the_backend(prepared):
    preload_models.download_models(
        transcription_backend="parakeet",
        whisper_model_size="small",
        parakeet_model="parakeet-tdt-0.6b-v3",
        include_core=True,
        include_whisper=True,
    )

    assert prepared == [
        "whisper:small",
        "pyannote",
        "onnx:parakeet/parakeet-tdt-0.6b-v3",
    ]


# --- API startup ------------------------------------------------------------

_INSERT_USER = text(
    "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
    " is_active, is_superuser, force_password_change, role, token_version,"
    " settings, has_seen_demo_recording)"
    " VALUES (:id, '2026-01-01', '2026-01-01', :name, 'x', :active, 0, 0, :role,"
    " 0, :settings, 0)"
)


def _use_config(monkeypatch, values: dict) -> None:
    monkeypatch.setattr(
        model_preparation.config_manager,
        "get",
        lambda key, default=None: values.get(key, default),
    )


@contextlib.asynccontextmanager
async def _users_db(
    users: list[tuple[str, bool, object]], *, with_users_table: bool = True
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A database holding these users: (role, is_active, settings), in id order."""
    # The User mapper resolves its relationships by name, so every model has to
    # be registered before the first query, as the API's startup has done.
    importlib.import_module("backend.models.registry")
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        async with engine.begin() as connection:
            if with_users_table:
                await connection.execute(text(USERS_SCHEMA))
            for index, (role, active, settings) in enumerate(users, start=1):
                await connection.execute(
                    _INSERT_USER,
                    {
                        "id": index,
                        "name": f"user{index}",
                        "active": active,
                        "role": role,
                        "settings": None if settings is None else json.dumps(settings),
                    },
                )
        yield async_sessionmaker(engine, class_=AsyncSession)
    finally:
        await engine.dispose()


def _capture_dispatch(monkeypatch) -> list[dict]:
    dispatched: list[dict] = []

    async def fake_dispatch(name, *, kwargs, ignore_result):
        dispatched.append(kwargs)
        return type("Task", (), {"id": "task-1"})()

    monkeypatch.setattr(model_preparation, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        model_preparation, "set_download_progress", lambda *args, **kwargs: None
    )
    return dispatched


def _startup_dispatch(
    monkeypatch,
    users: list[tuple[str, bool, object]],
    *,
    with_users_table: bool = True,
) -> dict:
    """Run the startup entry point over these users; return the task kwargs."""
    dispatched = _capture_dispatch(monkeypatch)

    async def run() -> None:
        async with _users_db(users, with_users_table=with_users_table) as maker:
            await model_preparation.enqueue_startup_model_preparation(maker)

    asyncio.run(run())
    assert len(dispatched) == 1
    return dispatched[0]


def _config_dispatch(monkeypatch) -> dict:
    """The task kwargs of the config-only path startup used before reading users."""
    dispatched = _capture_dispatch(monkeypatch)
    asyncio.run(model_preparation.enqueue_model_preparation(include_core=True))
    return dispatched[0]


def test_startup_skips_whisper_when_the_owner_chose_parakeet_in_settings(
    prepared, monkeypatch
):
    """The UI writes the engine to the owner's row; config.json still says whisper."""
    _use_config(monkeypatch, {})

    kwargs = _startup_dispatch(
        monkeypatch, [("owner", True, {"transcription_backend": "parakeet"})]
    )
    preload_models.download_models(**kwargs)

    assert kwargs["transcription_backend"] == "parakeet"
    assert prepared == ["pyannote", "onnx:parakeet/parakeet-tdt-0.6b-v3"]


def test_startup_keeps_whisper_while_any_user_transcribes_with_it(
    prepared, monkeypatch
):
    _use_config(monkeypatch, {"transcription_backend": "whisper"})

    kwargs = _startup_dispatch(
        monkeypatch,
        [
            ("user", True, {"whisper_model_size": "small"}),
            ("owner", True, {"transcription_backend": "canary"}),
        ],
    )
    preload_models.download_models(**kwargs)

    # The owner's engine is prepared, and Whisper at the size its user chose.
    assert prepared == ["whisper:small", "pyannote", "onnx:canary/nemo-canary-1b-v2"]


def test_startup_ignores_deactivated_users(monkeypatch):
    _use_config(monkeypatch, {"transcription_backend": "parakeet"})

    kwargs = _startup_dispatch(
        monkeypatch,
        [
            ("owner", True, None),
            ("user", False, {"transcription_backend": "whisper"}),
        ],
    )

    assert kwargs["transcription_backend"] == "parakeet"
    assert "include_whisper" not in kwargs


@pytest.mark.parametrize("configured", ["whisper", "parakeet"])
def test_startup_before_any_user_exists_follows_the_install_config(
    monkeypatch, configured
):
    _use_config(monkeypatch, {"transcription_backend": configured})

    kwargs = _startup_dispatch(monkeypatch, [])

    assert kwargs == _config_dispatch(monkeypatch)
    assert kwargs["transcription_backend"] == configured


@pytest.mark.parametrize(
    "users",
    [
        [("owner", True, {"transcription_backend": "whisper"})],
        [("owner", True, {"transcription_backend": "parakeet"}), ("user", True, None)],
    ],
    ids=["owner-on-whisper", "nobody-on-whisper"],
)
def test_startup_sends_no_whisper_flag_when_the_engine_already_implies_it(
    monkeypatch, users
):
    """A worker on an image older than the flag rejects any unknown kwarg."""
    _use_config(monkeypatch, {"transcription_backend": "parakeet"})

    kwargs = _startup_dispatch(monkeypatch, users)

    assert "include_whisper" not in kwargs


def test_startup_treats_an_empty_engine_as_the_pipeline_does(prepared, monkeypatch):
    """The pipeline keeps a stored "" over config and fails, so Whisper is unused."""
    _use_config(monkeypatch, {"transcription_backend": "whisper"})

    kwargs = _startup_dispatch(
        monkeypatch, [("owner", True, {"transcription_backend": ""})]
    )
    preload_models.download_models(**kwargs)

    assert prepared == ["pyannote"]


@pytest.mark.parametrize(
    ("users", "with_users_table"),
    [
        ([], False),
        ([("owner", True, ["transcription_backend", "parakeet"])], True),
    ],
    ids=["database-error", "settings-not-an-object"],
)
def test_startup_falls_back_to_the_config_when_the_users_cannot_be_read(
    prepared, monkeypatch, caplog, users, with_users_table
):
    _use_config(monkeypatch, {"transcription_backend": "whisper"})

    with caplog.at_level(logging.WARNING, logger=model_preparation.__name__):
        kwargs = _startup_dispatch(
            monkeypatch, users, with_users_table=with_users_table
        )
    preload_models.download_models(**kwargs)

    assert kwargs == _config_dispatch(monkeypatch)
    assert prepared == ["whisper:turbo", "pyannote"]
    assert "config.json decides" in caplog.text
