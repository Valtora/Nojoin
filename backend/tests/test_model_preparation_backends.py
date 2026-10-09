"""Which models a preparation run puts on disk.

API startup queues preparation with the core batch on. Only the Whisper engine
loads Whisper, so an install whose users transcribe with Parakeet or Canary must
not download it at every start; the diarisation and voice-embedding models are
needed whatever the engine. The engine is stored per user (Settings >
Transcription writes it to the choosing administrator's row, not config.json),
so startup reads the users, and the admin health check reports what startup
prepared.
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend import preload_models
from backend.api.services import health_service
from backend.celery_app import GPU_QUEUE, celery_app
from backend.models.user import User
from backend.services import model_preparation
from backend.tests.sqlite_schemas import USERS_SCHEMA
from backend.worker.tasks.system import download_models_task


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


@pytest.mark.parametrize(
    ("linked_task", "last_status"),
    [
        (None, "complete"),
        (model_preparation.MODEL_PREPARATION_TASK, "downloading"),
        ("backend.worker.tasks.generate_notes_task", "complete"),
    ],
    ids=["nothing-linked", "preparation-linked", "other-task-linked"],
)
def test_a_task_with_a_preparation_linked_after_it_does_not_report_complete(
    prepared, monkeypatch, linked_task, last_status
):
    """Settings stops polling the progress at the first "complete"."""
    statuses: list[str] = []
    monkeypatch.setattr(
        preload_models,
        "set_download_progress",
        lambda *args, status, **kwargs: statuses.append(status),
    )
    monkeypatch.setattr(download_models_task, "update_state", lambda **kwargs: None)
    callbacks = [] if linked_task is None else [{"task": linked_task, "kwargs": {}}]

    download_models_task.push_request(callbacks=callbacks)
    try:
        download_models_task.run(
            transcription_backend="whisper", whisper_model_size="small", include_core=True
        )
    finally:
        download_models_task.pop_request()

    assert prepared == ["whisper:small", "pyannote"]
    assert statuses[-1] == last_status


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
    """Record each task sent to the broker: its name, kwargs and options."""
    dispatched: list[dict] = []

    async def fake_dispatch(name, *, kwargs, **options):
        dispatched.append({"name": name, "kwargs": kwargs, **options})
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
) -> list[dict]:
    """Run the startup entry point over these users; return what it sent."""
    dispatched = _capture_dispatch(monkeypatch)

    async def run() -> None:
        async with _users_db(users, with_users_table=with_users_table) as maker:
            await model_preparation.enqueue_startup_model_preparation(maker)

    asyncio.run(run())
    return dispatched


def _in_run_order(dispatched: list[dict]) -> list[dict]:
    """Each sent task's kwargs, followed by those of the tasks linked after it."""
    tasks = []
    for message in dispatched:
        tasks.append(message["kwargs"])
        follower = message.get("link")
        while follower is not None:
            tasks.append(follower.kwargs)
            (follower,) = follower.options.get("link", [None])
    return tasks


def _startup_tasks(
    monkeypatch,
    users: list[tuple[str, bool, object]],
    *,
    with_users_table: bool = True,
) -> list[dict]:
    """Run the startup entry point; return each task's kwargs in run order."""
    return _in_run_order(
        _startup_dispatch(monkeypatch, users, with_users_table=with_users_table)
    )


def _run_on_the_worker(tasks: list[dict]) -> None:
    """Run each queued task's preparation in order, as the GPU lane does."""
    for kwargs in tasks:
        preload_models.download_models(**kwargs)


def _config_dispatch(monkeypatch) -> dict:
    """The task kwargs of the config-only path startup used before reading users."""
    dispatched = _capture_dispatch(monkeypatch)
    asyncio.run(model_preparation.enqueue_model_preparation(include_core=True))
    return dispatched[0]["kwargs"]


def test_startup_skips_whisper_when_the_owner_chose_parakeet_in_settings(
    prepared, monkeypatch
):
    """The UI writes the engine to the owner's row; config.json still says whisper."""
    _use_config(monkeypatch, {})

    tasks = _startup_tasks(
        monkeypatch, [("owner", True, {"transcription_backend": "parakeet"})]
    )
    _run_on_the_worker(tasks)

    assert [task["transcription_backend"] for task in tasks] == ["parakeet"]
    assert prepared == ["pyannote", "onnx:parakeet/parakeet-tdt-0.6b-v3"]


def test_startup_keeps_whisper_while_any_user_transcribes_with_it(
    prepared, monkeypatch
):
    _use_config(monkeypatch, {"transcription_backend": "whisper"})

    tasks = _startup_tasks(
        monkeypatch,
        [
            ("user", True, {"whisper_model_size": "small"}),
            ("owner", True, {"transcription_backend": "canary"}),
        ],
    )
    _run_on_the_worker(tasks)

    # Whisper at the size its user chose, with the core batch, then the
    # owner's engine on its own.
    assert [task["transcription_backend"] for task in tasks] == ["whisper", "canary"]
    assert prepared == ["whisper:small", "pyannote", "onnx:canary/nemo-canary-1b-v2"]


def test_startup_queues_the_owners_engine_only_once_the_whisper_task_succeeds(
    monkeypatch,
):
    """Both tasks write one progress key. Queued independently, the second
    would clear a failed first task's error and end on "complete"."""
    _use_config(monkeypatch, {"transcription_backend": "whisper"})

    dispatched = _startup_dispatch(
        monkeypatch,
        [
            ("owner", True, {"transcription_backend": "parakeet"}),
            ("user", True, None),
        ],
    )

    # Only the Whisper task is sent; the owner's engine is linked to it, and
    # the worker queues a link only when the task it hangs on succeeds.
    (message,) = dispatched
    assert message["kwargs"]["transcription_backend"] == "whisper"
    follower = message["link"]
    assert follower.task == model_preparation.MODEL_PREPARATION_TASK
    assert follower.kwargs["transcription_backend"] == "parakeet"
    assert follower.kwargs["include_core"] is False
    assert follower.options["ignore_result"] is True
    assert "link" not in follower.options
    # The worker hands a link the finished task's return value as its first
    # positional argument (hf_token, here); the linked task must drop it.
    assert follower.clone(args=({"status": "success"},)).args == ()
    route = celery_app.amqp.router.route(
        dict(follower.options), follower.task, (), follower.kwargs
    )
    assert route["queue"].name == GPU_QUEUE


def test_startup_ignores_deactivated_users(prepared, monkeypatch):
    _use_config(monkeypatch, {"transcription_backend": "parakeet"})

    tasks = _startup_tasks(
        monkeypatch,
        [
            ("owner", True, None),
            ("user", False, {"transcription_backend": "whisper"}),
        ],
    )
    _run_on_the_worker(tasks)

    assert prepared == ["pyannote", "onnx:parakeet/parakeet-tdt-0.6b-v3"]


@pytest.mark.parametrize("configured", ["whisper", "parakeet"])
def test_startup_before_any_user_exists_follows_the_install_config(
    monkeypatch, configured
):
    _use_config(monkeypatch, {"transcription_backend": configured})

    tasks = _startup_tasks(monkeypatch, [])

    assert tasks == [_config_dispatch(monkeypatch)]
    assert tasks[0]["transcription_backend"] == configured


# download_models_task's keyword arguments on worker images released before
# startup read the users. Such a worker rejects any other keyword argument and
# prepares nothing, so a half-upgraded install must never be sent one.
_OLDER_WORKER_KWARGS = {
    "hf_token",
    "whisper_model_size",
    "transcription_backend",
    "parakeet_model",
    "canary_model",
    "include_core",
}


@pytest.mark.parametrize(
    ("configured", "owner_engine"),
    [("parakeet", "whisper"), ("parakeet", "parakeet"), ("whisper", "canary")],
    ids=["owner-on-whisper", "nobody-on-whisper", "others-on-whisper"],
)
def test_startup_tasks_use_only_arguments_older_workers_accept(
    monkeypatch, configured, owner_engine
):
    _use_config(monkeypatch, {"transcription_backend": configured})

    tasks = _startup_tasks(
        monkeypatch,
        [
            ("owner", True, {"transcription_backend": owner_engine}),
            ("user", True, None),
        ],
    )

    assert tasks
    assert all(set(task) <= _OLDER_WORKER_KWARGS for task in tasks)


def test_startup_does_not_count_an_empty_engine_as_whisper(prepared, monkeypatch):
    """The pipeline keeps a stored "" over config and fails, so it needs no Whisper."""
    _use_config(monkeypatch, {"transcription_backend": "whisper"})

    tasks = _startup_tasks(
        monkeypatch,
        [
            ("owner", True, {"transcription_backend": "parakeet"}),
            ("user", True, {"transcription_backend": ""}),
        ],
    )
    _run_on_the_worker(tasks)

    assert prepared == ["pyannote", "onnx:parakeet/parakeet-tdt-0.6b-v3"]


def test_startup_falls_back_to_the_config_when_the_users_cannot_be_read(
    prepared, monkeypatch, caplog
):
    _use_config(monkeypatch, {"transcription_backend": "whisper"})

    with caplog.at_level(logging.WARNING, logger=model_preparation.__name__):
        tasks = _startup_tasks(monkeypatch, [], with_users_table=False)
    _run_on_the_worker(tasks)

    assert tasks == [_config_dispatch(monkeypatch)]
    assert prepared == ["whisper:turbo", "pyannote"]
    assert "config.json decides" in caplog.text
    assert "no such table: users" in caplog.text
    # The health check polls every 30 seconds; it logs the error, not the SQL.
    assert "SELECT" not in caplog.text


def test_a_failed_users_read_keeps_the_callers_pending_work(monkeypatch):
    """The health check shares the request's session with the rest of the request."""
    _use_config(monkeypatch, {})

    async def run() -> int:
        async with _users_db([], with_users_table=False) as maker:
            async with maker() as session:
                await session.execute(text("CREATE TABLE marker (id INTEGER)"))
                await session.commit()
            async with maker() as session:
                await session.execute(text("INSERT INTO marker (id) VALUES (1)"))
                await model_preparation.resolve_install_transcription_selection(session)
                await session.commit()
            async with maker() as session:
                count = await session.execute(text("SELECT count(*) FROM marker"))
                return int(count.scalar_one())

    assert asyncio.run(run()) == 1


def test_a_failed_users_read_leaves_a_postgres_transaction_usable(
    postgres_test_url, monkeypatch
):
    """Postgres aborts the whole transaction on an error; SQLite does not.

    Only a savepoint around the read lets the caller go on using its session
    after the fallback without losing what it had already written.
    """
    _use_config(monkeypatch, {})
    importlib.import_module("backend.models.registry")
    schema = "model_preparation_fallback_test"

    async def run() -> int:
        engine = create_async_engine(
            postgres_test_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                )
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
                await connection.execute(text(f"CREATE TABLE {schema}.marker (id int)"))
            async with AsyncSession(engine) as session:
                # The schema holds no users table, so the read fails.
                await session.execute(text(f"SET search_path TO {schema}"))
                await session.execute(text("INSERT INTO marker (id) VALUES (1)"))
                await model_preparation.resolve_install_transcription_selection(session)
                await session.execute(text("INSERT INTO marker (id) VALUES (2)"))
                await session.commit()
            async with engine.connect() as connection:
                count = await connection.execute(
                    text(f"SELECT count(*) FROM {schema}.marker")
                )
                return int(count.scalar_one())
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                )
            await engine.dispose()

    assert asyncio.run(run()) == 2


def test_a_failed_flush_of_the_callers_pending_work_is_not_a_failed_read(
    monkeypatch, caplog
):
    """Opening the read's savepoint flushes the caller's objects first.

    That flush is the caller's work. Its failure must reach the caller rather
    than be logged as an unreadable users table, with the work silently gone.
    """
    _use_config(monkeypatch, {})

    async def run() -> None:
        async with _users_db([("owner", True, None)]) as maker, maker() as session:
            # A second row with the owner's id, which only the flush rejects.
            session.add(User(id=1, username="duplicate", hashed_password="x"))
            await model_preparation.resolve_install_transcription_selection(session)

    with (
        caplog.at_level(logging.WARNING, logger=model_preparation.__name__),
        pytest.raises(IntegrityError),
    ):
        asyncio.run(run())
    assert "config.json decides" not in caplog.text


@pytest.mark.parametrize(
    "malformed",
    [
        ["transcription_backend", "whisper"],
        {"transcription_backend": 7, "whisper_model_size": 3},
    ],
    ids=["settings-not-an-object", "values-of-the-wrong-type"],
)
def test_a_malformed_settings_row_falls_back_to_the_config_for_that_user_only(
    prepared, monkeypatch, malformed
):
    """One bad row must not move the owner's engine back to config.json's."""
    _use_config(monkeypatch, {"transcription_backend": "whisper"})

    tasks = _startup_tasks(
        monkeypatch,
        [
            ("owner", True, {"transcription_backend": "parakeet"}),
            ("user", True, malformed),
        ],
    )
    _run_on_the_worker(tasks)

    # The malformed user runs config.json's Whisper turbo; the owner keeps Parakeet.
    assert [task["transcription_backend"] for task in tasks] == ["whisper", "parakeet"]
    assert prepared == [
        "whisper:turbo",
        "pyannote",
        "onnx:parakeet/parakeet-tdt-0.6b-v3",
    ]


# --- Admin health check -------------------------------------------------------


def _stub_other_health_checks(monkeypatch, model_status: dict) -> list[str | None]:
    """Stub every health component but transcription; return the sizes checked."""
    ok = {"status": "ok"}

    async def ready(*args, **kwargs):
        return ok, True

    for name in (
        "_get_db_component",
        "_get_queue_component",
        "_get_worker_component",
        "_get_diarization_component",
        "_get_device_component",
    ):
        monkeypatch.setattr(health_service, name, ready)

    async def optional_ai(db):
        return ok

    monkeypatch.setattr(health_service, "_get_optional_ai_component", optional_ai)
    monkeypatch.setattr(health_service, "_get_ffmpeg_component", lambda: (ok, True))
    monkeypatch.setattr(health_service, "_get_storage_component", lambda: (ok, True))
    monkeypatch.setattr(
        health_service,
        "_current_download_summary",
        lambda: {"in_progress": False, "stage": None},
    )
    checked_sizes: list[str | None] = []

    def check_model_status(whisper_model_size=None):
        checked_sizes.append(whisper_model_size)
        return model_status

    monkeypatch.setattr(health_service, "check_model_status", check_model_status)
    return checked_sizes


def _admin_health(users: list[tuple[str, bool, object]]) -> dict:
    async def run() -> dict:
        async with _users_db(users) as maker, maker() as session:
            return await health_service.get_admin_health_status(session)

    return asyncio.run(run())


def test_admin_health_reports_the_engine_startup_prepared(monkeypatch):
    """Owner on Parakeet, config.json on whisper, Whisper deleted from the cache."""
    _use_config(monkeypatch, {"transcription_backend": "whisper"})
    _stub_other_health_checks(
        monkeypatch,
        {
            "whisper": {"downloaded": False, "path": None},
            "parakeet": {"downloaded": True, "path": "/cache/parakeet"},
        },
    )

    health = _admin_health([("owner", True, {"transcription_backend": "parakeet"})])

    component = health["checks"]["transcription_model"]
    assert component["backend"] == "parakeet"
    assert component["label"] == "Ready"
    assert health["summary"]["pipeline_status"] == "ready"


def test_admin_health_checks_the_whisper_size_the_owner_chose(monkeypatch):
    """Owner on Whisper small in Settings, config.json on Whisper turbo."""
    _use_config(
        monkeypatch, {"transcription_backend": "whisper", "whisper_model_size": "turbo"}
    )
    checked_sizes = _stub_other_health_checks(
        monkeypatch, {"whisper": {"downloaded": True, "path": "/cache/small.pt"}}
    )

    health = _admin_health(
        [
            (
                "owner",
                True,
                {"transcription_backend": "whisper", "whisper_model_size": "small"},
            )
        ]
    )

    assert checked_sizes == ["small"]
    assert health["checks"]["transcription_model"]["configured_model"] == "small"
