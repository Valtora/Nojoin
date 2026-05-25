from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.pipeline import ProcessingRunKind, TranscriptUtteranceState
from backend.models.transcript import Transcript
from backend.utils.canonical_pipeline import (
    list_pending_startup_cutover_recording_ids,
    process_startup_cutover_recording,
)


RECORDINGS_SCHEMA = """
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
    trim_start_s FLOAT,
    trim_end_s FLOAT,
    file_size_bytes INTEGER,
    status VARCHAR(32) NOT NULL,
    client_status VARCHAR(32),
    upload_progress INTEGER NOT NULL,
    processing_progress INTEGER NOT NULL,
    processing_step VARCHAR(255),
    processing_started_at DATETIME,
    processing_completed_at DATETIME,
    pipeline_generation VARCHAR(32) DEFAULT 'unified',
    is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    user_id INTEGER,
    calendar_event_id INTEGER
)
"""

TRANSCRIPTS_SCHEMA = """
CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL UNIQUE,
    text TEXT,
    segments JSON,
    notes TEXT,
    user_notes TEXT,
    meeting_edge_focus TEXT,
    meeting_edge_payload JSON,
    meeting_edge_status VARCHAR(32) NOT NULL DEFAULT 'idle',
    meeting_edge_error_message TEXT,
    meeting_edge_source_signature TEXT,
    speaker_name_suggestions JSON,
    notes_status VARCHAR(32) NOT NULL,
    transcript_status VARCHAR(32) NOT NULL,
    error_message TEXT
)
"""

CHAT_MESSAGES_SCHEMA = """
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL,
    user_id INTEGER,
    role VARCHAR(32) NOT NULL,
    content TEXT
)
"""

CONTEXT_CHUNKS_SCHEMA = """
CREATE TABLE context_chunks (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL,
    document_id INTEGER,
    content TEXT,
    embedding JSON
)
"""

GLOBAL_SPEAKERS_SCHEMA = """
CREATE TABLE global_speakers (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    user_id INTEGER,
    name VARCHAR(255),
    embedding JSON,
    is_voiceprint_locked BOOLEAN NOT NULL DEFAULT 0,
    color VARCHAR(32),
    title VARCHAR(255),
    company VARCHAR(255),
    email VARCHAR(255),
    phone_number VARCHAR(255),
    notes TEXT,
    description TEXT
)
"""

RECORDING_SPEAKERS_SCHEMA = """
CREATE TABLE recording_speakers (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    global_speaker_id INTEGER,
    diarization_label VARCHAR(255),
    local_name VARCHAR(255),
    name VARCHAR(255),
    snippet_start FLOAT,
    snippet_end FLOAT,
    voice_snippet_path VARCHAR(1024),
    embedding JSON,
    color VARCHAR(32),
    merged_into_id INTEGER,
    speaker_status VARCHAR(32) NOT NULL,
    speaker_kind VARCHAR(32) NOT NULL,
    processing_run_id INTEGER,
    last_speaker_correction_event_id INTEGER,
    last_diarization_window_result_id INTEGER,
    first_seen_ms INTEGER,
    last_seen_ms INTEGER,
    identity_confidence FLOAT,
    identity_locked BOOLEAN NOT NULL
)
"""

PROCESSING_RUNS_SCHEMA = """
CREATE TABLE processing_runs (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    parent_run_id INTEGER,
    run_kind VARCHAR(32) NOT NULL,
    trigger_source VARCHAR(32) NOT NULL,
    requested_by_user_id INTEGER,
    status VARCHAR(32) NOT NULL,
    config_hash VARCHAR(255),
    transcription_backend VARCHAR(255),
    diarization_backend VARCHAR(255),
    model_metadata JSON,
    span_start_ms INTEGER,
    span_end_ms INTEGER,
    reused_live_asr BOOLEAN NOT NULL,
    idempotency_key VARCHAR(255),
    metrics JSON,
    error_summary TEXT,
    started_at DATETIME,
    completed_at DATETIME
)
"""

RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA = """
CREATE TABLE recording_audio_window_manifests (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    window_index INTEGER NOT NULL,
    source_kind VARCHAR(32) NOT NULL,
    target_window_ms INTEGER NOT NULL,
    hop_ms INTEGER NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_end_ms INTEGER NOT NULL,
    chunk_start_sequence INTEGER NOT NULL,
    chunk_end_sequence INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,
    is_partial BOOLEAN NOT NULL,
    is_sealed BOOLEAN NOT NULL,
    processing_run_id INTEGER,
    last_error TEXT
)
"""

DIARIZATION_WINDOW_RESULTS_SCHEMA = """
CREATE TABLE diarization_window_results (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    processing_run_id INTEGER,
    window_index INTEGER NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_end_ms INTEGER NOT NULL,
    chunk_start_sequence INTEGER,
    chunk_end_sequence INTEGER,
    model_name VARCHAR(255),
    model_version VARCHAR(255),
    device VARCHAR(255),
    config_hash VARCHAR(255),
    status VARCHAR(32) NOT NULL,
    raw_payload JSON
)
"""

DIARIZATION_WINDOW_TURNS_SCHEMA = """
CREATE TABLE diarization_window_turns (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    window_result_id INTEGER NOT NULL,
    local_speaker_key VARCHAR(255) NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    confidence FLOAT,
    matched_recording_speaker_id INTEGER,
    metadata_payload JSON
)
"""

TRANSCRIPT_UTTERANCES_SCHEMA = """
CREATE TABLE transcript_utterances (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    sort_key VARCHAR(64) NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    speaker_label VARCHAR(255),
    recording_speaker_id INTEGER,
    state VARCHAR(32) NOT NULL,
    source_kind VARCHAR(255) NOT NULL,
    processing_run_id INTEGER,
    last_utterance_event_id INTEGER,
    last_diarization_window_result_id INTEGER,
    revision INTEGER NOT NULL,
    overlap_group_id VARCHAR(64),
    overlap_rank INTEGER NOT NULL,
    manual_text_locked BOOLEAN NOT NULL,
    manual_speaker_locked BOOLEAN NOT NULL,
    text_confidence FLOAT,
    speaker_confidence FLOAT,
    confidence_payload JSON
)
"""

TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA = """
CREATE TABLE transcript_utterance_events (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL,
    utterance_id INTEGER NOT NULL,
    processing_run_id INTEGER,
    actor_user_id INTEGER,
    event_type VARCHAR(64) NOT NULL,
    source VARCHAR(64) NOT NULL,
    old_values JSON,
    new_values JSON,
    resulting_revision INTEGER NOT NULL
)
"""

RECORDING_SPEAKER_ALIASES_SCHEMA = """
CREATE TABLE recording_speaker_aliases (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_speaker_id INTEGER NOT NULL,
    alias_type VARCHAR(64) NOT NULL,
    alias_value VARCHAR(255) NOT NULL,
    source_run_id INTEGER,
    active BOOLEAN NOT NULL,
    valid_from_ms INTEGER,
    valid_to_ms INTEGER,
    confidence FLOAT
)
"""

PEOPLE_TAGS_SCHEMA = """
CREATE TABLE people_tags (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    tag_id INTEGER,
    global_speaker_id INTEGER
)
"""

SPEAKER_CORRECTION_EVENTS_SCHEMA = """
CREATE TABLE speaker_correction_events (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    actor_user_id INTEGER,
    utterance_id INTEGER,
    source_recording_speaker_id INTEGER,
    target_recording_speaker_id INTEGER,
    target_global_speaker_id INTEGER,
    event_type VARCHAR(64) NOT NULL,
    scope VARCHAR(64) NOT NULL,
    effective_from_ms INTEGER,
    payload JSON
)
"""


def build_test_user(user_id: int = 1, username: str = "alice"):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=user_id,
        username=username,
        settings={},
        force_password_change=False,
    )


@pytest.fixture
async def test_session_maker() -> sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.execute(text(RECORDINGS_SCHEMA))
        await connection.execute(text(TRANSCRIPTS_SCHEMA))
        await connection.execute(text(CHAT_MESSAGES_SCHEMA))
        await connection.execute(text(CONTEXT_CHUNKS_SCHEMA))
        await connection.execute(text(GLOBAL_SPEAKERS_SCHEMA))
        await connection.execute(text(RECORDING_SPEAKERS_SCHEMA))
        await connection.execute(text(PROCESSING_RUNS_SCHEMA))
        await connection.execute(text(RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA))
        await connection.execute(text(DIARIZATION_WINDOW_RESULTS_SCHEMA))
        await connection.execute(text(DIARIZATION_WINDOW_TURNS_SCHEMA))
        await connection.execute(text(TRANSCRIPT_UTTERANCES_SCHEMA))
        await connection.execute(text(TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA))
        await connection.execute(text(RECORDING_SPEAKER_ALIASES_SCHEMA))
        await connection.execute(text(SPEAKER_CORRECTION_EVENTS_SCHEMA))
        await connection.execute(text(PEOPLE_TAGS_SCHEMA))

    try:
        yield session_maker
    finally:
        await engine.dispose()


@pytest.fixture
async def api_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(api_app: FastAPI, test_session_maker: sessionmaker, monkeypatch) -> AsyncClient:
    from backend.api.v1.endpoints import transcripts as transcripts_module

    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    api_app.dependency_overrides[get_db] = override_get_db
    api_app.dependency_overrides[get_current_user] = lambda: build_test_user()
    monkeypatch.setattr(transcripts_module, "_dispatch_meeting_edge_refresh", lambda *args, **kwargs: None)

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

    api_app.dependency_overrides.clear()


async def _seed_processed_recording(
    session_maker: sessionmaker,
    public_id: str = "canon-rec",
    *,
    recording_id: int = 1,
    segment_id: str | None = None,
    pipeline_generation: str | None = "unified",
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (
                    :recording_id, :now, :now, 'Canonical meeting', :public_id, :meeting_uid,
                    '/tmp/canon.wav', 'PROCESSED', 0, 100, :pipeline_generation, 0, 0, 1
                )
                """
            ),
            {
                "now": "2026-05-19 00:00:00",
                "recording_id": recording_id,
                "meeting_uid": f"meeting-uid-{recording_id}",
                "public_id": public_id,
                "pipeline_generation": pipeline_generation,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    notes, user_notes, meeting_edge_status, notes_status,
                    transcript_status
                ) VALUES (
                    :recording_id, :now, :now, :recording_id, 'hello there', :segments,
                    NULL, NULL, 'idle', 'pending', 'completed'
                )
                """
            ),
            {
                "now": "2026-05-19 00:00:00",
                "recording_id": recording_id,
                "segments": (
                    '[{"id": "' + segment_id + '", "start": 0.0, "end": 1.2, "speaker": "SPEAKER_00", "text": "hello there", "segment_source": "legacy"}]'
                    if segment_id
                    else '[{"start": 0.0, "end": 1.2, "speaker": "SPEAKER_00", "text": "hello there", "segment_source": "legacy"}]'
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    :recording_id, :now, :now, :speaker_public_id, :recording_id,
                    NULL, 'SPEAKER_00', NULL, 'Speaker 1',
                    NULL, NULL, 'active', 'automated',
                    NULL, NULL, NULL, 0
                )
                """
            ),
            {
                "now": "2026-05-19 00:00:00",
                "recording_id": recording_id,
                "speaker_public_id": f"speaker-public-{recording_id}",
            },
        )
        await session.commit()


async def _seed_uploading_recording(
    session_maker: sessionmaker,
    public_id: str = "live-rec",
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (
                    1, :now, :now, 'Live meeting', :public_id, 'meeting-uid-live',
                    '/tmp/live.wav', 'UPLOADING', 50, 10, 'unified', 0, 0, 1
                )
                """
            ),
            {"now": "2026-05-20 00:00:00", "public_id": public_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    notes, user_notes, meeting_edge_status, notes_status,
                    transcript_status
                ) VALUES (
                    1, :now, :now, 1, '', '[]',
                    NULL, NULL, 'idle', 'pending', 'processing'
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()


async def _set_transcript_speaker_suggestions(
    session_maker: sessionmaker,
    suggestions: list[dict[str, object]],
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE transcripts SET speaker_name_suggestions = :suggestions WHERE recording_id = 1"
            ),
            {"suggestions": json.dumps(suggestions)},
        )
        await session.commit()


def _build_pending_speaker_suggestion(
    *,
    diarization_label: str,
    suggested_name: str,
) -> dict[str, object]:
    return {
        "id": f"suggestion-{diarization_label.lower()}",
        "diarization_label": diarization_label,
        "recording_speaker_id": 1,
        "suggested_name": suggested_name,
        "suggested_global_speaker_id": None,
        "confidence": 0.91,
        "status": "pending",
        "origin": "manual_retry",
        "source": "llm",
        "provider": "openai",
        "rationale": "The speaker introduces themselves by name.",
        "evidence_spans": [
            {
                "quote": "hello there",
                "reason": "self_introduction",
                "start_seconds": 0.0,
                "end_seconds": 1.2,
            }
        ],
        "signals": ["self_introduction"],
        "created_at": "2026-05-19T00:00:00+00:00",
        "updated_at": "2026-05-19T00:00:00+00:00",
        "resolved_at": None,
        "resolution_reason": None,
        "resolution_actor_user_id": None,
    }


@pytest.mark.anyio
async def test_get_utterances_backfills_processed_transcript(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    response = await client.get("/api/v1/transcripts/canon-rec/utterances")

    assert response.status_code == 200
    body = response.json()
    assert body["revision"] >= 1
    assert len(body["utterances"]) == 1
    assert body["utterances"][0]["speaker"] == "SPEAKER_00"
    assert body["utterances"][0]["id"]

    async with test_session_maker() as session:
        transcript = (await session.execute(select(Transcript).where(Transcript.recording_id == 1))).scalar_one()
        assert transcript.segments[0]["id"] == body["utterances"][0]["id"]
        count = (await session.execute(text("SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1"))).scalar_one()
        last_event_id = (
            await session.execute(
                text("SELECT last_utterance_event_id FROM transcript_utterances WHERE recording_id = 1")
            )
        ).scalar_one()
        assert count == 1
        assert last_event_id is not None


@pytest.mark.anyio
async def test_legacy_recording_remains_readable_but_blocks_transcript_mutation(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(
        test_session_maker,
        segment_id="legacy-segment-1",
        pipeline_generation="legacy_backfilled",
    )

    read_response = await client.get("/api/v1/transcripts/canon-rec/utterances")
    assert read_response.status_code == 200
    assert len(read_response.json()["utterances"]) == 1

    mutate_response = await client.patch(
        "/api/v1/transcripts/canon-rec/utterances/legacy-segment-1/text",
        json={"text": "updated text", "expected_revision": 1},
    )

    assert mutate_response.status_code == 409
    assert "must be reprocessed" in mutate_response.json()["detail"]


@pytest.mark.anyio
async def test_legacy_reprocess_required_recording_remains_readable(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(
        test_session_maker,
        segment_id="legacy-segment-reprocess-required",
        pipeline_generation="legacy_reprocess_required",
    )

    response = await client.get("/api/v1/transcripts/canon-rec/utterances")

    assert response.status_code == 200
    body = response.json()
    assert len(body["utterances"]) == 1
    assert body["utterances"][0]["speaker"] == "SPEAKER_00"

    async with test_session_maker() as session:
        generation = (
            await session.execute(text("SELECT pipeline_generation FROM recordings WHERE id = 1"))
        ).scalar_one()

    assert generation == "legacy_reprocess_required"


@pytest.mark.anyio
async def test_legacy_recording_blocks_recording_speaker_mutation(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(
        test_session_maker,
        pipeline_generation="legacy_backfilled",
    )

    response = await client.put(
        "/api/v1/speakers/recordings/canon-rec",
        json={
            "diarization_label": "SPEAKER_00",
            "global_speaker_name": "Jordan",
        },
    )

    assert response.status_code == 409
    assert "must be reprocessed" in response.json()["detail"]


@pytest.mark.anyio
async def test_startup_cutover_backfills_pending_legacy_recording(
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(
        test_session_maker,
        pipeline_generation=None,
    )

    async with test_session_maker() as session:
        pending_ids = await session.run_sync(
            lambda sync_session: list_pending_startup_cutover_recording_ids(sync_session, batch_size=10)
        )
        assert pending_ids == [1]

        outcome = await session.run_sync(
            lambda sync_session: process_startup_cutover_recording(
                sync_session,
                recording_id=1,
            )
        )
        await session.commit()

        generation = (
            await session.execute(text("SELECT pipeline_generation FROM recordings WHERE id = 1"))
        ).scalar_one()
        utterance_count = (
            await session.execute(text("SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1"))
        ).scalar_one()

    assert outcome == "backfilled"
    assert generation == "legacy_backfilled"
    assert utterance_count == 1


@pytest.mark.anyio
async def test_startup_cutover_is_idempotent_after_partial_completion(
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(
        test_session_maker,
        pipeline_generation=None,
    )

    async with test_session_maker() as session:
        first_outcome = await session.run_sync(
            lambda sync_session: process_startup_cutover_recording(
                sync_session,
                recording_id=1,
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        second_outcome = await session.run_sync(
            lambda sync_session: process_startup_cutover_recording(
                sync_session,
                recording_id=1,
            )
        )
        await session.commit()

        generation = (
            await session.execute(text("SELECT pipeline_generation FROM recordings WHERE id = 1"))
        ).scalar_one()
        utterance_count = (
            await session.execute(text("SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1"))
        ).scalar_one()
        processing_run_count = (
            await session.execute(text("SELECT COUNT(*) FROM processing_runs WHERE recording_id = 1"))
        ).scalar_one()

    assert first_outcome == "backfilled"
    assert second_outcome == "already_backfilled"
    assert generation == "legacy_backfilled"
    assert utterance_count == 1
    assert processing_run_count == 1


@pytest.mark.anyio
async def test_startup_cutover_resumes_remaining_recordings_after_restart(
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(
        test_session_maker,
        public_id="canon-rec-1",
        recording_id=1,
        pipeline_generation=None,
    )
    await _seed_processed_recording(
        test_session_maker,
        public_id="canon-rec-2",
        recording_id=2,
        pipeline_generation=None,
    )

    async with test_session_maker() as session:
        pending_before_restart = await session.run_sync(
            lambda sync_session: list_pending_startup_cutover_recording_ids(sync_session, batch_size=10)
        )
        first_outcome = await session.run_sync(
            lambda sync_session: process_startup_cutover_recording(
                sync_session,
                recording_id=1,
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        pending_after_restart = await session.run_sync(
            lambda sync_session: list_pending_startup_cutover_recording_ids(sync_session, batch_size=10)
        )
        second_outcome = await session.run_sync(
            lambda sync_session: process_startup_cutover_recording(
                sync_session,
                recording_id=2,
            )
        )
        await session.commit()

        generations = (
            await session.execute(text("SELECT id, pipeline_generation FROM recordings ORDER BY id"))
        ).all()

    assert pending_before_restart == [1, 2]
    assert first_outcome == "backfilled"
    assert pending_after_restart == [2]
    assert second_outcome == "backfilled"
    assert generations == [
        (1, "legacy_backfilled"),
        (2, "legacy_backfilled"),
    ]


@pytest.mark.anyio
async def test_startup_cutover_marks_inflight_legacy_recording_reprocess_required(
    test_session_maker: sessionmaker,
) -> None:
    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(text("UPDATE recordings SET pipeline_generation = NULL WHERE id = 1"))
        await session.commit()

        outcome = await session.run_sync(
            lambda sync_session: process_startup_cutover_recording(
                sync_session,
                recording_id=1,
            )
        )
        await session.commit()

        generation, status_value = (
            await session.execute(
                text("SELECT pipeline_generation, status FROM recordings WHERE id = 1")
            )
        ).one()

    assert outcome == "classified_inflight"
    assert generation == "legacy_reprocess_required"
    assert status_value == "ERROR"


@pytest.mark.anyio
async def test_manual_recording_speaker_rename_supersedes_pending_suggestion(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)
    await _set_transcript_speaker_suggestions(
        test_session_maker,
        [_build_pending_speaker_suggestion(diarization_label="SPEAKER_00", suggested_name="Alex")],
    )

    utterances_response = await client.get("/api/v1/transcripts/canon-rec/utterances")
    assert utterances_response.status_code == 200

    response = await client.put(
        "/api/v1/speakers/recordings/canon-rec",
        json={
            "diarization_label": "SPEAKER_00",
            "global_speaker_name": "Jordan",
        },
    )

    assert response.status_code == 200

    async with test_session_maker() as session:
        transcript = (
            await session.execute(select(Transcript).where(Transcript.recording_id == 1))
        ).scalar_one()

    assert transcript.speaker_name_suggestions[0]["status"] == "superseded"
    assert transcript.speaker_name_suggestions[0]["resolution_reason"] == "manual_name_change"
    assert transcript.speaker_name_suggestions[0]["resolution_actor_user_id"] == 1


@pytest.mark.anyio
async def test_accept_recording_speaker_suggestion_updates_identity_and_marks_accepted(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)
    await _set_transcript_speaker_suggestions(
        test_session_maker,
        [_build_pending_speaker_suggestion(diarization_label="SPEAKER_00", suggested_name="Alex")],
    )

    utterances_response = await client.get("/api/v1/transcripts/canon-rec/utterances")
    assert utterances_response.status_code == 200

    response = await client.post(
        "/api/v1/speakers/recordings/canon-rec/speakers/SPEAKER_00/suggestions/accept"
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    async with test_session_maker() as session:
        transcript = (
            await session.execute(select(Transcript).where(Transcript.recording_id == 1))
        ).scalar_one()
        speaker_row = (
            await session.execute(
                text(
                    "SELECT local_name, name FROM recording_speakers WHERE recording_id = 1 AND diarization_label = 'SPEAKER_00'"
                )
            )
        ).one()

    assert transcript.speaker_name_suggestions[0]["status"] == "accepted"
    assert transcript.speaker_name_suggestions[0]["resolution_reason"] == "accepted_by_user"
    assert transcript.speaker_name_suggestions[0]["resolution_actor_user_id"] == 1
    assert speaker_row[0] == "Alex" or speaker_row[1] == "Alex"


@pytest.mark.anyio
async def test_reject_recording_speaker_suggestion_marks_rejected(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)
    await _set_transcript_speaker_suggestions(
        test_session_maker,
        [_build_pending_speaker_suggestion(diarization_label="SPEAKER_00", suggested_name="Alex")],
    )

    response = await client.post(
        "/api/v1/speakers/recordings/canon-rec/speakers/SPEAKER_00/suggestions/reject"
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    async with test_session_maker() as session:
        transcript = (
            await session.execute(select(Transcript).where(Transcript.recording_id == 1))
        ).scalar_one()

    assert transcript.speaker_name_suggestions[0]["status"] == "rejected"
    assert transcript.speaker_name_suggestions[0]["resolution_reason"] == "rejected_by_user"
    assert transcript.speaker_name_suggestions[0]["resolution_actor_user_id"] == 1


@pytest.mark.anyio
async def test_legacy_segment_text_update_syncs_canonical_utterance(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)
    utterances = await client.get("/api/v1/transcripts/canon-rec/utterances")
    utterance_id = utterances.json()["utterances"][0]["id"]

    response = await client.put(
        "/api/v1/transcripts/canon-rec/segments/0/text",
        json={"text": "updated text"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["segments"][0]["id"] == utterance_id
    assert body["segments"][0]["text"] == "updated text"

    async with test_session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT text, revision, last_utterance_event_id FROM transcript_utterances WHERE public_id = :public_id"
                ),
                {"public_id": utterance_id},
            )
        ).one()
        recent_event_types = (
            await session.execute(
                text(
                    "SELECT event_type FROM transcript_utterance_events WHERE utterance_id = (SELECT id FROM transcript_utterances WHERE public_id = :public_id) ORDER BY id DESC LIMIT 2"
                ),
                {"public_id": utterance_id},
            )
        ).scalars().all()
        assert row[0] == "updated text"
        assert row[1] == 2
        assert row[2] is not None
        assert recent_event_types == ["manual_lock_text", "update_text"]


@pytest.mark.anyio
async def test_bulk_segment_replace_preserves_manual_text_lock(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)
    initial = await client.get("/api/v1/transcripts/canon-rec/utterances")
    utterance = initial.json()["utterances"][0]

    text_update = await client.put(
        "/api/v1/transcripts/canon-rec/segments/0/text",
        json={"text": "manual text"},
    )
    assert text_update.status_code == 200

    response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "id": utterance["id"],
                    "start": 0.0,
                    "end": 1.4,
                    "speaker": "SPEAKER_00",
                    "text": "asr overwrite",
                    "segment_source": "live",
                }
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["segments"][0]["text"] == "manual text"
    assert body["segments"][0]["text_manually_edited"] is True

    async with test_session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT text, manual_text_locked FROM transcript_utterances WHERE public_id = :public_id"
                ),
                {"public_id": utterance["id"]},
            )
        ).one()
        assert row[0] == "manual text"
        assert bool(row[1]) is True


@pytest.mark.anyio
async def test_bulk_segment_replace_preserves_stable_ids_for_processed_recording(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)
    initial = await client.get("/api/v1/transcripts/canon-rec/utterances")
    utterance = initial.json()["utterances"][0]

    response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "id": utterance["id"],
                    "start": 0.0,
                    "end": 1.2,
                    "speaker": "SPEAKER_00",
                    "text": "undo redo text",
                    "segment_source": "legacy",
                    "revision": utterance["revision"],
                }
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["segments"][0]["id"] == utterance["id"]
    assert body["segments"][0]["text"] == "undo redo text"

    async with test_session_maker() as session:
        total_rows = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterances WHERE public_id = :public_id"
                ),
                {"public_id": utterance["id"]},
            )
        ).scalar_one()
        current_revision = (
            await session.execute(
                text(
                    "SELECT revision FROM transcript_utterances WHERE public_id = :public_id"
                ),
                {"public_id": utterance["id"]},
            )
        ).scalar_one()
        assert total_rows == 1
        assert current_revision == 2


@pytest.mark.anyio
async def test_bulk_segment_replace_records_timing_revision_event(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)
    initial = await client.get("/api/v1/transcripts/canon-rec/utterances")
    utterance = initial.json()["utterances"][0]

    response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "id": utterance["id"],
                    "start": 0.0,
                    "end": 1.4,
                    "speaker": "SPEAKER_00",
                    "text": utterance["text"],
                    "segment_source": "legacy",
                    "revision": utterance["revision"],
                }
            ]
        },
    )

    assert response.status_code == 200

    async with test_session_maker() as session:
        event_row = (
            await session.execute(
                text(
                    "SELECT event_type, new_values, last_utterance_event_id FROM transcript_utterance_events JOIN transcript_utterances ON transcript_utterances.id = transcript_utterance_events.utterance_id WHERE transcript_utterances.public_id = :public_id ORDER BY transcript_utterance_events.id DESC LIMIT 1"
                ),
                {"public_id": utterance["id"]},
            )
        ).one()
        assert event_row[0] == "update_timing"
        assert event_row[2] is not None


@pytest.mark.anyio
async def test_get_utterance_delta_returns_tombstones_after_supersession(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)
    initial = await client.get("/api/v1/transcripts/canon-rec/utterances")
    initial_body = initial.json()
    initial_revision = initial_body["revision"]
    original_id = initial_body["utterances"][0]["id"]

    replace_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "speaker": "SPEAKER_00",
                    "text": "hello",
                    "segment_source": "legacy",
                },
                {
                    "start": 0.5,
                    "end": 1.2,
                    "speaker": "SPEAKER_00",
                    "text": "there",
                    "segment_source": "legacy",
                },
            ]
        },
    )

    assert replace_response.status_code == 200

    delta = await client.get(
        f"/api/v1/transcripts/canon-rec/utterances?after_revision={initial_revision}"
    )

    assert delta.status_code == 200
    body = delta.json()
    assert len(body["utterances"]) == 2
    assert original_id in body["tombstones"]
    assert all(item["id"] != original_id for item in body["utterances"])


@pytest.mark.anyio
async def test_rollback_flag_disables_canonical_writes_and_uses_legacy_json(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    from backend.api.v1.endpoints import transcripts as transcripts_module

    await _seed_processed_recording(
        test_session_maker,
        segment_id="legacy-segment-1",
    )

    original_get = transcripts_module.config_manager.get

    def _fake_get(key, default=None):
        if key == "enable_canonical_transcript_writes":
            return False
        return original_get(key, default)

    monkeypatch.setattr(transcripts_module.config_manager, "get", _fake_get)

    utterances = await client.get("/api/v1/transcripts/canon-rec/utterances")

    assert utterances.status_code == 200
    payload = utterances.json()
    assert payload["revision"] == 0
    assert payload["utterances"][0]["id"] == "legacy-segment-1"

    update = await client.patch(
        "/api/v1/transcripts/canon-rec/utterances/legacy-segment-1/text",
        json={"text": "legacy rollback text"},
    )

    assert update.status_code == 200
    body = update.json()
    assert body["segments"][0]["id"] == "legacy-segment-1"
    assert body["segments"][0]["text"] == "legacy rollback text"

    async with test_session_maker() as session:
        transcript = (
            await session.execute(select(Transcript).where(Transcript.recording_id == 1))
        ).scalar_one()
        canonical_count = (
            await session.execute(
                text("SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1")
            )
        ).scalar_one()
        assert transcript.segments[0]["text"] == "legacy rollback text"
        assert canonical_count == 0


@pytest.mark.anyio
async def test_speaker_patch_scope_updates_all_matching_utterances_and_creates_manual_alias(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    replace_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "speaker": "SPEAKER_00",
                    "text": "first",
                    "segment_source": "legacy",
                },
                {
                    "start": 0.5,
                    "end": 1.0,
                    "speaker": "SPEAKER_00",
                    "text": "second",
                    "segment_source": "legacy",
                },
            ]
        },
    )
    assert replace_response.status_code == 200

    utterances = await client.get("/api/v1/transcripts/canon-rec/utterances")
    utterance_id = utterances.json()["utterances"][0]["id"]

    response = await client.patch(
        f"/api/v1/transcripts/canon-rec/utterances/{utterance_id}/speaker",
        json={
            "new_speaker_name": "Dana",
            "scope": "speaker_everywhere_in_recording",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["segments"]) == 2
    assert body["segments"][0]["speaker"] == body["segments"][1]["speaker"]
    assert body["segments"][0]["speaker"].startswith("MANUAL_")
    assert body["segments"][0]["speaker_manually_edited"] is True
    assert body["segments"][1]["speaker_manually_edited"] is True

    async with test_session_maker() as session:
        revisions = (
            await session.execute(
                text(
                        "SELECT revision FROM transcript_utterances WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED' ORDER BY sort_key"
                )
            )
        ).scalars().all()
        alias_rows = (
            await session.execute(
                text(
                    "SELECT alias_type, alias_value FROM recording_speaker_aliases ORDER BY alias_value"
                )
            )
        ).all()
        normalized_alias_rows = {(alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows}
        correction_scope = (
            await session.execute(
                text(
                    "SELECT scope FROM speaker_correction_events ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar_one()
        target_speaker_provenance = (
            await session.execute(
                text(
                    "SELECT last_speaker_correction_event_id FROM recording_speakers WHERE local_name = 'Dana' ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar_one()
        recent_event_types = (
            await session.execute(
                text(
                    "SELECT event_type FROM transcript_utterance_events WHERE recording_id = 1 ORDER BY id DESC LIMIT 2"
                )
            )
        ).scalars().all()
        assert revisions == [2, 2]
        assert ("display_name", "Dana") in normalized_alias_rows
        assert correction_scope.lower() == "speaker_everywhere_in_recording"
        assert target_speaker_provenance is not None
        assert "manual_lock_speaker" in recent_event_types


@pytest.mark.anyio
async def test_bulk_segment_replace_preserves_manual_speaker_lock(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    replace_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "speaker": "LIVE_01",
                    "text": "live speaker",
                    "segment_source": "live",
                }
            ]
        },
    )
    assert replace_response.status_code == 200

    utterances = await client.get("/api/v1/transcripts/canon-rec/utterances")
    utterance = utterances.json()["utterances"][0]

    speaker_update = await client.patch(
        f"/api/v1/transcripts/canon-rec/utterances/{utterance['id']}/speaker",
        json={
            "new_speaker_name": "Dana",
            "scope": "speaker_everywhere_in_recording",
        },
    )
    assert speaker_update.status_code == 200
    target_label = speaker_update.json()["segments"][0]["speaker"]

    response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "id": utterance["id"],
                    "start": 0.0,
                    "end": 1.2,
                    "speaker": "LIVE_01",
                    "text": "live speaker updated",
                    "segment_source": "live",
                }
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["segments"][0]["speaker"] == target_label
    assert body["segments"][0]["speaker_manually_edited"] is True

    async with test_session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT speaker_label, manual_speaker_locked FROM transcript_utterances WHERE public_id = :public_id"
                ),
                {"public_id": utterance["id"]},
            )
        ).one()
        assert row[0] == target_label
        assert bool(row[1]) is True


@pytest.mark.anyio
async def test_speaker_patch_with_global_target_creates_global_alias(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO global_speakers (
                    id, created_at, updated_at, user_id, name, embedding,
                    is_voiceprint_locked, color, description
                ) VALUES (
                    7, :now, :now, 1, 'Jane Doe', NULL, 0, NULL, NULL
                )
                """
            ),
            {"now": "2026-05-19 00:00:00"},
        )
        await session.commit()

    utterances = await client.get("/api/v1/transcripts/canon-rec/utterances")
    utterance_id = utterances.json()["utterances"][0]["id"]

    response = await client.patch(
        f"/api/v1/transcripts/canon-rec/utterances/{utterance_id}/speaker",
        json={
            "new_speaker_name": "Jane Doe",
            "global_speaker_id": 7,
            "scope": "utterance_only",
        },
    )

    assert response.status_code == 200

    async with test_session_maker() as session:
        alias_rows = (
            await session.execute(
                text(
                    "SELECT alias_type, alias_value FROM recording_speaker_aliases ORDER BY alias_value"
                )
            )
        ).all()
        normalized_alias_rows = {(alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows}
        linked_global_id = (
            await session.execute(
                text(
                    "SELECT global_speaker_id FROM recording_speakers WHERE diarization_label LIKE 'MANUAL_%' ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar_one()
        assert ("global_name", "Jane Doe") in normalized_alias_rows
        assert linked_global_id == 7


@pytest.mark.anyio
async def test_merge_scope_marks_source_speaker_as_merged(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    replace_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "speaker": "SPEAKER_00",
                    "text": "first",
                    "segment_source": "legacy",
                },
                {
                    "start": 0.5,
                    "end": 1.0,
                    "speaker": "SPEAKER_01",
                    "text": "second",
                    "segment_source": "legacy",
                },
            ]
        },
    )
    assert replace_response.status_code == 200

    utterances = await client.get("/api/v1/transcripts/canon-rec/utterances")
    first_utterance_id = utterances.json()["utterances"][0]["id"]

    response = await client.patch(
        f"/api/v1/transcripts/canon-rec/utterances/{first_utterance_id}/speaker",
        json={
            "new_speaker_name": "SPEAKER_01",
            "diarization_label": "SPEAKER_01",
            "scope": "merge_into_speaker",
        },
    )

    assert response.status_code == 200

    async with test_session_maker() as session:
        merged_row = (
            await session.execute(
                text(
                    "SELECT merged_into_id, speaker_status FROM recording_speakers WHERE diarization_label = 'SPEAKER_00'"
                )
            )
        ).one()
        target_id = (
            await session.execute(
                text(
                    "SELECT id FROM recording_speakers WHERE diarization_label = 'SPEAKER_01'"
                )
            )
        ).scalar_one()
        event_type = (
            await session.execute(
                text(
                    "SELECT event_type FROM speaker_correction_events ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar_one()
        assert merged_row[0] == target_id
        assert merged_row[1] == "merged"
        assert event_type.lower() == "merge_speakers"


@pytest.mark.anyio
async def test_boundary_replacements_record_split_and_merge_events(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    original = await client.get("/api/v1/transcripts/canon-rec/utterances")
    original_id = original.json()["utterances"][0]["id"]

    split_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "speaker": "SPEAKER_00",
                    "text": "first",
                    "segment_source": "legacy",
                },
                {
                    "start": 0.5,
                    "end": 1.0,
                    "speaker": "SPEAKER_00",
                    "text": "second",
                    "segment_source": "legacy",
                },
            ]
        },
    )

    assert split_response.status_code == 200

    async with test_session_maker() as session:
        split_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterance_events WHERE recording_id = 1 AND event_type = 'split'"
                )
            )
        ).scalar_one()
        superseded_event = (
            await session.execute(
                text(
                    "SELECT event_type FROM transcript_utterance_events WHERE utterance_id = (SELECT id FROM transcript_utterances WHERE public_id = :public_id) ORDER BY id DESC LIMIT 1"
                ),
                {"public_id": original_id},
            )
        ).scalar_one()

        assert split_count == 2
        assert superseded_event == "supersede"

    merge_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "speaker": "SPEAKER_00",
                    "text": "first second",
                    "segment_source": "legacy",
                }
            ]
        },
    )

    assert merge_response.status_code == 200

    async with test_session_maker() as session:
        merge_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterance_events WHERE recording_id = 1 AND event_type = 'merge'"
                )
            )
        ).scalar_one()
        active_event = (
            await session.execute(
                text(
                    "SELECT transcript_utterance_events.event_type FROM transcript_utterances JOIN transcript_utterance_events ON transcript_utterance_events.id = transcript_utterances.last_utterance_event_id WHERE transcript_utterances.recording_id = 1 AND UPPER(transcript_utterances.state) != 'SUPERSEDED' ORDER BY transcript_utterances.id DESC LIMIT 1"
                )
            )
        ).scalar_one()

        assert merge_count == 1
        assert active_event == "merge"


@pytest.mark.anyio
async def test_merge_supersession_delta_reports_all_prior_tombstones(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    split_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "speaker": "SPEAKER_00",
                    "text": "first",
                    "segment_source": "legacy",
                },
                {
                    "start": 0.5,
                    "end": 1.0,
                    "speaker": "SPEAKER_00",
                    "text": "second",
                    "segment_source": "legacy",
                },
            ]
        },
    )
    assert split_response.status_code == 200

    current = await client.get("/api/v1/transcripts/canon-rec/utterances")
    current_body = current.json()
    current_revision = current_body["revision"]
    old_ids = {item["id"] for item in current_body["utterances"]}

    merge_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "speaker": "SPEAKER_00",
                    "text": "first second",
                    "segment_source": "legacy",
                }
            ]
        },
    )

    assert merge_response.status_code == 200

    delta = await client.get(
        f"/api/v1/transcripts/canon-rec/utterances?after_revision={current_revision}"
    )

    assert delta.status_code == 200
    body = delta.json()
    assert len(body["utterances"]) == 1
    assert old_ids.issubset(set(body["tombstones"]))


@pytest.mark.anyio
async def test_merge_scope_preserves_source_alias_for_future_resolution(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    replace_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "speaker": "SPEAKER_00",
                    "text": "first",
                    "segment_source": "legacy",
                },
                {
                    "start": 0.5,
                    "end": 1.0,
                    "speaker": "SPEAKER_01",
                    "text": "second",
                    "segment_source": "legacy",
                },
            ]
        },
    )
    assert replace_response.status_code == 200

    utterances = await client.get("/api/v1/transcripts/canon-rec/utterances")
    first_utterance_id = utterances.json()["utterances"][0]["id"]

    merge_response = await client.patch(
        f"/api/v1/transcripts/canon-rec/utterances/{first_utterance_id}/speaker",
        json={
            "new_speaker_name": "SPEAKER_01",
            "diarization_label": "SPEAKER_01",
            "scope": "merge_into_speaker",
        },
    )
    assert merge_response.status_code == 200

    future_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 1.0,
                    "end": 1.5,
                    "speaker": "SPEAKER_00",
                    "text": "follow up",
                    "segment_source": "legacy",
                }
            ]
        },
    )

    assert future_response.status_code == 200
    assert future_response.json()["segments"][0]["speaker"] == "SPEAKER_01"

    async with test_session_maker() as session:
        target_id = (
            await session.execute(
                text(
                    "SELECT id FROM recording_speakers WHERE diarization_label = 'SPEAKER_01'"
                )
            )
        ).scalar_one()
        latest_utterance = (
            await session.execute(
                text(
                    "SELECT recording_speaker_id, speaker_label FROM transcript_utterances WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED' ORDER BY id DESC LIMIT 1"
                )
            )
        ).one()
        target_alias_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM recording_speaker_aliases WHERE recording_speaker_id = :target_id AND alias_value = 'SPEAKER_00'"
                ),
                {"target_id": target_id},
            )
        ).scalar_one()

        assert latest_utterance[0] == target_id
        assert latest_utterance[1] == "SPEAKER_01"
        assert target_alias_count >= 1


@pytest.mark.anyio
async def test_live_and_speaker_aliases_are_persisted_for_canonical_speakers(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)
    await client.get("/api/v1/transcripts/canon-rec/utterances")

    response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "speaker": "LIVE_01",
                    "text": "live speaker",
                    "segment_source": "live",
                }
            ]
        },
    )

    assert response.status_code == 200

    async with test_session_maker() as session:
        alias_rows = (
            await session.execute(
                text(
                    "SELECT alias_type, alias_value FROM recording_speaker_aliases ORDER BY alias_value"
                )
            )
        ).all()
        normalized_alias_rows = {(alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows}
        assert ("diarization_label", "SPEAKER_00") in normalized_alias_rows
        assert ("live_label", "LIVE_01") in normalized_alias_rows


@pytest.mark.anyio
async def test_live_label_alias_continuity_routes_future_live_segments_to_corrected_speaker(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    initial_replace = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "speaker": "LIVE_01",
                    "text": "live speaker",
                    "segment_source": "live",
                }
            ]
        },
    )
    assert initial_replace.status_code == 200

    utterances = await client.get("/api/v1/transcripts/canon-rec/utterances")
    utterance_id = utterances.json()["utterances"][0]["id"]

    correction = await client.patch(
        f"/api/v1/transcripts/canon-rec/utterances/{utterance_id}/speaker",
        json={
            "new_speaker_name": "Dana",
            "scope": "speaker_everywhere_in_recording",
        },
    )
    assert correction.status_code == 200
    target_label = correction.json()["segments"][0]["speaker"]

    future_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 1.0,
                    "end": 2.0,
                    "speaker": "LIVE_01",
                    "text": "follow up",
                    "segment_source": "live",
                }
            ]
        },
    )

    assert future_response.status_code == 200
    assert future_response.json()["segments"][0]["speaker"] == target_label

    async with test_session_maker() as session:
        target_id = (
            await session.execute(
                text(
                    "SELECT id FROM recording_speakers WHERE diarization_label = :label ORDER BY id DESC LIMIT 1"
                ),
                {"label": target_label},
            )
        ).scalar_one()
        latest_utterance = (
            await session.execute(
                text(
                    "SELECT recording_speaker_id, speaker_label FROM transcript_utterances WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED' ORDER BY id DESC LIMIT 1"
                )
            )
        ).one()
        target_alias_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM recording_speaker_aliases WHERE recording_speaker_id = :target_id AND alias_value = 'LIVE_01' AND active = 1"
                ),
                {"target_id": target_id},
            )
        ).scalar_one()

        assert latest_utterance[0] == target_id
        assert latest_utterance[1] == target_label
        assert target_alias_count >= 1


@pytest.mark.anyio
async def test_recording_speaker_rename_records_correction_event_and_alias(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    response = await client.put(
        "/api/v1/speakers/recordings/canon-rec",
        json={
            "diarization_label": "SPEAKER_00",
            "global_speaker_name": "Alex",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["local_name"] == "Alex"

    async with test_session_maker() as session:
        event_row = (
            await session.execute(
                text(
                    "SELECT event_type, scope, target_global_speaker_id FROM speaker_correction_events ORDER BY id DESC LIMIT 1"
                )
            )
        ).one()
        speaker_row = (
            await session.execute(
                text(
                    "SELECT local_name, last_speaker_correction_event_id FROM recording_speakers WHERE diarization_label = 'SPEAKER_00'"
                )
            )
        ).one()
        alias_rows = (
            await session.execute(
                text(
                    "SELECT alias_type, alias_value FROM recording_speaker_aliases ORDER BY alias_value"
                )
            )
        ).all()
        normalized_alias_rows = {(alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows}

        assert event_row[0].lower() == "rename"
        assert event_row[1].lower() == "speaker_everywhere_in_recording"
        assert event_row[2] is None
        assert speaker_row[0] == "Alex"
        assert speaker_row[1] is not None
        assert ("display_name", "Alex") in normalized_alias_rows


@pytest.mark.anyio
async def test_recording_speaker_rename_repairs_projection_when_projection_is_empty(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    initial_utterances = await client.get("/api/v1/transcripts/canon-rec/utterances")
    assert initial_utterances.status_code == 200
    assert initial_utterances.json()["utterances"]

    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE transcripts SET text = '', segments = '[]' WHERE recording_id = 1")
        )
        await session.commit()

    response = await client.put(
        "/api/v1/speakers/recordings/canon-rec",
        json={
            "diarization_label": "SPEAKER_00",
            "global_speaker_name": "Alex",
        },
    )

    assert response.status_code == 200

    utterances_response = await client.get("/api/v1/transcripts/canon-rec/utterances")
    assert utterances_response.status_code == 200
    utterance_speakers = [
        item["speaker"] for item in utterances_response.json()["utterances"]
    ]

    async with test_session_maker() as session:
        transcript = (
            await session.execute(select(Transcript).where(Transcript.recording_id == 1))
        ).scalar_one()

        assert transcript.segments
        assert [segment["speaker"] for segment in transcript.segments] == utterance_speakers


@pytest.mark.anyio
async def test_recording_speaker_merge_repairs_canonical_segments_when_projection_is_empty(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    replace_response = await client.put(
        "/api/v1/transcripts/canon-rec/segments",
        json={
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "speaker": "SPEAKER_00",
                    "text": "first",
                    "segment_source": "legacy",
                },
                {
                    "start": 0.5,
                    "end": 1.0,
                    "speaker": "SPEAKER_01",
                    "text": "second",
                    "segment_source": "legacy",
                },
            ]
        },
    )
    assert replace_response.status_code == 200

    utterances_response = await client.get("/api/v1/transcripts/canon-rec/utterances")
    assert utterances_response.status_code == 200

    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE transcripts SET text = '', segments = '[]' WHERE recording_id = 1")
        )
        await session.commit()

    merge_response = await client.post(
        "/api/v1/speakers/recordings/canon-rec/merge",
        json={
            "source_speaker_label": "SPEAKER_00",
            "target_speaker_label": "SPEAKER_01",
        },
    )

    assert merge_response.status_code == 200

    updated_utterances = await client.get("/api/v1/transcripts/canon-rec/utterances")
    assert updated_utterances.status_code == 200
    assert [item["speaker"] for item in updated_utterances.json()["utterances"]] == [
        "SPEAKER_01",
        "SPEAKER_01",
    ]

    async with test_session_maker() as session:
        transcript = (
            await session.execute(select(Transcript).where(Transcript.recording_id == 1))
        ).scalar_one()
        assert [segment["speaker"] for segment in transcript.segments] == [
            "SPEAKER_01",
            "SPEAKER_01",
        ]


@pytest.mark.anyio
async def test_recording_speaker_link_to_global_records_correction_event_and_alias(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO global_speakers (
                    id, created_at, updated_at, user_id, name, embedding,
                    is_voiceprint_locked, color, description
                ) VALUES (
                    7, :now, :now, 1, 'Jane Doe', NULL, 0, NULL, NULL
                )
                """
            ),
            {"now": "2026-05-19 00:00:00"},
        )
        await session.commit()

    response = await client.put(
        "/api/v1/speakers/recordings/canon-rec",
        json={
            "diarization_label": "SPEAKER_00",
            "global_speaker_name": "Jane Doe",
        },
    )

    assert response.status_code == 200

    async with test_session_maker() as session:
        event_row = (
            await session.execute(
                text(
                    "SELECT event_type, target_global_speaker_id FROM speaker_correction_events ORDER BY id DESC LIMIT 1"
                )
            )
        ).one()
        speaker_row = (
            await session.execute(
                text(
                    "SELECT global_speaker_id, local_name, last_speaker_correction_event_id FROM recording_speakers WHERE diarization_label = 'SPEAKER_00'"
                )
            )
        ).one()
        alias_rows = (
            await session.execute(
                text(
                    "SELECT alias_type, alias_value FROM recording_speaker_aliases ORDER BY alias_value"
                )
            )
        ).all()
        normalized_alias_rows = {(alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows}

        assert event_row[0].lower() == "link_global_speaker"
        assert event_row[1] == 7
        assert speaker_row[0] == 7
        assert speaker_row[1] is None
        assert speaker_row[2] is not None
        assert ("global_name", "Jane Doe") in normalized_alias_rows


@pytest.mark.anyio
async def test_global_speaker_merge_records_recording_speaker_relink_event(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO global_speakers (
                    id, created_at, updated_at, user_id, name, embedding,
                    is_voiceprint_locked, color, description
                ) VALUES
                    (7, :now, :now, 1, 'Alice', NULL, 0, NULL, NULL),
                    (8, :now, :now, 1, 'Jane Doe', NULL, 0, NULL, NULL)
                """
            ),
            {"now": "2026-05-19 00:00:00"},
        )
        await session.execute(
            text(
                "UPDATE recording_speakers SET global_speaker_id = 7, local_name = NULL, name = NULL WHERE diarization_label = 'SPEAKER_00'"
            )
        )
        await session.commit()

    response = await client.post(
        "/api/v1/speakers/merge",
        json={
            "source_speaker_id": 7,
            "target_speaker_id": 8,
        },
    )

    assert response.status_code == 200

    async with test_session_maker() as session:
        speaker_row = (
            await session.execute(
                text(
                    "SELECT global_speaker_id, local_name, last_speaker_correction_event_id FROM recording_speakers WHERE diarization_label = 'SPEAKER_00'"
                )
            )
        ).one()
        event_row = (
            await session.execute(
                text(
                    "SELECT event_type, target_global_speaker_id FROM speaker_correction_events ORDER BY id DESC LIMIT 1"
                )
            )
        ).one()
        source_exists = (
            await session.execute(
                text("SELECT COUNT(*) FROM global_speakers WHERE id = 7")
            )
        ).scalar_one()

        assert speaker_row[0] == 8
        assert speaker_row[1] is None
        assert speaker_row[2] is not None
        assert event_row[0].lower() == "link_global_speaker"
        assert event_row[1] == 8
        assert source_exists == 0


@pytest.mark.anyio
async def test_promote_speaker_records_correction_event_and_alias(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_processed_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text(
                "UPDATE recording_speakers SET local_name = 'Alex', name = NULL WHERE id = 1"
            )
        )
        await session.commit()

    response = await client.post(
        "/api/v1/speakers/recordings/canon-rec/speakers/SPEAKER_00/promote"
    )

    assert response.status_code == 200

    async with test_session_maker() as session:
        promoted_global_id = (
            await session.execute(
                text(
                    "SELECT global_speaker_id FROM recording_speakers WHERE diarization_label = 'SPEAKER_00'"
                )
            )
        ).scalar_one()
        event_row = (
            await session.execute(
                text(
                    "SELECT event_type, target_global_speaker_id FROM speaker_correction_events ORDER BY id DESC LIMIT 1"
                )
            )
        ).one()
        speaker_provenance = (
            await session.execute(
                text(
                    "SELECT last_speaker_correction_event_id FROM recording_speakers WHERE diarization_label = 'SPEAKER_00'"
                )
            )
        ).scalar_one()
        alias_rows = (
            await session.execute(
                text(
                    "SELECT alias_type, alias_value FROM recording_speaker_aliases ORDER BY alias_value"
                )
            )
        ).all()
        normalized_alias_rows = {(alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows}

        assert promoted_global_id is not None
        assert event_row[0].lower() == "promote_global_speaker"
        assert event_row[1] == promoted_global_id
        assert speaker_provenance is not None
        assert ("global_name", "Alex") in normalized_alias_rows


@pytest.mark.anyio
async def test_finalize_run_records_finalize_event_and_provenance(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import replace_utterances_from_segments

    await _seed_processed_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: replace_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "SPEAKER_00",
                        "text": "finalized",
                        "segment_source": "finalize",
                    }
                ],
                run_kind=ProcessingRunKind.FINALIZE,
                source="finalize",
                force=True,
                state_override=TranscriptUtteranceState.FINALIZED,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        finalize_row = (
            await session.execute(
                text(
                    "SELECT transcript_utterance_events.event_type, transcript_utterances.processing_run_id, transcript_utterances.last_utterance_event_id, transcript_utterances.state "
                    "FROM transcript_utterances JOIN transcript_utterance_events ON transcript_utterance_events.id = transcript_utterances.last_utterance_event_id "
                    "WHERE transcript_utterances.recording_id = 1 AND UPPER(transcript_utterances.state) = 'FINALIZED' ORDER BY transcript_utterances.id DESC LIMIT 1"
                )
            )
        ).one()
        assert finalize_row[0] == "finalize"
        assert finalize_row[1] is not None
        assert finalize_row[2] is not None
        assert finalize_row[3].lower() == "finalized"


@pytest.mark.anyio
async def test_finalize_utterances_from_segments_reuses_live_public_id_when_boundaries_match(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        finalize_utterances_from_segments,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "hello live",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: finalize_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "hello live",
                        "segment_source": "finalize",
                    }
                ],
                reused_live_asr=True,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        utterance_row = (
            await session.execute(
                text(
                    "SELECT public_id, state, source_kind, processing_run_id, revision FROM transcript_utterances WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED'"
                )
            )
        ).one()
        processing_run_row = (
            await session.execute(
                text(
                    "SELECT run_kind, reused_live_asr FROM processing_runs WHERE recording_id = 1 AND UPPER(run_kind) = 'FINALIZE' ORDER BY id DESC LIMIT 1"
                )
            )
        ).one()

        assert utterance_row[0] == "live-utt-1"
        assert utterance_row[1].lower() == "finalized"
        assert utterance_row[2] == "live"
        assert utterance_row[3] is not None
        assert utterance_row[4] == 2
        assert processing_run_row[0].lower() == "finalize"
        assert bool(processing_run_row[1]) is True


@pytest.mark.anyio
async def test_finalize_utterances_from_segments_preserves_manual_text_lock(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        finalize_utterances_from_segments,
        update_utterance_text,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "hello live",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.run_sync(
            lambda sync_session: update_utterance_text(
                sync_session,
                recording_id=1,
                utterance_public_id="live-utt-1",
                text="manual text",
                actor_user_id=1,
                source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: finalize_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "model text",
                        "segment_source": "finalize",
                    }
                ],
                reused_live_asr=True,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        utterance_row = (
            await session.execute(
                text(
                    "SELECT public_id, text, manual_text_locked, state FROM transcript_utterances WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED'"
                )
            )
        ).one()
        transcript_segments = (
            await session.execute(text("SELECT segments FROM transcripts WHERE recording_id = 1"))
        ).scalar_one()
        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments

        assert utterance_row[0] == "live-utt-1"
        assert utterance_row[1] == "manual text"
        assert bool(utterance_row[2]) is True
        assert utterance_row[3].lower() == "finalized"
        assert transcript_segments[0]["text"] == "manual text"
        assert transcript_segments[0]["text_manually_edited"] is True


@pytest.mark.anyio
async def test_finalize_utterances_from_segments_supersedes_boundary_changes(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        finalize_utterances_from_segments,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "hello there",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: finalize_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "start": 0.0,
                        "end": 0.5,
                        "speaker": "LIVE_01",
                        "text": "hello",
                        "segment_source": "finalize",
                    },
                    {
                        "start": 0.5,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "there",
                        "segment_source": "finalize",
                    },
                ],
                reused_live_asr=True,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        old_state = (
            await session.execute(
                text(
                    "SELECT state FROM transcript_utterances WHERE public_id = 'live-utt-1' ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar_one()
        active_rows = (
            await session.execute(
                text(
                    "SELECT public_id, state FROM transcript_utterances WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED' ORDER BY sort_key"
                )
            )
        ).all()
        split_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterance_events WHERE recording_id = 1 AND event_type = 'split'"
                )
            )
        ).scalar_one()

        assert old_state.lower() == "superseded"
        assert len(active_rows) == 2
        assert all(public_id != "live-utt-1" for public_id, _state in active_rows)
        assert all(state.lower() == "finalized" for _public_id, state in active_rows)
        assert split_count == 2


@pytest.mark.anyio
async def test_finalize_utterances_from_segments_inherits_manual_speaker_lock_when_boundaries_shift(
    test_session_maker: sessionmaker,
) -> None:
    from backend.models.pipeline import SpeakerCorrectionScope
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        finalize_utterances_from_segments,
        update_utterance_speaker,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "opening question",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.run_sync(
            lambda sync_session: update_utterance_speaker(
                sync_session,
                recording_id=1,
                utterance_public_id="live-utt-1",
                new_speaker_name="Dwarkesh Patel",
                scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
                actor_user_id=1,
                source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: finalize_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "start": 0.0,
                        "end": 1.2,
                        "speaker": "UNKNOWN",
                        "text": "opening question with final boundary",
                        "segment_source": "finalize",
                    }
                ],
                reused_live_asr=True,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        old_state = (
            await session.execute(
                text("SELECT state FROM transcript_utterances WHERE public_id = 'live-utt-1'")
            )
        ).scalar_one()
        active_row = (
            await session.execute(
                text(
                    "SELECT u.public_id, u.speaker_label, u.manual_speaker_locked, "
                    "COALESCE(s.local_name, s.name), u.confidence_payload "
                    "FROM transcript_utterances u "
                    "JOIN recording_speakers s ON s.id = u.recording_speaker_id "
                    "WHERE u.recording_id = 1 AND UPPER(u.state) != 'SUPERSEDED'"
                )
            )
        ).one()
        transcript_segments = (
            await session.execute(text("SELECT segments FROM transcripts WHERE recording_id = 1"))
        ).scalar_one()
        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments
        confidence_payload = json.loads(active_row[4]) if isinstance(active_row[4], str) else active_row[4]

        assert old_state.lower() == "superseded"
        assert active_row[0] != "live-utt-1"
        assert active_row[1].startswith("MANUAL_")
        assert bool(active_row[2]) is True
        assert active_row[3] == "Dwarkesh Patel"
        assert confidence_payload["inherited_manual_speaker"]["source_public_ids"] == [
            "live-utt-1"
        ]
        assert transcript_segments[0]["speaker"] == active_row[1]
        assert transcript_segments[0]["speaker_manually_edited"] is True


@pytest.mark.anyio
async def test_diarization_replay_preserves_inherited_manual_speaker_lock(
    test_session_maker: sessionmaker,
) -> None:
    from backend.models.pipeline import SpeakerCorrectionScope
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        finalize_utterances_from_segments,
        reconcile_diarization_window_result,
        update_utterance_speaker,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "manual speaker survives replay",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.run_sync(
            lambda sync_session: update_utterance_speaker(
                sync_session,
                recording_id=1,
                utterance_public_id="live-utt-1",
                new_speaker_name="Dwarkesh Patel",
                scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
                actor_user_id=1,
                source="test",
            )
        )
        await session.run_sync(
            lambda sync_session: finalize_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "start": 0.0,
                        "end": 1.2,
                        "speaker": "UNKNOWN",
                        "text": "manual speaker survives replay",
                        "segment_source": "finalize",
                    }
                ],
                reused_live_asr=True,
                trigger_source="test",
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    99, :now, :now, 'speaker-public-99', 1,
                    NULL, 'LIVE_99', NULL, NULL,
                    NULL, NULL, 'active', 'automated',
                    0, 1200, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    99, :now, :now, 'window-public-99', 1,
                    NULL, 1, 0, 1200, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-1', 'completed',
                    :raw_payload
                )
                """
            ),
            {
                "now": "2026-05-20 00:00:00",
                "raw_payload": json.dumps(
                    {
                        "speaker_metadata": {
                            "SPEAKER_00": {
                                "best_recording_speaker_id": 99,
                                "best_recording_speaker_score": 0.99,
                            }
                        }
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    99, :now, :now, 99,
                    'SPEAKER_00', 0, 1200, NULL,
                    NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=99,
                source="test",
            )
        )
        await session.commit()

        assert summary["matched_turn_count"] == 1
        assert summary["updated_utterance_count"] == 0
        assert summary["preserved_manual_lock_count"] == 1

    async with test_session_maker() as session:
        active_row = (
            await session.execute(
                text(
                    "SELECT COALESCE(s.local_name, s.name), u.manual_speaker_locked, "
                    "u.last_diarization_window_result_id "
                    "FROM transcript_utterances u "
                    "JOIN recording_speakers s ON s.id = u.recording_speaker_id "
                    "WHERE u.recording_id = 1 AND UPPER(u.state) != 'SUPERSEDED'"
                )
            )
        ).one()

        assert active_row[0] == "Dwarkesh Patel"
        assert bool(active_row[1]) is True
        assert active_row[2] == 99


@pytest.mark.anyio
async def test_recording_speaker_public_read_filter_hides_zero_utterance_speakers_after_rename(
    test_session_maker: sessionmaker,
) -> None:
    from backend.models.pipeline import SpeakerCorrectionScope
    from backend.models.speaker import RecordingSpeaker
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        filter_recording_speakers_for_public_read,
        update_utterance_speaker,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "rename me",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.run_sync(
            lambda sync_session: update_utterance_speaker(
                sync_session,
                recording_id=1,
                utterance_public_id="live-utt-1",
                new_speaker_name="Dana",
                scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
                actor_user_id=1,
                source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT diarization_label, COALESCE(local_name, name), speaker_status "
                    "FROM recording_speakers WHERE recording_id = 1 ORDER BY id"
                )
            )
        ).all()
        public_speakers = await session.run_sync(
            lambda sync_session: filter_recording_speakers_for_public_read(
                sync_session,
                1,
                list(
                    sync_session.execute(
                        select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
                    ).scalars().all()
                ),
            )
        )

        assert any(
            label == "LIVE_01" and status == "inactive"
            for label, _name, status in rows
        )
        assert [speaker.local_name or speaker.name for speaker in public_speakers] == ["Dana"]


@pytest.mark.anyio
async def test_append_utterances_from_segments_creates_live_provisional_run_and_projection(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import append_utterances_from_segments

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        created = await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.25,
                        "speaker": "LIVE_01",
                        "text": "hello live",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
                config_hash="cfg-live-1",
                transcription_backend="whisper",
                model_metadata={
                    "model_name": "base",
                    "chunk_start_sequence": 1,
                    "chunk_end_sequence": 1,
                },
            )
        )
        await session.commit()

    assert len(created) == 1
    assert created[0].public_id == "live-utt-1"

    async with test_session_maker() as session:
        utterance_row = (
            await session.execute(
                text(
                    "SELECT public_id, state, source_kind, processing_run_id, text, speaker_label "
                    "FROM transcript_utterances WHERE recording_id = 1"
                )
            )
        ).one()
        processing_run_row = (
            await session.execute(
                text(
                    "SELECT run_kind, config_hash, transcription_backend, model_metadata "
                    "FROM processing_runs WHERE recording_id = 1"
                )
            )
        ).one()
        transcript_row = (
            await session.execute(
                text(
                    "SELECT text, segments FROM transcripts WHERE recording_id = 1"
                )
            )
        ).one()

        assert utterance_row[0] == "live-utt-1"
        assert utterance_row[1].lower() == "provisional"
        assert utterance_row[2] == "live"
        assert utterance_row[3] is not None
        assert utterance_row[4] == "hello live"
        assert utterance_row[5] == "LIVE_01"

        assert processing_run_row[0].lower() == "live"
        assert processing_run_row[1] == "cfg-live-1"
        assert processing_run_row[2] == "whisper"
        assert processing_run_row[3] is not None
        assert "base" in str(processing_run_row[3])

        assert transcript_row[0] == "hello live"
        transcript_segments = json.loads(transcript_row[1]) if isinstance(transcript_row[1], str) else transcript_row[1]
        assert transcript_segments[0]["id"] == "live-utt-1"
        assert transcript_segments[0]["segment_source"] == "live"
        assert transcript_segments[0]["provisional"] is True


@pytest.mark.anyio
async def test_append_utterances_from_segments_live_retry_is_idempotent(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import append_utterances_from_segments

    await _seed_uploading_recording(test_session_maker)

    payload = [
        {
            "id": "live-utt-1",
            "start": 0.0,
            "end": 1.25,
            "speaker": "LIVE_01",
            "text": "hello live",
            "provisional": True,
            "segment_source": "live",
        }
    ]

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=payload,
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
                config_hash="cfg-live-1",
                transcription_backend="whisper",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=payload,
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
                config_hash="cfg-live-1",
                transcription_backend="whisper",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        processing_run_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM processing_runs WHERE recording_id = 1 AND UPPER(run_kind) = 'LIVE'"
                )
            )
        ).scalar_one()
        utterance_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1"
                )
            )
        ).scalar_one()
        transcript_segments = (
            await session.execute(
                text("SELECT segments FROM transcripts WHERE recording_id = 1")
            )
        ).scalar_one()
        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments

        assert processing_run_count == 1
        assert utterance_count == 1


@pytest.mark.anyio
async def test_persist_diarization_window_result_records_model_and_speaker_metadata(
    test_session_maker: sessionmaker,
) -> None:
    from backend.models.pipeline import RecordingAudioWindowManifest
    from backend.utils.rolling_diarization import persist_diarization_window_result

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recording_audio_window_manifests (
                    id, created_at, updated_at, public_id, recording_id,
                    window_index, source_kind, target_window_ms, hop_ms,
                    window_start_ms, window_end_ms, chunk_start_sequence,
                    chunk_end_sequence, status, is_partial, is_sealed,
                    processing_run_id, last_error
                ) VALUES (
                    11, :now, :now, 'manifest-public-11', 1,
                    0, 'companion', 20000, 5000,
                    0, 20000, 1,
                    10, 'live_processed', 0, 0,
                    NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    class _Segment:
        def __init__(self, start: float, end: float):
            self.start = start
            self.end = end

    class _Annotation:
        def itertracks(self, yield_label=False):
            assert yield_label is True
            yield _Segment(0.0, 0.8), "A", "SPEAKER_00"
            yield _Segment(0.8, 1.6), "B", "SPEAKER_01"

    async with test_session_maker() as session:
        manifest_row = await session.get(RecordingAudioWindowManifest, 11)
        await session.run_sync(
            lambda sync_session: persist_diarization_window_result(
                sync_session,
                recording_id=1,
                manifest_row=manifest_row,
                processing_run_id=None,
                diarization_result=_Annotation(),
                config_hash="rolling-cfg-1",
                device="cpu",
                model_name="pyannote/speaker-diarization-community-1",
                speaker_metadata_by_key={
                    "SPEAKER_00": {
                        "best_recording_speaker_id": 7,
                        "best_recording_speaker_score": 0.91,
                    }
                },
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        result_row = (
            await session.execute(
                text(
                    "SELECT model_name, model_version, device, config_hash, status, raw_payload "
                    "FROM diarization_window_results WHERE recording_id = 1 AND window_index = 0"
                )
            )
        ).one()
        turn_rows = (
            await session.execute(
                text(
                    "SELECT local_speaker_key, start_ms, end_ms, metadata_payload "
                    "FROM diarization_window_turns ORDER BY id"
                )
            )
        ).all()

        raw_payload = json.loads(result_row[5]) if isinstance(result_row[5], str) else result_row[5]
        first_turn_payload = json.loads(turn_rows[0][3]) if isinstance(turn_rows[0][3], str) else turn_rows[0][3]

        assert result_row[0] == "pyannote/speaker-diarization-community-1"
        assert result_row[1] == "community-1"
        assert result_row[2] == "cpu"
        assert result_row[3] == "rolling-cfg-1"
        assert result_row[4] == "completed"
        assert raw_payload["speaker_metadata"]["SPEAKER_00"]["best_recording_speaker_id"] == 7
        assert len(turn_rows) == 2
        assert turn_rows[0][0] == "SPEAKER_00"
        assert first_turn_payload["track"] == "A"


@pytest.mark.anyio
async def test_reconcile_diarization_window_result_revises_earlier_live_speaker_assignment(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        reconcile_diarization_window_result,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "earlier speaker guess",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    2, :now, :now, 'speaker-public-2', 1,
                    NULL, 'LIVE_02', NULL, NULL,
                    NULL, NULL, 'active', 'automated',
                    0, 1000, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    30, :now, :now, 'window-public-30', 1,
                    NULL, -1, 0,
                    15000, 1, 8,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-0', 'completed',
                    '{}'
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    40, :now, :now, 30,
                    'SPEAKER_99', 0, 1000, 0.92,
                    2, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    31, :now, :now, 'window-public-31', 1,
                    NULL, 0, 0,
                    20000, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-1', 'completed',
                    :raw_payload
                )
                """
            ),
            {
                "now": "2026-05-20 00:00:00",
                "raw_payload": json.dumps(
                    {
                        "speaker_metadata": {
                            "SPEAKER_00": {
                                "best_recording_speaker_id": 2,
                                "best_recording_speaker_score": 0.91,
                            }
                        }
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    41, :now, :now, 31,
                    'SPEAKER_00', 0, 1000, NULL,
                    NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=31,
                source="test",
            )
        )
        await session.commit()

        assert summary["matched_turn_count"] == 1
        assert summary["updated_utterance_count"] == 1
        assert summary["preserved_manual_lock_count"] == 0

    async with test_session_maker() as session:
        utterance_row = (
            await session.execute(
                text(
                    "SELECT speaker_label, speaker_confidence, last_diarization_window_result_id, text "
                    "FROM transcript_utterances WHERE public_id = 'live-utt-1'"
                )
            )
        ).one()
        transcript_segments = (
            await session.execute(text("SELECT segments FROM transcripts WHERE recording_id = 1"))
        ).scalar_one()
        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments

        assert utterance_row[0] == "LIVE_02"
        assert utterance_row[1] >= 0.55
        assert utterance_row[2] == 31
        assert utterance_row[3] == "earlier speaker guess"
        assert transcript_segments[0]["speaker"] == "LIVE_02"
        assert transcript_segments[0]["text"] == "earlier speaker guess"


@pytest.mark.anyio
async def test_reconcile_diarization_window_result_merges_adjacent_live_utterances_for_continuous_same_speaker_turn(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        reconcile_diarization_window_result,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 0.5,
                        "speaker": "LIVE_01",
                        "text": "hello",
                        "provisional": True,
                        "segment_source": "live",
                    },
                    {
                        "id": "live-utt-2",
                        "start": 0.5,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "there",
                        "provisional": True,
                        "segment_source": "live",
                    },
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    2, :now, :now, 'speaker-public-2', 1,
                    NULL, 'LIVE_02', NULL, NULL,
                    NULL, NULL, 'active', 'automated',
                    0, 1000, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    40, :now, :now, 'window-public-40', 1,
                    NULL, 0, 0,
                    20000, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-merge', 'completed',
                    :raw_payload
                )
                """
            ),
            {
                "now": "2026-05-20 00:00:00",
                "raw_payload": json.dumps(
                    {
                        "speaker_metadata": {
                            "SPEAKER_00": {
                                "best_recording_speaker_id": 2,
                                "best_recording_speaker_score": 1.0,
                            }
                        }
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    51, :now, :now, 40,
                    'SPEAKER_00', 0, 1000, NULL,
                    NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=40,
                source="test",
            )
        )
        await session.commit()

        assert summary["matched_turn_count"] == 1
        assert summary["updated_utterance_count"] == 1
        assert summary["preserved_manual_lock_count"] == 0

    async with test_session_maker() as session:
        utterance_rows = (
            await session.execute(
                text(
                    "SELECT public_id, state, speaker_label, text FROM transcript_utterances WHERE recording_id = 1 ORDER BY id"
                )
            )
        ).all()
        transcript_segments = (
            await session.execute(text("SELECT segments FROM transcripts WHERE recording_id = 1"))
        ).scalar_one()
        merge_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterance_events WHERE recording_id = 1 AND event_type = 'merge'"
                )
            )
        ).scalar_one()

        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments
        active_rows = [row for row in utterance_rows if str(row[1]).lower() != TranscriptUtteranceState.SUPERSEDED.value]

        assert len(active_rows) == 1
        assert active_rows[0][2] == "LIVE_02"
        assert active_rows[0][3] == "hello there"
        assert len(transcript_segments) == 1
        assert transcript_segments[0]["speaker"] == "LIVE_02"
        assert transcript_segments[0]["text"] == "hello there"
        assert merge_count == 1


@pytest.mark.anyio
async def test_reconcile_diarization_window_result_splits_live_utterance_from_word_timestamps(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        reconcile_diarization_window_result,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.2,
                        "speaker": "LIVE_01",
                        "text": "hello there general kenobi",
                        "provisional": True,
                        "segment_source": "live",
                        "confidence_payload": {
                            "asr_segments": [
                                {
                                    "start_ms": 0,
                                    "end_ms": 1200,
                                    "text": "hello there general kenobi",
                                    "words": [
                                        {"start_ms": 0, "end_ms": 300, "word": "hello"},
                                        {"start_ms": 300, "end_ms": 600, "word": "there"},
                                        {"start_ms": 600, "end_ms": 900, "word": "general"},
                                        {"start_ms": 900, "end_ms": 1200, "word": "kenobi"},
                                    ],
                                }
                            ],
                            "asr_word_timestamps_available": True,
                        },
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        live_speaker_id = (
            await session.execute(
                text(
                    "SELECT id FROM recording_speakers WHERE recording_id = 1 AND diarization_label = 'LIVE_01'"
                )
            )
        ).scalar_one()
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    2, :now, :now, 'speaker-public-2', 1,
                    NULL, 'LIVE_02', NULL, NULL,
                    NULL, NULL, 'active', 'automated',
                    600, 1200, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    39, :now, :now, 'window-public-39', 1,
                    NULL, 0, 0,
                    20000, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-split', 'completed',
                    :raw_payload
                )
                """
            ),
            {
                "now": "2026-05-20 00:00:00",
                "raw_payload": json.dumps(
                    {
                        "speaker_metadata": {
                            "SPEAKER_00": {
                                "best_recording_speaker_id": int(live_speaker_id),
                                "best_recording_speaker_score": 0.97,
                            },
                            "SPEAKER_01": {
                                "best_recording_speaker_id": 2,
                                "best_recording_speaker_score": 0.96,
                            },
                        }
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES
                    (49, :now, :now, 39, 'SPEAKER_00', 0, 600, NULL, NULL, NULL),
                    (50, :now, :now, 39, 'SPEAKER_01', 600, 1200, NULL, NULL, NULL)
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=39,
                source="test",
            )
        )
        await session.commit()

        assert summary["matched_turn_count"] == 2
        assert summary["updated_utterance_count"] == 2
        assert summary["preserved_manual_lock_count"] == 0

    async with test_session_maker() as session:
        transcript_segments = (
            await session.execute(text("SELECT segments FROM transcripts WHERE recording_id = 1"))
        ).scalar_one()
        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments
        utterance_rows = (
            await session.execute(
                text(
                    "SELECT public_id, state, speaker_label, text, confidence_payload "
                    "FROM transcript_utterances WHERE recording_id = 1 ORDER BY id"
                )
            )
        ).all()
        split_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterance_events "
                    "WHERE recording_id = 1 AND event_type = 'split'"
                )
            )
        ).scalar_one()

        active_rows = [row for row in utterance_rows if str(row[1]).lower() != TranscriptUtteranceState.SUPERSEDED.value]
        original_row = next(row for row in utterance_rows if row[0] == "live-utt-1")
        first_payload = json.loads(active_rows[0][4]) if isinstance(active_rows[0][4], str) else active_rows[0][4]
        second_payload = json.loads(active_rows[1][4]) if isinstance(active_rows[1][4], str) else active_rows[1][4]

        assert str(original_row[1]).lower() == TranscriptUtteranceState.SUPERSEDED.value
        assert [segment["speaker"] for segment in transcript_segments] == ["LIVE_01", "LIVE_02"]
        assert [segment["text"] for segment in transcript_segments] == ["hello there", "general kenobi"]
        assert [row[2] for row in active_rows] == ["LIVE_01", "LIVE_02"]
        assert [row[3] for row in active_rows] == ["hello there", "general kenobi"]
        assert first_payload["rolling_diarization"]["split_from_public_id"] == "live-utt-1"
        assert second_payload["rolling_diarization"]["split_from_public_id"] == "live-utt-1"
        assert first_payload["asr_segments"][0]["words"][0]["word"] == "hello"
        assert second_payload["asr_segments"][0]["words"][0]["word"] == "general"
        assert split_count == 2


@pytest.mark.anyio
async def test_reconcile_diarization_window_result_projects_overlapping_speakers_without_hiding_primary_utterance(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        reconcile_diarization_window_result,
        serialize_canonical_utterances,
        update_utterance_text,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "primary words stay visible",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.run_sync(
            lambda sync_session: update_utterance_text(
                sync_session,
                recording_id=1,
                utterance_public_id="live-utt-1",
                text="manual text survives overlap fallback",
                actor_user_id=1,
                source="test",
            )
        )
        live_speaker_id = (
            await session.execute(
                text(
                    "SELECT id FROM recording_speakers WHERE recording_id = 1 AND diarization_label = 'LIVE_01'"
                )
            )
        ).scalar_one()
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    2, :now, :now, 'speaker-public-2', 1,
                    NULL, 'LIVE_02', NULL, NULL,
                    NULL, NULL, 'active', 'automated',
                    400, 900, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    60, :now, :now, 'window-public-60', 1,
                    NULL, 0, 0,
                    20000, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-overlap', 'completed',
                    :raw_payload
                )
                """
            ),
            {
                "now": "2026-05-20 00:00:00",
                "raw_payload": json.dumps(
                    {
                        "speaker_metadata": {
                            "SPEAKER_00": {
                                "best_recording_speaker_id": int(live_speaker_id),
                                "best_recording_speaker_score": 1.0,
                            },
                            "SPEAKER_01": {
                                "best_recording_speaker_id": 2,
                                "best_recording_speaker_score": 1.0,
                            },
                        }
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES
                    (61, :now, :now, 60, 'SPEAKER_00', 0, 1000, NULL, NULL, NULL),
                    (62, :now, :now, 60, 'SPEAKER_01', 400, 900, NULL, NULL, NULL)
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=60,
                source="test",
            )
        )
        await session.commit()

        assert summary["matched_turn_count"] == 2
        assert summary["updated_utterance_count"] == 0
        assert summary["preserved_manual_lock_count"] == 0

    async with test_session_maker() as session:
        serialized_segments = await session.run_sync(
            lambda sync_session: serialize_canonical_utterances(sync_session, 1)
        )
        utterance_row = (
            await session.execute(
                text(
                    "SELECT speaker_label, text, manual_text_locked, confidence_payload "
                    "FROM transcript_utterances WHERE public_id = 'live-utt-1'"
                )
            )
        ).one()
        transcript_segments = (
            await session.execute(text("SELECT segments FROM transcripts WHERE recording_id = 1"))
        ).scalar_one()
        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments
        confidence_payload = json.loads(utterance_row[3]) if isinstance(utterance_row[3], str) else utterance_row[3]

        assert utterance_row[0] == "LIVE_01"
        assert utterance_row[1] == "manual text survives overlap fallback"
        assert bool(utterance_row[2]) is True
        assert len(transcript_segments) == 1
        assert transcript_segments[0]["speaker"] == "LIVE_01"
        assert transcript_segments[0]["text"] == "manual text survives overlap fallback"
        assert transcript_segments[0]["overlapping_speakers"] == ["LIVE_02"]
        assert serialized_segments[0]["overlapping_speakers"] == ["LIVE_02"]
        assert confidence_payload["rolling_diarization"]["overlapping_recording_speaker_ids"] == [2]
        assert confidence_payload["rolling_diarization"]["overlapping_speakers"] == ["LIVE_02"]


@pytest.mark.anyio
async def test_record_recording_speaker_corrections_replays_completed_windows_after_global_link(
    test_session_maker: sessionmaker,
) -> None:
    from backend.models.pipeline import SpeakerCorrectionEventType, SpeakerCorrectionScope
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        reconcile_diarization_window_result,
        record_recording_speaker_corrections,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "UNKNOWN",
                        "text": "identity resolves later",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    2, :now, :now, 'speaker-public-2', 1,
                    NULL, 'LIVE_02', NULL, NULL,
                    NULL, NULL, 'active', 'automated',
                    0, 1000, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO global_speakers (
                    id, created_at, updated_at, user_id, name, embedding,
                    is_voiceprint_locked, color, title, company, email,
                    phone_number, notes, description
                ) VALUES (
                    7, :now, :now, 1, 'Dana', NULL,
                    0, NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    41, :now, :now, 'window-public-41', 1,
                    NULL, 0, 0,
                    20000, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-global', 'completed',
                    :raw_payload
                )
                """
            ),
            {
                "now": "2026-05-20 00:00:00",
                "raw_payload": json.dumps(
                    {
                        "speaker_metadata": {
                            "SPEAKER_00": {
                                "best_global_speaker_id": 7,
                                    "best_global_speaker_score": 1.0,
                            }
                        }
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    52, :now, :now, 41,
                    'SPEAKER_00', 0, 1000, NULL,
                    NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=41,
                source="test",
            )
        )
        await session.commit()

        assert summary["matched_turn_count"] == 0
        assert summary["updated_utterance_count"] == 0

    async with test_session_maker() as session:
        await session.execute(
            text(
                "UPDATE recording_speakers SET global_speaker_id = 7 WHERE id = 2"
            )
        )
        await session.run_sync(
            lambda sync_session: record_recording_speaker_corrections(
                sync_session,
                recording_id=1,
                target_recording_speaker_ids=[2],
                actor_user_id=1,
                event_type=SpeakerCorrectionEventType.LINK_GLOBAL_SPEAKER,
                scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
                target_global_speaker_id=7,
                payload={"matched_global_speaker": True},
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        utterance_row = (
            await session.execute(
                text(
                    "SELECT speaker_label, last_diarization_window_result_id, confidence_payload "
                    "FROM transcript_utterances WHERE public_id = 'live-utt-1'"
                )
            )
        ).one()
        replay_event_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterance_events "
                    "WHERE recording_id = 1 AND source = 'speaker_identity_replay'"
                )
            )
        ).scalar_one()
        confidence_payload = json.loads(utterance_row[2]) if isinstance(utterance_row[2], str) else utterance_row[2]

        assert utterance_row[0] == "LIVE_02"
        assert utterance_row[1] == 41
        assert confidence_payload["rolling_diarization"]["matched_recording_speaker_id"] == 2
        assert replay_event_count == 1


@pytest.mark.anyio
async def test_reconcile_diarization_window_result_preserves_manual_speaker_lock(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        reconcile_diarization_window_result,
        update_utterance_speaker,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "manual speaker stays",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.run_sync(
            lambda sync_session: update_utterance_speaker(
                sync_session,
                recording_id=1,
                utterance_public_id="live-utt-1",
                new_speaker_name="LIVE_01",
                diarization_label="LIVE_01",
                source="test",
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    2, :now, :now, 'speaker-public-2', 1,
                    NULL, 'LIVE_02', NULL, NULL,
                    NULL, NULL, 'active', 'automated',
                    0, 1000, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    32, :now, :now, 'window-public-32', 1,
                    NULL, 1, 0,
                    20000, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-1', 'completed',
                    :raw_payload
                )
                """
            ),
            {
                "now": "2026-05-20 00:00:00",
                "raw_payload": json.dumps(
                    {
                        "speaker_metadata": {
                            "SPEAKER_00": {
                                "best_recording_speaker_id": 2,
                                "best_recording_speaker_score": 0.93,
                            }
                        }
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    42, :now, :now, 32,
                    'SPEAKER_00', 0, 1000, NULL,
                    NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=32,
                source="test",
            )
        )
        await session.commit()

        assert summary["matched_turn_count"] == 1
        assert summary["updated_utterance_count"] == 0
        assert summary["preserved_manual_lock_count"] == 1

    async with test_session_maker() as session:
        utterance_row = (
            await session.execute(
                text(
                    "SELECT speaker_label, manual_speaker_locked, last_diarization_window_result_id "
                    "FROM transcript_utterances WHERE public_id = 'live-utt-1'"
                )
            )
        ).one()
        transcript_segments = (
            await session.execute(text("SELECT segments FROM transcripts WHERE recording_id = 1"))
        ).scalar_one()
        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments

        assert utterance_row[0] == "LIVE_01"
        assert bool(utterance_row[1]) is True
        assert utterance_row[2] == 32
        assert transcript_segments[0]["speaker"] == "LIVE_01"
        assert len(transcript_segments) == 1
        assert transcript_segments[0]["id"] == "live-utt-1"


@pytest.mark.anyio
async def test_phase4_exit_gate_rolling_diarization_updates_earlier_live_assignments_without_losing_manual_corrections(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        reconcile_diarization_window_result,
        update_utterance_speaker,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "earlier guess changes",
                        "provisional": True,
                        "segment_source": "live",
                    },
                    {
                        "id": "live-utt-2",
                        "start": 1.0,
                        "end": 2.0,
                        "speaker": "LIVE_01",
                        "text": "manual speaker stays",
                        "provisional": True,
                        "segment_source": "live",
                    },
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.run_sync(
            lambda sync_session: update_utterance_speaker(
                sync_session,
                recording_id=1,
                utterance_public_id="live-utt-2",
                new_speaker_name="LIVE_01",
                diarization_label="LIVE_01",
                source="test",
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    2, :now, :now, 'speaker-public-2', 1,
                    NULL, 'LIVE_02', NULL, NULL,
                    NULL, NULL, 'active', 'automated',
                    0, 2000, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    42, :now, :now, 'window-public-42', 1,
                    NULL, 0, 0,
                    20000, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-exit-gate', 'completed',
                    :raw_payload
                )
                """
            ),
            {
                "now": "2026-05-20 00:00:00",
                "raw_payload": json.dumps(
                    {
                        "speaker_metadata": {
                            "SPEAKER_00": {
                                "best_recording_speaker_id": 2,
                                "best_recording_speaker_score": 1.0,
                            },
                            "SPEAKER_01": {
                                "best_recording_speaker_id": 2,
                                "best_recording_speaker_score": 1.0,
                            },
                        }
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES
                    (53, :now, :now, 42, 'SPEAKER_00', 0, 1000, NULL, NULL, NULL),
                    (54, :now, :now, 42, 'SPEAKER_01', 1000, 2000, NULL, NULL, NULL)
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=42,
                source="test",
            )
        )
        await session.commit()

    assert summary["matched_turn_count"] == 2
    assert summary["updated_utterance_count"] == 1
    assert summary["preserved_manual_lock_count"] == 1

    async with test_session_maker() as session:
        utterance_rows = (
            await session.execute(
                text(
                    "SELECT public_id, speaker_label, manual_speaker_locked "
                    "FROM transcript_utterances WHERE recording_id = 1 ORDER BY sort_key"
                )
            )
        ).all()
        transcript_segments = (
            await session.execute(text("SELECT segments FROM transcripts WHERE recording_id = 1"))
        ).scalar_one()
        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments

        assert utterance_rows[0][0] == "live-utt-1"
        assert utterance_rows[0][1] == "LIVE_02"
        assert bool(utterance_rows[0][2]) is False
        assert utterance_rows[1][0] == "live-utt-2"
        assert utterance_rows[1][1] == "LIVE_01"
        assert bool(utterance_rows[1][2]) is True
        assert [segment["speaker"] for segment in transcript_segments] == ["LIVE_02", "LIVE_01"]


@pytest.mark.anyio
async def test_reconcile_diarization_window_result_marks_live_speaker_stable_after_repeated_windows(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        reconcile_diarization_window_result,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "stable speaker now",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        speaker_id = (
            await session.execute(
                text(
                    "SELECT id FROM recording_speakers WHERE recording_id = 1 AND diarization_label = 'LIVE_01'"
                )
            )
        ).scalar_one()
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    33, :now, :now, 'window-public-33', 1,
                    NULL, -1, 0,
                    15000, 1, 8,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-0', 'completed',
                    '{}'
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    43, :now, :now, 33,
                    'SPEAKER_00', 0, 1000, 0.92,
                    :speaker_id, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00", "speaker_id": speaker_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    34, :now, :now, 'window-public-34', 1,
                    NULL, 0, 0,
                    20000, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-1', 'completed',
                    :raw_payload
                )
                """
            ),
            {
                "now": "2026-05-20 00:00:00",
                "raw_payload": json.dumps(
                    {
                        "speaker_metadata": {
                            "SPEAKER_00": {
                                "best_recording_speaker_id": int(speaker_id),
                                "best_recording_speaker_score": 0.93,
                            }
                        }
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    44, :now, :now, 34,
                    'SPEAKER_00', 0, 1000, NULL,
                    NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=34,
                source="test",
            )
        )
        await session.commit()

        assert summary["matched_turn_count"] == 1
        assert summary["updated_utterance_count"] == 0
        assert summary["preserved_manual_lock_count"] == 0

    async with test_session_maker() as session:
        utterance_row = (
            await session.execute(
                text(
                    "SELECT speaker_label, confidence_payload FROM transcript_utterances WHERE public_id = 'live-utt-1'"
                )
            )
        ).one()
        transcript_segments = (
            await session.execute(text("SELECT segments FROM transcripts WHERE recording_id = 1"))
        ).scalar_one()
        confidence_payload = json.loads(utterance_row[1]) if isinstance(utterance_row[1], str) else utterance_row[1]
        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments

        assert utterance_row[0] == "LIVE_01"
        assert confidence_payload["rolling_diarization"]["speaker_state"] == "stable"
        assert confidence_payload["rolling_diarization"]["supporting_window_count"] == 2
        assert transcript_segments[0]["speaker"] == "LIVE_01"
        assert transcript_segments[0]["speaker_state"] == "stable"


@pytest.mark.anyio
async def test_reconcile_diarization_window_result_keeps_stable_live_speaker_without_repeated_conflicting_windows(
    test_session_maker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models.speaker import RecordingSpeaker
    from backend.utils import canonical_pipeline
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        reconcile_diarization_window_result,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "live-utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_01",
                        "text": "stable speaker resists flip",
                        "provisional": True,
                        "segment_source": "live",
                    }
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        stable_speaker_id = (
            await session.execute(
                text(
                    "SELECT id FROM recording_speakers WHERE recording_id = 1 AND diarization_label = 'LIVE_01'"
                )
            )
        ).scalar_one()
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    2, :now, :now, 'speaker-public-2', 1,
                    NULL, 'LIVE_02', NULL, NULL,
                    NULL, NULL, 'active', 'automated',
                    0, 1000, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    36, :now, :now, 'window-public-36', 1,
                    NULL, -1, 0,
                    15000, 1, 8,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-0', 'completed',
                    '{}'
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    46, :now, :now, 36,
                    'SPEAKER_00', 0, 1000, 0.92,
                    :speaker_id, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00", "speaker_id": stable_speaker_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    37, :now, :now, 'window-public-37', 1,
                    NULL, 0, 0,
                    20000, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-1', 'completed',
                    :raw_payload
                )
                """
            ),
            {
                "now": "2026-05-20 00:00:00",
                "raw_payload": json.dumps(
                    {
                        "speaker_metadata": {
                            "SPEAKER_00": {
                                "best_recording_speaker_id": int(stable_speaker_id),
                                "best_recording_speaker_score": 0.93,
                            }
                        }
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    47, :now, :now, 37,
                    'SPEAKER_00', 0, 1000, NULL,
                    NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_results (
                    id, created_at, updated_at, public_id, recording_id,
                    processing_run_id, window_index, window_start_ms,
                    window_end_ms, chunk_start_sequence, chunk_end_sequence,
                    model_name, model_version, device, config_hash, status,
                    raw_payload
                ) VALUES (
                    38, :now, :now, 'window-public-38', 1,
                    NULL, 1, 0,
                    20000, 1, 10,
                    'pyannote/speaker-diarization-community-1', 'community-1', 'cpu', 'rolling-cfg-2', 'completed',
                    '{}'
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.execute(
            text(
                """
                INSERT INTO diarization_window_turns (
                    id, created_at, updated_at, window_result_id,
                    local_speaker_key, start_ms, end_ms, confidence,
                    matched_recording_speaker_id, metadata_payload
                ) VALUES (
                    48, :now, :now, 38,
                    'SPEAKER_99', 0, 1000, NULL,
                    NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=37,
                source="test",
            )
        )
        await session.commit()

        assert summary["matched_turn_count"] == 1
        assert summary["updated_utterance_count"] == 0
        assert summary["preserved_manual_lock_count"] == 0

    def _force_conflicting_match(*args, **kwargs):
        session = args[0]
        conflicting_speaker = session.get(RecordingSpeaker, 2)
        assert conflicting_speaker is not None
        return conflicting_speaker, 1.0, {"provisional": False, "forced": True}

    monkeypatch.setattr(canonical_pipeline, "_match_window_local_speaker", _force_conflicting_match)

    async with test_session_maker() as session:
        summary = await session.run_sync(
            lambda sync_session: reconcile_diarization_window_result(
                sync_session,
                recording_id=1,
                window_result_id=38,
                source="test",
            )
        )
        await session.commit()

        assert summary["matched_turn_count"] == 1
        assert summary["updated_utterance_count"] == 0
        assert summary["preserved_manual_lock_count"] == 0

    async with test_session_maker() as session:
        utterance_row = (
            await session.execute(
                text(
                    "SELECT speaker_label, last_diarization_window_result_id, confidence_payload "
                    "FROM transcript_utterances WHERE public_id = 'live-utt-1'"
                )
            )
        ).one()
        transcript_segments = (
            await session.execute(text("SELECT segments FROM transcripts WHERE recording_id = 1"))
        ).scalar_one()
        confidence_payload = json.loads(utterance_row[2]) if isinstance(utterance_row[2], str) else utterance_row[2]
        transcript_segments = json.loads(transcript_segments) if isinstance(transcript_segments, str) else transcript_segments

        assert utterance_row[0] == "LIVE_01"
        assert utterance_row[1] == 38
        assert confidence_payload["rolling_diarization"]["speaker_state"] == "stable"
        assert confidence_payload["rolling_diarization"]["applied_recording_speaker_id"] == int(stable_speaker_id)
        assert confidence_payload["rolling_diarization"]["candidate_recording_speaker_id"] == 2
        assert confidence_payload["rolling_diarization"]["candidate_rejected"] is True
        assert confidence_payload["rolling_diarization"]["rejection_reason"] == "stable_speaker_requires_repeated_evidence"
        assert transcript_segments[0]["speaker"] == "LIVE_01"
        assert transcript_segments[0]["speaker_state"] == "stable"


@pytest.mark.anyio
async def test_identical_finalize_retry_is_idempotent(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import replace_utterances_from_segments

    await _seed_processed_recording(test_session_maker)

    payload = [
        {
            "start": 0.0,
            "end": 1.0,
            "speaker": "SPEAKER_00",
            "text": "finalized",
            "segment_source": "finalize",
        }
    ]

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: replace_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=payload,
                run_kind=ProcessingRunKind.FINALIZE,
                source="finalize",
                force=True,
                state_override=TranscriptUtteranceState.FINALIZED,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: replace_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=payload,
                run_kind=ProcessingRunKind.FINALIZE,
                source="finalize",
                force=True,
                state_override=TranscriptUtteranceState.FINALIZED,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        processing_run_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM processing_runs WHERE recording_id = 1 AND UPPER(run_kind) = 'FINALIZE'"
                )
            )
        ).scalar_one()
        active_utterance_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED'"
                )
            )
        ).scalar_one()

        assert processing_run_count == 1
        assert active_utterance_count == 1

def _make_turn(turn_id: int, start_ms: int, end_ms: int, speaker_id: int | None) -> Any:
    from backend.models.pipeline import DiarizationWindowTurn

    return DiarizationWindowTurn(
        id=turn_id,
        window_result_id=1,
        local_speaker_key=f"SPEAKER_{turn_id:02d}",
        start_ms=start_ms,
        end_ms=end_ms,
        matched_recording_speaker_id=speaker_id,
    )


def _make_speaker(speaker_id: int, label: str) -> Any:
    from backend.models.speaker import RecordingSpeaker

    return RecordingSpeaker(
        id=speaker_id,
        recording_id=1,
        diarization_label=label,
    )


def _make_utterance(
    *,
    start_ms: int,
    end_ms: int,
    text: str,
    words: list[dict[str, int | str]] | None = None,
    public_id: str = "utt-test-1",
) -> Any:
    from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState

    payload: dict[str, Any] = {}
    if words is not None:
        payload = {
            "asr_segments": [
                {
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": text,
                    "words": words,
                }
            ],
            "asr_word_timestamps_available": True,
        }

    return TranscriptUtterance(
        id=1,
        public_id=public_id,
        recording_id=1,
        sort_key="0001",
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        speaker_label="LIVE_01",
        state=TranscriptUtteranceState.PROVISIONAL,
        source_kind="live",
        confidence_payload=payload or None,
    )


def test_word_level_split_tolerates_unmapped_middle_word() -> None:
    """Phase D1: a single word without a matched turn must not abort the split."""
    from backend.utils.canonical_pipeline import (
        _build_split_replacement_segments_from_diarization,
    )

    speakers = {1: _make_speaker(1, "LIVE_01"), 2: _make_speaker(2, "LIVE_02")}
    # SPEAKER_00 covers 0-400ms (mapped to id=1), SPEAKER_01 covers 800-1200ms
    # (mapped to id=2). The middle word at 500-700ms has no overlapping turn.
    turn_rows = [
        _make_turn(1, 0, 400, 1),
        _make_turn(2, 800, 1200, 2),
    ]
    utterance = _make_utterance(
        start_ms=0,
        end_ms=1200,
        text="hello cruel world",
        words=[
            {"start_ms": 0, "end_ms": 400, "word": "hello"},
            {"start_ms": 500, "end_ms": 700, "word": "cruel"},
            {"start_ms": 800, "end_ms": 1200, "word": "world"},
        ],
    )

    segments = _build_split_replacement_segments_from_diarization(
        utterance,
        turn_rows=turn_rows,
        recording_speakers_by_id=speakers,
        window_result_id=99,
    )

    assert len(segments) == 2
    assert [segment["speaker"] for segment in segments] == ["LIVE_01", "LIVE_02"]
    # Middle word inherits a neighbour rather than aborting the split.
    text_by_speaker = {segment["speaker"]: segment["text"] for segment in segments}
    assert "hello" in text_by_speaker["LIVE_01"]
    assert "world" in text_by_speaker["LIVE_02"]


def test_turn_boundary_splitter_handles_utterance_without_word_timestamps() -> None:
    """Phase D2: utterances missing word timestamps still split at turn boundaries."""
    from backend.utils.canonical_pipeline import (
        _build_turn_boundary_split_segments_from_diarization,
    )

    speakers = {1: _make_speaker(1, "LIVE_01"), 2: _make_speaker(2, "LIVE_02")}
    turn_rows = [
        _make_turn(1, 0, 500, 1),
        _make_turn(2, 500, 1000, 2),
    ]
    utterance = _make_utterance(
        start_ms=0,
        end_ms=1000,
        text="one two three four",
        words=None,
    )

    segments = _build_turn_boundary_split_segments_from_diarization(
        utterance,
        turn_rows=turn_rows,
        recording_speakers_by_id=speakers,
        window_result_id=42,
    )

    assert len(segments) == 2
    assert [segment["speaker"] for segment in segments] == ["LIVE_01", "LIVE_02"]
    # Proportional whitespace-token split: 4 words across two equal halves.
    assert segments[0]["text"].split() == ["one", "two"]
    assert segments[1]["text"].split() == ["three", "four"]
    assert (
        segments[0]["confidence_payload"]["rolling_diarization"]["split_strategy"]
        == "diarization_turn_boundary"
    )
    assert (
        segments[0]["confidence_payload"]["rolling_diarization"]["split_from_public_id"]
        == utterance.public_id
    )
    assert segments[0]["confidence_payload"]["asr_word_timestamps_available"] is False


def test_match_utterance_flags_boundary_and_dampens_confidence() -> None:
    """Phase D3: ambiguous overlap flags is_boundary_utterance and dampens confidence."""
    from backend.utils.canonical_pipeline import (
        ROLLING_DIARIZATION_BOUNDARY_CONFIDENCE_DAMPENER,
        _match_utterance_from_diarization_turns,
    )

    speakers = {1: _make_speaker(1, "LIVE_01"), 2: _make_speaker(2, "LIVE_02")}
    # 60/40 overlap split — top ratio 0.6, second ratio 0.4 — both ≥ 0.30.
    turn_rows = [
        _make_turn(1, 0, 600, 1),
        _make_turn(2, 600, 1000, 2),
    ]
    utterance = _make_utterance(
        start_ms=0,
        end_ms=1000,
        text="ambiguous boundary speech",
        words=None,
    )

    matched, confidence, evidence = _match_utterance_from_diarization_turns(
        utterance,
        turn_rows=turn_rows,
        recording_speakers_by_id=speakers,
    )

    assert matched is not None
    assert int(matched.id) == 1
    assert evidence["is_boundary_utterance"] is True
    assert 2 in evidence["boundary_overlapping_recording_speaker_ids"]
    raw_confidence = evidence["raw_confidence"]
    assert confidence == round(raw_confidence * ROLLING_DIARIZATION_BOUNDARY_CONFIDENCE_DAMPENER, 4)
    assert evidence.get("boundary_dampened") is True


def test_match_utterance_does_not_dampen_when_overlap_is_clear() -> None:
    """Phase D3 (negative): dominant overlap leaves confidence un-dampened."""
    from backend.utils.canonical_pipeline import (
        _match_utterance_from_diarization_turns,
    )

    speakers = {1: _make_speaker(1, "LIVE_01"), 2: _make_speaker(2, "LIVE_02")}
    # 90/10 overlap split — second ratio 0.1 < 0.30 ambiguity threshold.
    turn_rows = [
        _make_turn(1, 0, 900, 1),
        _make_turn(2, 900, 1000, 2),
    ]
    utterance = _make_utterance(
        start_ms=0,
        end_ms=1000,
        text="clearly speaker one",
        words=None,
    )

    matched, confidence, evidence = _match_utterance_from_diarization_turns(
        utterance,
        turn_rows=turn_rows,
        recording_speakers_by_id=speakers,
    )

    assert matched is not None
    assert int(matched.id) == 1
    assert evidence["is_boundary_utterance"] is False
    assert evidence.get("boundary_dampened") is not True
    assert confidence == 0.9
