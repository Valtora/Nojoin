"""Real capture routes retire word comparisons before a new capture episode."""

import importlib
from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import select

from backend.api.v1.endpoints.recordings import routes_capture as capture
from backend.core.db import driver_urls
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline.live_settling import (
    SettlingPass,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.live_settling_projection import row_words
from backend.utils.config_manager import config_manager
from backend.utils.time import utc_now
from backend.utils.transcript_settling import (
    capture_publication_token,
    resolve_capture_settling_policy,
)

SCHEMA = "live_settling_lifecycle_test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def lifecycle_sessions(
    postgres_test_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    importlib.import_module("backend.models.registry")
    engine = create_async_engine(
        driver_urls(postgres_test_url)[0],
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(lambda sync: fresh_schema(sync, SCHEMA))
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: {
            "live_text_settling": "auto",
            "live_text_settling_k": 5,
            "live_text_settling_window_s": 10,
            "live_speaker_labels": "off",
        }.get(key, default),
    )
    monkeypatch.setattr(
        capture.recordings_module, "_get_last_uploaded_sequence", lambda _id: -1
    )
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed(sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int, str]:
    async with sessions() as db:
        user = User(username="settling-lifecycle", hashed_password="x")
        db.add(user)
        await db.flush()
        recording = Recording(
            name="Live meeting",
            audio_path="/data/live.wav",
            user_id=user.id,
            status=RecordingStatus.UPLOADING,
            client_status=ClientStatus.RECORDING,
            capture_epoch=4,
            last_activity_at=utc_now() - timedelta(minutes=10),
        )
        db.add(recording)
        await db.flush()
        transcript = Transcript(
            recording_id=recording.id,
            segments=[],
            settling_state={
                "version": 1,
                "eligible": True,
                "generation": "prior-episode",
                "capture_epoch": 4,
                "sealed": False,
                "phase": "live",
                "policy": {"k": 3, "window_s": 10, "enabled": True},
            },
        )
        db.add(transcript)
        await db.flush()
        for step in range(2):
            await db.run_sync(
                lambda sync: publish_live_settling(
                    sync,
                    request=SettlingPass(
                        recording.id,
                        capture_publication_token(recording, transcript),
                        f"initial-{step}",
                        step,
                        -8000,
                        2000,
                        transcript.change_sequence,
                    ),
                    words=[{"word": "Accepted", "start_ms": 100, "end_ms": 500}],
                )
            )
            await db.commit()
        return user.id, recording.id, recording.public_id


async def _snapshot(
    sessions: async_sessionmaker[AsyncSession],
    recording_id: int,
) -> tuple[dict[str, Any], list[tuple[str, list[dict[str, Any]]]]]:
    async with sessions() as db:
        transcript = (
            await db.execute(
                select(Transcript).where(Transcript.recording_id == recording_id)
            )
        ).scalar_one()
        rows = (
            (
                await db.execute(
                    select(TranscriptUtterance)
                    .where(
                        TranscriptUtterance.recording_id == recording_id,
                        TranscriptUtterance.state
                        == TranscriptUtteranceState.PROVISIONAL,
                    )
                    .order_by(TranscriptUtterance.start_ms)
                )
            )
            .scalars()
            .all()
        )
        return dict(transcript.settling_state), [
            (row.public_id, row_words(row)) for row in rows
        ]


@pytest.mark.anyio
@pytest.mark.parametrize("transition", ["pause", "stalled-takeover"])
async def test_capture_transition_closes_old_comparisons_before_new_policy(
    lifecycle_sessions: async_sessionmaker[AsyncSession],
    transition: str,
) -> None:
    sessions = lifecycle_sessions
    user_id, recording_id, public_id = await _seed(sessions)
    _, initial = await _snapshot(sessions, recording_id)
    assert initial[0][1][0]["unchanged_count"] == 1
    async with sessions() as db:
        if transition == "pause":
            await capture.pause_upload(
                public_id, epoch=4, db=db, current_user=SimpleNamespace(id=user_id)
            )
        await capture.resume_upload(
            public_id,
            epoch=4 if transition == "pause" else None,
            db=db,
            current_user=SimpleNamespace(id=user_id),
        )
    state, closed = await _snapshot(sessions, recording_id)
    assert state["generation"] != "prior-episode"
    assert state["policy"]["k"] == 5
    assert "last_decode_id" not in state
    assert closed == [
        (
            initial[0][0],
            [{**initial[0][1][0], "locked": True, "lock_reason": "closure"}],
        )
    ]
    async with sessions() as db:
        recording = await db.get(Recording, recording_id)
        transcript = (
            await db.execute(
                select(Transcript).where(Transcript.recording_id == recording_id)
            )
        ).scalar_one()
        resolve_capture_settling_policy(transcript, device="cuda")
        result = await db.run_sync(
            lambda sync: publish_live_settling(
                sync,
                request=SettlingPass(
                    recording_id,
                    capture_publication_token(recording, transcript),
                    "new-episode",
                    2,
                    -6000,
                    4000,
                    transcript.change_sequence,
                ),
                words=[{"word": "Later", "start_ms": 2100, "end_ms": 2500}],
            )
        )
        assert result.accepted
        await db.commit()
    _, current = await _snapshot(sessions, recording_id)
    assert current[0] == closed[0]
    assert current[1][1][0]["unchanged_count"] == 0


@pytest.mark.anyio
async def test_same_holder_live_resume_keeps_its_comparisons_in_flight(
    lifecycle_sessions: async_sessionmaker[AsyncSession],
) -> None:
    user_id, recording_id, public_id = await _seed(lifecycle_sessions)
    before = await _snapshot(lifecycle_sessions, recording_id)
    async with lifecycle_sessions() as db:
        await capture.resume_upload(
            public_id, epoch=4, db=db, current_user=SimpleNamespace(id=user_id)
        )
    after = await _snapshot(lifecycle_sessions, recording_id)
    assert after == before
