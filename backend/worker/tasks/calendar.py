from .constants import *


@celery_app.task(name="backend.worker.tasks.sync_calendar_connection_task", bind=True)
def sync_calendar_connection_task(self, connection_id: int):
    """
    Refresh a single connected calendar account.
    """
    import asyncio

    from backend.services.calendar_service import sync_connection_by_id

    asyncio.run(sync_connection_by_id(connection_id))
    return {"status": "success", "connection_id": connection_id}


@celery_app.task(name="backend.worker.tasks.sync_calendar_connections_task", bind=True)
def sync_calendar_connections_task(self):
    """
    Periodic sync for all selected calendar connections.
    """
    import asyncio

    from backend.services.calendar_service import sync_all_connections

    synced_connections = asyncio.run(sync_all_connections())
    return {"status": "success", "connections_synced": synced_connections}


__all__ = [name for name in globals() if not name.startswith("__")]
