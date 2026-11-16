"""Restart an idle solo worker in place, so it hands back what its ML stack holds.

worker-gpu releases its models after every task, but the process keeps what
importing torch and the pyannote stack and running a first inference left
behind, about 1.5 GB on a CUDA host, and only an exit returns it (#1054). Once
the worker has run a task and then sat idle for NOJOIN_WORKER_IDLE_RECYCLE_MINUTES
with no capture recording or paused, it shuts down warm and runs its own command
line again in the same process: same PID, container and environment, with no
reliance on a restart policy. Models load again on the next task.

The check runs on the event loop's timer. The solo pool runs each task inline on
that thread, so none is running when it fires, and raising WorkerShutdown there
ends the loop before it reads another message: a task sent at that moment stays
queued for the restarted worker. Prefork lanes never restart this way.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from celery.signals import task_postrun, task_prerun, worker_ready, worker_shutdown

logger = logging.getLogger(__name__)

IDLE_RECYCLE_ENV = "NOJOIN_WORKER_IDLE_RECYCLE_MINUTES"
DEFAULT_IDLE_MINUTES = 15.0
# Set in the environment the restarted process inherits, so its startup sweep
# can tell an idle restart from a cold start. Internal, not an operator setting.
IDLE_RESTART_MARKER = "NOJOIN_WORKER_IDLE_RESTARTED"


def idle_limit_seconds(raw: str | None = None) -> float:
    """The idle limit from the environment, in seconds. 0 means never restart."""
    value = (os.environ.get(IDLE_RECYCLE_ENV, "") if raw is None else raw).strip()
    if not value:
        return DEFAULT_IDLE_MINUTES * 60
    try:
        minutes = float(value)
    except ValueError:
        minutes = math.nan
    if not math.isfinite(minutes) or minutes < 0:
        logger.warning(
            "Ignoring %s=%r; using %g minutes.",
            IDLE_RECYCLE_ENV,
            value,
            DEFAULT_IDLE_MINUTES,
        )
        return DEFAULT_IDLE_MINUTES * 60
    return minutes * 60


def restarted_when_idle() -> bool:
    """Whether this process began as an idle restart of the worker.

    The restart happens only with no task running or reserved, so nothing was
    lost with the old process and every waiting recording still has its message
    queued. A container start or a crash begins without the marker.
    """
    return os.environ.get(IDLE_RESTART_MARKER) == "1"


def skip_startup_sweep() -> bool:
    """For the GPU lane's startup sweep: True after an idle restart, logged once.

    Sending a QUEUED recording's task again would process the recording twice,
    and a reprocess sent again by id alone would also lose its chosen engine.
    """
    if not restarted_when_idle():
        return False
    logger.info("Idle restart: skipping the startup sweep of pending recordings.")
    return True


def poll_interval(limit_s: float) -> float:
    """How often the timer checks: a quarter of the limit, between 1 s and 60 s."""
    return min(60.0, max(1.0, limit_s / 4))


class IdleClock:
    """When the worker last finished a task or saw a capture, and whether it may
    restart now."""

    def __init__(self, limit_s: float, now: float) -> None:
        self.limit_s = limit_s
        self.running = 0
        self.tasks_run = 0
        self.idle_since = now
        self.capture_seen_at: float | None = None

    def task_started(self) -> None:
        self.running += 1

    def task_finished(self, now: float) -> None:
        self.running = max(0, self.running - 1)
        self.tasks_run += 1
        self.idle_since = now

    def should_restart(
        self,
        now: float,
        *,
        holds_tasks: bool,
        capture_in_progress: Callable[[], bool],
    ) -> bool:
        # A fresh worker has run nothing, so a restarted one never loops.
        if self.limit_s <= 0 or self.running or holds_tasks or not self.tasks_run:
            return False
        if now - self.idle_since < self.limit_s:
            return False
        # Asked at every check from here on, so the limit counts from the last
        # check that still saw a capture, within one poll of its end.
        if capture_in_progress():
            self.capture_seen_at = now
            return False
        seen = self.capture_seen_at
        return seen is None or now - seen >= self.limit_s


def capture_in_progress() -> bool:
    """Whether any live capture is recording or paused.

    Unlike the per-task release gate in celery_app, a paused capture counts:
    its resume should not wait for a cold start. A failed check counts as a
    capture, since a restart is never urgent.
    """
    try:
        from sqlmodel import col, select

        from backend.core.db import get_sync_session
        from backend.models.recording import (
            Recording,
            RecordingStatus,
            live_capture_clause,
        )

        open_statuses = [RecordingStatus.UPLOADING, RecordingStatus.PAUSED]
        with get_sync_session() as session:
            row = session.exec(
                select(Recording.id)
                .where(col(Recording.status).in_(open_statuses))
                .where(live_capture_clause())
                .where(col(Recording.is_deleted).is_(False))
                .limit(1)
            ).first()
    except Exception as exc:  # noqa: BLE001 -- boundary: an unreadable database keeps the worker as it is rather than restart it under a capture
        logger.debug("Capture check failed; not restarting: %s", exc)
        return True
    return row is not None


_clock: IdleClock | None = None


@task_prerun.connect
def _task_started(**_kwargs: Any) -> None:
    if _clock is not None:
        _clock.task_started()


@task_postrun.connect
def _task_finished(**_kwargs: Any) -> None:
    if _clock is not None:
        _clock.task_finished(time.monotonic())


@worker_ready.connect
def start_idle_restart(sender: Any = None, **_kwargs: Any) -> None:
    """Arm the idle restart on a solo worker's event loop."""
    global _clock
    from celery.concurrency.solo import TaskPool as SoloPool

    if not isinstance(getattr(sender, "pool", None), SoloPool):
        return
    limit = idle_limit_seconds()
    if limit <= 0:
        return
    hub = getattr(sender, "hub", None)
    if hub is None:
        logger.warning("This worker has no event loop, so it never restarts when idle.")
        return
    _clock = IdleClock(limit, time.monotonic())
    hub.call_repeatedly(poll_interval(limit), check_idle)


def check_idle() -> None:
    """Hub timer: start the warm shutdown that ends in a restart, when due."""
    from celery.exceptions import WorkerShutdown
    from celery.worker import state as worker_state

    clock = _clock
    if clock is None:
        return
    now = time.monotonic()
    busy = bool(worker_state.reserved_requests or worker_state.active_requests)
    if not clock.should_restart(
        now, holds_tasks=busy, capture_in_progress=capture_in_progress
    ):
        return
    logger.info(
        "Idle for %.1f minutes: restarting this worker in place to free memory. "
        "Models load again on the next task.",
        (now - clock.idle_since) / 60,
    )
    worker_shutdown.connect(exec_self, weak=False, dispatch_uid=__name__)
    raise WorkerShutdown(0)


def exec_self(**_kwargs: Any) -> None:
    """worker_shutdown: run the worker's own command line again in this process."""
    os.environ[IDLE_RESTART_MARKER] = "1"
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        if stream is not None:
            try:
                stream.flush()
            except (OSError, ValueError):
                pass
    try:
        os.execv(sys.executable, sys.orig_argv)
    except OSError as exc:
        logger.error("Could not restart the worker in place, so it exits: %s", exc)
