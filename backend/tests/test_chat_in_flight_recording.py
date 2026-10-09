"""Meeting Chat refuses a recording whose transcript is still in flight.

The recording view blanks the transcript while a recording is paused,
uploading, queued or processing, and the UI hides the chat panel. A direct API
call must get the same answer instead of a reply built from provisional rows.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from backend.api.v1.endpoints.transcripts import routes_chat
from backend.api.v1.endpoints.transcripts.helpers import ChatRequest
from backend.celery_app import celery_app
from backend.models.recording import IN_FLIGHT_TRANSCRIPT_STATUSES
from backend.tests.test_chat_cli_relay_api import _make_session_maker


@pytest.mark.parametrize(
    "status", sorted(status.value for status in IN_FLIGHT_TRANSCRIPT_STATUSES)
)
def test_chat_is_refused_while_the_recording_is_in_flight(monkeypatch, status):
    dispatched: list[str] = []
    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda name, *a, **k: dispatched.append(name),
    )

    async def _run():
        engine, maker = await _make_session_maker()
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE recordings SET status = :status WHERE id = 1"),
                {"status": status},
            )
        user = SimpleNamespace(id=1, settings={})
        async with maker() as session:
            with pytest.raises(HTTPException) as refused:
                await routes_chat.chat_with_meeting(
                    "p1", ChatRequest(message="Hi"), db=session, current_user=user
                )
        async with engine.connect() as connection:
            saved = await connection.execute(text("SELECT COUNT(*) FROM chat_messages"))
            saved_messages = saved.scalar_one()
        await engine.dispose()
        return refused.value, saved_messages

    refusal, saved_messages = asyncio.run(_run())

    assert refusal.status_code == 409
    assert saved_messages == 0
    assert dispatched == []
