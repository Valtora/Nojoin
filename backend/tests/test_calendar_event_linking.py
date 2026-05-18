"""Tests for calendar event linking and meeting-context enrichment (MR-E)."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.recording import Recording
from backend.services.calendar_link_service import (
    AUTO_LINK_MIN_SCORE,
    auto_link_recording,
    score_event_match,
)
from backend.services.calendar_service import (
    _normalise_google_event,
    _normalise_microsoft_event,
)
from backend.utils.meeting_notes import (
    MeetingEventContext,
    build_meeting_context_prompt_section,
    meeting_event_context_from_calendar_event,
)


# ---------------------------------------------------------------------------
# score_event_match - pure helper
# ---------------------------------------------------------------------------

BASE = datetime(2026, 5, 16, 10, 0, 0)


def test_score_event_match_exact_overlap() -> None:
    score = score_event_match(
        BASE, BASE + timedelta(hours=1), BASE, BASE + timedelta(hours=1)
    )
    assert score == 1.0


def test_score_event_match_partial_overlap() -> None:
    # Recording 10:00-11:00, event 10:30-11:30 -> 30 min overlap of a 60 min interval.
    score = score_event_match(
        BASE,
        BASE + timedelta(hours=1),
        BASE + timedelta(minutes=30),
        BASE + timedelta(minutes=90),
    )
    assert score == pytest.approx(0.5)


def test_score_event_match_no_overlap() -> None:
    score = score_event_match(
        BASE,
        BASE + timedelta(hours=1),
        BASE + timedelta(hours=2),
        BASE + timedelta(hours=3),
    )
    assert score == 0.0


def test_score_event_match_zero_duration_event_returns_zero() -> None:
    score = score_event_match(BASE, BASE + timedelta(hours=1), BASE, BASE)
    assert score == 0.0


def test_score_event_match_missing_event_window_returns_zero() -> None:
    assert score_event_match(BASE, BASE + timedelta(hours=1), None, None) == 0.0


def test_score_event_match_normalises_to_shorter_interval() -> None:
    # A short recording (10 min) fully inside a long meeting (2 h) scores 1.0
    # because the overlap equals the shorter interval.
    score = score_event_match(
        BASE + timedelta(minutes=20),
        BASE + timedelta(minutes=30),
        BASE,
        BASE + timedelta(hours=2),
    )
    assert score == 1.0


# ---------------------------------------------------------------------------
# auto_link_recording - conservative worker hook
# ---------------------------------------------------------------------------

CALENDAR_SCHEMA = [
    """
    CREATE TABLE calendar_connections (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        user_id INTEGER NOT NULL,
        provider VARCHAR(32) NOT NULL,
        provider_account_id VARCHAR(255) NOT NULL,
        email VARCHAR(320),
        display_name VARCHAR(255),
        access_token_encrypted TEXT,
        refresh_token_encrypted TEXT,
        granted_scopes JSON NOT NULL,
        token_expires_at DATETIME,
        sync_status VARCHAR(32) NOT NULL,
        sync_error VARCHAR(512),
        last_sync_started_at DATETIME,
        last_sync_completed_at DATETIME,
        last_synced_at DATETIME
    )
    """,
    """
    CREATE TABLE calendar_sources (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        connection_id INTEGER NOT NULL,
        provider_calendar_id VARCHAR(512) NOT NULL,
        name VARCHAR(255) NOT NULL,
        description TEXT,
        time_zone VARCHAR(128),
        colour VARCHAR(32),
        user_colour VARCHAR(32),
        is_primary BOOLEAN NOT NULL,
        is_read_only BOOLEAN NOT NULL,
        is_selected BOOLEAN NOT NULL,
        sync_cursor TEXT,
        last_synced_at DATETIME,
        sync_window_start DATETIME,
        sync_window_end DATETIME
    )
    """,
    """
    CREATE TABLE calendar_events (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        calendar_id INTEGER NOT NULL,
        provider_event_id VARCHAR(512) NOT NULL,
        title VARCHAR(512) NOT NULL,
        status VARCHAR(32) NOT NULL,
        is_all_day BOOLEAN NOT NULL,
        starts_at DATETIME,
        ends_at DATETIME,
        start_date DATE,
        end_date DATE,
        location_text TEXT,
        description TEXT,
        attendees JSON,
        meeting_url VARCHAR(2048),
        source_url VARCHAR(2048),
        external_updated_at DATETIME
    )
    """,
    """
    CREATE TABLE recordings (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        name VARCHAR(255) NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        meeting_uid VARCHAR(36) NOT NULL,
        audio_path VARCHAR(1024) NOT NULL,
        proxy_path VARCHAR(1024),
        celery_task_id VARCHAR(255),
        duration_seconds FLOAT,
        file_size_bytes INTEGER,
        status VARCHAR(32) NOT NULL,
        client_status VARCHAR(32),
        upload_progress INTEGER NOT NULL,
        processing_progress INTEGER NOT NULL,
        processing_step VARCHAR(255),
        processing_started_at DATETIME,
        processing_completed_at DATETIME,
        is_archived BOOLEAN NOT NULL,
        is_deleted BOOLEAN NOT NULL,
        user_id INTEGER,
        calendar_event_id INTEGER
    )
    """,
]


@pytest.fixture
def sync_session():
    """A SQLModel-style sync session over an in-memory SQLite database."""
    from sqlmodel import Session, create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        for statement in CALENDAR_SCHEMA:
            connection.execute(text(statement))
    with Session(engine) as session:
        yield session
    engine.dispose()


def _seed_calendar(
    session,
    *,
    user_id: int = 1,
    is_selected: bool = True,
) -> int:
    """Insert a connection + selected source, return the calendar source id."""
    now = "2026-05-16 00:00:00"
    session.execute(
        text(
            """
            INSERT INTO calendar_connections (
                id, created_at, updated_at, user_id, provider,
                provider_account_id, granted_scopes, sync_status
            ) VALUES (:id, :now, :now, :user_id, 'google', 'acct', '[]', 'idle')
            """
        ),
        {"id": user_id * 10, "now": now, "user_id": user_id},
    )
    session.execute(
        text(
            """
            INSERT INTO calendar_sources (
                id, created_at, updated_at, connection_id, provider_calendar_id,
                name, is_primary, is_read_only, is_selected
            ) VALUES (:id, :now, :now, :connection_id, 'cal', 'Work', 0, 0, :selected)
            """
        ),
        {
            "id": user_id * 10,
            "now": now,
            "connection_id": user_id * 10,
            "selected": 1 if is_selected else 0,
        },
    )
    session.commit()
    return user_id * 10


def _insert_event(
    session,
    *,
    event_id: int,
    calendar_id: int,
    starts_at: datetime | None,
    ends_at: datetime | None,
    is_all_day: bool = False,
    title: str = "Sync",
) -> None:
    session.execute(
        text(
            """
            INSERT INTO calendar_events (
                id, created_at, updated_at, calendar_id, provider_event_id,
                title, status, is_all_day, starts_at, ends_at
            ) VALUES (
                :id, :now, :now, :calendar_id, :provider_event_id,
                :title, 'confirmed', :is_all_day, :starts_at, :ends_at
            )
            """
        ),
        {
            "id": event_id,
            "now": "2026-05-16 00:00:00",
            "calendar_id": calendar_id,
            "provider_event_id": f"evt-{event_id}",
            "title": title,
            "is_all_day": 1 if is_all_day else 0,
            "starts_at": starts_at,
            "ends_at": ends_at,
        },
    )
    session.commit()


def _make_recording(
    *,
    user_id: int = 1,
    duration_seconds: float | None = 3600.0,
    calendar_event_id: int | None = None,
) -> Recording:
    return Recording(
        id=500,
        name="Recorded meeting",
        audio_path="/tmp/rec.wav",
        created_at=BASE,
        updated_at=BASE,
        user_id=user_id,
        duration_seconds=duration_seconds,
        calendar_event_id=calendar_event_id,
    )


def test_auto_link_links_clear_single_match(sync_session) -> None:
    calendar_id = _seed_calendar(sync_session)
    _insert_event(
        sync_session,
        event_id=1,
        calendar_id=calendar_id,
        starts_at=BASE,
        ends_at=BASE + timedelta(hours=1),
    )
    recording = _make_recording()

    auto_link_recording(sync_session, recording)

    assert recording.calendar_event_id == 1


def test_auto_link_skips_ambiguous_match(sync_session) -> None:
    calendar_id = _seed_calendar(sync_session)
    # Two events both fully overlapping the recording window -> both score 1.0.
    _insert_event(
        sync_session,
        event_id=1,
        calendar_id=calendar_id,
        starts_at=BASE,
        ends_at=BASE + timedelta(hours=1),
    )
    _insert_event(
        sync_session,
        event_id=2,
        calendar_id=calendar_id,
        starts_at=BASE,
        ends_at=BASE + timedelta(hours=1),
    )
    recording = _make_recording()

    auto_link_recording(sync_session, recording)

    assert recording.calendar_event_id is None


def test_auto_link_skips_all_day_event(sync_session) -> None:
    calendar_id = _seed_calendar(sync_session)
    _insert_event(
        sync_session,
        event_id=1,
        calendar_id=calendar_id,
        starts_at=None,
        ends_at=None,
        is_all_day=True,
    )
    recording = _make_recording()

    auto_link_recording(sync_session, recording)

    assert recording.calendar_event_id is None


def test_auto_link_skips_zero_duration_event(sync_session) -> None:
    calendar_id = _seed_calendar(sync_session)
    _insert_event(
        sync_session,
        event_id=1,
        calendar_id=calendar_id,
        starts_at=BASE,
        ends_at=BASE,
    )
    recording = _make_recording()

    auto_link_recording(sync_session, recording)

    assert recording.calendar_event_id is None


def test_auto_link_noop_when_already_linked(sync_session) -> None:
    calendar_id = _seed_calendar(sync_session)
    _insert_event(
        sync_session,
        event_id=1,
        calendar_id=calendar_id,
        starts_at=BASE,
        ends_at=BASE + timedelta(hours=1),
    )
    recording = _make_recording(calendar_event_id=999)

    auto_link_recording(sync_session, recording)

    # A manual link must never be clobbered.
    assert recording.calendar_event_id == 999


def test_auto_link_noop_without_selected_calendars(sync_session) -> None:
    calendar_id = _seed_calendar(sync_session, is_selected=False)
    _insert_event(
        sync_session,
        event_id=1,
        calendar_id=calendar_id,
        starts_at=BASE,
        ends_at=BASE + timedelta(hours=1),
    )
    recording = _make_recording()

    auto_link_recording(sync_session, recording)

    assert recording.calendar_event_id is None


def test_auto_link_excludes_cross_user_events(sync_session) -> None:
    # The overlapping event belongs to user 2; the recording belongs to user 1.
    other_calendar = _seed_calendar(sync_session, user_id=2)
    _insert_event(
        sync_session,
        event_id=1,
        calendar_id=other_calendar,
        starts_at=BASE,
        ends_at=BASE + timedelta(hours=1),
    )
    recording = _make_recording(user_id=1)

    auto_link_recording(sync_session, recording)

    assert recording.calendar_event_id is None


def test_auto_link_noop_without_duration(sync_session) -> None:
    calendar_id = _seed_calendar(sync_session)
    _insert_event(
        sync_session,
        event_id=1,
        calendar_id=calendar_id,
        starts_at=BASE,
        ends_at=BASE + timedelta(hours=1),
    )
    recording = _make_recording(duration_seconds=None)

    auto_link_recording(sync_session, recording)

    assert recording.calendar_event_id is None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def build_test_user(user_id: int = 1, username: str = "alice"):
    return SimpleNamespace(
        id=user_id, username=username, force_password_change=False
    )


@pytest.fixture
async def async_session_maker() -> sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        for statement in CALENDAR_SCHEMA:
            await connection.execute(text(statement))
    try:
        yield session_maker
    finally:
        await engine.dispose()


@pytest.fixture
async def client(async_session_maker: sessionmaker) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with async_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: build_test_user()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _insert_recording_async(
    session_maker: sessionmaker,
    *,
    recording_id: int = 700,
    public_id: str = "link-rec-public-id",
    user_id: int = 1,
    duration_seconds: float = 3600.0,
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    is_archived, is_deleted, user_id, duration_seconds
                ) VALUES (
                    :id, :now, :now, 'Meeting', :public_id, :meeting_uid,
                    '/tmp/rec.wav', 'PROCESSED', 0, 100, 0, 0, :user_id, :duration
                )
                """
            ),
            {
                "id": recording_id,
                "now": "2026-05-16 10:00:00",
                "public_id": public_id,
                "meeting_uid": f"uid-{recording_id}",
                "user_id": user_id,
                "duration": duration_seconds,
            },
        )
        await session.commit()


async def _insert_calendar_async(
    session_maker: sessionmaker,
    *,
    user_id: int = 1,
    calendar_id: int | None = None,
) -> int:
    calendar_id = calendar_id if calendar_id is not None else user_id * 10
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO calendar_connections (
                    id, created_at, updated_at, user_id, provider,
                    provider_account_id, granted_scopes, sync_status
                ) VALUES (:id, :now, :now, :user_id, 'google', 'acct', '[]', 'idle')
                """
            ),
            {"id": calendar_id, "now": "2026-05-16 00:00:00", "user_id": user_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO calendar_sources (
                    id, created_at, updated_at, connection_id, provider_calendar_id,
                    name, is_primary, is_read_only, is_selected
                ) VALUES (:id, :now, :now, :connection_id, 'cal', 'Work', 0, 0, 1)
                """
            ),
            {
                "id": calendar_id,
                "now": "2026-05-16 00:00:00",
                "connection_id": calendar_id,
            },
        )
        await session.commit()
    return calendar_id


async def _insert_event_async(
    session_maker: sessionmaker,
    *,
    event_id: int,
    calendar_id: int,
    starts_at: str = "2026-05-16 10:00:00",
    ends_at: str = "2026-05-16 11:00:00",
    title: str = "Planning",
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO calendar_events (
                    id, created_at, updated_at, calendar_id, provider_event_id,
                    title, status, is_all_day, starts_at, ends_at
                ) VALUES (
                    :id, :now, :now, :calendar_id, :provider_event_id,
                    :title, 'confirmed', 0, :starts_at, :ends_at
                )
                """
            ),
            {
                "id": event_id,
                "now": "2026-05-16 00:00:00",
                "calendar_id": calendar_id,
                "provider_event_id": f"evt-{event_id}",
                "title": title,
                "starts_at": starts_at,
                "ends_at": ends_at,
            },
        )
        await session.commit()


@pytest.mark.anyio
async def test_link_endpoint_links_changes_and_unlinks(
    client: AsyncClient, async_session_maker: sessionmaker
) -> None:
    await _insert_recording_async(async_session_maker)
    calendar_id = await _insert_calendar_async(async_session_maker)
    await _insert_event_async(
        async_session_maker, event_id=1, calendar_id=calendar_id, title="First"
    )
    await _insert_event_async(
        async_session_maker, event_id=2, calendar_id=calendar_id, title="Second"
    )

    # Link
    response = await client.put(
        "/api/v1/recordings/link-rec-public-id/calendar-event",
        json={"calendar_event_id": 1},
    )
    assert response.status_code == 200
    assert response.json()["calendar_event"]["id"] == 1
    assert response.json()["calendar_event"]["title"] == "First"

    # Change
    response = await client.put(
        "/api/v1/recordings/link-rec-public-id/calendar-event",
        json={"calendar_event_id": 2},
    )
    assert response.status_code == 200
    assert response.json()["calendar_event"]["id"] == 2

    # Unlink
    response = await client.put(
        "/api/v1/recordings/link-rec-public-id/calendar-event",
        json={"calendar_event_id": None},
    )
    assert response.status_code == 200
    assert response.json()["calendar_event"] is None


@pytest.mark.anyio
async def test_link_endpoint_rejects_cross_user_event(
    client: AsyncClient, async_session_maker: sessionmaker
) -> None:
    await _insert_recording_async(async_session_maker)
    # Calendar + event belong to a different user.
    other_calendar = await _insert_calendar_async(async_session_maker, user_id=2)
    await _insert_event_async(
        async_session_maker, event_id=99, calendar_id=other_calendar
    )

    response = await client.put(
        "/api/v1/recordings/link-rec-public-id/calendar-event",
        json={"calendar_event_id": 99},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_link_endpoint_unknown_recording_returns_404(
    client: AsyncClient,
) -> None:
    response = await client.put(
        "/api/v1/recordings/does-not-exist/calendar-event",
        json={"calendar_event_id": None},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_candidates_endpoint_returns_scored_owner_scoped_events(
    client: AsyncClient, async_session_maker: sessionmaker
) -> None:
    await _insert_recording_async(async_session_maker)
    calendar_id = await _insert_calendar_async(async_session_maker)
    # A near-perfect overlap and a poorer overlap.
    await _insert_event_async(
        async_session_maker,
        event_id=1,
        calendar_id=calendar_id,
        starts_at="2026-05-16 10:00:00",
        ends_at="2026-05-16 11:00:00",
        title="Best",
    )
    await _insert_event_async(
        async_session_maker,
        event_id=2,
        calendar_id=calendar_id,
        starts_at="2026-05-16 10:45:00",
        ends_at="2026-05-16 11:45:00",
        title="Worse",
    )
    # A cross-user event must not appear.
    other_calendar = await _insert_calendar_async(
        async_session_maker, user_id=2, calendar_id=20
    )
    await _insert_event_async(
        async_session_maker, event_id=3, calendar_id=other_calendar, title="Other"
    )

    response = await client.get(
        "/api/v1/recordings/link-rec-public-id/calendar-event/candidates"
    )
    assert response.status_code == 200
    body = response.json()
    titles = [event["title"] for event in body]
    assert "Other" not in titles
    # Best overlap first.
    assert titles[0] == "Best"
    assert set(titles) == {"Best", "Worse"}


# ---------------------------------------------------------------------------
# Provider normalisers - description + attendees capture
# ---------------------------------------------------------------------------


def test_normalise_google_event_captures_description_and_attendees() -> None:
    event = _normalise_google_event(
        {
            "id": "g-1",
            "summary": "Roadmap",
            "status": "confirmed",
            "start": {"dateTime": "2026-05-16T09:00:00Z"},
            "end": {"dateTime": "2026-05-16T10:00:00Z"},
            "description": "Quarterly roadmap review",
            "attendees": [
                {"displayName": "Alice Smith", "email": "alice@example.com"},
                {"email": "bob@example.com"},
            ],
        }
    )
    assert event is not None
    assert event.description == "Quarterly roadmap review"
    assert event.attendees == [
        {"name": "Alice Smith", "email": "alice@example.com"},
        {"name": "bob@example.com", "email": "bob@example.com"},
    ]


def test_normalise_microsoft_event_captures_body_preview_and_skips_resources() -> None:
    event = _normalise_microsoft_event(
        {
            "id": "m-1",
            "subject": "Roadmap",
            "isCancelled": False,
            "isAllDay": False,
            "start": {"dateTime": "2026-05-16T09:00:00.000000"},
            "end": {"dateTime": "2026-05-16T10:00:00.000000"},
            "bodyPreview": "Plain text agenda",
            "attendees": [
                {
                    "type": "required",
                    "emailAddress": {"name": "Carol", "address": "carol@example.com"},
                },
                {
                    "type": "resource",
                    "emailAddress": {"name": "Room A", "address": "room@example.com"},
                },
            ],
        }
    )
    assert event is not None
    assert event.description == "Plain text agenda"
    # The resource (meeting room) attendee is skipped.
    assert event.attendees == [
        {"name": "Carol", "email": "carol@example.com"},
    ]


# ---------------------------------------------------------------------------
# Enrichment - build_meeting_context_prompt_section
# ---------------------------------------------------------------------------


def test_meeting_context_section_fallback_when_no_event() -> None:
    section = build_meeting_context_prompt_section(None)
    assert section == "No calendar event is linked to this meeting."


def test_meeting_context_section_includes_title_description_and_attendees() -> None:
    context = MeetingEventContext(
        title="Sprint review",
        description="Demo the new features",
        attendees=["Dana", "Erin"],
    )
    section = build_meeting_context_prompt_section(context)
    assert "Sprint review" in section
    assert "Demo the new features" in section
    assert "Dana" in section
    assert "Erin" in section
    # Attendees are presented as candidate speaker names.
    assert "candidate speaker names" in section.lower()


def test_meeting_event_context_from_calendar_event_maps_attendee_names() -> None:
    event = SimpleNamespace(
        title="1:1",
        description="Career chat",
        attendees=[
            {"name": "Frank", "email": "frank@example.com"},
            {"email": "grace@example.com"},
        ],
    )
    context = meeting_event_context_from_calendar_event(event)
    assert context is not None
    assert context.title == "1:1"
    assert context.attendees == ["Frank", "grace@example.com"]


def test_meeting_event_context_from_calendar_event_none_for_missing_event() -> None:
    assert meeting_event_context_from_calendar_event(None) is None


def test_auto_link_min_score_is_conservative() -> None:
    # Sanity check that the threshold is not accidentally lowered to 0.
    assert AUTO_LINK_MIN_SCORE >= 0.5
