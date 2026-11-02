"""MCP reprocess keeps, sets or clears a recording's speaker limit (#871).

The route reads three states from its body: max_speakers absent keeps the
stored limit, null clears it, and a number sets it. The tool's docstring said
omitting it meant auto-detect, and no argument could clear a limit.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from backend.api.v1.endpoints.recordings import routes_actions
from backend.core import db as core_db
from backend.core.security import MCP_READ_SCOPE, MCP_WRITE_SCOPE
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.mcp_server.tools_manage import reprocess_recording

pytestmark = pytest.mark.anyio


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    bodies: list[Any] = []

    async def route(recording_id: str, body: Any, **kwargs: Any) -> Any:
        bodies.append(body)
        return SimpleNamespace(id=recording_id, status="QUEUED")

    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr(routes_actions, "reprocess_recording", route)
    monkeypatch.setattr(core_db, "async_session_maker", session)
    return bodies


def _bind() -> None:
    # Set inside the test: anyio copies the context when the test starts.
    current_mcp_user.set(SimpleNamespace(id=1))
    current_mcp_scopes.set(frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE}))


async def test_omitting_the_limit_keeps_the_stored_one(sent: list[Any]) -> None:
    _bind()
    await reprocess_recording("rec-1", "whisper")
    assert "max_speakers" not in sent[0].model_fields_set


async def test_a_limit_is_set(sent: list[Any]) -> None:
    _bind()
    await reprocess_recording("rec-1", "whisper", max_speakers=4)
    assert "max_speakers" in sent[0].model_fields_set
    assert sent[0].max_speakers == 4


async def test_the_limit_can_be_cleared(sent: list[Any]) -> None:
    _bind()
    await reprocess_recording("rec-1", "whisper", clear_max_speakers=True)
    assert "max_speakers" in sent[0].model_fields_set
    assert sent[0].max_speakers is None


async def test_setting_and_clearing_together_is_refused(sent: list[Any]) -> None:
    _bind()
    with pytest.raises(ToolError, match="not both"):
        await reprocess_recording(
            "rec-1", "whisper", max_speakers=4, clear_max_speakers=True
        )
    assert sent == []
