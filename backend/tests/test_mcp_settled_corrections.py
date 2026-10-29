"""MCP corrections on a settled recording while canonical writes are off.

The rollback switch turns canonical writes off for ordinary recordings, but a
settled recording's canonical lines stay its only authority, so the MCP
corrections still apply there, after the same scope and ownership checks.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import text
from sqlmodel import select

from backend.core.security import MCP_READ_SCOPE
from backend.mcp_server.tools_manage import correct_utterance_text
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.test_mcp_tools import anyio_backend as anyio_backend
from backend.tests.test_mcp_tools import (
    bind_mcp_identity,
    seed_canonical_transcript,
    seed_recording_with_speakers,
)
from backend.tests.test_mcp_tools import mcp_context as mcp_context
from backend.tests.test_mcp_tools import (
    no_meeting_edge_dispatch as no_meeting_edge_dispatch,
)
from backend.tests.test_mcp_tools import session_maker as session_maker
from backend.tests.test_mcp_tools import test_user as test_user
from backend.utils.config_manager import config_manager

pytestmark = pytest.mark.anyio


async def _recording(session_maker, user: User, monkeypatch, *, settled: bool) -> str:
    public_id = await seed_recording_with_speakers(session_maker, user_id=user.id)
    await seed_canonical_transcript(session_maker)
    if settled:
        async with session_maker() as session:
            transcript = (await session.execute(select(Transcript))).scalar_one()
            transcript.settling_state = {"settled": True, "policy": {"enabled": False}}
            await session.commit()
    read = config_manager.get
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: (
            False if key == "enable_canonical_transcript_writes" else read(key, default)
        ),
    )
    return public_id


async def _line(session_maker) -> tuple[str, str | None]:
    async with session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT text, text_last_edit_source FROM transcript_utterances "
                    "WHERE public_id = 'u-1'"
                )
            )
        ).one()
    return row[0], row[1]


async def test_a_settled_recording_takes_mcp_corrections_after_scope_and_ownership(
    session_maker,
    test_user: User,
    mcp_context,
    no_meeting_edge_dispatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_id = await _recording(session_maker, test_user, monkeypatch, settled=True)

    bind_mcp_identity(test_user, scopes=frozenset({MCP_READ_SCOPE}))
    with pytest.raises(ToolError, match="read-only"):
        await correct_utterance_text(public_id, "u-1", "Read-only.")

    stranger = User(
        id=test_user.id + 1, username="stranger", hashed_password="x", settings={}
    )
    bind_mcp_identity(stranger)
    # Called directly, the tool raises the route's refusal; the server hands
    # the assistant its message.
    with pytest.raises(HTTPException) as refused:
        await correct_utterance_text(public_id, "u-1", "Not yours.")
    assert (refused.value.status_code, refused.value.detail) == (
        404,
        "Recording not found",
    )
    assert await _line(session_maker) == ("Hello everyone.", None)

    bind_mcp_identity(test_user)
    result = await correct_utterance_text(public_id, "u-1", "Hello, corrected.")

    assert result["utterance_id"] == "u-1"
    assert await _line(session_maker) == ("Hello, corrected.", "mcp")


async def test_an_unsettled_recording_still_refuses_them(
    session_maker,
    test_user: User,
    mcp_context,
    no_meeting_edge_dispatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_id = await _recording(session_maker, test_user, monkeypatch, settled=False)
    bind_mcp_identity(test_user)

    with pytest.raises(ToolError, match="canonical transcript writes disabled"):
        await correct_utterance_text(public_id, "u-1", "Hello, corrected.")

    assert await _line(session_maker) == ("Hello everyone.", None)
