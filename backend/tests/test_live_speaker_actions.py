"""Speaker actions on a settled capture protect the lines they change."""

import copy
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlmodel import select

from backend.models.pipeline import TranscriptUtterance
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.tests.live_naming_fixtures import ONE
from backend.tests.sqlite_schemas import (
    DOCUMENTS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    USERS_SCHEMA,
)
from backend.tests.test_live_speaker_naming_api import REC, _seed
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


async def _people(
    session_maker: Any, *, marker: bool, all_locked: bool, collision: bool
) -> None:
    await _marked(session_maker)
    async with session_maker() as session:
        for schema in (P_TAGS_SCHEMA, PEOPLE_TAGS_SCHEMA):
            await session.execute(text(schema))
        source = GlobalSpeaker(id=1, name="Source", user_id=1)
        target = GlobalSpeaker(id=2, name="Target", user_id=1)
        session.add_all([source, target])
        await session.flush()
        local = RecordingSpeaker(
            recording_id=1,
            diarization_label=ONE,
            global_speaker_id=1,
            name="Source",
            identity_locked=True,
        )
        session.add(local)
        if collision:
            session.add(
                RecordingSpeaker(
                    recording_id=1,
                    diarization_label="SPEAKER_02",
                    global_speaker_id=2,
                    name="Target",
                )
            )
        await session.flush()
        rows = (await session.execute(select(TranscriptUtterance))).scalars().all()
        for row in rows:
            row.recording_speaker_id = local.id
            if all_locked:
                payload = copy.deepcopy(row.confidence_payload)
                payload["live_text_settling"]["line_locked"] = True
                row.confidence_payload = payload
            session.add(row)
        if not marker:
            (await session.get(Transcript, 1)).settling_state = None
        await session.commit()


async def test_compatibility_speaker_change_gets_manual_protection_without_client_flags(
    client: AsyncClient, session_maker: Any
) -> None:
    await _people(session_maker, marker=True, all_locked=True, collision=True)
    read = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    segments = read.json()["utterances"]
    segments[0]["speaker"] = "SPEAKER_02"
    response = await client.put(
        f"/api/v1/transcripts/{REC}/segments", json={"segments": segments}
    )
    assert response.status_code == 200, response.text
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(TranscriptUtterance).order_by(TranscriptUtterance.start_ms)
                )
            )
            .scalars()
            .all()
        )
        assert rows[0].speaker_label == "SPEAKER_02"
        assert rows[0].manual_speaker_locked is True
        assert rows[0].speaker_last_edit_source == "api"
        assert rows[0].speaker_assignment_authority == "manual"
        assert rows[1].manual_speaker_locked is False


async def test_local_speaker_split_keeps_assignment_through_final_drift(
    client: AsyncClient, session_maker: Any
) -> None:
    from backend.utils.canonical_pipeline import (
        finalize_utterances_from_segments,
        list_active_utterances,
    )
    from backend.utils.canonical_pipeline.live_settling_projection import row_words

    await _people(session_maker, marker=True, all_locked=True, collision=False)
    async with session_maker() as session:
        first = (
            (
                await session.execute(
                    select(TranscriptUtterance).order_by(TranscriptUtterance.start_ms)
                )
            )
            .scalars()
            .first()
        )
        original_words = row_words(first)
    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/{ONE}/split",
        json={
            "new_speaker_name": "Alex",
            "segments": [{"recording_id": REC, "start": 0, "end": 1.5}],
        },
    )
    assert response.status_code == 200, response.text
    async with session_maker() as session:
        before = await session.run_sync(list_active_utterances, 1)
        assigned_id, assigned_label = (
            before[0].recording_speaker_id,
            before[0].speaker_label,
        )
        assert before[0].manual_speaker_locked is True
        assert row_words(before[0]) == original_words
        await session.run_sync(
            lambda sync: finalize_utterances_from_segments(
                sync,
                recording_id=1,
                # The split line is 0-1.5 s. An untimed final segment takes
                # its speaker only where the line covers most of it.
                segments=[
                    {
                        "start": 0,
                        "end": 1.6,
                        "text": "Final segmentation drift.",
                        "speaker": "SPEAKER_99",
                    }
                ],
            )
        )
        await session.commit()
        after = await session.run_sync(list_active_utterances, 1)
        # A speaker-only correction protects the speaker; the wording is final.
        final = next(row for row in after if row.start_ms == 0)
        assert final.recording_speaker_id == assigned_id
        assert final.speaker_label == assigned_label
        assert final.manual_speaker_locked is True
        assert final.text == "Final segmentation drift."


async def test_full_and_delta_envelopes_keep_the_settled_policy_and_names_go_through(
    client: AsyncClient, session_maker: Any
) -> None:
    await _marked(session_maker)
    full = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    assert full.status_code == 200
    assert full.json()["settling_required"] is True
    delta = await client.get(
        f"/api/v1/transcripts/{REC}/utterances",
        params={"after_revision": full.json()["revision"]},
    )
    assert delta.json()["settling_required"] is True
    assert delta.json()["utterances"] == []
    # A settling line no longer blocks naming its speaker.
    named = await client.put(
        f"/api/v1/speakers/recordings/{REC}/live/{ONE}", json={"name": "Alex"}
    )
    assert named.status_code == 200, named.text
