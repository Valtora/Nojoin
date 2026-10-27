"""Captured complete wording fences selections across mutable line revisions."""

import copy
from typing import Any

import pytest
from httpx import AsyncClient
from sqlmodel import select

from backend.models.pipeline import TranscriptUtterance
from backend.models.transcript import Transcript
from backend.tests.test_live_action_routes import _marked
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker
from backend.tests.test_timeline_note_word_guards import PATH, _selection
from backend.utils.live_word_settling import close_word_state, reconcile_word_state
from backend.utils.word_text import reconstruct_text_from_words

pytestmark = pytest.mark.anyio


async def _captured_cat(session_maker: Any) -> dict[str, Any]:
    source_id = (await _marked(session_maker))[1]
    async with session_maker() as session:
        row = (
            await session.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == source_id
                )
            )
        ).scalar_one()
        metadata = copy.deepcopy(row.confidence_payload)
        word = metadata["asr_segments"][0]["words"][0]
        word.update(word="cat", locked=False)
        metadata["asr_segments"][0]["words"] = [word]
        row.text, row.confidence_payload = "cat", metadata
        payload = _selection(source_id, row.revision, [word["id"]])
        payload["quoted_text"] = "cat"
        payload["source_utterances"][0]["word_texts"] = {word["id"]: "cat"}
        session.add(row)
        await session.commit()
        return payload


async def _lock_next_decode(
    session_maker: Any,
    payload: dict[str, Any],
    wording: str,
    *,
    grow: bool = False,
) -> None:
    reference = payload["source_utterances"][0]
    async with session_maker() as session:
        row = (
            await session.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == reference["id"]
                )
            )
        ).scalar_one()
        metadata = copy.deepcopy(row.confidence_payload)
        previous = metadata["asr_segments"][0]["words"]
        incoming = [{**previous[0], "word": wording}]
        if grow:
            incoming.append({"word": " later", "start_ms": 2500, "end_ms": 2950})
        current = reconcile_word_state(
            previous,
            incoming,
            k=2,
            decode_id="second",
            window_start_ms=-1000,
            audio_end_ms=3000,
        )
        assert current[0]["id"] == reference["word_ids"][0]
        current = close_word_state(current[:1]) + current[1:]
        metadata["asr_segments"][0]["words"] = current
        row.text = reconstruct_text_from_words(current)
        row.confidence_payload = metadata
        row.revision += 1
        session.add(row)
        await session.commit()


async def test_corrected_pending_word_keeps_id_but_refuses_captured_quote(
    client: AsyncClient, session_maker: Any
) -> None:
    payload = await _captured_cat(session_maker)
    await _lock_next_decode(session_maker, payload, "dog")

    response = await client.post(PATH, json=payload)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "line_stale"
    assert (await client.get(PATH)).json() == []


@pytest.mark.parametrize("quote", ["cat", "at"])
async def test_unchanged_pending_word_locks_and_line_grows_with_captured_evidence(
    client: AsyncClient, session_maker: Any, quote: str
) -> None:
    payload = await _captured_cat(session_maker)
    payload["quoted_text"] = quote
    await _lock_next_decode(session_maker, payload, "cat", grow=True)

    response = await client.post(PATH, json=payload)

    assert response.status_code == 201, response.text
    assert response.json()["quoted_text"] == quote
    assert (response.json()["start_ms"], response.json()["end_ms"]) == (2000, 2450)


async def test_partial_quote_cannot_replace_complete_word_evidence(
    client: AsyncClient, session_maker: Any
) -> None:
    payload = await _captured_cat(session_maker)
    payload["quoted_text"] = "at"
    reference = payload["source_utterances"][0]
    reference["word_texts"][reference["word_ids"][0]] = "at"
    await _lock_next_decode(session_maker, payload, "cat")

    response = await client.post(PATH, json=payload)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "line_stale"
    assert (await client.get(PATH)).json() == []


@pytest.mark.parametrize("evidence", [None, {}, {"different-id": "cat"}])
async def test_revision_drift_requires_complete_captured_evidence(
    client: AsyncClient, session_maker: Any, evidence: dict[str, str] | None
) -> None:
    payload = await _captured_cat(session_maker)
    reference = payload["source_utterances"][0]
    if evidence is None:
        reference.pop("word_texts")
    else:
        reference["word_texts"] = evidence
    await _lock_next_decode(session_maker, payload, "cat")

    response = await client.post(PATH, json=payload)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "line_stale"
    assert (await client.get(PATH)).json() == []


async def test_supplied_wording_is_checked_even_at_current_revision(
    client: AsyncClient, session_maker: Any
) -> None:
    payload = await _captured_cat(session_maker)
    await _lock_next_decode(session_maker, payload, "dog")
    payload["source_utterances"][0]["revision"] += 1

    response = await client.post(PATH, json=payload)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "line_stale"


async def test_current_revision_older_client_can_still_select_locked_word(
    client: AsyncClient, session_maker: Any
) -> None:
    payload = await _captured_cat(session_maker)
    await _lock_next_decode(session_maker, payload, "cat")
    reference = payload["source_utterances"][0]
    reference["revision"] += 1
    reference.pop("word_texts")

    response = await client.post(PATH, json=payload)

    assert response.status_code == 201, response.text


async def test_unmarked_recording_ignores_changed_wording_evidence(
    client: AsyncClient, session_maker: Any
) -> None:
    payload = await _captured_cat(session_maker)
    await _lock_next_decode(session_maker, payload, "dog")
    async with session_maker() as session:
        transcript = await session.get(Transcript, 1)
        assert transcript is not None
        transcript.settling_state = None
        session.add(transcript)
        await session.commit()

    response = await client.post(PATH, json=payload)

    assert response.status_code == 201, response.text
    assert response.json()["quoted_text"] == "cat"
