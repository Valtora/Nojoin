"""Starting queued notes renews the same claim before the first heartbeat."""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, text

from backend.tests.test_notes_cancel import (
    BlockingModel,
    cancel,
    generate,
    state,
    use_model,
    worker,
)
from backend.tests.test_notes_cancel import (
    client as client,
)
from backend.tests.test_notes_cancel import (
    database as database,
)
from backend.tests.test_notes_cancel import (
    engine as engine,
)
from backend.tests.test_notes_cancel import (
    maker as maker,
)
from backend.utils.time import utc_now

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stub_text_embedding")]


@pytest.mark.parametrize("automatic", [False, True], ids=["manual", "deferred"])
async def test_queued_worker_renews_its_claim_without_replacing_ownership(
    client: AsyncClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: list,
    automatic: bool,
) -> None:
    token = await generate(client)
    queued_at = utc_now() - timedelta(minutes=14, seconds=59)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE transcripts SET updated_at=:queued_at WHERE recording_id=1"),
            {"queued_at": queued_at},
        )
    before = state(engine)
    model = BlockingModel(threading.Event(), threading.Event())
    use_model(monkeypatch, model)
    running = asyncio.create_task(asyncio.to_thread(worker, engine, token, automatic))
    try:
        assert await asyncio.to_thread(model.entered.wait, 10), (
            "worker never reached model"
        )
        during = state(engine)
        assert during["updated_at"] > queued_at + timedelta(minutes=14)
        assert during["notes_run_id"] == UUID(token)
        assert during["notes_progress"] == before["notes_progress"]
        assert during["error_message"] == before["error_message"]
        assert during["notes_status"] == "generating"
        await cancel(client, token)
    finally:
        model.release.set()
        await running
