"""A People split moves the chosen lines to the new person's speaker."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import Session, select

from backend.api.v1.endpoints.speakers import routes_global
from backend.api.v1.endpoints.speakers.helpers import (
    SegmentSelection,
    SpeakerSplitRequest,
)
from backend.core.db import driver_urls
from backend.models.pipeline import (
    ProcessingRunKind,
    TranscriptUtterance,
    TranscriptUtteranceState,
)
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline import append_utterances_from_segments

SCHEMA = "people_split_test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def split_database(
    postgres_test_url: str,
) -> AsyncIterator[tuple[Engine, async_sessionmaker[AsyncSession], dict[str, Any]]]:
    async_url, sync_url = driver_urls(postgres_test_url)
    engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={SCHEMA},public"}
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(engine, SCHEMA)
    with Session(engine) as session:
        owner = User(username="split-owner", hashed_password="x")
        session.add(owner)
        session.flush()
        person = GlobalSpeaker(name="Dana", user_id=owner.id)
        recording = Recording(
            name="Split",
            audio_path="/tmp/split.wav",
            user_id=owner.id,
            status=RecordingStatus.PROCESSED,
        )
        session.add(person)
        session.add(recording)
        session.flush()
        session.add(Transcript(recording_id=recording.id, text="", segments=[]))
        session.add(
            RecordingSpeaker(
                recording_id=recording.id,
                diarization_label="SPEAKER_00",
                global_speaker_id=person.id,
                name="Dana",
            )
        )
        rows = append_utterances_from_segments(
            session,
            recording_id=recording.id,
            segments=[
                {
                    "start": float(index * 5),
                    "end": float((index + 1) * 5),
                    "text": f"line {index}",
                    "speaker": "SPEAKER_00",
                    "confidence_payload": {
                        "asr_segments": [{"words": [{"word": f"word {index}"}]}]
                    },
                }
                for index in range(2)
            ],
            run_kind=ProcessingRunKind.FINALIZE,
            source="finalize",
            state_override=TranscriptUtteranceState.FINALIZED,
        )
        session.commit()
        identifiers = {
            "owner": owner.id,
            "person": person.id,
            "recording": recording.id,
            "public_id": recording.public_id,
            "line": rows[0].public_id,
        }
    async_engine = create_async_engine(
        async_url, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    try:
        yield (
            engine,
            async_sessionmaker(async_engine, expire_on_commit=False),
            identifiers,
        )
    finally:
        await async_engine.dispose()
        engine.dispose()


@pytest.mark.anyio
async def test_split_lines_belong_to_the_new_persons_speaker(
    split_database: tuple[Engine, async_sessionmaker[AsyncSession], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions, ids = split_database

    class Result:
        def get(self, timeout: int) -> dict[str, Any]:
            return {"embedding": [1.0, 0.0], "speech_seconds": 25.0}

    async def dispatch(*args: Any, **kwargs: Any) -> Result:
        return Result()

    monkeypatch.setattr(routes_global, "dispatch_task", dispatch)
    async with sessions() as session:
        owner = await session.get(User, ids["owner"])
        request = SpeakerSplitRequest(
            new_speaker_name="Robin",
            segments=[
                SegmentSelection(recording_id=ids["public_id"], start=0.0, end=5.0)
            ],
        )
        result = await routes_global.split_speaker(
            ids["person"], request, session, owner
        )
        assert result.name == "Robin"
    with Session(engine) as session:
        robin = session.exec(
            select(GlobalSpeaker).where(GlobalSpeaker.name == "Robin")
        ).one()
        rows = session.exec(
            select(TranscriptUtterance).order_by(TranscriptUtterance.sort_key)
        ).all()
        speakers = {
            speaker.id: speaker
            for speaker in session.exec(select(RecordingSpeaker)).all()
        }
        moved = speakers[rows[0].recording_speaker_id]
        assert moved.global_speaker_id == robin.id
        assert moved.diarization_label.startswith("SPLIT_")
        assert speakers[rows[1].recording_speaker_id].name == "Dana"
        assert not any(
            speaker.diarization_label.startswith("MANUAL_")
            for speaker in speakers.values()
        )
