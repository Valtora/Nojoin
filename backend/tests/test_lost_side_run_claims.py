"""A side run lost with its worker no longer strands the meeting (issue #828).

Celery acknowledges a task when a worker takes it, so a notes, AI analysis or
delivery run lost with its worker is never delivered again. Its ``generating``
claim used to refuse every later request for good, and a notes run also held
the recording in PROCESSING. A claim now expires 15 minutes after the row was
last written, which the running worker renews; an expired one reads as failed
and can be claimed again, and notes never move the recording's status.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.recordings import routes_actions
from backend.api.v1.endpoints.recordings.routes_query import _recording_etag
from backend.api.v1.endpoints.transcripts import routes_analytics, routes_notes
from backend.models.recording import Recording, RecordingStatus
from backend.models.recording_public import serialize_transcript
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services import ai_analysis_budget
from backend.tests.sqlite_schemas import (
    AI_ANALYSIS_RUNS_SCHEMA,
    GLOBAL_SPEAKERS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.generation_claim import (
    GENERATION_CLAIM_EXPIRY,
    LOST_GENERATION_MESSAGE,
    claim_generation,
    generation_active,
    reported_generation_error,
    reported_generation_status,
)
from backend.utils.time import utc_now

pytestmark = pytest.mark.anyio

NOTES_TASK = "backend.worker.tasks.generate_notes_task"
LOST = GENERATION_CLAIM_EXPIRY + timedelta(minutes=1)


def _ago(delta: timedelta):
    return utc_now() - delta


# --- the claim itself ---


def test_a_fresh_claim_is_active_and_an_old_one_is_lost() -> None:
    assert generation_active("generating", _ago(timedelta(minutes=1)))
    assert not generation_active("generating", _ago(LOST))
    assert not generation_active("completed", _ago(timedelta(minutes=1)))


def test_a_lost_claim_reads_as_failed_and_says_why() -> None:
    assert reported_generation_status("generating", _ago(LOST)) == "error"
    assert reported_generation_status("generating", utc_now()) == "generating"
    assert reported_generation_status("completed", _ago(LOST)) == "completed"
    assert reported_generation_error("generating", _ago(LOST), None) == (
        LOST_GENERATION_MESSAGE
    )
    assert reported_generation_error("error", _ago(LOST), "Model refused") == (
        "Model refused"
    )


def test_claiming_over_a_lost_claim_stamps_a_new_claim_time() -> None:
    """Writing "generating" over "generating" changes nothing the ORM flushes."""
    transcript = Transcript(
        recording_id=1, notes_status="generating", updated_at=_ago(LOST)
    )

    claim_generation(transcript, "notes_status")

    assert transcript.notes_status == "generating"
    assert generation_active(transcript.notes_status, transcript.updated_at)


def test_the_page_sees_a_lost_notes_claim_as_failed() -> None:
    transcript = Transcript(
        id=1,
        recording_id=1,
        notes_status="generating",
        transcript_status="completed",
        updated_at=_ago(LOST),
    )

    read = serialize_transcript(transcript, recording_public_id="rec-1")

    assert read.notes_status == "error"
    assert read.error_message == LOST_GENERATION_MESSAGE


def test_a_failed_transcription_keeps_its_own_message() -> None:
    transcript = Transcript(
        id=1,
        recording_id=1,
        notes_status="pending",
        transcript_status="error",
        error_message="Transcription failed: no speech model",
        updated_at=_ago(LOST),
    )

    read = serialize_transcript(transcript, recording_public_id="rec-1")

    assert read.error_message == "Transcription failed: no speech model"


def test_the_etag_moves_when_a_notes_claim_is_lost() -> None:
    """A finished page polls with If-None-Match; it must hear of the expiry."""
    live = _recording_etag(RecordingStatus.PROCESSED, 4, "generating", utc_now())
    lost = _recording_etag(RecordingStatus.PROCESSED, 4, "generating", _ago(LOST))

    assert live != lost
    assert lost == 'W/"PROCESSED-4-error"'


# --- the routes ---


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            AI_ANALYSIS_RUNS_SCHEMA,
        ):
            await connection.execute(text(schema))
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def owner(session_maker) -> User:
    async with session_maker() as session:
        user = User(
            username="alice",
            hashed_password="hashed",
            role="user",
            is_active=True,
            settings={},
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.fixture
def seed(session_maker, owner: User):
    """A finished meeting "rec-1" whose side runs claimed at ``claimed``."""

    async def _seed(
        *,
        claimed,
        notes_status: str = "pending",
        analytics_status: str = "pending",
        analytics_ai_status: str = "pending",
    ) -> None:
        async with session_maker() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO recordings (
                        id, created_at, updated_at, name, public_id,
                        meeting_uid, audio_path, status, client_status,
                        upload_progress, processing_progress, processing_step,
                        processing_completed_at, first_processed_at,
                        is_archived, is_deleted, user_id
                    ) VALUES (
                        1, :ts, :ts, 'Weekly sync', 'rec-1', 'uid-1',
                        '/tmp/a.wav', 'PROCESSED', 'IDLE', 100, 100,
                        'Completed', :ts, :ts, 0, 0, :user_id
                    )
                    """
                ),
                {"ts": claimed, "user_id": owner.id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO transcripts (
                        id, created_at, updated_at, recording_id, segments,
                        notes, notes_status, transcript_status,
                        analytics_payload, analytics_status, analytics_ai_status
                    ) VALUES (
                        1, :ts, :ts, 1, :segments, 'Earlier notes', :notes,
                        'completed', '{}', :delivery, :ai
                    )
                    """
                ),
                {
                    "ts": claimed,
                    "segments": (
                        '[{"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00",'
                        ' "text": "We agreed on the budget."}]'
                    ),
                    "notes": notes_status,
                    "delivery": analytics_status,
                    "ai": analytics_ai_status,
                },
            )
            await session.commit()

    return _seed


@pytest.fixture
async def client(session_maker, owner: User):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def database():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: owner
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def llm_configured(monkeypatch) -> None:
    async def resolved(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(missing_configuration_message=lambda: None)

    monkeypatch.setattr(routes_notes, "resolve_llm_config_async", resolved)


@pytest.fixture
def analytics_dispatches(monkeypatch) -> list:
    dispatched: list = []

    async def dispatch(name, *, args, context):
        dispatched.append((name, list(args)))
        return SimpleNamespace(id=f"task-{len(dispatched)}")

    monkeypatch.setattr(routes_analytics, "dispatch_task_best_effort", dispatch)
    monkeypatch.setattr(
        routes_analytics,
        "_backfill_and_compute",
        lambda _session, _recording: (
            1,
            {"speakers": [], "metrics": {}, "attribution_warning": None},
            {},
        ),
    )
    monkeypatch.setattr(
        ai_analysis_budget.config_manager, "get", lambda key, default=None: default
    )
    return dispatched


async def _claim_row(session_maker) -> tuple[Any, ...]:
    async with session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT r.status, t.notes_status, t.updated_at FROM recordings r "
                    "JOIN transcripts t ON t.recording_id = r.id WHERE r.id = 1"
                )
            )
        ).one()
    return tuple(row)


async def test_regenerate_notes_starts_over_a_claim_lost_with_its_worker(
    client: AsyncClient, seed, session_maker, stub_celery_dispatch: list
) -> None:
    await seed(claimed=_ago(LOST), notes_status="generating")

    response = await client.post("/api/v1/transcripts/rec-1/notes/generate")

    assert response.status_code == 200, response.text
    assert response.json()["notes_status"] == "generating"
    assert stub_celery_dispatch == [(NOTES_TASK, [1, None], None)]
    status, notes_status, claimed_at = await _claim_row(session_maker)
    # The new claim is current, and the meeting stays finished meanwhile.
    assert status == "PROCESSED"
    assert notes_status == "generating"
    assert generation_active(notes_status, datetime.fromisoformat(str(claimed_at)))


async def test_regenerate_notes_still_refuses_a_run_in_progress(
    client: AsyncClient, seed, stub_celery_dispatch: list
) -> None:
    await seed(claimed=_ago(timedelta(minutes=2)), notes_status="generating")

    response = await client.post("/api/v1/transcripts/rec-1/notes/generate")

    assert response.status_code == 409
    assert response.json()["detail"] == "Meeting notes are already generating."
    assert stub_celery_dispatch == []


async def test_reprocess_waits_for_notes_in_progress(
    client: AsyncClient, seed, session_maker, stub_celery_dispatch: list
) -> None:
    """Notes no longer hold the recording in PROCESSING, so their claim fences
    a reprocess that would replace the transcript under them."""
    await seed(claimed=_ago(timedelta(minutes=2)), notes_status="generating")

    response = await client.post(
        "/api/v1/recordings/rec-1/reprocess",
        json={"transcription_backend": "whisper"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        routes_actions.REPROCESS_NOTES_GENERATING_MESSAGE
    )
    assert stub_celery_dispatch == []
    assert (await _claim_row(session_maker))[:2] == ("PROCESSED", "generating")


def test_a_lost_notes_claim_does_not_fence_reprocess() -> None:
    recording = Recording(
        id=1, name="Weekly sync", audio_path="/tmp/a.wav", status="PROCESSED"
    )
    lost = Transcript(recording_id=1, notes_status="generating", updated_at=_ago(LOST))
    live = Transcript(recording_id=1, notes_status="generating", updated_at=utc_now())

    routes_actions._ensure_reprocessable(recording, lost)
    with pytest.raises(HTTPException) as refused:
        routes_actions._ensure_reprocessable(recording, live)
    assert refused.value.status_code == 409


async def test_analyse_meeting_starts_over_a_claim_lost_with_its_worker(
    client: AsyncClient, seed, analytics_dispatches: list
) -> None:
    await seed(claimed=_ago(LOST), analytics_ai_status="generating")

    response = await client.post("/api/v1/transcripts/rec-1/analytics/ai/generate")

    assert response.status_code == 200, response.text
    assert analytics_dispatches == [
        ("backend.worker.tasks.compute_meeting_analysis_task", [1])
    ]


async def test_analyse_meeting_still_refuses_a_run_in_progress(
    client: AsyncClient, seed, analytics_dispatches: list
) -> None:
    await seed(claimed=_ago(timedelta(minutes=2)), analytics_ai_status="generating")

    response = await client.post("/api/v1/transcripts/rec-1/analytics/ai/generate")

    assert response.status_code == 409
    assert analytics_dispatches == []


async def test_measure_delivery_starts_over_a_claim_lost_with_its_worker(
    client: AsyncClient, seed, analytics_dispatches: list
) -> None:
    await seed(claimed=_ago(LOST), analytics_status="generating")

    response = await client.post("/api/v1/transcripts/rec-1/analytics/generate")

    assert response.status_code == 200, response.text
    assert analytics_dispatches[0] == (
        "backend.worker.tasks.compute_delivery_analytics_task",
        [1],
    )


async def test_the_analytics_tab_reports_lost_claims_as_failed(
    client: AsyncClient, seed, analytics_dispatches: list
) -> None:
    await seed(
        claimed=_ago(LOST),
        analytics_status="generating",
        analytics_ai_status="generating",
    )

    response = await client.get("/api/v1/transcripts/rec-1/analytics")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["delivery_status"] == "error"
    assert body["delivery_error_message"] == LOST_GENERATION_MESSAGE
    assert body["ai_status"] == "error"
    assert body["ai_error_message"] == LOST_GENERATION_MESSAGE
