"""Capture intervals survive typed readers without enabling settling semantics."""

from typing import Any

import pytest
from pydantic import ValidationError
from sqlmodel import select

from backend.mcp_server.auth import current_mcp_user
from backend.mcp_server.server import get_transcript_utterances
from backend.models.live_settling_public import (
    LiveSettlingPublicRead,
    public_settling_fields,
)
from backend.models.pipeline import TranscriptUtterance
from backend.tests.test_live_settling_read_contract import REC, USER, _mark_line
from backend.tests.test_live_settling_read_contract import read_client as read_client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker


def test_interval_marker_is_optional_positive_and_independent_of_word_settling() -> (
    None
):
    assert LiveSettlingPublicRead().model_dump() == {}
    assert public_settling_fields(None) == {}
    assert public_settling_fields({"capture_interval": 2}) == {"capture_interval": 2}
    assert LiveSettlingPublicRead(capture_interval=2).model_dump() == {
        "capture_interval": 2
    }
    for invalid in [0, -1]:
        with pytest.raises(ValidationError):
            LiveSettlingPublicRead(capture_interval=invalid)


@pytest.mark.anyio
@pytest.mark.parametrize("surface", ["rest", "mcp"])
@pytest.mark.parametrize("delta", [False, True])
async def test_capture_interval_survives_public_full_and_delta_readers(
    read_client: Any,
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    delta: bool,
) -> None:
    cursor = await _mark_line(session_maker, locked=False)
    async with session_maker() as session:
        line = (await session.execute(select(TranscriptUtterance))).scalar_one()
        line.confidence_payload = {**line.confidence_payload, "capture_interval": 2}
        await session.commit()
    if surface == "rest":
        response = await read_client.get(
            f"/api/v1/transcripts/{REC}/utterances",
            params={"after_revision": cursor} if delta else {},
        )
        assert response.status_code == 200
        result = response.json()
    else:
        monkeypatch.setattr("backend.core.db.async_session_maker", session_maker)
        token = current_mcp_user.set(USER)
        try:
            result = await get_transcript_utterances(
                REC, after_revision=cursor if delta else None
            )
        finally:
            current_mcp_user.reset(token)
    (line,) = result["utterances"]
    assert line["capture_interval"] == 2
    assert line["locked"] is False
