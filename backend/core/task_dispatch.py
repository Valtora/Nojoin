"""Queueing Celery work from async code without blocking the event loop.

``celery_app.send_task`` is a blocking socket call. The API dispatches from
``async def`` handlers, so calling it inline stalls that process's event loop
and every request it is serving, not only the one dispatching. ADR-0007 bounds
how long an unreachable Redis can make that last; these helpers stop it
happening at all.

Worker-side code calls ``celery_app.send_task`` directly and should keep doing
so. It runs on its own thread, with no event loop to protect.
"""

import asyncio
import logging
from typing import Any, Optional

from celery.result import AsyncResult

from backend.celery_app import celery_app

logger = logging.getLogger(__name__)


async def dispatch_task(
    name: str,
    *,
    args: Optional[list] = None,
    kwargs: Optional[dict] = None,
    **options: Any,
) -> AsyncResult:
    """Queue a Celery task from async code, off the event loop.

    Uses ``asyncio.to_thread`` rather than Starlette's threadpool for two
    reasons. This module is imported by code shared with the worker, whose
    image ships no ASGI stack. And the loop's default executor is separate from
    the anyio limiter that serves sync route handlers, so a Redis outage slows
    dispatching without consuming the threads those handlers need.

    Raises whatever the publish raises. Callers that must not fail on a
    dispatch failure should use :func:`dispatch_task_best_effort` instead, so
    that the choice to swallow is visible at the call site.
    """
    return await asyncio.to_thread(
        celery_app.send_task, name, args=args, kwargs=kwargs, **options
    )


async def dispatch_task_best_effort(
    name: str,
    *,
    args: Optional[list] = None,
    kwargs: Optional[dict] = None,
    context: str = "",
    **options: Any,
) -> Optional[AsyncResult]:
    """Queue a task whose failure must not fail the request that triggered it.

    For work the caller does not depend on, where the user's actual action has
    already succeeded and failing the response would discard it. Returns
    ``None`` when the dispatch failed, so a caller that wants to know can ask.

    ``context`` is appended to the warning log to identify the subject, since
    the task name alone rarely says which recording or user was involved.
    """
    try:
        return await dispatch_task(name, args=args, kwargs=kwargs, **options)
    except Exception as exc:  # noqa: BLE001 -- boundary: best-effort by design
        logger.warning(
            "Failed to dispatch %s%s: %s",
            name,
            f" ({context})" if context else "",
            exc,
        )
        return None
