"""Machinery for the suite's "no test may reach Redis" guard.

Lives outside `conftest.py` so it has one module identity. pytest imports
`conftest.py` as the top-level module `conftest`, so a test importing
`backend.tests.conftest` would load the file a second time and get a *different*
exception class that `pytest.raises` would not match.

The fixtures that use this are in `conftest.py`.
"""

import importlib
import pkgutil
import traceback
from types import ModuleType

from backend.celery_app import celery_app


class RedisContactedInTests(RuntimeError):
    """Raised instead of opening a real Redis connection during a test.

    Deliberately not a ``ConnectionError``: redis-py retries those internally
    with backoff, which would reintroduce the multi-second stall this guard
    exists to remove. A plain ``RuntimeError`` is retried by nobody and
    propagates on the first attempt.
    """


def harden_celery_retry_policies() -> None:
    """Make a dispatch that reaches no broker fail in milliseconds.

    ``celery_app.send_task`` touches Redis twice, and neither call is optional
    at the call site. It first subscribes the *result backend* to the new task's
    pubsub channel (``backend.on_task_call``), then publishes on the *broker*.
    No Redis runs during tests, and both subsystems retry rather than fail: the
    result backend for 19s (20 attempts roughly a second apart) and the
    publisher for a few more.

    That cost is invisible, because every best-effort dispatcher in the API
    swallows the eventual failure. A test that forgets to stub a dispatch still
    passes; it just takes 19s longer.
    """
    celery_app.conf.task_publish_retry = False
    celery_app.conf.broker_connection_retry = False
    celery_app.conf.broker_connection_max_retries = 0
    celery_app.conf.task_publish_retry_policy = {"max_retries": 0}
    # The publish settings above do not cover the result backend, which is the
    # expensive half: `RedisBackend` keeps its own policy (20 retries, ~1s
    # apart) that no top-level Celery setting reaches. Zero means "do not
    # retry", so the first refused connection propagates immediately.
    #
    # Set through the same transport-options surface the API uses (see
    # `apply_api_dispatch_limits`) rather than by assigning to the backend, so
    # that building an app during a test re-derives a bounded policy instead of
    # discarding this one.
    celery_app.conf.result_backend_transport_options = {
        **celery_app.conf.result_backend_transport_options,
        "retry_policy": {
            "max_retries": 0,
            "interval_start": 0,
            "interval_step": 0,
            "interval_max": 0,
        },
    }
    celery_app._backend_cache = None
    celery_app._local.__dict__.pop("backend", None)


def app_frames(stack: list[str]) -> str:
    """Keep this project's own frames from a formatted stack.

    The raw stack is dozens of frames of pytest, anyio and httpx internals
    wrapped around the two or three that name the code which reached Redis.
    Reporting all of them buries the answer.
    """
    frames = [
        frame
        for frame in stack
        if "/backend/" in frame and "/backend/tests/" not in frame
    ]
    return "".join(frames or stack[-8:])


def record_and_refuse(attempts: list[str]):
    """Build the replacement for ``AbstractConnection.connect``."""

    def _refuse(self, *args, **kwargs):
        attempts.append(app_frames(traceback.format_stack()[:-1]))
        raise RedisContactedInTests(
            "A test tried to open a real Redis connection. Stub the dispatch "
            "(celery_app.send_task) or the Redis client instead."
        )

    return _refuse


def transcripts_route_modules() -> list[ModuleType]:
    """Every submodule of the transcripts endpoint package.

    Imported by name rather than read off the package, because a
    ``from .helpers import ...`` binding lives in the importing module and is
    fixed at import time. Walking the package finds those bindings wherever the
    routes are currently split, so a future decomposition does not quietly
    strand a stub on a module nobody calls.
    """
    package = importlib.import_module("backend.api.v1.endpoints.transcripts")
    modules = [package]
    for info in pkgutil.iter_modules(package.__path__):
        modules.append(importlib.import_module(f"{package.__name__}.{info.name}"))
    return modules
