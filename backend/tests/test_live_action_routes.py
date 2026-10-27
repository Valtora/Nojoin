"""REST and MCP expose the same locked-line rules without blocking typed notes."""

from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import text
from sqlmodel import select

from backend.core.security import MCP_WRITE_SCOPE
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.mcp_server.tools_manage import (
    correct_utterance_speaker,
    correct_utterance_text,
)
from backend.models.pipeline import TranscriptUtterance
from backend.models.transcript import Transcript
from backend.tests.live_naming_fixtures import ONE
from backend.tests.sqlite_schemas import DOCUMENTS_SCHEMA, USERS_SCHEMA
from backend.tests.test_live_speaker_naming_api import REC, USER, _seed
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker
from backend.utils.canonical_pipeline import (
    refresh_transcript_projection_from_canonical,
)

pytestmark = pytest.mark.anyio


async def _marked(session_maker: Any) -> list[str]:
    await _seed(session_maker, lines=[(0, 1.5, ONE, True), (2, 3.5, ONE, False)])
    async with session_maker() as session:
        await session.execute(text(USERS_SCHEMA))
        await session.execute(text(DOCUMENTS_SCHEMA))
        transcript = await session.get(Transcript, 1)
        transcript.settling_state = {"settled": True}
        rows = (
            (
                await session.execute(
                    select(TranscriptUtterance).order_by(TranscriptUtterance.start_ms)
                )
            )
            .scalars()
            .all()
        )
        for index, row in enumerate(rows):
            locked = index == 0
            row.text = "First words."
            row.confidence_payload = {
                "live_text_settling": {
                    "version": 1,
                    "line_locked": locked,
                    "text_locked": locked,
                },
                "live_speaker_label": {"locked": locked},
                "asr_segments": [
                    {
                        "words": [
                            {
                                "id": str(uuid4()),
                                "order": index * 2 + j,
                                "word": word,
                                "start_ms": row.start_ms + j * 500,
                                "end_ms": row.start_ms + j * 500 + 450,
                                "locked": locked,
                            }
                            for j, word in enumerate(("First", " words."))
                        ]
                    }
                ],
            }
            session.add(row)
        await session.flush()
        await session.run_sync(refresh_transcript_projection_from_canonical, 1)
        ids = [row.public_id for row in rows]
        await session.commit()
        return ids


@pytest.mark.parametrize("action", ["text", "speaker", "split"])
@pytest.mark.parametrize("canonical_enabled", [True, False])
async def test_rest_refuses_each_unlocked_action_with_a_readable_409(
    client: AsyncClient,
    session_maker: Any,
    action: str,
    canonical_enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _marked(session_maker)
    if not canonical_enabled:
        monkeypatch.setattr(
            "backend.utils.config_manager.config_manager.get",
            lambda key, default=None: (
                False if key == "enable_canonical_transcript_writes" else default
            ),
        )
    path = f"/api/v1/transcripts/{REC}/utterances/{ids[1]}/{action}"
    if action == "split":
        response = await client.post(
            path, json={"before_word_id": str(uuid4()), "expected_revision": 2}
        )
    else:
        payload = (
            {"text": "changed"}
            if action == "text"
            else {"new_speaker_name": "Alex", "scope": "utterance_only"}
        )
        response = await client.patch(path, json=payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] in {"line_settling", "line_stale"}
    assert response.json()["detail"]["message"]


async def test_selection_note_and_drag_refuse_unlocked_but_typed_note_succeeds(
    client: AsyncClient, session_maker: Any
) -> None:
    ids = await _marked(session_maker)
    path = f"/api/v1/transcripts/{REC}/timeline-notes"
    response = await client.post(
        path,
        json={
            "kind": "selection",
            "body": "on source",
            "start_ms": 0,
            "end_ms": 3500,
            "quoted_text": "First words. First words.",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "line_settling"
    typed = await client.post(
        path, json={"kind": "point", "body": "typed live", "anchor_ms": 2200}
    )
    assert typed.status_code == 201, typed.text
    moved = await client.patch(
        f"{path}/{typed.json()['id']}",
        json={
            "expected_revision": typed.json()["revision"],
            "anchor_ms": 2000,
            "target_utterance_id": ids[1],
            "target_utterance_revision": 2,
        },
    )
    assert moved.status_code == 409
    stored = await client.get(path)
    assert len(stored.json()) == 1 and stored.json()[0]["anchor_ms"] == 2200


async def test_full_and_delta_envelopes_keep_sticky_policy_and_naming_checks_anchor(
    client: AsyncClient, session_maker: Any
) -> None:
    ids = await _marked(session_maker)
    full = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    assert full.status_code == 200
    assert full.json()["settling_required"] is True
    delta = await client.get(
        f"/api/v1/transcripts/{REC}/utterances",
        params={"after_revision": full.json()["revision"]},
    )
    assert delta.json()["settling_required"] is True
    assert delta.json()["utterances"] == []
    named = await client.put(
        f"/api/v1/speakers/recordings/{REC}/live/{ONE}",
        json={"name": "Alex", "anchor_utterance_id": ids[1], "expected_revision": 2},
    )
    assert named.status_code == 409
    assert named.json()["detail"]["code"] in {"line_settling", "line_stale"}


@pytest.mark.parametrize("tool", [correct_utterance_text, correct_utterance_speaker])
@pytest.mark.parametrize("canonical_enabled", [True, False])
async def test_mcp_corrections_use_the_same_guard(
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    tool: Any,
    canonical_enabled: bool,
) -> None:
    ids = await _marked(session_maker)
    if not canonical_enabled:
        monkeypatch.setattr(
            "backend.utils.config_manager.config_manager.get",
            lambda key, default=None: (
                False if key == "enable_canonical_transcript_writes" else default
            ),
        )
    monkeypatch.setattr("backend.core.db.async_session_maker", session_maker)
    user_token = current_mcp_user.set(USER)
    scope_token = current_mcp_scopes.set({MCP_WRITE_SCOPE})
    try:
        args = (
            {"text": "changed"}
            if tool is correct_utterance_text
            else {"speaker_name": "Alex", "only_this_utterance": True}
        )
        with pytest.raises(HTTPException) as caught:
            await tool(REC, ids[1], **args)
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "line_settling"
    finally:
        current_mcp_user.reset(user_token)
        current_mcp_scopes.reset(scope_token)


async def test_unmarked_envelope_keeps_policy_field_absent(
    client: AsyncClient, session_maker: Any
) -> None:
    await _seed(session_maker)
    response = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    assert response.status_code == 200
    assert "settling_required" not in response.json()


async def test_note_anchor_references_cannot_cross_note_kinds(
    client: AsyncClient, session_maker: Any
) -> None:
    ids = await _marked(session_maker)
    response = await client.post(
        f"/api/v1/transcripts/{REC}/timeline-notes",
        json={
            "kind": "selection",
            "body": "bad target",
            "start_ms": 0,
            "end_ms": 3500,
            "quoted_text": "words",
            "target_utterance_id": ids[0],
            "target_utterance_revision": 2,
        },
    )
    assert response.status_code == 422
