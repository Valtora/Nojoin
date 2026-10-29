"""People history reads stay private, accurate across history, and bounded."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import Session

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core.db import driver_urls
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services.meeting_analytics.query import compute_recording_analytics
from backend.tests.postgres_setup import fresh_schema

SCHEMA = "person_meetings_test"
JANUARY = datetime(2026, 1, 1, 12)
FEBRUARY = datetime(2026, 2, 1, 12)


@dataclass
class HistoryDatabase:
    engine: Engine
    async_engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    owner: User
    other: User
    person: GlobalSpeaker
    foreign_person: GlobalSpeaker


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def history(postgres_test_url: str) -> AsyncIterator[HistoryDatabase]:
    async_url, sync_url = driver_urls(postgres_test_url)
    engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={SCHEMA},public"}
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(engine, SCHEMA)
    with Session(engine, expire_on_commit=False) as session:
        owner = User(username="history-owner", hashed_password="x")
        other = User(username="history-other", hashed_password="x")
        session.add_all([owner, other])
        session.flush()
        person = GlobalSpeaker(name="Dana", user_id=owner.id)
        foreign_person = GlobalSpeaker(name="Robin", user_id=other.id)
        session.add_all([person, foreign_person])
        session.commit()
    async_engine = create_async_engine(
        async_url, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    try:
        yield HistoryDatabase(
            engine,
            async_engine,
            async_sessionmaker(async_engine, expire_on_commit=False),
            owner,
            other,
            person,
            foreign_person,
        )
    finally:
        await async_engine.dispose()
        engine.dispose()


@pytest.fixture
async def client(history: HistoryDatabase) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def database() -> AsyncIterator[AsyncSession]:
        async with history.sessions() as session:
            yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: history.owner
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


def _meeting(session: Session, owner: User, name: str, **fields: Any) -> Recording:
    recording = Recording(
        name=name,
        audio_path=f"/tmp/history-{name}.wav",
        user_id=owner.id,
        status=RecordingStatus.PROCESSED,
        created_at=fields.pop("created_at", JANUARY),
        **fields,
    )
    session.add(recording)
    session.flush()
    return recording


def _link(
    session: Session,
    recording: Recording,
    person: GlobalSpeaker,
    label: str = "SPEAKER_00",
) -> RecordingSpeaker:
    speaker = RecordingSpeaker(
        recording_id=recording.id,
        global_speaker_id=person.id,
        diarization_label=label,
        name=person.name,
    )
    session.add(speaker)
    session.flush()
    return speaker


def _line(
    session: Session,
    speaker: RecordingSpeaker,
    seconds: float,
    state: TranscriptUtteranceState = TranscriptUtteranceState.FINALIZED,
) -> None:
    session.add(
        TranscriptUtterance(
            recording_id=speaker.recording_id,
            recording_speaker_id=speaker.id,
            speaker_label=speaker.diarization_label,
            sort_key="0001",
            start_ms=0,
            end_ms=round(seconds * 1000),
            text="speech",
            state=state,
        )
    )


async def _read(client: AsyncClient, person: GlobalSpeaker) -> dict[str, Any]:
    response = await client.get(f"/api/v1/speakers/{person.id}/meetings")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.anyio
async def test_person_and_linked_recordings_are_both_owner_scoped(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        visible = _meeting(session, history.owner, "visible", duration_seconds=45)
        archived = _meeting(session, history.owner, "archived", is_archived=True)
        deleted = _meeting(
            session,
            history.owner,
            "deleted",
            is_deleted=True,
            created_at=datetime(2020, 1, 1),
        )
        foreign = _meeting(
            session, history.other, "foreign", created_at=datetime(2030, 1, 1)
        )
        # A foreign recording can carry an inconsistent link to our own person.
        for recording, seconds in (
            (visible, 2),
            (archived, 3),
            (deleted, 700),
            (foreign, 900),
        ):
            _line(session, _link(session, recording, history.person), seconds)
        _link(session, visible, history.person, "SPEAKER_01")
        expected = {visible.public_id, archived.public_id}
        session.commit()
    result = await _read(client, history.person)
    assert {row["recording_id"] for row in result["meetings"]} == expected
    assert result["meeting_count"] == 2
    assert result["total_talk_time_seconds"] == 5
    assert result["talk_time_by_month"] == [
        {"month": "2026-01", "talk_time_seconds": 5}
    ]
    assert datetime.fromisoformat(result["first_seen"]).replace(tzinfo=None) == JANUARY
    assert result["first_seen"] == result["last_seen"]
    listing = await client.get("/api/v1/speakers/")
    assert listing.status_code == 200
    assert [(row["id"], row["recording_count"]) for row in listing.json()] == [
        (history.person.id, 2)
    ]
    foreign_response = await client.get(
        f"/api/v1/speakers/{history.foreign_person.id}/meetings"
    )
    missing = await client.get("/api/v1/speakers/999999/meetings")
    assert foreign_response.status_code == missing.status_code == 404
    assert foreign_response.json() == missing.json()


@pytest.mark.anyio
async def test_canonical_speech_sums_labels_once_and_ignores_obsolete_projection(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        first = _meeting(session, history.owner, "first", duration_seconds=99)
        second = _meeting(session, history.owner, "second", created_at=FEBRUARY)
        a = _link(session, first, history.person)
        b = _link(session, first, history.person, "SPEAKER_01")
        c = _link(session, second, history.person, "SPEAKER_01")
        stranger = _link(session, second, history.foreign_person)
        _line(session, a, 2.5, TranscriptUtteranceState.STABLE)
        _line(session, b, 3.25, TranscriptUtteranceState.PROVISIONAL)
        _line(session, c, 4.125)
        _line(session, stranger, 800)
        _line(session, a, 1000, TranscriptUtteranceState.SUPERSEDED)
        _line(session, b, 2000, TranscriptUtteranceState.DELETED)
        _line(session, a, -30)  # A corrupt negative interval contributes zero.
        merged = _link(session, first, history.person, "SPEAKER_02")
        merged.merged_into_id = a.id
        merged_status = _link(session, first, history.person, "SPEAKER_03")
        merged_status.speaker_status = "merged"
        session.add_all([merged, merged_status])
        _line(session, merged, 1000)
        _line(session, merged_status, 2000)
        session.add(
            Transcript(
                recording_id=first.id,
                segments=[
                    {"start": 0, "end": 9000, "speaker": "SPEAKER_00", "text": "stale"},
                ],
            )
        )
        session.commit()
    result = await _read(client, history.person)
    assert result["meeting_count"] == 2
    assert result["total_talk_time_seconds"] == pytest.approx(9.875)
    assert {row["title"]: row["talk_time_seconds"] for row in result["meetings"]} == {
        "first": 5.75,
        "second": 4.125,
    }
    assert result["talk_time_by_month"] == [
        {"month": "2026-02", "talk_time_seconds": 4.125},
        {"month": "2026-01", "talk_time_seconds": 5.75},
    ]


@pytest.mark.anyio
async def test_latest_fifty_are_distinct_but_statistics_cover_all_history(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    oldest = datetime(2025, 12, 31, 23, 59)
    with Session(history.engine) as session:
        ids = []
        for index in range(53):
            date = oldest if index == 0 else JANUARY if index == 1 else FEBRUARY
            recording = _meeting(
                session, history.owner, f"meeting-{index}", created_at=date
            )
            _line(
                session,
                _link(session, recording, history.person),
                7 if index == 0 else 1,
            )
            # Multiple labels must not consume the cap or multiply totals.
            _link(session, recording, history.person, "SPEAKER_01")
            ids.append(recording.public_id)
        session.commit()
    result = await _read(client, history.person)
    assert [row["recording_id"] for row in result["meetings"]] == list(
        reversed(ids[3:])
    )
    assert result["meeting_count"] == 53
    assert result["total_talk_time_seconds"] == 59
    assert datetime.fromisoformat(result["first_seen"]).replace(tzinfo=None) == oldest
    assert datetime.fromisoformat(result["last_seen"]).replace(tzinfo=None) == FEBRUARY
    assert result["talk_time_by_month"] == [
        {"month": "2026-02", "talk_time_seconds": 51},
        {"month": "2026-01", "talk_time_seconds": 1},
        {"month": "2025-12", "talk_time_seconds": 7},
    ]


@pytest.mark.anyio
async def test_unlinked_person_has_empty_history(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    assert await _read(client, history.person) == {
        "meetings": [],
        "meeting_count": 0,
        "total_talk_time_seconds": 0,
        "first_seen": None,
        "last_seen": None,
        "talk_time_by_month": [],
    }


@pytest.mark.anyio
async def test_link_without_transcript_still_counts_a_meeting(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "no-transcript")
        _link(session, recording, history.person)
        session.commit()
    result = await _read(client, history.person)
    assert result["meeting_count"] == 1
    assert result["total_talk_time_seconds"] == 0
    assert result["first_seen"] == result["last_seen"]
    assert result["first_seen"] is not None
    assert result["meetings"][0]["duration_seconds"] is None
    assert result["meetings"][0]["talk_time_seconds"] == 0


@pytest.mark.anyio
async def test_legacy_projection_fallback_is_scoped_to_linked_labels(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    segments = [
        {"start": 0, "end": 2.5, "speaker": "SPEAKER_00", "text": "one"},
        {"start": 3, "end": 6.25, "speaker": "SPEAKER_01", "text": "two"},
        {"start": 0, "end": 800, "speaker": "SPEAKER_02", "text": "other"},
        {"start": 10, "end": 9, "speaker": "SPEAKER_00", "text": "negative"},
    ]
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "legacy")
        _link(session, recording, history.person)
        _link(session, recording, history.person, "SPEAKER_01")
        session.add(Transcript(recording_id=recording.id, segments=segments))
        session.commit()
    result = await _read(client, history.person)
    assert result["meeting_count"] == 1
    assert result["total_talk_time_seconds"] == 5.75
    assert result["meetings"][0]["talk_time_seconds"] == 5.75


@pytest.mark.anyio
async def test_malformed_legacy_segments_count_as_no_speech_for_that_meeting(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    malformed: list[Any] = [
        {"start": "soon", "end": 800, "speaker": "SPEAKER_00"},
        {"start": "", "end": 800, "speaker": "SPEAKER_00"},
        {"start": True, "end": 800, "speaker": "SPEAKER_00"},
        {"start": [0], "end": 800, "speaker": "SPEAKER_00"},
        "not a segment",
        {"start": 0, "end": 1.5, "speaker": "SPEAKER_00"},
    ]
    with Session(history.engine) as session:
        for name, segments in (("malformed", malformed), ("encoded", "{not json")):
            recording = _meeting(session, history.owner, name)
            _link(session, recording, history.person)
            session.add(Transcript(recording_id=recording.id, segments=segments))
        good = _meeting(session, history.owner, "good")
        _line(session, _link(session, good, history.person), 2)
        session.commit()
    result = await _read(client, history.person)
    assert {row["title"]: row["talk_time_seconds"] for row in result["meetings"]} == {
        "malformed": 1.5,
        "encoded": 0,
        "good": 2,
    }
    assert result["total_talk_time_seconds"] == 3.5


@pytest.mark.anyio
async def test_meeting_talk_time_matches_the_recordings_analytics(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "analysed", duration_seconds=60)
        first = _link(session, recording, history.person)
        second = _link(session, recording, history.person, "SPEAKER_01")
        stranger = _link(session, recording, history.foreign_person, "SPEAKER_02")
        _line(session, first, 2.5)
        _line(session, first, 1.25, TranscriptUtteranceState.STABLE)
        _line(session, second, 3.5, TranscriptUtteranceState.PROVISIONAL)
        _line(session, second, 40, TranscriptUtteranceState.SUPERSEDED)
        _line(session, first, -3)
        _line(session, stranger, 9)
        session.commit()
        analytics = compute_recording_analytics(session, recording)
    person_keys = {
        speaker["speaker_key"]
        for speaker in analytics["speakers"]
        if speaker["global_speaker_id"] == history.person.id
    }
    expected = sum(
        analytics["metrics"]["talk_time"][key]["speech_ms"] for key in person_keys
    )
    result = await _read(client, history.person)
    assert len(person_keys) == 2
    assert result["meetings"][0]["talk_time_seconds"] * 1000 == expected == 7250


@pytest.mark.anyio
async def test_empty_authoritative_settling_transcript_never_resurrects_old_json(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "settling")
        _link(session, recording, history.person)
        session.add(
            Transcript(
                recording_id=recording.id,
                segments=[
                    {"start": 0, "end": 800, "speaker": "SPEAKER_00", "text": "stale"}
                ],
                settling_state={
                    "eligible": True,
                    "policy": {"enabled": True},
                    "phase": "live",
                },
            )
        )
        session.commit()
    result = await _read(client, history.person)
    assert result["meeting_count"] == 1
    assert result["total_talk_time_seconds"] == 0
    assert result["meetings"][0]["talk_time_seconds"] == 0


@pytest.mark.anyio
async def test_canonical_speech_reassigned_to_someone_else_blocks_stale_fallback(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "reassigned")
        _link(session, recording, history.person)
        stranger = _link(session, recording, history.foreign_person, "SPEAKER_01")
        _line(session, stranger, 5)
        session.add(
            Transcript(
                recording_id=recording.id,
                segments=[
                    {"start": 0, "end": 800, "speaker": "SPEAKER_00", "text": "old"},
                ],
            )
        )
        session.commit()
    result = await _read(client, history.person)
    assert result["meeting_count"] == 1
    assert result["total_talk_time_seconds"] == 0
    assert result["meetings"][0]["talk_time_seconds"] == 0


@pytest.mark.anyio
async def test_explicit_legacy_speaker_id_overrules_an_outdated_label(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "legacy-id")
        ours = _link(session, recording, history.person)
        theirs = _link(session, recording, history.foreign_person, "SPEAKER_01")
        session.add(
            Transcript(
                recording_id=recording.id,
                segments=[
                    {
                        "start": 0,
                        "end": 2.5,
                        "speaker": "SPEAKER_01",
                        "recording_speaker_id": ours.id,
                    },
                    {
                        "start": 0,
                        "end": 800,
                        "speaker": "SPEAKER_00",
                        "recording_speaker_id": theirs.id,
                    },
                ],
            )
        )
        session.commit()
    result = await _read(client, history.person)
    assert result["total_talk_time_seconds"] == 2.5


@pytest.mark.anyio
@pytest.mark.parametrize("merged_field", ["merged_into_id", "speaker_status"])
async def test_merged_away_links_do_not_keep_a_meeting_in_person_history(
    history: HistoryDatabase,
    client: AsyncClient,
    merged_field: str,
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "merged-away")
        source = _link(session, recording, history.person)
        target = _link(session, recording, history.foreign_person, "SPEAKER_01")
        setattr(
            source,
            merged_field,
            target.id if merged_field == "merged_into_id" else "merged",
        )
        session.add(source)
        _line(session, source, 800)
        session.commit()
    result = await _read(client, history.person)
    assert result["meetings"] == []
    assert result["meeting_count"] == 0
    assert result["total_talk_time_seconds"] == 0


async def _select_count(client: AsyncClient, history: HistoryDatabase) -> int:
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    event.listen(history.async_engine.sync_engine, "before_cursor_execute", record)
    try:
        await _read(client, history.person)
    finally:
        event.remove(history.async_engine.sync_engine, "before_cursor_execute", record)
    return len(statements)


@pytest.mark.anyio
async def test_read_query_count_does_not_grow_with_meeting_history(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "initial")
        _line(session, _link(session, recording, history.person), 1)
        session.commit()
    small = await _select_count(client, history)
    with Session(history.engine) as session:
        for index in range(499):
            recording = _meeting(session, history.owner, f"larger-{index}")
            speaker = _link(session, recording, history.person)
            if index % 2:
                _line(session, speaker, 1)
            else:
                session.add(
                    Transcript(
                        recording_id=recording.id,
                        segments=[
                            {
                                "start": 0,
                                "end": 1,
                                "speaker": "SPEAKER_00",
                                "text": "old",
                            },
                        ],
                    )
                )
        session.commit()
    large = await _select_count(client, history)
    assert small == large == 2
