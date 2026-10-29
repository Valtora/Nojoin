"""Live chip polling, acceptance and dismissal through the owned recording API."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from backend.models.transcript import Transcript
from backend.tests.live_naming_fixtures import ONE, TWO
from backend.tests.test_live_speaker_naming_api import (
    REC,
    _count,
    _events,
    _name,
    _seed,
)
from backend.tests.test_live_speaker_naming_api import (
    client as client,
)
from backend.tests.test_live_speaker_naming_api import (
    session_maker as session_maker,
)
from backend.utils.speaker_name_suggestions import load_transcript_speaker_suggestions
from backend.utils.speaker_names.live_suggestions import (
    refresh_live_speaker_suggestions,
)

pytestmark = pytest.mark.anyio


async def _refresh(session_maker: Any) -> None:
    async with session_maker() as db:
        await db.run_sync(lambda session: refresh_live_speaker_suggestions(session, 1))


async def _seed_proposals(session_maker: Any, **kwargs: Any) -> None:
    await _seed(session_maker, **kwargs)
    async with session_maker() as db:
        await db.execute(
            text(
                "UPDATE transcript_utterances SET text = "
                "CASE speaker_label WHEN :one THEN :alex WHEN :two THEN :blair ELSE text END"
            ),
            {"one": ONE, "two": TWO, "alex": "I'm Alex.", "blair": "My name is Blair."},
        )
        await db.commit()
    await _refresh(session_maker)


async def _review(session_maker: Any) -> list[dict[str, Any]]:
    async with session_maker() as db:
        return load_transcript_speaker_suggestions(await db.get(Transcript, 1))


async def _dismiss(client: AsyncClient, label: str = ONE) -> Any:
    return await client.post(
        f"/api/v1/speakers/recordings/{REC}/live/{label}/suggestions/reject"
    )


async def test_polling_carries_whole_proposal_map_even_without_new_utterances(
    client: AsyncClient, session_maker: Any
) -> None:
    await _seed_proposals(session_maker)

    full = (await client.get(f"/api/v1/transcripts/{REC}/utterances")).json()
    delta = await client.get(
        f"/api/v1/transcripts/{REC}/utterances",
        params={"after_revision": full["revision"]},
    )

    assert full["live_speaker_suggestions"] == {ONE: "Alex", TWO: "Blair"}
    assert delta.status_code == 200, delta.text
    assert delta.json()["utterances"] == []
    assert delta.json()["live_speaker_suggestions"] == full["live_speaker_suggestions"]
    assert delta.json()["live_speaker_names"] == {}
    assert await _events(session_maker) == []


@pytest.mark.parametrize(
    ("name", "status"), [("Alex", "accepted"), ("Alexis", "superseded")]
)
async def test_acceptance_or_manual_rename_is_the_users_authoritative_event(
    client: AsyncClient, session_maker: Any, name: str, status: str
) -> None:
    await _seed_proposals(session_maker)

    response = await _name(client, ONE, name)
    await _refresh(session_maker)
    read = (await client.get(f"/api/v1/transcripts/{REC}/utterances")).json()

    assert response.status_code == 200, response.text
    assert read["live_speaker_names"] == {ONE: name}
    assert read["live_speaker_suggestions"] == {TWO: "Blair"}
    (event,) = await _events(session_maker)
    assert (event["name"], event["source"], event["authority"], event["surface"]) == (
        name,
        "user",
        40,
        "live",
    )
    assert event["created_by_user_id"] == 1
    assert (event["anchor_start_ms"], event["anchor_end_ms"]) == (0, 6000)
    review = {item["diarization_label"]: item for item in await _review(session_maker)}
    assert review[ONE]["status"] == status
    assert review[ONE]["resolution_actor_user_id"] == 1
    assert await _count(session_maker, "global_speakers") == 0
    assert await _count(session_maker, "recording_speakers") == 0


async def test_dismissal_survives_more_evidence_and_a_different_candidate(
    client: AsyncClient, session_maker: Any
) -> None:
    await _seed_proposals(session_maker)
    before = await _review(session_maker)

    response = await _dismiss(client)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "live_speaker_names": {},
        "live_speaker_suggestions": {TWO: "Blair"},
    }
    await _refresh(session_maker)
    async with session_maker() as db:
        await db.execute(
            text(
                "UPDATE transcript_utterances SET text = :words WHERE speaker_label = :label"
            ),
            {"words": "My name is Casey.", "label": ONE},
        )
        await db.commit()
    await _refresh(session_maker)

    read = (await client.get(f"/api/v1/transcripts/{REC}/utterances")).json()
    assert read["live_speaker_suggestions"] == {TWO: "Blair"}
    after = await _review(session_maker)
    assert [item["id"] for item in after] == [item["id"] for item in before]
    rejected = next(item for item in after if item["diarization_label"] == ONE)
    assert rejected["status"] == "rejected"
    assert rejected["resolution_actor_user_id"] == 1
    assert await _events(session_maker) == []
    assert await _count(session_maker, "global_speakers") == 0


@pytest.mark.parametrize("label", ["LIVE_PROVISIONAL_9", "SPEAKER_00", "UNKNOWN"])
async def test_a_label_without_a_current_proposal_cannot_be_dismissed(
    client: AsyncClient, session_maker: Any, label: str
) -> None:
    await _seed_proposals(session_maker)
    before = await _review(session_maker)

    response = await _dismiss(client, label)

    assert response.status_code == 404, response.text
    assert await _review(session_maker) == before
    assert await _events(session_maker) == []


async def test_another_owners_live_proposal_cannot_be_dismissed(
    client: AsyncClient, session_maker: Any
) -> None:
    await _seed_proposals(session_maker, owner=2)
    before = await _review(session_maker)

    response = await _dismiss(client)

    assert response.status_code == 404, response.text
    assert await _review(session_maker) == before


@pytest.mark.parametrize("status", ["QUEUED", "PROCESSING", "PROCESSED", "ERROR"])
async def test_stopping_capture_closes_dismissal_and_hides_the_pending_map(
    client: AsyncClient, session_maker: Any, status: str
) -> None:
    await _seed_proposals(session_maker)
    before = await _review(session_maker)
    async with session_maker() as db:
        await db.execute(
            text("UPDATE recordings SET status = :status"), {"status": status}
        )
        await db.commit()

    response = await _dismiss(client)
    read = (await client.get(f"/api/v1/transcripts/{REC}/utterances")).json()

    assert response.status_code == 409, response.text
    assert read["live_speaker_suggestions"] == {}
    assert await _review(session_maker) == before
    assert await _events(session_maker) == []
