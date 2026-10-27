"""Library merge and voiceprint naming cannot bypass settled source guards."""

import copy
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlmodel import select

from backend.models.pipeline import TranscriptUtterance
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.transcript import Transcript
from backend.tests.live_naming_fixtures import ONE
from backend.tests.sqlite_schemas import P_TAGS_SCHEMA, PEOPLE_TAGS_SCHEMA
from backend.tests.test_live_action_routes import _marked
from backend.tests.test_live_speaker_naming_api import REC
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker

pytestmark = pytest.mark.anyio


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


@pytest.mark.parametrize(
    "case",
    [
        (marker, locked, collision, enabled)
        for marker, locked in ((True, False), (True, True), (False, False))
        for collision in (False, True)
        for enabled in (False, True)
    ],
)
async def test_library_merge_preflights_all_sources_before_identity_changes(
    client: AsyncClient,
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[bool, bool, bool, bool],
) -> None:
    marker, all_locked, collision, canonical_enabled = case
    await _people(
        session_maker, marker=marker, all_locked=all_locked, collision=collision
    )
    if not canonical_enabled:
        monkeypatch.setattr(
            "backend.utils.config_manager.config_manager.get",
            lambda key, default=None: (
                False if key == "enable_canonical_transcript_writes" else default
            ),
        )
    response = await client.post(
        "/api/v1/speakers/merge", json={"source_speaker_id": 1, "target_speaker_id": 2}
    )
    refused = marker and not all_locked
    assert response.status_code == (409 if refused else 200), response.text
    async with session_maker() as session:
        source = await session.get(GlobalSpeaker, 1)
        speakers = (await session.execute(select(RecordingSpeaker))).scalars().all()
        if refused:
            assert response.json()["detail"]["code"] == "line_settling"
            assert source is not None
            assert (
                next(
                    speaker for speaker in speakers if speaker.diarization_label == ONE
                ).global_speaker_id
                == 1
            )
            assert (
                await session.execute(select(SpeakerNameEvent))
            ).scalars().all() == []
        else:
            assert source is None
            assert any(speaker.global_speaker_id == 2 for speaker in speakers)


@pytest.mark.parametrize("action", ["create_new", "link_existing", "force_link"])
async def test_voiceprint_naming_refuses_before_creating_a_person_or_linking(
    client: AsyncClient,
    session_maker: Any,
    action: str,
) -> None:
    await _people(session_maker, marker=True, all_locked=False, collision=False)
    async with session_maker() as session:
        row = (await session.execute(select(RecordingSpeaker))).scalar_one()
        row.embedding = [0.25, 0.75]
        session.add(row)
        await session.commit()
    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/{ONE}/voiceprint/apply",
        json={
            "action": action,
            "new_speaker_name": "New person",
            "global_speaker_id": 2,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "line_settling"
    async with session_maker() as session:
        assert len((await session.execute(select(GlobalSpeaker))).scalars().all()) == 2
        row = (await session.execute(select(RecordingSpeaker))).scalar_one()
        assert row.global_speaker_id == 1
        assert (await session.execute(select(SpeakerNameEvent))).scalars().all() == []


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


async def test_local_speaker_split_preserves_assignment_and_words_through_final_drift(
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
                segments=[
                    {
                        "start": 0,
                        "end": 4,
                        "text": "Final segmentation drift.",
                        "speaker": "SPEAKER_99",
                    }
                ],
            )
        )
        await session.commit()
        after = await session.run_sync(list_active_utterances, 1)
        protected = next(row for row in after if row.start_ms == 0)
        assert protected.recording_speaker_id == assigned_id
        assert protected.speaker_label == assigned_label
        assert protected.manual_speaker_locked is True
        assert row_words(protected) == original_words
