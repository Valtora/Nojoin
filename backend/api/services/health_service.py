from __future__ import annotations

from typing import Any
import os

import redis.asyncio as redis
from sqlmodel import Session, text

from backend.celery_app import celery_app
from backend.core.db import sync_engine

APP_HEALTH_VERSION = "2.0.0"


async def get_system_health_status() -> dict[str, Any]:
    health_status = {
        "status": "ok",
        "version": APP_HEALTH_VERSION,
        "components": {
            "db": "unknown",
            "worker": "unknown",
        },
    }

    try:
        with Session(sync_engine) as session:
            session.execute(text("SELECT 1"))
        health_status["components"]["db"] = "connected"
    except Exception:
        health_status["components"]["db"] = "disconnected"
        health_status["status"] = "error"

    worker_status = "unknown"

    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        client = redis.from_url(redis_url)
        if await client.get("nojoin:worker:heartbeat"):
            worker_status = "active"
        await client.close()
    except Exception:
        pass

    if worker_status != "active":
        try:
            inspector = celery_app.control.inspect()
            active_workers = inspector.ping()

            if active_workers:
                worker_status = "active"
            else:
                worker_status = "inactive"
        except Exception:
            worker_status = "error"

    health_status["components"]["worker"] = worker_status

    if worker_status in ["inactive", "error"] and health_status["status"] == "ok":
        health_status["status"] = "warning"

    return health_status