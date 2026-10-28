"""An explicit live-name intent creates a Person only while naming is open."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.v1.endpoints.speakers.routes_live_names import (
    LiveSpeakerNameUpdate,
    name_live_speaker,
)
from backend.models.live_settling_public import public_transcript_segment
from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.tests.live_naming_fixtures import ONE
from backend.tests.test_live_speaker_naming_postgres import _seed
from backend.tests.test_live_speaker_naming_postgres import sessions as sessions
from backend.utils.canonical_pipeline import serialize_canonical_utterances


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _request(
    db: AsyncSession, user_id: int, public_id: str, **values: Any
) -> Any:
    return await name_live_speaker(
        public_id,
        ONE,
        LiveSpeakerNameUpdate(name="Alex", **values),
        db=db,
        current_user=SimpleNamespace(id=user_id),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("create_person", [False, True])
async def test_live_name_creates_only_an_explicit_owned_person_without_voiceprint(
    sessions: Any, create_person: bool
) -> None:
    user_id, recording_id, public_id = await _seed(sessions)
    async with sessions() as db:
        line = (
            await db.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == f"live-{recording_id}-0"
                )
            )
        ).scalar_one()
        assert line.speaker_label == ONE
        result = await _request(
            db,
            user_id,
            public_id,
            **({"create_person": True} if create_person else {}),
        )
        assert result.live_speaker_names == {ONE: "Alex"}
    async with sessions() as db:
        people = (await db.execute(select(GlobalSpeaker))).scalars().all()
        event = (await db.execute(select(SpeakerNameEvent))).scalar_one()
        assert len(people) == int(create_person)
        assert event.created_by_user_id == user_id
        assert event.recording_id == recording_id
        assert (event.anchor_start_ms, event.anchor_end_ms) == (0, 4000)
        assert event.global_speaker_id == (people[0].id if create_person else None)
        assert (await db.execute(select(RecordingSpeaker))).scalars().all() == []
        if create_person:
            assert people[0].user_id == user_id
            assert people[0].name == "Alex"
            assert people[0].embedding is None
            assert people[0].embedding_version is None


@pytest.mark.anyio
async def test_closed_naming_creates_neither_person_nor_name(sessions: Any) -> None:
    user_id, recording_id, public_id = await _seed(sessions)
    async with sessions() as db:
        recording = await db.get(Recording, recording_id)
        recording.status = RecordingStatus.PROCESSING
        await db.commit()
        with pytest.raises(HTTPException) as refused:
            await _request(db, user_id, public_id, create_person=True)
        assert refused.value.status_code == 409
        # Deliberately commit after refusal: validation must precede creation.
        await db.commit()
    async with sessions() as db:
        assert (await db.execute(select(GlobalSpeaker))).scalars().all() == []
        assert (await db.execute(select(SpeakerNameEvent))).scalars().all() == []


@pytest.mark.anyio
async def test_ordinary_live_reader_exposes_label_lock_without_private_evidence(
    sessions: Any,
) -> None:
    _, recording_id, _ = await _seed(sessions)
    async with sessions() as db:
        segments = await db.run_sync(
            lambda session: serialize_canonical_utterances(session, recording_id)
        )
        locked = next(row for row in segments if row["id"] == f"live-{recording_id}-0")
        unlocked = next(
            row for row in segments if row["id"] == f"live-{recording_id}-3"
        )
        assert locked["locked"] is True
        assert unlocked["locked"] is False
        assert locked["revision"] >= 1
        assert "confidence_payload" not in locked
        line = (
            await db.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == locked["id"]
                )
            )
        ).scalar_one()
        assert public_transcript_segment(
            {"speaker": ONE, "confidence_payload": line.confidence_payload}
            # Covered lines carry false so a delta can clear earlier pending state.
        ) == {"speaker": ONE, "locked": True, "diarisation_pending": False}
