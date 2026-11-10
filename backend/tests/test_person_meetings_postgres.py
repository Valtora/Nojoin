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
from backend.core.security import MCP_READ_SCOPE
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.mcp_server.tools_people import get_person
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services.meeting_analytics.query import compute_recording_analytics
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline.speaker import (
    refresh_recording_speaker_usage_state,
)

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
) -> TranscriptUtterance:
    utterance = TranscriptUtterance(
        recording_id=speaker.recording_id,
        recording_speaker_id=speaker.id,
        speaker_label=speaker.diarization_label,
        sort_key="0001",
        start_ms=0,
        end_ms=round(seconds * 1000),
        text="speech",
        state=state,
    )
    session.add(utterance)
    return utterance


async def _read(client: AsyncClient, person: GlobalSpeaker) -> dict[str, Any]:
    response = await client.get(f"/api/v1/speakers/{person.id}/meetings")
    assert response.status_code == 200, response.text
    return response.json()


async def _listed_count(client: AsyncClient, person: GlobalSpeaker) -> int:
    """The meeting count the People list shows for this person."""
    response = await client.get("/api/v1/speakers/")
    assert response.status_code == 200, response.text
    return next(
        row["recording_count"] for row in response.json() if row["id"] == person.id
    )


def _settle_statuses(session: Session, *recording_ids: int | None) -> None:
    """Set speaker statuses from the lines, as every line change does."""
    session.flush()
    for recording_id in recording_ids:
        assert recording_id is not None
        refresh_recording_speaker_usage_state(session, recording_id)
    session.commit()


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
async def test_each_meeting_row_reports_its_own_date_duration_and_talk_time(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    mid_january = datetime(2026, 1, 15, 8, 30)
    with Session(history.engine) as session:
        long = _meeting(
            session, history.owner, "long", created_at=FEBRUARY, duration_seconds=45.5
        )
        _line(session, _link(session, long, history.person), 3.5)
        # Linked with no speech; a zero duration must not read as unknown.
        silent = _meeting(
            session,
            history.owner,
            "silent",
            created_at=mid_january,
            duration_seconds=0,
        )
        _link(session, silent, history.person)
        unknown = _meeting(session, history.owner, "unknown", created_at=JANUARY)
        _line(session, _link(session, unknown, history.person), 2)
        expected = [
            (long.public_id, "long", FEBRUARY, 45.5, 3.5),
            (silent.public_id, "silent", mid_january, 0, 0),
            (unknown.public_id, "unknown", JANUARY, None, 2),
        ]
        session.commit()
    result = await _read(client, history.person)
    assert [
        (
            row["recording_id"],
            row["title"],
            datetime.fromisoformat(row["date"]).replace(tzinfo=None),
            row["duration_seconds"],
            row["talk_time_seconds"],
        )
        for row in result["meetings"]
    ] == expected


@pytest.mark.anyio
async def test_monthly_talk_time_groups_by_the_users_time_zone(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    # 03:00 UTC on 1 February is still 31 January in Los Angeles.
    with Session(history.engine) as session:
        late = _meeting(
            session, history.owner, "late", created_at=datetime(2026, 2, 1, 3)
        )
        _line(session, _link(session, late, history.person), 4)
        later = _meeting(session, history.owner, "later", created_at=FEBRUARY)
        _line(session, _link(session, later, history.person), 6)
        session.commit()
    # The control read proves the setting, not the host's zone, decides.
    history.owner.settings = {"timezone": "UTC"}
    utc = await _read(client, history.person)
    history.owner.settings = {"timezone": "America/Los_Angeles"}
    local = await _read(client, history.person)
    assert utc["talk_time_by_month"] == [{"month": "2026-02", "talk_time_seconds": 10}]
    assert local["talk_time_by_month"] == [
        {"month": "2026-02", "talk_time_seconds": 6},
        {"month": "2026-01", "talk_time_seconds": 4},
    ]
    assert local["total_talk_time_seconds"] == 10


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


@pytest.mark.anyio
async def test_meeting_whose_lines_all_moved_to_someone_else_stops_counting(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        moved = _meeting(session, history.owner, "moved", duration_seconds=30)
        ours = _link(session, moved, history.person)
        stranger = _link(session, moved, history.foreign_person, "SPEAKER_01")
        line = _line(session, ours, 5)
        session.add(
            Transcript(
                recording_id=moved.id,
                segments=[
                    {
                        "start": 0,
                        "end": 800,
                        "speaker": "SPEAKER_00",
                        "recording_speaker_id": ours.id,
                        "text": "old",
                    },
                ],
            )
        )
        kept = _meeting(session, history.owner, "kept", created_at=FEBRUARY)
        _line(session, _link(session, kept, history.person), 2)
        session.flush()
        line.recording_speaker_id = stranger.id
        session.add(line)
        _settle_statuses(session, moved.id, kept.id)
        assert ours.speaker_status == "inactive"
        expected = [kept.public_id]
    result = await _read(client, history.person)
    assert [row["recording_id"] for row in result["meetings"]] == expected
    assert result["meeting_count"] == 1
    assert result["total_talk_time_seconds"] == 2
    assert datetime.fromisoformat(result["first_seen"]).replace(tzinfo=None) == FEBRUARY
    assert result["first_seen"] == result["last_seen"]
    assert result["talk_time_by_month"] == [
        {"month": "2026-02", "talk_time_seconds": 2}
    ]
    assert await _listed_count(client, history.person) == 1


@pytest.mark.anyio
async def test_active_label_keeps_a_meeting_once_with_only_its_own_speech(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        processed = _meeting(session, history.owner, "processed")
        _line(session, _link(session, processed, history.person), 3)
        _link(session, processed, history.person, "SPEAKER_01")
        # A restored meeting has its speakers' statuses but no lines until
        # they are rebuilt from the JSON segments.
        restored = _meeting(session, history.owner, "restored", created_at=FEBRUARY)
        active = _link(session, restored, history.person)
        inactive = _link(session, restored, history.person, "SPEAKER_01")
        inactive.speaker_status = "inactive"
        session.add(inactive)
        session.add(
            Transcript(
                recording_id=restored.id,
                segments=[
                    {
                        "start": 0,
                        "end": 2,
                        "speaker": "SPEAKER_00",
                        "recording_speaker_id": active.id,
                    },
                    {
                        "start": 0,
                        "end": 5,
                        "speaker": "SPEAKER_01",
                        "recording_speaker_id": inactive.id,
                    },
                    {"start": 0, "end": 7, "speaker": "SPEAKER_01"},
                ],
            )
        )
        _settle_statuses(session, processed.id, restored.id)
        assert [
            speaker.speaker_status
            for speaker in sorted(processed.speakers, key=lambda s: s.id or 0)
        ] == ["active", "inactive"]
    result = await _read(client, history.person)
    assert result["meeting_count"] == 2
    assert [(row["title"], row["talk_time_seconds"]) for row in result["meetings"]] == [
        ("restored", 2),
        ("processed", 3),
    ]
    assert result["total_talk_time_seconds"] == 5
    assert await _listed_count(client, history.person) == 2


@pytest.mark.anyio
async def test_meeting_counts_again_once_a_line_is_moved_back(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "back-and-forth")
        ours = _link(session, recording, history.person)
        stranger = _link(session, recording, history.foreign_person, "SPEAKER_01")
        line = _line(session, ours, 4)
        _line(session, stranger, 9)
        session.flush()
        recording_id, ours_id, line_id = recording.id, ours.id, line.id
        line.recording_speaker_id = stranger.id
        session.add(line)
        _settle_statuses(session, recording_id)
    moved_away = await _read(client, history.person)
    assert moved_away["meetings"] == []
    assert moved_away["meeting_count"] == 0
    assert moved_away["first_seen"] is None
    assert await _listed_count(client, history.person) == 0
    with Session(history.engine) as session:
        moved = session.get(TranscriptUtterance, line_id)
        assert moved is not None
        moved.recording_speaker_id = ours_id
        session.add(moved)
        _settle_statuses(session, recording_id)
    moved_back = await _read(client, history.person)
    assert moved_back["meeting_count"] == 1
    assert moved_back["total_talk_time_seconds"] == 4
    assert moved_back["meetings"][0]["talk_time_seconds"] == 4
    assert await _listed_count(client, history.person) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "counted"),
    [("active", 1), ("inactive", 0), ("merged", 0), ("unrecognised", 0)],
)
async def test_history_and_people_list_count_the_same_speaker_statuses(
    history: HistoryDatabase, client: AsyncClient, status: str, counted: int
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, status)
        speaker = _link(session, recording, history.person)
        speaker.speaker_status = status
        session.add(speaker)
        session.add(
            Transcript(
                recording_id=recording.id,
                segments=[{"start": 0, "end": 3, "speaker": "SPEAKER_00"}],
            )
        )
        session.commit()
    result = await _read(client, history.person)
    assert result["meeting_count"] == len(result["meetings"]) == counted
    assert result["total_talk_time_seconds"] == 3 * counted
    assert await _listed_count(client, history.person) == counted


@pytest.fixture
def mcp_db(monkeypatch: pytest.MonkeyPatch, history: HistoryDatabase) -> None:
    """Point the MCP tools' lazily imported session maker at this schema."""
    import backend.core.db as core_db

    monkeypatch.setattr(core_db, "async_session_maker", history.sessions)


async def _filtered(client: AsyncClient, *people: GlobalSpeaker) -> list[str]:
    """The recordings list filtered to these people, newest first."""
    response = await client.get(
        "/api/v1/recordings/", params={"speaker_ids": [person.id for person in people]}
    )
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()]


async def _get_person_meetings(
    history: HistoryDatabase, person: GlobalSpeaker
) -> list[str]:
    """The meetings the MCP get_person tool lists, as the owner sees them."""
    current_mcp_user.set(history.owner)
    current_mcp_scopes.set(frozenset({MCP_READ_SCOPE}))
    result = await get_person(person.id)
    return [meeting["id"] for meeting in result["meetings"]]


async def _readers(
    client: AsyncClient, history: HistoryDatabase, person: GlobalSpeaker
) -> dict[str, Any]:
    """What each reader of a person's meetings says, to compare in one assert."""
    read = await _read(client, person)
    return {
        "history": [row["recording_id"] for row in read["meetings"]],
        "history_count": read["meeting_count"],
        "people_count": await _listed_count(client, person),
        "filter": await _filtered(client, person),
        "get_person": await _get_person_meetings(history, person),
    }


def _agreeing(expected: list[str]) -> dict[str, Any]:
    return {
        "history": expected,
        "history_count": len(expected),
        "people_count": len(expected),
        "filter": expected,
        "get_person": expected,
    }


@pytest.mark.anyio
async def test_every_reader_skips_meetings_their_lines_left_or_merged_away(
    history: HistoryDatabase, client: AsyncClient, mcp_db: None
) -> None:
    with Session(history.engine) as session:
        kept = _meeting(session, history.owner, "kept", created_at=FEBRUARY)
        _line(session, _link(session, kept, history.person), 2)
        moved = _meeting(session, history.owner, "moved")
        ours = _link(session, moved, history.person)
        stranger = _link(session, moved, history.foreign_person, "SPEAKER_01")
        line = _line(session, ours, 5)
        merged = _meeting(session, history.owner, "merged")
        source = _link(session, merged, history.person)
        target = _link(session, merged, history.foreign_person, "SPEAKER_01")
        _line(session, target, 3)
        session.flush()
        line.recording_speaker_id = stranger.id
        source.merged_into_id = target.id
        session.add_all([line, source])
        _settle_statuses(session, kept.id, moved.id, merged.id)
        assert (ours.speaker_status, source.speaker_status) == ("inactive", "merged")
        expected = [kept.public_id]
    assert await _readers(client, history, history.person) == _agreeing(expected)


@pytest.mark.anyio
async def test_every_reader_lists_a_meeting_again_once_a_line_moves_back(
    history: HistoryDatabase, client: AsyncClient, mcp_db: None
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "back-and-forth")
        ours = _link(session, recording, history.person)
        stranger = _link(session, recording, history.foreign_person, "SPEAKER_01")
        line = _line(session, ours, 4)
        _line(session, stranger, 9)
        session.flush()
        recording_id, ours_id, line_id = recording.id, ours.id, line.id
        public_id = recording.public_id
        line.recording_speaker_id = stranger.id
        session.add(line)
        _settle_statuses(session, recording_id)
    assert await _readers(client, history, history.person) == _agreeing([])
    with Session(history.engine) as session:
        moved = session.get(TranscriptUtterance, line_id)
        assert moved is not None
        moved.recording_speaker_id = ours_id
        session.add(moved)
        _settle_statuses(session, recording_id)
    assert await _readers(client, history, history.person) == _agreeing([public_id])


@pytest.mark.anyio
async def test_an_active_and_an_inactive_label_in_one_meeting_match_it_once(
    history: HistoryDatabase, client: AsyncClient, mcp_db: None
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, "two-labels")
        _line(session, _link(session, recording, history.person), 3)
        silent = _link(session, recording, history.person, "SPEAKER_01")
        _settle_statuses(session, recording.id)
        assert silent.speaker_status == "inactive"
        expected = [recording.public_id]
    assert await _readers(client, history, history.person) == _agreeing(expected)


@pytest.mark.anyio
async def test_filtering_by_two_people_lists_meetings_that_count_for_either(
    history: HistoryDatabase, client: AsyncClient
) -> None:
    # Alice is read after the session closes.
    with Session(history.engine, expire_on_commit=False) as session:
        alice = GlobalSpeaker(name="Alice Example", user_id=history.owner.id)
        session.add(alice)
        session.flush()
        first = _meeting(session, history.owner, "first", created_at=JANUARY)
        _line(session, _link(session, first, history.person), 1)
        second = _meeting(session, history.owner, "second", created_at=FEBRUARY)
        _line(session, _link(session, second, alice), 1)
        both = _meeting(session, history.owner, "both", created_at=datetime(2026, 3, 1))
        _line(session, _link(session, both, history.person), 1)
        _line(session, _link(session, both, alice, "SPEAKER_01"), 1)
        # Dana's line here moved to someone outside the filter.
        left = _meeting(session, history.owner, "left", created_at=datetime(2026, 4, 1))
        dana = _link(session, left, history.person)
        stranger = _link(session, left, history.foreign_person, "SPEAKER_01")
        line = _line(session, dana, 2)
        unrelated = _meeting(session, history.owner, "unrelated")
        _line(session, _link(session, unrelated, history.foreign_person), 1)
        session.flush()
        line.recording_speaker_id = stranger.id
        session.add(line)
        _settle_statuses(session, first.id, second.id, both.id, left.id, unrelated.id)
        expected = [both.public_id, second.public_id, first.public_id]
    assert await _filtered(client, history.person, alice) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "counted"),
    [("active", 1), ("inactive", 0), ("merged", 0), ("unrecognised", 0)],
)
async def test_person_filter_and_get_person_count_the_same_speaker_statuses(
    history: HistoryDatabase,
    client: AsyncClient,
    mcp_db: None,
    status: str,
    counted: int,
) -> None:
    with Session(history.engine) as session:
        recording = _meeting(session, history.owner, status)
        speaker = _link(session, recording, history.person)
        speaker.speaker_status = status
        session.add(speaker)
        expected = [recording.public_id] * counted
        session.commit()
    assert await _readers(client, history, history.person) == _agreeing(expected)


@pytest.mark.anyio
async def test_get_person_lists_only_the_users_own_meetings(
    history: HistoryDatabase, client: AsyncClient, mcp_db: None
) -> None:
    with Session(history.engine) as session:
        owned = _meeting(session, history.owner, "owned")
        _link(session, owned, history.person)
        # A foreign recording can carry an inconsistent link to our own person.
        foreign = _meeting(
            session, history.other, "foreign", created_at=datetime(2030, 1, 1)
        )
        _link(session, foreign, history.person)
        expected = [owned.public_id]
        session.commit()
    assert await _readers(client, history, history.person) == _agreeing(expected)


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
