"""Only the current browser capture can edit its retained Meeting Edge focus."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlmodel import select

from backend.api.v1.endpoints.transcripts import routes_notes
from backend.api.v1.endpoints.transcripts.helpers import MeetingEdgeFocusUpdate
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.tests.test_live_speaker_naming_postgres import BLOCK_PROBE_SECONDS, _seed
from backend.tests.test_live_speaker_naming_postgres import sessions as sessions


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _focus(sessions: Any, public_id: str, user_id: int) -> Any:
    async with sessions() as db:
        return await routes_notes.update_meeting_edge_focus(
            public_id,
            MeetingEdgeFocusUpdate(meeting_edge_focus="New focus"),
            db=db,
            current_user=SimpleNamespace(id=user_id, settings={}),
        )


@pytest.mark.anyio
@pytest.mark.parametrize("status", list(RecordingStatus))
async def test_focus_refuses_every_state_outside_active_or_paused_capture(
    sessions: Any, monkeypatch: pytest.MonkeyPatch, status: RecordingStatus
) -> None:
    user_id, recording_id, public_id = await _seed(sessions)
    dispatch = AsyncMock()
    monkeypatch.setattr(routes_notes, "_dispatch_meeting_edge_refresh", dispatch)
    async with sessions() as db:
        recording = await db.get(Recording, recording_id)
        recording.status = status
        recording.client_status = (
            ClientStatus.RECORDING
            if status == RecordingStatus.UPLOADING
            else ClientStatus.IDLE
        )
        transcript = (await db.execute(select(Transcript))).scalar_one()
        transcript.meeting_edge_focus = "Retained focus"
        await db.commit()
    if status in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        assert (await _focus(sessions, public_id, user_id))[
            "meeting_edge_focus"
        ] == "New focus"
        dispatch.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as refused:
            await _focus(sessions, public_id, user_id)
        assert refused.value.status_code == 409
        dispatch.assert_not_awaited()
        async with sessions() as db:
            transcript = (await db.execute(select(Transcript))).scalar_one()
            assert transcript.meeting_edge_focus == "Retained focus"


@pytest.mark.anyio
async def test_focus_rechecks_status_after_waiting_for_terminal_publication(
    sessions: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, recording_id, public_id = await _seed(sessions)
    dispatch = AsyncMock()
    monkeypatch.setattr(routes_notes, "_dispatch_meeting_edge_refresh", dispatch)
    async with sessions() as publisher:
        await publisher.execute(select(Transcript).with_for_update())
        recording = await publisher.get(Recording, recording_id)
        recording.status = RecordingStatus.PROCESSED
        await publisher.flush()
        request = asyncio.ensure_future(_focus(sessions, public_id, user_id))
        try:
            await asyncio.sleep(BLOCK_PROBE_SECONDS)
            assert not request.done(), "Focus write bypassed the publication fence"
        finally:
            await publisher.commit()
    with pytest.raises(HTTPException) as refused:
        await request
    assert refused.value.status_code == 409
    dispatch.assert_not_awaited()
