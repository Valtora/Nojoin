"""The idle restart of the solo worker (issue #1054).

An idle worker-gpu keeps what its ML libraries hold after the models are
released, and only an exit returns it. So once it has run a task and then sat
idle for the limit, with no capture recording or paused, it shuts down warm and
executes its own command line again. These tests pin each condition, the off
switch, the solo-only rule and the restart itself; the end-to-end run on a real
worker is in the pull request.
"""

from __future__ import annotations

import logging
import sys
import threading
import types
import weakref
from contextlib import contextmanager
from typing import Any

import pytest
from celery.concurrency.prefork import TaskPool as PreforkPool
from celery.concurrency.solo import TaskPool as SoloPool
from celery.exceptions import WorkerShutdown
from celery.signals import task_postrun, task_prerun, worker_ready, worker_shutdown
from celery.worker import state as worker_state
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

import backend.celery_app as celery_app_module
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.utils import worker_idle_recycle as recycle
from backend.utils.worker_idle_recycle import IdleClock

LIMIT = 900.0


def _no_capture() -> bool:
    return False


def _ran_one_task(finished_at: float = 1000.0) -> IdleClock:
    clock = IdleClock(LIMIT, now=0.0)
    clock.task_started()
    clock.task_finished(finished_at)
    return clock


# -- the setting ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [("", 900.0), ("  ", 900.0), ("15", 900.0), ("0", 0.0), ("2.5", 150.0)],
)
def test_the_limit_is_read_in_minutes(raw: str, seconds: float) -> None:
    assert recycle.idle_limit_seconds(raw) == seconds


def test_an_unset_variable_means_fifteen_minutes(monkeypatch) -> None:
    monkeypatch.delenv(recycle.IDLE_RECYCLE_ENV, raising=False)
    assert recycle.idle_limit_seconds() == 900.0
    monkeypatch.setenv(recycle.IDLE_RECYCLE_ENV, "0")
    assert recycle.idle_limit_seconds() == 0.0


@pytest.mark.parametrize("raw", ["soon", "-1", "nan", "inf"])
def test_an_unusable_value_falls_back_to_the_default(raw: str, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=recycle.__name__):
        assert recycle.idle_limit_seconds(raw) == 900.0
    assert recycle.IDLE_RECYCLE_ENV in caplog.text


def test_the_timer_checks_a_quarter_of_the_limit_within_bounds() -> None:
    assert recycle.poll_interval(900.0) == 60.0
    assert recycle.poll_interval(15.0) == 3.75
    assert recycle.poll_interval(2.0) == 1.0


# -- the decision ----------------------------------------------------------------


def test_it_restarts_once_the_limit_has_passed_since_the_last_task() -> None:
    clock = _ran_one_task(finished_at=1000.0)
    check = dict(holds_tasks=False, capture_in_progress=_no_capture)

    assert clock.should_restart(1000.0 + LIMIT - 1, **check) is False
    assert clock.should_restart(1000.0 + LIMIT, **check) is True


def test_a_fresh_worker_never_restarts() -> None:
    """A restarted worker has run nothing, so it cannot restart in a loop."""
    clock = IdleClock(LIMIT, now=0.0)
    assert (
        clock.should_restart(
            10 * LIMIT, holds_tasks=False, capture_in_progress=_no_capture
        )
        is False
    )


def test_the_clock_counts_from_when_a_task_ends_not_when_it_starts() -> None:
    clock = IdleClock(LIMIT, now=0.0)
    clock.task_started()
    check = dict(holds_tasks=False, capture_in_progress=_no_capture)
    # A task running for longer than the limit holds the worker.
    assert clock.should_restart(2 * LIMIT, **check) is False

    clock.task_finished(2 * LIMIT)
    assert clock.should_restart(3 * LIMIT - 1, **check) is False
    assert clock.should_restart(3 * LIMIT, **check) is True


def test_a_task_running_or_held_blocks_it() -> None:
    clock = _ran_one_task()
    later = 1000.0 + 2 * LIMIT
    assert (
        clock.should_restart(later, holds_tasks=True, capture_in_progress=_no_capture)
        is False
    )

    clock.task_started()
    assert (
        clock.should_restart(later, holds_tasks=False, capture_in_progress=_no_capture)
        is False
    )


def test_zero_turns_it_off() -> None:
    clock = IdleClock(0.0, now=0.0)
    clock.task_started()
    clock.task_finished(1.0)
    assert (
        clock.should_restart(
            10_000.0, holds_tasks=False, capture_in_progress=_no_capture
        )
        is False
    )


def test_the_capture_is_checked_only_once_the_limit_has_passed() -> None:
    clock = _ran_one_task(finished_at=1000.0)
    asked: list[float] = []

    def capture() -> bool:
        asked.append(1.0)
        return False

    clock.should_restart(
        1000.0 + LIMIT / 2, holds_tasks=False, capture_in_progress=capture
    )
    assert asked == []
    clock.should_restart(1000.0 + LIMIT, holds_tasks=False, capture_in_progress=capture)
    assert asked == [1.0]


def test_a_capture_holds_the_clock_until_it_ends() -> None:
    """Recording or paused, a capture blocks the restart. It is asked at every
    check while it blocks, and the limit then counts from the last check that
    still saw it, so a capture's end starts the clock within one poll."""
    clock = _ran_one_task(finished_at=0.0)
    capture = {"open": True}
    asked: list[float] = []

    def in_progress() -> bool:
        asked.append(1.0)
        return capture["open"]

    check = dict(holds_tasks=False, capture_in_progress=in_progress)
    for at in (LIMIT, LIMIT + 60, LIMIT + 120):
        assert clock.should_restart(at, **check) is False
    assert len(asked) == 3

    capture["open"] = False
    last_seen = LIMIT + 120
    assert clock.should_restart(last_seen + 60, **check) is False
    assert clock.should_restart(last_seen + LIMIT - 1, **check) is False
    assert clock.should_restart(last_seen + LIMIT, **check) is True


def test_a_task_after_a_capture_restarts_the_clock_as_usual() -> None:
    clock = _ran_one_task(finished_at=0.0)
    check = dict(holds_tasks=False, capture_in_progress=lambda: True)
    assert clock.should_restart(LIMIT, **check) is False

    clock.task_started()
    clock.task_finished(3 * LIMIT)
    check = dict(holds_tasks=False, capture_in_progress=_no_capture)
    assert clock.should_restart(4 * LIMIT - 1, **check) is False
    assert clock.should_restart(4 * LIMIT, **check) is True


# -- the capture check -------------------------------------------------------------


@pytest.fixture
def recordings_db(monkeypatch, tmp_path):
    import backend.core.db as db_module

    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    SQLModel.metadata.create_all(engine, tables=[Recording.__table__])

    @contextmanager
    def sync_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(db_module, "get_sync_session", sync_session)

    def with_rows(*recordings: Recording) -> None:
        with Session(engine) as session:
            session.execute(text("DELETE FROM recordings"))
            for index, recording in enumerate(recordings):
                # SQLite does not number a BIGINT key by itself.
                recording.id = index + 1
                recording.audio_path = f"/tmp/idle-{index}.wav"
                session.add(recording)
            session.commit()

    return with_rows


def _row(
    status: RecordingStatus,
    client_status: ClientStatus | None = None,
    *,
    is_deleted: bool = False,
) -> Recording:
    return Recording(
        name="Weekly sync",
        status=status,
        client_status=client_status,
        is_deleted=is_deleted,
    )


@pytest.mark.parametrize(
    ("row", "blocks"),
    [
        (_row(RecordingStatus.UPLOADING, ClientStatus.RECORDING), True),
        (_row(RecordingStatus.PAUSED, ClientStatus.PAUSED), True),
        # Paused before captures recorded a client status.
        (_row(RecordingStatus.PAUSED), True),
        (_row(RecordingStatus.UPLOADING, ClientStatus.PAUSED), True),
        # A file import sits in UPLOADING too, and is not a capture.
        (_row(RecordingStatus.UPLOADING), False),
        (_row(RecordingStatus.UPLOADING, ClientStatus.IDLE), False),
        (
            _row(RecordingStatus.UPLOADING, ClientStatus.RECORDING, is_deleted=True),
            False,
        ),
        (_row(RecordingStatus.PROCESSED, ClientStatus.RECORDING), False),
        (_row(RecordingStatus.QUEUED, ClientStatus.IDLE), False),
    ],
)
def test_live_and_paused_captures_block_the_restart(recordings_db, row, blocks) -> None:
    recordings_db(row)
    assert recycle.capture_in_progress() is blocks


def test_a_paused_capture_still_lets_the_models_go_after_a_task(
    recordings_db, monkeypatch
) -> None:
    """The restart's own query counts a paused capture; the per-task release
    gate keeps its meaning, so a pause still releases the models."""
    recordings_db(_row(RecordingStatus.PAUSED, ClientStatus.PAUSED))
    monkeypatch.setattr(celery_app_module, "_live_capture_check_cache", None)

    assert recycle.capture_in_progress() is True
    assert celery_app_module._has_active_live_capture() is False


def test_an_unreadable_database_counts_as_a_capture(monkeypatch) -> None:
    import backend.core.db as db_module

    def broken():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db_module, "get_sync_session", broken)
    assert recycle.capture_in_progress() is True


# -- arming it on a worker -----------------------------------------------------------


class _Hub:
    def __init__(self) -> None:
        self.timers: list[tuple[float, Any]] = []

    def call_repeatedly(self, delay: float, callback: Any, *args: Any) -> None:
        self.timers.append((delay, callback))


def _consumer(pool_cls: type, hub: _Hub | None) -> types.SimpleNamespace:
    """The ``sender`` of ``worker_ready``: the worker's consumer."""
    return types.SimpleNamespace(pool=pool_cls.__new__(pool_cls), hub=hub)


@pytest.fixture
def no_clock(monkeypatch):
    monkeypatch.setattr(recycle, "_clock", None)


def test_a_solo_worker_arms_the_timer_on_its_event_loop(monkeypatch, no_clock) -> None:
    monkeypatch.setenv(recycle.IDLE_RECYCLE_ENV, "15")
    hub = _Hub()

    recycle.start_idle_restart(sender=_consumer(SoloPool, hub))

    assert hub.timers == [(60.0, recycle.check_idle)]
    assert recycle._clock is not None and recycle._clock.limit_s == 900.0


def test_a_prefork_lane_never_arms_it(monkeypatch, no_clock) -> None:
    monkeypatch.setenv(recycle.IDLE_RECYCLE_ENV, "15")
    hub = _Hub()

    recycle.start_idle_restart(sender=_consumer(PreforkPool, hub))

    assert hub.timers == []
    assert recycle._clock is None


def test_zero_arms_nothing(monkeypatch, no_clock) -> None:
    monkeypatch.setenv(recycle.IDLE_RECYCLE_ENV, "0")
    hub = _Hub()

    recycle.start_idle_restart(sender=_consumer(SoloPool, hub))

    assert hub.timers == []
    assert recycle._clock is None


def test_without_an_event_loop_it_stays_off(monkeypatch, no_clock, caplog) -> None:
    monkeypatch.delenv(recycle.IDLE_RECYCLE_ENV, raising=False)
    with caplog.at_level(logging.WARNING, logger=recycle.__name__):
        recycle.start_idle_restart(sender=_consumer(SoloPool, None))
    assert recycle._clock is None
    assert "no event loop" in caplog.text


def _receivers(signal: Any) -> list[Any]:
    found = []
    for _key, receiver in signal.receivers:
        if isinstance(receiver, weakref.ReferenceType):
            receiver = receiver()
        found.append(receiver)
    return found


def test_importing_the_celery_app_connects_the_restart() -> None:
    assert recycle.start_idle_restart in _receivers(worker_ready)
    assert recycle._task_started in _receivers(task_prerun)
    assert recycle._task_finished in _receivers(task_postrun)


def test_task_signals_move_the_clock(monkeypatch) -> None:
    clock = IdleClock(LIMIT, now=0.0)
    monkeypatch.setattr(recycle, "_clock", clock)
    monkeypatch.setattr(recycle, "time", types.SimpleNamespace(monotonic=lambda: 42.0))

    recycle._task_started()
    assert clock.running == 1
    recycle._task_finished()
    assert (clock.running, clock.tasks_run, clock.idle_since) == (0, 1, 42.0)


# -- the restart -----------------------------------------------------------------------


@pytest.fixture
def due_clock(monkeypatch):
    clock = _ran_one_task(finished_at=0.0)
    monkeypatch.setattr(recycle, "_clock", clock)
    monkeypatch.setattr(
        recycle, "time", types.SimpleNamespace(monotonic=lambda: LIMIT + 30.0)
    )
    monkeypatch.setattr(recycle, "capture_in_progress", _no_capture)
    yield clock
    worker_shutdown.disconnect(dispatch_uid=recycle.__name__)


def test_when_due_it_logs_once_and_shuts_down_into_the_restart(
    due_clock, caplog
) -> None:
    with caplog.at_level(logging.INFO, logger=recycle.__name__):
        with pytest.raises(WorkerShutdown) as stopped:
            recycle.check_idle()

    assert stopped.value.code == 0
    assert recycle.exec_self in _receivers(worker_shutdown)
    lines = [r for r in caplog.records if r.name == recycle.__name__]
    assert len(lines) == 1
    assert lines[0].levelno == logging.INFO
    assert "Idle for 15.5 minutes" in lines[0].getMessage()
    assert "load again on the next task" in lines[0].getMessage()


def test_before_the_limit_a_check_is_silent(due_clock, monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        recycle, "time", types.SimpleNamespace(monotonic=lambda: LIMIT - 1.0)
    )
    with caplog.at_level(logging.DEBUG, logger=recycle.__name__):
        assert recycle.check_idle() is None
    assert [r for r in caplog.records if r.name == recycle.__name__] == []
    assert recycle.exec_self not in _receivers(worker_shutdown)


def test_a_capture_blocks_the_check(due_clock, monkeypatch) -> None:
    monkeypatch.setattr(recycle, "capture_in_progress", lambda: True)
    assert recycle.check_idle() is None
    assert recycle.exec_self not in _receivers(worker_shutdown)


def test_a_task_the_worker_holds_blocks_the_check(due_clock, monkeypatch) -> None:
    """A message reserved for later, such as a retry with a countdown, counts."""
    monkeypatch.setattr(worker_state, "reserved_requests", {"task-held"})
    assert recycle.check_idle() is None


def test_the_restart_runs_the_same_command_in_this_process(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        recycle.os, "execv", lambda path, argv: calls.append((path, argv))
    )
    monkeypatch.setattr(
        sys, "orig_argv", ["/opt/venv/bin/python", "/opt/venv/bin/celery", "worker"]
    )

    recycle.exec_self(sender=None)

    assert calls == [
        (sys.executable, ["/opt/venv/bin/python", "/opt/venv/bin/celery", "worker"])
    ]


def test_a_failed_exec_is_logged_and_the_worker_exits(monkeypatch, caplog) -> None:
    def refuse(path, argv):
        raise OSError("exec format error")

    monkeypatch.setattr(recycle.os, "execv", refuse)
    with caplog.at_level(logging.ERROR, logger=recycle.__name__):
        recycle.exec_self(sender=None)
    assert "Could not restart the worker in place" in caplog.text


# -- the heartbeat ---------------------------------------------------------------------


def test_the_heartbeat_thread_stops_without_waiting_out_its_interval(
    monkeypatch,
) -> None:
    """The heartbeat's sleep held every warm shutdown, and so every idle
    restart, for up to its five-second interval."""

    class _Redis:
        def set(self, *args: Any, **kwargs: Any) -> None:
            beat.set()

    beat = threading.Event()
    monkeypatch.setattr(celery_app_module.redis, "from_url", lambda url: _Redis())
    thread = celery_app_module.HeartbeatThread("redis://unused", interval=5.0)
    thread.start()
    assert beat.wait(2.0)

    thread.stop()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
