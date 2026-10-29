"""The line split route: who may split, which lines, and against which revision."""

from typing import Any
from uuid import uuid4

import pytest

from backend.models.pipeline import ProcessingRunKind, TranscriptUtteranceState
from backend.models.transcript import Transcript
from backend.tests.test_live_speaker_naming_api import REC, _seed
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    list_active_utterances,
)
from backend.utils.config_manager import config_manager

pytestmark = pytest.mark.anyio

SETTLING_STATE = {
    "version": 1,
    "eligible": True,
    "generation": "episode-a",
    "capture_epoch": 0,
    "phase": "live",
    "policy": {"enabled": True},
    "settled": True,
}


def _line(index: int, *, locked: bool, marked: bool) -> dict[str, Any]:
    start = index * 2000
    words = [
        {
            "id": str(uuid4()),
            "order": order,
            "word": word,
            "start_ms": start + order * 500,
            "end_ms": start + order * 500 + 450,
            "locked": locked,
        }
        for order, word in enumerate(("First", " words."))
    ]
    payload: dict[str, Any] = {"asr_segments": [{"words": words}]}
    if marked:
        payload["live_text_settling"] = {
            "version": 1,
            "generation": "episode-a",
            "text_locked": locked,
            "line_locked": locked,
        }
    return {
        "id": f"line-{index}",
        "start": start / 1000,
        "end": (start + 1500) / 1000,
        "text": "First words.",
        "speaker": "UNKNOWN",
        "confidence_payload": payload,
    }


async def _lines(session_maker: Any, *, owner: int = 1, marked: bool = True):
    """A locked line and a line still settling; unmarked, neither settles."""
    await _seed(session_maker, owner=owner, lines=[])
    segments = [
        _line(0, locked=True, marked=marked),
        _line(1, locked=False, marked=marked),
    ]
    async with session_maker() as session:
        transcript = await session.get(Transcript, 1)
        transcript.settling_state = dict(SETTLING_STATE) if marked else None
        await session.commit()
        await session.run_sync(
            lambda sync: append_utterances_from_segments(
                sync,
                recording_id=1,
                segments=segments,
                source="live",
                run_kind=ProcessingRunKind.LIVE,
                state_override=TranscriptUtteranceState.PROVISIONAL,
            )
        )
        await session.commit()
    # Split before " words.", the second word of each line.
    return {
        segment["id"]: segment["confidence_payload"]["asr_segments"][0]["words"][1][
            "id"
        ]
        for segment in segments
    }


async def _revisions(session_maker: Any) -> dict[str, int]:
    async with session_maker() as session:
        rows = await session.run_sync(lambda sync: list_active_utterances(sync, 1))
        return {row.public_id: row.revision for row in rows}


async def _split(client: Any, line: str, word: str, revision: int):
    return await client.post(
        f"/api/v1/transcripts/{REC}/utterances/{line}/split",
        json={"before_word_id": word, "expected_revision": revision},
    )


def _canonical_writes(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    read = config_manager.get
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: (
            enabled
            if key == "enable_canonical_transcript_writes"
            else read(key, default)
        ),
    )


async def test_a_locked_line_splits_and_a_line_still_settling_is_refused(
    client: Any, session_maker: Any
) -> None:
    words = await _lines(session_maker)
    revisions = await _revisions(session_maker)

    refused = await _split(client, "line-1", words["line-1"], revisions["line-1"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == (
        "This line is still settling. Split it once it locks."
    )
    assert (await _revisions(session_maker))["line-1"] == revisions["line-1"]

    split = await _split(client, "line-0", words["line-0"], revisions["line-0"])
    assert split.status_code == 200, split.text
    assert "line-0" not in await _revisions(session_maker)


async def test_a_stale_revision_is_refused_and_leaves_the_line(
    client: Any, session_maker: Any
) -> None:
    words = await _lines(session_maker)
    revision = (await _revisions(session_maker))["line-0"]

    response = await _split(client, "line-0", words["line-0"], revision + 1)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "This line changed. Reload it and try again."
    assert (await _revisions(session_maker))["line-0"] == revision


async def test_another_users_recording_is_not_found(
    client: Any, session_maker: Any
) -> None:
    words = await _lines(session_maker, owner=2)
    revision = (await _revisions(session_maker))["line-0"]

    response = await _split(client, "line-0", words["line-0"], revision)

    assert response.status_code == 404, response.text
    assert (await _revisions(session_maker))["line-0"] == revision


@pytest.mark.parametrize("enabled", [False, True])
async def test_canonical_writes_off_refuse_a_split_on_an_unsettled_recording(
    client: Any,
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    words = await _lines(session_maker, marked=False)
    revision = (await _revisions(session_maker))["line-0"]
    _canonical_writes(monkeypatch, enabled)

    response = await _split(client, "line-0", words["line-0"], revision)

    if enabled:
        assert response.status_code == 200, response.text
        return
    assert response.status_code == 409, response.text
    assert "canonical transcript writes disabled" in response.json()["detail"]
    assert (await _revisions(session_maker))["line-0"] == revision
