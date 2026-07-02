from .constants import *


def _run_calendar_async(coro):
    """Run an async calendar coroutine from a synchronous Celery task.

    Each task invocation runs ``asyncio.run`` in a fresh event loop. The async
    DB engine pools connections bound to the loop that opened them, so a pooled
    connection from a previous task's loop fails in this one with "got Future
    attached to a different loop". Disposing the engine inside this loop, before
    it closes, releases those connections so the next task starts clean.
    """
    import asyncio

    from backend.core.db import engine

    async def _runner():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_runner())


@celery_app.task(name="backend.worker.tasks.sync_calendar_connection_task", bind=True)
def sync_calendar_connection_task(self, connection_id: int):
    """
    Refresh a single connected calendar account.
    """
    from backend.services.calendar_service import sync_connection_by_id

    _run_calendar_async(sync_connection_by_id(connection_id))
    return {"status": "success", "connection_id": connection_id}


@celery_app.task(name="backend.worker.tasks.sync_calendar_connections_task", bind=True)
def sync_calendar_connections_task(self):
    """
    Periodic sync for all selected calendar connections.
    """
    from backend.services.calendar_service import sync_all_connections

    synced_connections = _run_calendar_async(sync_all_connections())
    return {"status": "success", "connections_synced": synced_connections}


@celery_app.task(
    name="backend.worker.tasks.ensure_calendar_push_channels_task", bind=True
)
def ensure_calendar_push_channels_task(self, connection_id: int):
    """
    Provision, renew, or tear down push (webhook) channels for one connection.
    """
    from backend.services.calendar_push_service import (
        ensure_push_channels_for_connection,
    )

    _run_calendar_async(ensure_push_channels_for_connection(connection_id))
    return {"status": "success", "connection_id": connection_id}


@celery_app.task(
    name="backend.worker.tasks.renew_calendar_push_channels_task", bind=True
)
def renew_calendar_push_channels_task(self):
    """
    Periodic sweep to provision, renew, and clean up calendar push channels.
    """
    from backend.services.calendar_push_service import renew_push_channels

    reconciled = _run_calendar_async(renew_push_channels())
    return {"status": "success", "connections_reconciled": reconciled}


__all__ = [name for name in globals() if not name.startswith("__")]
