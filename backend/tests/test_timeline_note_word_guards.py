"""Selections depend on their words; ordinary timeline notes do not lock lines."""

import copy
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import select

from backend.core.security import MCP_WRITE_SCOPE
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.mcp_server.server import append_meeting_notes
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.timeline_note import TimelineNote
from backend.models.transcript import Transcript
from backend.tests.test_live_action_routes import _marked
from backend.tests.test_live_speaker_naming_api import REC, USER
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker

pytestmark = pytest.mark.anyio
PATH = f"/api/v1/transcripts/{REC}/timeline-notes"


async def _mixed(session_maker: Any) -> tuple[str, int, list[str]]:
    ids = await _marked(session_maker)
    async with session_maker() as session:
        row = (
            await session.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == ids[1]
                )
            )
        ).scalar_one()
        payload = copy.deepcopy(row.confidence_payload)
        words = payload["asr_segments"][0]["words"]
        words[0]["locked"] = True
        row.confidence_payload = payload
        session.add(row)
        await session.commit()
        return row.public_id, row.revision, [word["id"] for word in words]


def _selection(
    source_id: str, revision: int, words: list[str] | None
) -> dict[str, Any]:
    reference: dict[str, Any] = {"id": source_id, "revision": revision}
    if words is not None:
        reference["word_ids"] = words
    return {
        "kind": "selection",
        "body": "Selected words",
        "start_ms": 2000,
        "end_ms": 2450,
        "quoted_text": "First",
        "source_utterances": [reference],
    }


async def test_locked_word_in_open_line_survives_revision_drift_and_overlap(
    client: AsyncClient,
    session_maker: Any,
) -> None:
    source_id, revision, words = await _mixed(session_maker)
    request = _selection(source_id, revision, words[:1])
    async with session_maker() as session:
        rows = (await session.execute(select(TranscriptUtterance))).scalars().all()
        for row in rows:
            if row.public_id == source_id:
                captured = row.confidence_payload["asr_segments"][0]["words"][0]
                request["source_utterances"][0]["word_texts"] = {
                    captured["id"]: captured["word"].strip()
                }
                row.revision += 1
            else:
                row.start_ms, row.end_ms = 2000, 3500
                payload = copy.deepcopy(row.confidence_payload)
                for word in payload["asr_segments"][0]["words"]:
                    word["locked"] = False
                    word["start_ms"] += 2000
                    word["end_ms"] += 2000
                row.confidence_payload = payload
            session.add(row)
        await session.commit()
    response = await client.post(PATH, json=request)
    assert response.status_code == 201, response.text
    async with session_maker() as session:
        row = (
            await session.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == source_id
                )
            )
        ).scalar_one()
        assert row.confidence_payload["live_text_settling"]["line_locked"] is False
        assert row.confidence_payload["live_speaker_label"]["locked"] is False


@pytest.mark.parametrize(
    "pick", ["unlocked", "mixed", "missing", "future", "superseded"]
)
async def test_selected_word_conflicts_are_atomic(
    client: AsyncClient,
    session_maker: Any,
    pick: str,
) -> None:
    source_id, revision, words = await _mixed(session_maker)
    selected = words[1:] if pick == "unlocked" else words
    if pick == "missing":
        selected = [str(uuid4())]
    if pick == "future":
        revision += 1
    if pick == "superseded":
        async with session_maker() as session:
            row = (
                await session.execute(
                    select(TranscriptUtterance).where(
                        TranscriptUtterance.public_id == source_id
                    )
                )
            ).scalar_one()
            row.state = TranscriptUtteranceState.SUPERSEDED
            session.add(row)
            await session.commit()
    response = await client.post(PATH, json=_selection(source_id, revision, selected))
    assert response.status_code == 409, response.text
    expected = "line_settling" if pick in {"unlocked", "mixed"} else "line_stale"
    assert response.json()["detail"]["code"] == expected
    assert (await client.get(PATH)).json() == []


async def test_older_client_checks_native_word_times_and_exact_source_revision(
    client: AsyncClient,
    session_maker: Any,
) -> None:
    source_id, revision, _words = await _mixed(session_maker)
    payload = _selection(source_id, revision, None)
    accepted = await client.post(PATH, json=payload)
    assert accepted.status_code == 201, accepted.text
    payload["end_ms"] = 3000
    refused = await client.post(PATH, json=payload)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "line_settling"
    payload["end_ms"] = 2450
    payload["source_utterances"][0]["revision"] = revision + 1
    stale = await client.post(PATH, json=payload)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "line_stale"


async def test_older_client_excludes_unselected_punctuation_at_range_end(
    client: AsyncClient, session_maker: Any
) -> None:
    source_id, revision, _words = await _mixed(session_maker)
    async with session_maker() as session:
        row = (
            await session.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == source_id
                )
            )
        ).scalar_one()
        payload = copy.deepcopy(row.confidence_payload)
        punctuation = payload["asr_segments"][0]["words"][1]
        punctuation.update(word=".", start_ms=2450, end_ms=2450, locked=False)
        row.confidence_payload = payload
        session.add(row)
        await session.commit()
    response = await client.post(PATH, json=_selection(source_id, revision, None))
    assert response.status_code == 201, response.text


async def test_settled_final_words_without_live_lock_evidence_remain_selectable(
    client: AsyncClient,
    session_maker: Any,
) -> None:
    source_id, revision, _words = await _mixed(session_maker)
    async with session_maker() as session:
        row = (
            await session.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == source_id
                )
            )
        ).scalar_one()
        payload = copy.deepcopy(row.confidence_payload)
        payload.pop("live_text_settling")
        for word in payload["asr_segments"][0]["words"]:
            word.pop("locked")
        row.state = TranscriptUtteranceState.FINALIZED
        row.confidence_payload = payload
        session.add(row)
        await session.commit()
    selected = await client.post(PATH, json=_selection(source_id, revision, None))
    assert selected.status_code == 201, selected.text


@pytest.mark.parametrize("timing", ["punctuation", "untimed", "terminal"])
async def test_word_identity_handles_zero_duration_and_untimed_words(
    client: AsyncClient,
    session_maker: Any,
    timing: str,
) -> None:
    source_id, revision, words = await _mixed(session_maker)
    async with session_maker() as session:
        row = (
            await session.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == source_id
                )
            )
        ).scalar_one()
        payload = copy.deepcopy(row.confidence_payload)
        word = payload["asr_segments"][0]["words"][0]
        instant = row.end_ms if timing == "terminal" else 2200
        word["start_ms"] = word["end_ms"] = None if timing == "untimed" else instant
        row.confidence_payload = payload
        session.add(row)
        await session.commit()
    request = _selection(source_id, revision, words[:1])
    if timing == "terminal":
        request.update(start_ms=instant, end_ms=instant + 1, quoted_text=".")
    response = await client.post(PATH, json=request)
    assert response.status_code == 201, response.text


async def test_timeline_span_snapshot_and_drag_accept_open_line(
    client: AsyncClient,
    session_maker: Any,
) -> None:
    source_id, revision, _words = await _mixed(session_maker)
    note = await client.post(
        PATH,
        json={
            "kind": "point",
            "body": "On this line",
            "anchor_ms": 2000,
            "start_ms": 2000,
            "end_ms": 3500,
        },
    )
    assert note.status_code == 201, note.text
    assert note.json()["quoted_text"] is None
    moved = await client.patch(
        f"{PATH}/{note.json()['id']}",
        json={
            "expected_revision": 1,
            "anchor_ms": 2200,
            "target_utterance_id": source_id,
            "target_utterance_revision": revision,
        },
    )
    assert moved.status_code == 200, moved.text
    assert (moved.json()["start_ms"], moved.json()["end_ms"]) == (2200, 3700)
    stale = await client.patch(
        f"{PATH}/{note.json()['id']}",
        json={
            "expected_revision": 2,
            "anchor_ms": 2300,
            "target_utterance_id": source_id,
            "target_utterance_revision": revision + 1,
        },
    )
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "line_stale"


async def test_unmarked_note_paths_ignore_runtime_lock_and_reference_evidence(
    client: AsyncClient,
    session_maker: Any,
) -> None:
    source_id, revision, words = await _mixed(session_maker)
    async with session_maker() as session:
        transcript = await session.get(Transcript, 1)
        transcript.settling_state = None
        session.add(transcript)
        await session.commit()
    selected = await client.post(PATH, json=_selection(source_id, revision + 9, words))
    assert selected.status_code == 201, selected.text
    assert selected.json()["quoted_text"] == "First"
    typed = await client.post(
        PATH, json={"kind": "point", "body": "typed", "anchor_ms": 2200}
    )
    assert typed.status_code == 201
    assert typed.json()["start_ms"] is None and typed.json()["end_ms"] is None
    moved = await client.patch(
        f"{PATH}/{typed.json()['id']}",
        json={
            "expected_revision": 1,
            "anchor_ms": 2000,
            "target_utterance_id": "old-source",
            "target_utterance_revision": 99,
        },
    )
    assert moved.status_code == 200 and moved.json()["anchor_ms"] == 2000


async def test_mcp_typed_note_uses_same_route_without_line_lock(
    client: AsyncClient,
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _mixed(session_maker)
    monkeypatch.setattr("backend.core.db.async_session_maker", session_maker)
    user_token = current_mcp_user.set(USER)
    scope_token = current_mcp_scopes.set({MCP_WRITE_SCOPE})
    try:
        result = await append_meeting_notes(REC, "MCP on open line", at_seconds=2.2)
    finally:
        current_mcp_user.reset(user_token)
        current_mcp_scopes.reset(scope_token)
    assert result["note"]["kind"] == "point" and result["note"]["at_ms"] == 2200
    async with session_maker() as session:
        note = (await session.execute(select(TimelineNote))).scalar_one()
        assert (
            note.quoted_text is None and note.start_ms is None and note.end_ms is None
        )
