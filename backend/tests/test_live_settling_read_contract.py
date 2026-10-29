"""Word identities and locks survive every structured public read boundary."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints.transcripts.helpers import TranscriptUtteranceRead
from backend.api.v1.endpoints.transcripts.router import router
from backend.mcp_server.auth import current_mcp_user
from backend.mcp_server.server import get_transcript_utterances
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceEvent
from backend.models.recording_public import serialize_transcript
from backend.models.transcript import Transcript
from backend.tests.test_live_settling_public import _payload
from backend.tests.test_live_speaker_naming_api import REC, USER, _seed
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker
from backend.utils.canonical_pipeline import build_transcript_segments_for_read

FIELDS = {"words", "text_locked", "locked"}
PRIVATE = {"unchanged_count", "signature", "last_decode_id"}


def _utterance() -> dict[str, Any]:
    return {
        "id": "line",
        "start": 0.0,
        "end": 1.0,
        "start_ms": 0,
        "end_ms": 1000,
        "text": "Hello!",
        "speaker": "UNKNOWN",
        "state": "provisional",
        "revision": 1,
        "segment_source": "live",
    }


def _assert_settling(line: dict[str, Any], *, locked: bool = True) -> None:
    assert FIELDS <= line.keys()
    assert line["text_locked"] is locked
    assert line["locked"] is locked
    (word,) = line["words"]
    assert (word["id"], word["word"], word["locked"]) == ("word-1", "Hello!", locked)
    assert word["start_ms"] == 100
    assert word["source_end"] == 6
    assert PRIVATE.isdisjoint(word)
    assert "confidence_payload" not in line


def test_utterance_response_model_retains_typed_word_and_lock_fields() -> None:
    payload = _utterance() | {
        "words": _payload()["asr_segments"][0]["words"],
        "text_locked": True,
        "locked": True,
    }
    _assert_settling(TranscriptUtteranceRead(**payload).model_dump())


def test_utterance_response_model_rejects_incomplete_word_contract() -> None:
    with pytest.raises(ValidationError):
        TranscriptUtteranceRead(**(_utterance() | {"words": [{"id": "word-1"}]}))


def test_legacy_response_model_preserves_existing_neutral_fields() -> None:
    line = TranscriptUtteranceRead(**_utterance()).model_dump()
    assert FIELDS.isdisjoint(line)
    assert line["alignment_error"] is None
    assert line["text_confidence"] is None
    assert line["provisional"] is False


async def _mark_line(session_maker: Any, *, locked: bool = True) -> int:
    await _seed(session_maker, lines=[(0.0, 1.0, "UNKNOWN", False)])
    async with session_maker() as session:
        line = (await session.execute(select(TranscriptUtterance))).scalar_one()
        payload = _payload()
        payload["live_text_settling"]["line_locked"] = locked
        payload["asr_segments"][0]["words"][0]["locked"] = locked
        line.text = "Hello!"
        line.confidence_payload = payload
        transcript = await session.get(Transcript, 1)
        transcript.settling_state = {
            "eligible": True,
            "policy": {"enabled": True},
            "phase": "live",
        }
        event = TranscriptUtteranceEvent(
            recording_id=1,
            utterance_id=line.id,
            event_type="update",
            payload={},
        )
        session.add(event)
        await session.flush()
        cursor = event.id - 1
        assert cursor > 0
        await session.commit()
    return cursor


@pytest.fixture
async def read_client(session_maker: Any) -> Any:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/transcripts")

    async def database() -> Any:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: USER
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode", [(surface, delta) for surface in ("rest", "mcp") for delta in (False, True)]
)
@pytest.mark.parametrize("locked", [False, True])
async def test_structured_reads_preserve_words_and_both_lock_states(
    read_client: Any,
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    mode: tuple[str, bool],
    locked: bool,
) -> None:
    surface, delta = mode
    cursor = await _mark_line(session_maker, locked=locked)
    after_revision = cursor if delta else None
    if surface == "rest":
        response = await read_client.get(
            f"/api/v1/transcripts/{REC}/utterances",
            params={"after_revision": cursor} if delta else {},
        )
        assert response.status_code == 200, response.text
        result = response.json()
    else:
        monkeypatch.setattr("backend.core.db.async_session_maker", session_maker)
        token = current_mcp_user.set(USER)
        try:
            result = await get_transcript_utterances(REC, after_revision=after_revision)
        finally:
            current_mcp_user.reset(token)
    (line,) = result["utterances"]
    _assert_settling(line, locked=locked)
    assert result["revision"] > cursor
    assert result["tombstones"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("surface", ["rest", "mcp", "detail"])
async def test_legacy_reads_do_not_gain_settling_fields(
    read_client: Any,
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    await _seed(session_maker, lines=[(0.0, 1.0, "UNKNOWN", False)])
    if surface == "rest":
        response = await read_client.get(f"/api/v1/transcripts/{REC}/utterances")
        assert response.status_code == 200, response.text
        lines = response.json()["utterances"]
    elif surface == "mcp":
        monkeypatch.setattr("backend.core.db.async_session_maker", session_maker)
        token = current_mcp_user.set(USER)
        try:
            lines = (await get_transcript_utterances(REC))["utterances"]
        finally:
            current_mcp_user.reset(token)
    else:
        async with session_maker() as session:
            transcript = await session.get(Transcript, 1)
            segments = await session.run_sync(build_transcript_segments_for_read, 1)
            lines = serialize_transcript(
                transcript,
                recording_public_id=REC,
                segments_override=segments,
            ).model_dump()["segments"]
    assert len(lines) == 1
    assert FIELDS.isdisjoint(lines[0])


@pytest.mark.anyio
async def test_detail_public_read_preserves_settling_fields(session_maker: Any) -> None:
    await _mark_line(session_maker)
    async with session_maker() as session:
        transcript = await session.get(Transcript, 1)
        segments = await session.run_sync(build_transcript_segments_for_read, 1)
        public = serialize_transcript(
            transcript,
            recording_public_id=REC,
            segments_override=segments,
        ).model_dump()
    _assert_settling(public["segments"][0])


@pytest.mark.anyio
async def test_snapshot_fallback_preserves_marked_word_contract(
    read_client: Any,
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _mark_line(session_maker)
    async with session_maker() as session:
        transcript = await session.get(Transcript, 1)
        transcript.segments = [_utterance() | {"confidence_payload": _payload()}]
        await session.commit()
    monkeypatch.setattr(
        "backend.api.v1.endpoints.transcripts.routes_utterances._canonical_transcript_writes_enabled",
        lambda: False,
    )
    response = await read_client.get(f"/api/v1/transcripts/{REC}/utterances")
    assert response.status_code == 200, response.text
    _assert_settling(response.json()["utterances"][0])
