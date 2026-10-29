from __future__ import annotations

import json
from typing import Any

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
from backend.tests.sqlite_schemas import (
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    SPEAKER_NAME_EVENTS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
)
from backend.utils.canonical_pipeline import (
    list_pending_startup_cutover_recording_ids,
    process_startup_cutover_recording,
)

RECORDINGS_SCHEMA = """
CREATE TABLE recordings (
    gist TEXT,
    title_user_edited BOOLEAN NOT NULL DEFAULT 0,
    gist_live BOOLEAN NOT NULL DEFAULT 0,
    max_speakers INTEGER,
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
    pipeline_generation VARCHAR(32) DEFAULT 'unified',
    is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    last_activity_at DATETIME,
    capture_epoch INTEGER NOT NULL DEFAULT 0,
    transcription_language VARCHAR(16),
    user_id INTEGER,
    calendar_event_id INTEGER
)
"""

TRANSCRIPTS_SCHEMA = """
CREATE TABLE transcripts (
    change_sequence BIGINT NOT NULL DEFAULT 0,
    settling_state JSON,
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL UNIQUE,
    text TEXT,
    segments JSON,
    language_detection JSON,
    notes TEXT,
    meeting_edge_focus TEXT,
    meeting_edge_payload JSON,
    meeting_edge_status VARCHAR(32) NOT NULL DEFAULT 'idle',
    meeting_edge_error_message TEXT,
    meeting_edge_source_signature TEXT,
    speaker_name_suggestions JSON,
    notes_template_id INTEGER,
    notes_template_sections TEXT,
    notes_status VARCHAR(32) NOT NULL,
    notes_stale_documents BOOLEAN DEFAULT 0,
    transcript_status VARCHAR(32) NOT NULL,
    error_message TEXT,
    analytics_payload JSON,
    analytics_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    analytics_ai_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    analytics_ai_error_message TEXT,
    analytics_error_message TEXT
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
    document_page_id INTEGER,
    content TEXT,
    embedding JSON,
    embedding_version INTEGER
)
"""

GLOBAL_SPEAKERS_SCHEMA = """
CREATE TABLE global_speakers (
    embedding_version INTEGER,
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
    embedding_version INTEGER,
    embedding_speech_seconds FLOAT,
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
    identity_locked BOOLEAN NOT NULL,
    name_last_edit_source VARCHAR(32),
    name_source VARCHAR(16)
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
    asr_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    asr_processing_run_id INTEGER,
    asr_last_error TEXT,
    diarization_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    diarization_processing_run_id INTEGER,
    diarization_config_hash VARCHAR(255),
    diarization_window_result_id INTEGER,
    diarization_last_error TEXT,
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
    diarization_label VARCHAR(255),
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
    text_last_edit_source VARCHAR(32),
    speaker_last_edit_source VARCHAR(32),
    speaker_assignment_source VARCHAR(32) NOT NULL,
    speaker_assignment_authority VARCHAR(32) NOT NULL,
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
        await connection.execute(text(RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA))
        await connection.execute(text(PROCESSING_RUNS_SCHEMA))
        await connection.execute(text(RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA))
        await connection.execute(text(DIARIZATION_WINDOW_RESULTS_SCHEMA))
        await connection.execute(text(DIARIZATION_WINDOW_TURNS_SCHEMA))
        await connection.execute(text(TRANSCRIPT_UTTERANCES_SCHEMA))
        await connection.execute(text(TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA))
        await connection.execute(text(RECORDING_SPEAKER_ALIASES_SCHEMA))
        await connection.execute(text(SPEAKER_CORRECTION_EVENTS_SCHEMA))
        await connection.execute(text(SPEAKER_NAME_EVENTS_SCHEMA))
        await connection.execute(text(PEOPLE_TAGS_SCHEMA))
        await connection.execute(text(TIMELINE_NOTES_SCHEMA))

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
async def client(
    api_app: FastAPI, test_session_maker: sessionmaker, stub_meeting_edge_dispatch
) -> AsyncClient:
    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    api_app.dependency_overrides[get_db] = override_get_db
    api_app.dependency_overrides[get_current_user] = lambda: build_test_user()

    transport = ASGITransport(app=api_app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
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
                    notes, meeting_edge_status, notes_status, transcript_status
                ) VALUES (
                    :recording_id, :now, :now, :recording_id, 'hello there', :segments,
                    NULL, 'idle', 'pending', 'completed'
                )
                """
            ),
            {
                "now": "2026-05-19 00:00:00",
                "recording_id": recording_id,
                "segments": (
                    '[{"id": "'
                    + segment_id
                    + '", "start": 0.0, "end": 1.2, "speaker": "SPEAKER_00", "text": "hello there", "segment_source": "legacy"}]'
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
                    notes, meeting_edge_status, notes_status, transcript_status
                ) VALUES (
                    1, :now, :now, 1, '', '[]',
                    NULL, 'idle', 'pending', 'processing'
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
        transcript = (
            await session.execute(
                select(Transcript).where(Transcript.recording_id == 1)
            )
        ).scalar_one()
        assert transcript.segments[0]["id"] == body["utterances"][0]["id"]
        count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1"
                )
            )
        ).scalar_one()
        last_event_id = (
            await session.execute(
                text(
                    "SELECT last_utterance_event_id FROM transcript_utterances WHERE recording_id = 1"
                )
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
            await session.execute(
                text("SELECT pipeline_generation FROM recordings WHERE id = 1")
            )
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
            lambda sync_session: list_pending_startup_cutover_recording_ids(
                sync_session, batch_size=10
            )
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
            await session.execute(
                text("SELECT pipeline_generation FROM recordings WHERE id = 1")
            )
        ).scalar_one()
        utterance_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1"
                )
            )
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
            await session.execute(
                text("SELECT pipeline_generation FROM recordings WHERE id = 1")
            )
        ).scalar_one()
        utterance_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1"
                )
            )
        ).scalar_one()
        processing_run_count = (
            await session.execute(
                text("SELECT COUNT(*) FROM processing_runs WHERE recording_id = 1")
            )
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
            lambda sync_session: list_pending_startup_cutover_recording_ids(
                sync_session, batch_size=10
            )
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
            lambda sync_session: list_pending_startup_cutover_recording_ids(
                sync_session, batch_size=10
            )
        )
        second_outcome = await session.run_sync(
            lambda sync_session: process_startup_cutover_recording(
                sync_session,
                recording_id=2,
            )
        )
        await session.commit()

        generations = (
            await session.execute(
                text("SELECT id, pipeline_generation FROM recordings ORDER BY id")
            )
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
        await session.execute(
            text("UPDATE recordings SET pipeline_generation = NULL WHERE id = 1")
        )
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
        [
            _build_pending_speaker_suggestion(
                diarization_label="SPEAKER_00", suggested_name="Alex"
            )
        ],
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
            await session.execute(
                select(Transcript).where(Transcript.recording_id == 1)
            )
        ).scalar_one()

    assert transcript.speaker_name_suggestions[0]["status"] == "superseded"
    assert (
        transcript.speaker_name_suggestions[0]["resolution_reason"]
        == "manual_name_change"
    )
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
            (
                await session.execute(
                    text(
                        "SELECT event_type FROM transcript_utterance_events WHERE utterance_id = (SELECT id FROM transcript_utterances WHERE public_id = :public_id) ORDER BY id DESC LIMIT 2"
                    ),
                    {"public_id": utterance_id},
                )
            )
            .scalars()
            .all()
        )
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
async def test_utterance_read_model_exposes_edit_source(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    """The surface that made the edit reaches the payload the app renders.

    Without this the provenance is recorded but unreachable, which is the
    whole gap being closed.
    """
    await _seed_processed_recording(test_session_maker)

    before = await client.get("/api/v1/transcripts/canon-rec/utterances")
    assert before.json()["utterances"][0]["text_edit_source"] is None

    text_update = await client.put(
        "/api/v1/transcripts/canon-rec/segments/0/text",
        json={"text": "edited in the web app"},
    )
    assert text_update.status_code == 200

    after = await client.get("/api/v1/transcripts/canon-rec/utterances")
    utterance = after.json()["utterances"][0]

    assert utterance["text_manually_edited"] is True
    assert utterance["text_edit_source"] == "api"
    assert utterance["speaker_edit_source"] is None


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
            await session.execute(
                select(Transcript).where(Transcript.recording_id == 1)
            )
        ).scalar_one()
        canonical_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1"
                )
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
            (
                await session.execute(
                    text(
                        "SELECT revision FROM transcript_utterances WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED' ORDER BY sort_key"
                    )
                )
            )
            .scalars()
            .all()
        )
        alias_rows = (
            await session.execute(
                text(
                    "SELECT alias_type, alias_value FROM recording_speaker_aliases ORDER BY alias_value"
                )
            )
        ).all()
        normalized_alias_rows = {
            (alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows
        }
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
            (
                await session.execute(
                    text(
                        "SELECT event_type FROM transcript_utterance_events WHERE recording_id = 1 ORDER BY id DESC LIMIT 2"
                    )
                )
            )
            .scalars()
            .all()
        )
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
        normalized_alias_rows = {
            (alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows
        }
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
        normalized_alias_rows = {
            (alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows
        }
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
async def test_whole_transcript_rename_routes_future_generic_live_labels_to_corrected_speaker(
    test_session_maker: sessionmaker,
) -> None:
    from backend.models.pipeline import SpeakerCorrectionScope
    from backend.models.speaker import RecordingSpeaker
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        serialize_canonical_utterances,
        update_utterance_speaker,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    1, :now, :now, 'speaker-public-1', 1,
                    NULL, 'LIVE_01', NULL, 'Speaker 1',
                    NULL, NULL, 'active', 'live',
                    NULL, NULL, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    def apply_rename_and_append_future_label(sync_session):
        append_utterances_from_segments(
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
        update_utterance_speaker(
            sync_session,
            recording_id=1,
            utterance_public_id="live-utt-1",
            new_speaker_name="Ezra Klein",
            scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
            actor_user_id=1,
            source="test",
        )
        future_utterances = append_utterances_from_segments(
            sync_session,
            recording_id=1,
            segments=[
                {
                    "id": "live-utt-2",
                    "start": 1.0,
                    "end": 2.0,
                    "speaker": "Speaker 1",
                    "text": "follow up",
                    "provisional": True,
                    "segment_source": "live",
                }
            ],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
            state_override=TranscriptUtteranceState.PROVISIONAL,
            trigger_source="test",
        )
        target_speaker = sync_session.execute(
            select(RecordingSpeaker).where(RecordingSpeaker.local_name == "Ezra Klein")
        ).scalar_one()

        return (
            future_utterances,
            target_speaker,
            serialize_canonical_utterances(sync_session, 1),
        )

    async with test_session_maker() as session:
        future_utterances, target_speaker, transcript_segments = await session.run_sync(
            apply_rename_and_append_future_label
        )
        await session.commit()

    future_utterance = future_utterances[0]

    assert future_utterance.recording_speaker_id == target_speaker.id
    assert future_utterance.speaker_label == target_speaker.diarization_label
    assert transcript_segments[-1]["speaker"] == target_speaker.diarization_label

    async with test_session_maker() as session:
        generic_alias_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM recording_speaker_aliases "
                    "WHERE recording_speaker_id = :target_id AND alias_value = 'Speaker 1' AND active = 1"
                ),
                {"target_id": target_speaker.id},
            )
        ).scalar_one()

        assert generic_alias_count >= 1


@pytest.mark.anyio
async def test_live_scope_rename_reuses_live_speaker_identity_for_future_live_labels(
    test_session_maker: sessionmaker,
) -> None:
    from backend.models.pipeline import SpeakerCorrectionScope
    from backend.models.speaker import RecordingSpeaker
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        update_utterance_speaker,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES (
                    1, :now, :now, 'speaker-public-1', 1,
                    NULL, 'LIVE_01', NULL, 'Speaker 1',
                    NULL, NULL, 'active', 'live',
                    NULL, NULL, NULL, 0
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.commit()

    def apply_live_rename_and_append_future_label(sync_session):
        append_utterances_from_segments(
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
        updated_utterance, target_speaker = update_utterance_speaker(
            sync_session,
            recording_id=1,
            utterance_public_id="live-utt-1",
            new_speaker_name="Marques",
            scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
            actor_user_id=1,
            source="test",
        )
        future_utterances = append_utterances_from_segments(
            sync_session,
            recording_id=1,
            segments=[
                {
                    "id": "live-utt-2",
                    "start": 1.0,
                    "end": 2.0,
                    "speaker": "LIVE_01",
                    "text": "follow up",
                    "provisional": True,
                    "segment_source": "live",
                }
            ],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
            state_override=TranscriptUtteranceState.PROVISIONAL,
            trigger_source="test",
        )
        speaker_rows = (
            sync_session.execute(
                select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
            )
            .scalars()
            .all()
        )
        return updated_utterance, target_speaker, future_utterances, speaker_rows

    async with test_session_maker() as session:
        (
            updated_utterance,
            target_speaker,
            future_utterances,
            speaker_rows,
        ) = await session.run_sync(apply_live_rename_and_append_future_label)
        await session.commit()

    future_utterance = future_utterances[0]

    assert updated_utterance.recording_speaker_id == 1
    assert target_speaker.id == 1
    assert target_speaker.diarization_label == "LIVE_01"
    assert target_speaker.local_name == "Marques"
    assert future_utterance.recording_speaker_id == 1
    assert future_utterance.speaker_label == "LIVE_01"
    assert len(speaker_rows) == 1


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
        normalized_alias_rows = {
            (alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows
        }

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
            text(
                "UPDATE transcripts SET text = '', segments = '[]' WHERE recording_id = 1"
            )
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
            await session.execute(
                select(Transcript).where(Transcript.recording_id == 1)
            )
        ).scalar_one()

        assert transcript.segments
        assert [
            segment["speaker"] for segment in transcript.segments
        ] == utterance_speakers


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
            text(
                "UPDATE transcripts SET text = '', segments = '[]' WHERE recording_id = 1"
            )
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
            await session.execute(
                select(Transcript).where(Transcript.recording_id == 1)
            )
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
        normalized_alias_rows = {
            (alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows
        }

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
        normalized_alias_rows = {
            (alias_type.lower(), alias_value) for alias_type, alias_value in alias_rows
        }

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
            await session.execute(
                text("SELECT segments FROM transcripts WHERE recording_id = 1")
            )
        ).scalar_one()
        transcript_segments = (
            json.loads(transcript_segments)
            if isinstance(transcript_segments, str)
            else transcript_segments
        )

        assert utterance_row[0] == "live-utt-1"
        assert utterance_row[1] == "manual text"
        assert bool(utterance_row[2]) is True
        assert utterance_row[3].lower() == "finalized"
        assert transcript_segments[0]["text"] == "manual text"
        assert transcript_segments[0]["text_manually_edited"] is True


@pytest.mark.anyio
async def test_finalize_preserves_mcp_edit_source_in_place(
    test_session_maker: sessionmaker,
) -> None:
    """An assistant's correction stays attributed to it across a reprocess.

    The lock already survived finalize; without the source surviving with it,
    reprocessing would silently downgrade an assistant's edit to an
    unattributed one, which fails quietly rather than loudly.
    """
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
                text="corrected by the assistant",
                actor_user_id=1,
                source="mcp",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        source_before = (
            await session.execute(
                text(
                    "SELECT text_last_edit_source FROM transcript_utterances "
                    "WHERE public_id = 'live-utt-1'"
                )
            )
        ).scalar_one()
        assert source_before == "mcp"

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
                    "SELECT text, manual_text_locked, text_last_edit_source "
                    "FROM transcript_utterances "
                    "WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED'"
                )
            )
        ).one()
        transcript_segments = (
            await session.execute(
                text("SELECT segments FROM transcripts WHERE recording_id = 1")
            )
        ).scalar_one()
        transcript_segments = (
            json.loads(transcript_segments)
            if isinstance(transcript_segments, str)
            else transcript_segments
        )

        assert utterance_row[0] == "corrected by the assistant"
        assert bool(utterance_row[1]) is True
        assert utterance_row[2] == "mcp"
        assert transcript_segments[0]["text_edit_source"] == "mcp"


@pytest.mark.anyio
async def test_finalize_inherits_mcp_edit_source_when_boundaries_shift(
    test_session_maker: sessionmaker,
) -> None:
    """The supersede path carries provenance onto the replacement row.

    Shifting boundaries retire the edited utterance and build a new one, so the
    source has to ride the segment payload across rather than living only on
    the row that was superseded.
    """
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
                source="mcp",
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
                        "id": "live-utt-1",
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
        active_row = (
            await session.execute(
                text(
                    "SELECT manual_speaker_locked, speaker_last_edit_source "
                    "FROM transcript_utterances "
                    "WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED'"
                )
            )
        ).one()

        assert bool(active_row[0]) is True
        assert active_row[1] == "mcp"


@pytest.mark.anyio
async def test_clear_manual_locks_clears_edit_sources(
    test_session_maker: sessionmaker,
) -> None:
    """Releasing the lock releases the attribution it described.

    The pill is driven by the lock, so a source left behind would be state the
    interface can never reach.
    """
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        clear_utterance_manual_locks,
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
                text="corrected by the assistant",
                actor_user_id=1,
                source="mcp",
            )
        )
        await session.run_sync(
            lambda sync_session: clear_utterance_manual_locks(
                sync_session,
                recording_id=1,
                utterance_public_id="live-utt-1",
                actor_user_id=1,
                source="mcp",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT manual_text_locked, text_last_edit_source, "
                    "speaker_last_edit_source FROM transcript_utterances "
                    "WHERE public_id = 'live-utt-1'"
                )
            )
        ).one()

        assert bool(row[0]) is False
        assert row[1] is None
        assert row[2] is None


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
                        "id": "live-utt-1",
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
                text(
                    "SELECT state FROM transcript_utterances WHERE public_id = 'live-utt-1'"
                )
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
            await session.execute(
                text("SELECT segments FROM transcripts WHERE recording_id = 1")
            )
        ).scalar_one()
        transcript_segments = (
            json.loads(transcript_segments)
            if isinstance(transcript_segments, str)
            else transcript_segments
        )
        confidence_payload = (
            json.loads(active_row[4])
            if isinstance(active_row[4], str)
            else active_row[4]
        )

        assert old_state.lower() == "superseded"
        assert active_row[0] != "live-utt-1"
        assert active_row[1] == "LIVE_01"
        assert bool(active_row[2]) is True
        assert active_row[3] == "Dwarkesh Patel"
        assert confidence_payload["source_public_ids"] == ["live-utt-1"]
        assert confidence_payload["inherited_manual_speaker"]["source_public_ids"] == [
            "live-utt-1"
        ]
        assert transcript_segments[0]["speaker"] == active_row[1]
        assert transcript_segments[0]["speaker_manually_edited"] is True


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
                        select(RecordingSpeaker).where(
                            RecordingSpeaker.recording_id == 1
                        )
                    )
                    .scalars()
                    .all()
                ),
            )
        )

        assert any(
            label == "LIVE_01" and name == "Dana" and status == "active"
            for label, name, status in rows
        )
        assert [speaker.local_name or speaker.name for speaker in public_speakers] == [
            "Dana"
        ]


async def _seed_merged_recording_speakers(session_maker: sessionmaker) -> None:
    """Two rows resolved to one person, the merged one holding the lower id.

    The merge pass leaves the loser's global_speaker_id in place, so a lookup by
    global speaker that walks the rows in id order reaches the hidden row first.
    """
    async with session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO global_speakers (id, created_at, updated_at, user_id, name) "
                "VALUES (10, :now, :now, 1, 'Person A')"
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        for row_id, label, global_id, merged_into, status in (
            (1, "SPEAKER_A", 10, 2, "merged"),
            (2, "SPEAKER_A2", 10, None, "active"),
            (3, "SPEAKER_C", None, None, "active"),
        ):
            await session.execute(
                text(
                    "INSERT INTO recording_speakers ("
                    " id, created_at, updated_at, public_id, recording_id,"
                    " global_speaker_id, diarization_label, merged_into_id,"
                    " speaker_status, speaker_kind, identity_locked"
                    ") VALUES (:id, :now, :now, :public_id, 1, :global_id, :label,"
                    " :merged_into, :status, 'diarized', 0)"
                ),
                {
                    "id": row_id,
                    "now": "2026-05-20 00:00:00",
                    "public_id": f"rs-{row_id}",
                    "global_id": global_id,
                    "label": label,
                    "merged_into": merged_into,
                    "status": status,
                },
            )
        await session.commit()


@pytest.mark.anyio
async def test_global_speaker_assignment_never_targets_a_merged_recording_speaker(
    test_session_maker: sessionmaker,
) -> None:
    """Assigning to a person must land on a row the transcript can render.

    A merged row keeps its global_speaker_id but is hidden from the public
    speaker list, so targeting one pins the utterance to a speaker the UI cannot
    resolve and it falls back to the raw diarization label.
    """
    from backend.models.pipeline import SpeakerCorrectionScope
    from backend.models.speaker import RecordingSpeaker
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        filter_recording_speakers_for_public_read,
        update_utterance_speaker,
    )

    await _seed_uploading_recording(test_session_maker)
    await _seed_merged_recording_speakers(test_session_maker)

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "utt-1",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "SPEAKER_C",
                        "text": "hello",
                        "segment_source": "final",
                    }
                ],
                run_kind=ProcessingRunKind.IMPORT,
                source="import",
                state_override=TranscriptUtteranceState.STABLE,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        _, target = await session.run_sync(
            lambda sync_session: update_utterance_speaker(
                sync_session,
                recording_id=1,
                utterance_public_id="utt-1",
                new_speaker_name="Person A",
                global_speaker_id=10,
                scope=SpeakerCorrectionScope.UTTERANCE_ONLY,
                actor_user_id=1,
                source="test",
            )
        )
        await session.commit()

    assert target.merged_into_id is None
    # The survivor already carries the identity, so no new row is needed.
    assert target.id == 2

    async with test_session_maker() as session:
        public_speakers = await session.run_sync(
            lambda sync_session: filter_recording_speakers_for_public_read(
                sync_session,
                1,
                list(
                    sync_session.execute(
                        select(RecordingSpeaker).where(
                            RecordingSpeaker.recording_id == 1
                        )
                    )
                    .scalars()
                    .all()
                ),
            )
        )
        assigned_speaker_id, speaker_label = (
            await session.execute(
                text(
                    "SELECT recording_speaker_id, speaker_label FROM "
                    "transcript_utterances WHERE recording_id = 1 "
                    "AND UPPER(state) != 'SUPERSEDED'"
                )
            )
        ).one()

    assert assigned_speaker_id == 2
    assert speaker_label == "SPEAKER_A2"
    assert assigned_speaker_id in {speaker.id for speaker in public_speakers}


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
                text("SELECT text, segments FROM transcripts WHERE recording_id = 1")
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
        transcript_segments = (
            json.loads(transcript_row[1])
            if isinstance(transcript_row[1], str)
            else transcript_row[1]
        )
        assert transcript_segments[0]["id"] == "live-utt-1"
        assert transcript_segments[0]["segment_source"] == "live"
        assert transcript_segments[0]["provisional"] is True


@pytest.mark.anyio
async def test_live_append_spans_its_run_from_the_earliest_start_to_the_latest_end(
    test_session_maker: sessionmaker,
) -> None:
    """The run's span covers every segment, whatever order they arrive in, and
    rounds to the millisecond as the live lane's region bounds do."""
    from backend.utils.canonical_pipeline import append_utterances_from_segments

    await _seed_uploading_recording(test_session_maker)

    segments = [
        {
            "id": "live-utt-late",
            "start": 3.2004,
            "end": 4.5,
            "speaker": "LIVE_01",
            "text": "second region",
            "provisional": True,
            "segment_source": "live",
        },
        {
            "id": "live-utt-early",
            "start": 1.0006,
            "end": 2.0,
            "speaker": "LIVE_01",
            "text": "first region",
            "provisional": True,
            "segment_source": "live",
        },
    ]

    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=segments,
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
                trigger_source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        span = (
            await session.execute(
                text(
                    "SELECT span_start_ms, span_end_ms FROM processing_runs "
                    "WHERE recording_id = 1"
                )
            )
        ).one()

    assert tuple(span) == (1001, 4500)


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
        transcript_segments = (
            json.loads(transcript_segments)
            if isinstance(transcript_segments, str)
            else transcript_segments
        )

        assert processing_run_count == 1
        assert utterance_count == 1


@pytest.mark.anyio
async def test_merge_reassigns_to_target_without_replaying_window_rows(
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        merge_recording_speakers_by_label,
    )

    await _seed_uploading_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    embedding, merged_into_id, speaker_status, speaker_kind,
                    first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                ) VALUES
                    (
                        41, :now, :now, 'speaker-public-41', 1,
                        NULL, 'LIVE_01', NULL, NULL,
                        NULL, NULL, 'active', 'automated',
                        0, 1000, NULL, 0
                    ),
                    (
                        42, :now, :now, 'speaker-public-42', 1,
                        NULL, 'LIVE_02', NULL, NULL,
                        NULL, NULL, 'active', 'automated',
                        0, 1000, NULL, 0
                    )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "final-utt-merge",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "LIVE_02",
                        "text": "merge target wins",
                        "segment_source": "finalize",
                    }
                ],
                run_kind=ProcessingRunKind.FINALIZE,
                source="finalize",
                state_override=TranscriptUtteranceState.FINALIZED,
                trigger_source="test",
            )
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
                    91, :now, :now, 'window-public-91', 1,
                    NULL, 0, 0,
                    1000, 1, 1,
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
                                "best_recording_speaker_id": 42,
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
                    92, :now, :now, 91,
                    'SPEAKER_00', 0, 1000, NULL,
                    NULL, NULL
                )
                """
            ),
            {"now": "2026-05-20 00:00:00"},
        )
        await session.run_sync(
            lambda sync_session: merge_recording_speakers_by_label(
                sync_session,
                recording_id=1,
                source_diarization_label="LIVE_02",
                target_diarization_label="LIVE_01",
                actor_user_id=1,
                source="test",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        utterance_row = (
            await session.execute(
                text(
                    "SELECT speaker_label, recording_speaker_id, speaker_assignment_source, "
                    "speaker_assignment_authority FROM transcript_utterances "
                    "WHERE public_id = 'final-utt-merge'"
                )
            )
        ).one()
        source_status = (
            await session.execute(
                text("SELECT speaker_status FROM recording_speakers WHERE id = 42")
            )
        ).scalar_one()
        turn_match = (
            await session.execute(
                text(
                    "SELECT matched_recording_speaker_id FROM diarization_window_turns WHERE id = 92"
                )
            )
        ).scalar_one()
        transcript_segments = (
            await session.execute(
                text("SELECT segments FROM transcripts WHERE recording_id = 1")
            )
        ).scalar_one()
        transcript_segments = (
            json.loads(transcript_segments)
            if isinstance(transcript_segments, str)
            else transcript_segments
        )

        assert utterance_row[0] == "LIVE_01"
        assert utterance_row[1] == 41
        assert utterance_row[2] == "manual"
        assert utterance_row[3] == "manual"
        assert source_status == "merged"
        assert turn_match is None
        assert transcript_segments[0]["speaker"] == "LIVE_01"


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


@pytest.mark.anyio
async def test_worker_finalize_survives_a_later_stage_failure(
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    """The canonical finalize is committed before the notes and title stages run.

    When a later stage raises, the worker's handler rolls the session back. The
    finalized utterances and the transcript projection must both survive it, and
    still agree with each other.
    """
    from types import SimpleNamespace

    from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

    from backend.models.recording import Recording
    from backend.worker.tasks import pipeline as pipeline_module

    await _seed_processed_recording(test_session_maker)
    # The worker runs on a SQLModel session, which the stages query through
    # ``session.exec``.
    worker_session_maker = sessionmaker(
        test_session_maker.kw["bind"],
        class_=SQLModelAsyncSession,
        expire_on_commit=False,
    )

    original_get = pipeline_module.config_manager.get

    def _fake_get(key, default=None):
        if key == "enable_canonical_transcript_writes":
            return True
        return original_get(key, default)

    def _later_stage_fails(*_args, **_kwargs):
        raise RuntimeError("notes context failed")

    monkeypatch.setattr(pipeline_module.config_manager, "get", _fake_get)
    monkeypatch.setattr(
        pipeline_module,
        "_build_automatic_meeting_intelligence_transcript",
        _later_stage_fails,
    )

    final_segments = [
        {
            "start": 0.0,
            "end": 1.0,
            "speaker": "SPEAKER_00",
            "text": "finalized words",
        }
    ]

    def _finalize_then_fail(sync_session) -> None:
        recording = sync_session.get(Recording, 1)
        transcript = sync_session.exec(
            select(Transcript).where(Transcript.recording_id == 1)
        ).one()
        ctx = pipeline_module._PipelineRunContext(
            task=SimpleNamespace(update_state=lambda **_kwargs: None),
            session=sync_session,
            recording_id=1,
            device_suffix="",
            temp_files=[],
            merged_config={},
        )
        with pytest.raises(RuntimeError, match="notes context failed"):
            pipeline_module._finalize_transcript_and_notes(
                ctx,
                recording,
                transcript,
                final_segments,
                None,
                {},
            )
        # The worker's catch-all handler rolls back before recording the error.
        sync_session.rollback()

    async with worker_session_maker() as session:
        await session.run_sync(_finalize_then_fail)

    async with test_session_maker() as session:
        utterances = (
            await session.execute(
                text(
                    "SELECT public_id, text FROM transcript_utterances "
                    "WHERE recording_id = 1 AND UPPER(state) != 'SUPERSEDED' "
                    "ORDER BY start_ms"
                )
            )
        ).all()
        transcript = (
            await session.execute(
                select(Transcript).where(Transcript.recording_id == 1)
            )
        ).scalar_one()

    assert [row.text for row in utterances] == ["finalized words"]
    assert [(segment["id"], segment["text"]) for segment in transcript.segments] == [
        (row.public_id, row.text) for row in utterances
    ]
    assert transcript.text == "finalized words"
    # Finalize writes a projection of its own; only the refresh from the read
    # model adds the millisecond bounds, so these pin the commit after it.
    assert [
        (segment.get("start_ms"), segment.get("end_ms"))
        for segment in transcript.segments
    ] == [(0, 1000)]


def _make_utterance(*, start_ms: int, end_ms: int, text: str) -> Any:
    from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState

    return TranscriptUtterance(
        id=1,
        public_id="utt-test-1",
        recording_id=1,
        sort_key="0001",
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        speaker_label="LIVE_01",
        state=TranscriptUtteranceState.PROVISIONAL,
        source_kind="live",
    )


def test_projection_reads_speaker_evidence_older_rolling_rows_carry() -> None:
    """Lines finalised by v1.0.0 to v1.2.0 keep rolling-diarisation evidence in
    their payload, and the read side still reports it."""
    from backend.utils.canonical_pipeline import (
        _rolling_overlap_labels_for_utterance,
        _speaker_state_for_utterance,
    )

    utterance = _make_utterance(start_ms=0, end_ms=1000, text="hello")
    # Without stored evidence a provisional live line reads as provisional.
    assert _speaker_state_for_utterance(utterance) == "provisional"

    utterance.confidence_payload = {
        "rolling_diarization": {
            "speaker_state": "stable",
            "overlapping_speakers": ["LIVE_02", "LIVE_01", "", "LIVE_02"],
        }
    }

    assert _speaker_state_for_utterance(utterance) == "stable"
    # Stored labels win over the projection's; the line's own speaker, blanks
    # and repeats drop out.
    assert _rolling_overlap_labels_for_utterance(
        utterance, projection={"overlapping_speakers": ["LIVE_09"]}
    ) == ["LIVE_02"]

    utterance.manual_speaker_locked = True
    assert _speaker_state_for_utterance(utterance) == "manual_override"


def test_overlap_labels_read_as_the_speaker_each_label_was_merged_into() -> None:
    from backend.models.speaker import RecordingSpeaker
    from backend.utils.canonical_pipeline import (
        _active_speaker_labels,
        _rolling_overlap_labels_for_utterance,
    )

    def speaker(pk: int, label: str, merged_into: int | None = None):
        return RecordingSpeaker(
            id=pk, recording_id=1, diarization_label=label, merged_into_id=merged_into
        )

    speakers = {
        1: speaker(1, "SPEAKER_00"),
        2: speaker(2, "SPEAKER_01", merged_into=3),
        3: speaker(3, "SPEAKER_02", merged_into=1),
        # A cycle or a missing target stops the walk instead of looping.
        4: speaker(4, "SPEAKER_03", merged_into=5),
        5: speaker(5, "SPEAKER_04", merged_into=4),
        6: speaker(6, "SPEAKER_05", merged_into=99),
    }
    active = _active_speaker_labels(speakers)
    assert active == {
        "SPEAKER_00": "SPEAKER_00",
        "SPEAKER_01": "SPEAKER_00",
        "SPEAKER_02": "SPEAKER_00",
        "SPEAKER_03": "SPEAKER_03",
        "SPEAKER_04": "SPEAKER_04",
        "SPEAKER_05": "SPEAKER_05",
    }

    utterance = _make_utterance(start_ms=0, end_ms=1000, text="hello")
    utterance.speaker_label = "SPEAKER_00"
    projection = {"overlapping_speakers": ["SPEAKER_02", "SPEAKER_05"]}
    # Stored as diarised; read as the speaker each label now is, and a label
    # merged into the line's own speaker drops out.
    assert _rolling_overlap_labels_for_utterance(utterance, projection=projection) == [
        "SPEAKER_02",
        "SPEAKER_05",
    ]
    assert _rolling_overlap_labels_for_utterance(
        utterance, projection=projection, active_labels=active
    ) == ["SPEAKER_05"]

    utterance.confidence_payload = {
        "rolling_diarization": {"overlapping_recording_speaker_ids": [2, 6]}
    }
    assert _rolling_overlap_labels_for_utterance(
        utterance, recording_speakers_by_id=speakers, active_labels=active
    ) == ["SPEAKER_05"]


# ---------------------------------------------------------------------------
# Speaker corrections leave leftover rolling-window rows alone.
#
# Live captures finalised by v1.0.0 to v1.2.0 can still hold
# `diarization_window_results` / `diarization_window_turns` rows, though
# nothing writes them any more. A correction used to replay every completed
# window after the edit point. For a rename, two overlapping windows whose
# local speaker spanned another person's line moved that line onto the renamed
# speaker. A merge or global-speaker link replayed under an identity guard. It
# kept other identities' lines, but it moved lines between speakers sharing a
# global speaker and rewrote assignment sources and window evidence from the
# stale rows.
# ---------------------------------------------------------------------------

_STALE_WINDOW_NOW = "2026-05-28 00:00:00"

# A finalised transcript: Alice and Bob alternate, then Carol speaks once.
_STALE_WINDOW_LINES = (
    # id, start s (each line lasts 2 s), speaker, text
    ("sw-1", 0.0, "SPEAKER_00", "alice one"),
    ("sw-2", 2.0, "SPEAKER_01", "bob one"),
    ("sw-3", 4.0, "SPEAKER_00", "alice two"),
    ("sw-4", 6.0, "SPEAKER_01", "bob two"),
    ("sw-5", 8.0, "SPEAKER_02", "carol one"),
)

# Two overlapping rolling windows (20 s with a 5 s hop in production, scaled
# down). In both, local speaker L0 covers Bob's first line as well as Alice's:
# the live lane had folded Bob into Alice there.
_STALE_WINDOWS = {500: (0, 10000), 600: (2000, 10000)}  # id: (start ms, end ms)
_STALE_WINDOW_TURNS = (
    # turn id, window id, local speaker, start ms, end ms
    (501, 500, "L0", 0, 6000),
    (502, 500, "L1", 6000, 8000),
    (503, 500, "L2", 8000, 10000),
    (601, 600, "L0", 2000, 6000),
    (602, 600, "L1", 6000, 8000),
    (603, 600, "L2", 8000, 10000),
)

_STALE_WINDOW_SPEAKERS = (
    # id, label, name, first_seen_ms: SPEAKER_03 is a speaker left without lines.
    (1, "SPEAKER_00", "Speaker 1", 0),
    (2, "SPEAKER_01", "Speaker 2", 2000),
    (3, "SPEAKER_02", "Speaker 3", 8000),
    (4, "SPEAKER_03", "Speaker 4", 0),
)


async def _seed_recording_with_stale_windows(session_maker: sessionmaker) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (
                    1, :now, :now, 'May meeting', 'stale-rec', 'meeting-uid-stale',
                    '/tmp/stale.wav', 'PROCESSED', 0, 100, 'unified', 0, 0, 1
                )
                """
            ),
            {"now": _STALE_WINDOW_NOW},
        )
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    notes, meeting_edge_status, notes_status, transcript_status
                ) VALUES (
                    1, :now, :now, 1, '', :segments,
                    NULL, 'idle', 'pending', 'completed'
                )
                """
            ),
            {
                "now": _STALE_WINDOW_NOW,
                "segments": json.dumps(
                    [
                        {
                            "id": line_id,
                            "start": start,
                            "end": start + 2.0,
                            "speaker": speaker,
                            "text": line,
                            "segment_source": "legacy",
                        }
                        for line_id, start, speaker, line in _STALE_WINDOW_LINES
                    ]
                ),
            },
        )
        for speaker_id, label, name, first_seen_ms in _STALE_WINDOW_SPEAKERS:
            await session.execute(
                text(
                    """
                    INSERT INTO recording_speakers (
                        id, created_at, updated_at, public_id, recording_id,
                        global_speaker_id, diarization_label, local_name, name,
                        embedding, merged_into_id, speaker_status, speaker_kind,
                        first_seen_ms, last_seen_ms, identity_confidence, identity_locked
                    ) VALUES (
                        :id, :now, :now, :public_id, 1,
                        NULL, :label, NULL, :name,
                        NULL, NULL, 'active', 'automated',
                        :first_seen_ms, NULL, NULL, 0
                    )
                    """
                ),
                {
                    "id": speaker_id,
                    "now": _STALE_WINDOW_NOW,
                    "public_id": f"stale-speaker-{speaker_id}",
                    "label": label,
                    "name": name,
                    "first_seen_ms": first_seen_ms,
                },
            )
        await session.execute(
            text(
                """
                INSERT INTO global_speakers (
                    id, created_at, updated_at, user_id, name, embedding,
                    is_voiceprint_locked, color, description
                ) VALUES (7, :now, :now, 1, 'Alice Smith', NULL, 0, NULL, NULL)
                """
            ),
            {"now": _STALE_WINDOW_NOW},
        )
        for window_id, (start_ms, end_ms) in _STALE_WINDOWS.items():
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
                        :id, :now, :now, :public_id, 1,
                        NULL, :id, :start_ms, :end_ms, 1, 5,
                        'pyannote/speaker-diarization-community-1', 'community-1',
                        'cpu', 'stale-cfg', 'completed', '{}'
                    )
                    """
                ),
                {
                    "id": window_id,
                    "now": _STALE_WINDOW_NOW,
                    "public_id": f"stale-window-{window_id}",
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                },
            )
        for turn_id, window_id, key, start_ms, end_ms in _STALE_WINDOW_TURNS:
            await session.execute(
                text(
                    """
                    INSERT INTO diarization_window_turns (
                        id, created_at, updated_at, window_result_id,
                        local_speaker_key, start_ms, end_ms, confidence,
                        matched_recording_speaker_id, metadata_payload
                    ) VALUES (
                        :id, :now, :now, :window_id, :key, :start_ms, :end_ms,
                        NULL, NULL, NULL
                    )
                    """
                ),
                {
                    "id": turn_id,
                    "now": _STALE_WINDOW_NOW,
                    "window_id": window_id,
                    "key": key,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                },
            )
        await session.commit()


async def _stale_window_line_state(
    session_maker: sessionmaker,
) -> dict[str, tuple[str, str, int | None]]:
    """Every utterance's speaker label, assignment source and last window id.

    Keyed by public id over all rows, so a line the replay split or superseded
    shows up as a changed key instead of hiding behind another row's text.
    """
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT public_id, speaker_label, speaker_assignment_source, "
                "last_diarization_window_result_id FROM transcript_utterances "
                "WHERE recording_id = 1"
            )
        )
        return {row[0]: (row[1], row[2], row[3]) for row in rows.all()}


async def _seed_and_backfill_stale_window_recording(
    client: AsyncClient, session_maker: sessionmaker
) -> dict[str, tuple[str, str, int | None]]:
    """Seed the recording, backfill it through a read, and return its state."""
    await _seed_recording_with_stale_windows(session_maker)
    response = await client.get("/api/v1/transcripts/stale-rec/utterances")
    assert response.status_code == 200
    before = await _stale_window_line_state(session_maker)
    assert {line_id: state[0] for line_id, state in before.items()} == {
        line_id: speaker for line_id, _, speaker, _ in _STALE_WINDOW_LINES
    }
    assert all(state[2] is None for state in before.values())
    return before


@pytest.mark.anyio
@pytest.mark.usefixtures("stub_celery_dispatch")
@pytest.mark.parametrize(
    ("scope", "anchor", "renamed_lines"),
    [
        ("speaker_everywhere_in_recording", "sw-3", ("sw-1", "sw-3")),
        ("from_this_utterance_forward", "sw-1", ("sw-1", "sw-3")),
        ("from_this_utterance_forward", "sw-3", ("sw-3",)),
    ],
)
async def test_utterance_rename_does_not_replay_stale_window_rows(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    scope: str,
    anchor: str,
    renamed_lines: tuple[str, ...],
) -> None:
    before = await _seed_and_backfill_stale_window_recording(client, test_session_maker)

    response = await client.patch(
        f"/api/v1/transcripts/stale-rec/utterances/{anchor}/speaker",
        json={"new_speaker_name": "Alice", "scope": scope},
    )
    assert response.status_code == 200, response.text

    after = await _stale_window_line_state(test_session_maker)
    renamed = after[anchor][0]
    assert renamed != "SPEAKER_00"
    assert after == {
        **before,
        **{line_id: (renamed, "manual", None) for line_id in renamed_lines},
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("stub_celery_dispatch")
async def test_speaker_merge_does_not_replay_stale_window_rows(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    before = await _seed_and_backfill_stale_window_recording(client, test_session_maker)

    response = await client.post(
        "/api/v1/speakers/recordings/stale-rec/merge",
        json={
            "source_speaker_label": "SPEAKER_00",
            "target_speaker_label": "SPEAKER_02",
        },
    )
    assert response.status_code == 200, response.text

    assert await _stale_window_line_state(test_session_maker) == {
        **before,
        "sw-1": ("SPEAKER_02", "manual", None),
        "sw-3": ("SPEAKER_02", "manual", None),
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("stub_celery_dispatch")
async def test_merging_a_speaker_without_lines_does_not_replay_stale_window_rows(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    before = await _seed_and_backfill_stale_window_recording(client, test_session_maker)

    response = await client.post(
        "/api/v1/speakers/recordings/stale-rec/merge",
        json={
            "source_speaker_label": "SPEAKER_03",
            "target_speaker_label": "SPEAKER_00",
        },
    )
    assert response.status_code == 200, response.text

    assert await _stale_window_line_state(test_session_maker) == before


@pytest.mark.anyio
@pytest.mark.usefixtures("stub_celery_dispatch")
@pytest.mark.parametrize("action", ["link", "link_with_shared_global", "promote"])
async def test_global_speaker_identity_change_does_not_replay_stale_window_rows(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    action: str,
) -> None:
    before = await _seed_and_backfill_stale_window_recording(client, test_session_maker)

    if action == "link_with_shared_global":
        # SPEAKER_01 already belongs to the global speaker SPEAKER_00 is about
        # to join (the diariser split one voice, say). That puts both in the
        # identity guard's cluster, which let the replay move lines between them.
        async with test_session_maker() as session:
            await session.execute(
                text("UPDATE recording_speakers SET global_speaker_id = 7 WHERE id = 2")
            )
            await session.commit()
    if action == "promote":
        async with test_session_maker() as session:
            await session.execute(
                text("UPDATE recording_speakers SET local_name = 'Alice' WHERE id = 1")
            )
            await session.commit()
        response = await client.post(
            "/api/v1/speakers/recordings/stale-rec/speakers/SPEAKER_00/promote"
        )
    else:
        response = await client.put(
            "/api/v1/speakers/recordings/stale-rec",
            json={
                "diarization_label": "SPEAKER_00",
                "global_speaker_name": "Alice Smith",
            },
        )
    assert response.status_code == 200, response.text

    # An identity change says who a speaker is, not which lines are theirs.
    assert await _stale_window_line_state(test_session_maker) == before


@pytest.mark.anyio
async def test_serialized_utterances_expose_the_capture_source_channel(
    test_session_maker: sessionmaker,
) -> None:
    """Utterance reads carry the live lane's channel reading, when it was clear.

    The live lane persists its channel-dominance evidence inside
    ``confidence_payload``; the serializer lifts it to a flat ``source_channel``
    so the live transcript panel can label audio provenance without the client
    having to understand the evidence shape. Anything short of CLEAR authority
    must serialize as ``None`` so the panel renders no label at all.
    """
    from backend.utils.canonical_pipeline import (
        append_utterances_from_segments,
        serialize_canonical_utterances,
    )

    await _seed_uploading_recording(test_session_maker)

    def _evidence(authority: str, dominant_source: str | None) -> dict:
        return {
            "source_channel_evidence": {
                "authority": authority,
                "dominant_source": dominant_source,
            }
        }

    def append_live_utterances(sync_session):
        append_utterances_from_segments(
            sync_session,
            recording_id=1,
            segments=[
                {
                    "id": "live-utt-system",
                    "start": 0.0,
                    "end": 1.0,
                    "speaker": "UNKNOWN",
                    "text": "from the shared tab",
                    "provisional": True,
                    "segment_source": "live",
                    "confidence_payload": _evidence("clear", "system"),
                },
                {
                    "id": "live-utt-mic",
                    "start": 1.0,
                    "end": 2.0,
                    "speaker": "UNKNOWN",
                    "text": "from the microphone",
                    "provisional": True,
                    "segment_source": "live",
                    "confidence_payload": _evidence("clear", "microphone"),
                },
                {
                    "id": "live-utt-overlap",
                    "start": 2.0,
                    "end": 3.0,
                    "speaker": "UNKNOWN",
                    "text": "both at once",
                    "provisional": True,
                    "segment_source": "live",
                    "confidence_payload": _evidence("overlap", "microphone"),
                },
                {
                    "id": "live-utt-bare",
                    "start": 3.0,
                    "end": 4.0,
                    "speaker": "UNKNOWN",
                    "text": "no evidence at all",
                    "provisional": True,
                    "segment_source": "live",
                },
            ],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
            state_override=TranscriptUtteranceState.PROVISIONAL,
            trigger_source="test",
        )
        return serialize_canonical_utterances(sync_session, 1)

    async with test_session_maker() as session:
        serialized = await session.run_sync(append_live_utterances)
        await session.commit()

    by_id = {payload["id"]: payload for payload in serialized}

    assert by_id["live-utt-system"]["source_channel"] == "system"
    assert by_id["live-utt-mic"]["source_channel"] == "microphone"
    assert by_id["live-utt-overlap"]["source_channel"] is None
    assert by_id["live-utt-bare"]["source_channel"] is None

    # Live utterances carry no speaker: diarization only runs at finalize.
    assert {payload["speaker"] for payload in serialized} == {"UNKNOWN"}
    assert all(payload["provisional"] is True for payload in serialized)


@pytest.mark.anyio
async def test_speaker_rename_propagates_into_generated_notes(
    test_session_maker: sessionmaker,
) -> None:
    """A rename carries into the notes, which store names rather than labels.

    The transcript resolves a display name from a label at read time, so it
    updates for free. Notes are prose written once, and without this they keep
    calling someone by a name nothing else in the recording uses.
    """
    from backend.utils.canonical_pipeline.notes_propagation import (
        propagate_speaker_rename_to_notes,
    )

    await _seed_processed_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE transcripts SET notes = :notes WHERE recording_id = 1"),
            {
                "notes": (
                    "## Summary\n\nSPEAKER_00 opened the review. "
                    "Later SPEAKER_00 agreed to send the figures."
                )
            },
        )
        await session.commit()

    async with test_session_maker() as session:
        replaced = await session.run_sync(
            lambda s: propagate_speaker_rename_to_notes(
                s,
                recording_id=1,
                old_names=["SPEAKER_00"],
                new_name="Dana Whitfield",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        notes = (
            await session.execute(
                text("SELECT notes FROM transcripts WHERE recording_id = 1")
            )
        ).scalar_one()

    assert replaced == 2
    assert "SPEAKER_00" not in notes
    assert notes.count("Dana Whitfield") == 2


@pytest.mark.anyio
async def test_speaker_rename_notes_replacement_respects_word_boundaries(
    test_session_maker: sessionmaker,
) -> None:
    """Substitution is anchored, so a short name cannot maul a longer word.

    This is the guard on a known-sharp tool: replacing generated prose by
    string match is only defensible if it cannot rewrite text that merely
    contains the name.
    """
    from backend.utils.canonical_pipeline.notes_propagation import (
        propagate_speaker_rename_to_notes,
    )

    await _seed_processed_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE transcripts SET notes = :notes WHERE recording_id = 1"),
            {"notes": "Matt raised it. Matthew disagreed, as a matter of record."},
        )
        await session.commit()

    async with test_session_maker() as session:
        replaced = await session.run_sync(
            lambda s: propagate_speaker_rename_to_notes(
                s,
                recording_id=1,
                old_names=["Matt"],
                new_name="Mateusz",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        notes = (
            await session.execute(
                text("SELECT notes FROM transcripts WHERE recording_id = 1")
            )
        ).scalar_one()

    assert replaced == 1
    assert notes == "Mateusz raised it. Matthew disagreed, as a matter of record."


@pytest.mark.anyio
async def test_speaker_rename_leaves_user_notes_alone(
    test_session_maker: sessionmaker,
) -> None:
    """Only the generated notes are rewritten.

    The timeline notes, and the ``## User Notes`` section that quotes them at
    the end of the generated notes, are the user's own writing; editing those
    without being asked is a different act from updating text a model produced.
    """
    from backend.utils.canonical_pipeline.notes_propagation import (
        propagate_speaker_rename_to_notes,
    )

    await _seed_processed_recording(test_session_maker)

    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE transcripts SET notes = :notes WHERE recording_id = 1"),
            {
                "notes": (
                    "SPEAKER_00 presented.\n\n## User Notes\n"
                    "- [User] Ask SPEAKER_00 about the timeline."
                ),
            },
        )
        await session.execute(
            text(
                "INSERT INTO timeline_notes (id, created_at, updated_at, public_id, "
                "recording_id, kind, body, revision) VALUES (1, :now, :now, 'n-1', "
                "1, 'head', 'Ask SPEAKER_00 about the timeline.', 1)"
            ),
            {"now": "2026-05-19 00:00:00"},
        )
        await session.commit()

    async with test_session_maker() as session:
        await session.run_sync(
            lambda s: propagate_speaker_rename_to_notes(
                s,
                recording_id=1,
                old_names=["SPEAKER_00"],
                new_name="Dana",
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        notes = (
            await session.execute(
                text("SELECT notes FROM transcripts WHERE recording_id = 1")
            )
        ).scalar_one()
        note_body = (
            await session.execute(text("SELECT body FROM timeline_notes WHERE id = 1"))
        ).scalar_one()

    assert notes == (
        "Dana presented.\n\n## User Notes\n- [User] Ask SPEAKER_00 about the timeline."
    )
    assert note_body == "Ask SPEAKER_00 about the timeline."


# --- undoing an automatic merge ---------------------------------------------


async def _seed_pipeline_merged_cluster(session_maker: sessionmaker) -> None:
    """SPEAKER_01 merged into SPEAKER_00 by processing.

    Its lines sit on SPEAKER_00 but still carry SPEAKER_01 as their
    diarisation label. One of them was then assigned by hand.
    """
    from backend.utils.canonical_pipeline import append_utterances_from_segments

    await _seed_processed_recording(session_maker)
    async with session_maker() as session:
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
                    NULL, 'SPEAKER_01', NULL, 'Speaker 2',
                    '[0.1, 0.2]', 1, 'merged', 'automated',
                    NULL, NULL, NULL, 0
                )
                """
            ),
            {"now": "2026-05-19 00:00:00"},
        )
        await session.run_sync(
            lambda sync_session: append_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "id": "utt-own",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "SPEAKER_00",
                        "diarization_label": "SPEAKER_00",
                        "text": "mine",
                        "segment_source": "finalize",
                    },
                    {
                        "id": "utt-theirs",
                        "start": 1.0,
                        "end": 2.0,
                        "speaker": "SPEAKER_00",
                        "diarization_label": "SPEAKER_01",
                        "text": "theirs",
                        "segment_source": "finalize",
                    },
                    {
                        "id": "utt-hand-set",
                        "start": 2.0,
                        "end": 3.0,
                        "speaker": "SPEAKER_00",
                        "diarization_label": "SPEAKER_01",
                        "text": "assigned by hand",
                        "segment_source": "finalize",
                    },
                ],
                run_kind=ProcessingRunKind.FINALIZE,
                source="finalize",
                state_override=TranscriptUtteranceState.FINALIZED,
                trigger_source="test",
            )
        )
        await session.execute(
            text(
                "UPDATE transcript_utterances SET manual_speaker_locked = 1 "
                "WHERE public_id = 'utt-hand-set'"
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_merged_speakers_listing_counts_the_lines_an_unmerge_restores(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_pipeline_merged_cluster(test_session_maker)

    response = await client.get("/api/v1/speakers/recordings/canon-rec/merged-speakers")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 2,
            "public_id": "speaker-public-2",
            "diarization_label": "SPEAKER_01",
            "name": "Speaker 2",
            "merged_into_id": 1,
            "merged_into_label": "SPEAKER_00",
            # The hand-set line is not counted: it stays where it was put.
            "restorable_utterance_count": 1,
            "has_voiceprint": True,
        }
    ]


@pytest.mark.anyio
async def test_unmerge_gives_a_merged_cluster_back_its_lines(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_pipeline_merged_cluster(test_session_maker)

    response = await client.post(
        "/api/v1/speakers/recordings/canon-rec/speakers/SPEAKER_01/unmerge"
    )
    assert response.status_code == 200

    utterances = (await client.get("/api/v1/transcripts/canon-rec/utterances")).json()[
        "utterances"
    ]
    by_id = {utterance["id"]: utterance for utterance in utterances}
    assert by_id["utt-own"]["speaker"] == "SPEAKER_00"
    assert by_id["utt-theirs"]["speaker"] == "SPEAKER_01"
    assert by_id["utt-theirs"]["recording_speaker_id"] == 2
    assert by_id["utt-theirs"]["diarization_label"] == "SPEAKER_01"
    # Restored lines keep their pipeline provenance; nobody assigned them.
    assert by_id["utt-theirs"]["speaker_manually_edited"] is False
    assert by_id["utt-hand-set"]["speaker"] == "SPEAKER_00"
    assert by_id["utt-hand-set"]["speaker_manually_edited"] is True

    async with test_session_maker() as session:
        restored = (
            await session.execute(
                text(
                    "SELECT merged_into_id, speaker_status, name, local_name, "
                    "global_speaker_id, first_seen_ms, last_seen_ms "
                    "FROM recording_speakers WHERE id = 2"
                )
            )
        ).one()
        assert restored[0] is None
        assert restored[1] == "active"
        assert restored[2] == "Speaker 2"
        assert restored[3] is None
        assert restored[4] is None
        assert (restored[5], restored[6]) == (1000, 2000)
        event = (
            await session.execute(
                text(
                    "SELECT event_type, scope, source_recording_speaker_id, "
                    "target_recording_speaker_id, payload "
                    "FROM speaker_correction_events ORDER BY id DESC LIMIT 1"
                )
            )
        ).one()
        assert event[0].lower() == "unmerge_speakers"
        assert event[1].lower() == "speaker_everywhere_in_recording"
        assert (event[2], event[3]) == (1, 2)
        payload = json.loads(event[4]) if isinstance(event[4], str) else event[4]
        assert payload["utterance_count"] == 1
        assert payload["unmerged_from_label"] == "SPEAKER_00"
        transcript = (
            await session.execute(
                select(Transcript).where(Transcript.recording_id == 1)
            )
        ).scalar_one()
        projection = {
            segment.get("id"): segment for segment in transcript.segments or []
        }
        assert projection["utt-theirs"]["speaker"] == "SPEAKER_01"
        assert projection["utt-theirs"]["recording_speaker_id"] == 2

    # Nothing is left to undo, and the listing says so.
    listing = await client.get("/api/v1/speakers/recordings/canon-rec/merged-speakers")
    assert listing.json() == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        # A fold left no voiceprint on the merged row and 10 s added to the
        # survivor's 12 s; undoing it takes the 10 s back out.
        (None, 10.0, 22.0, 12.0, None),
        # Processing merges fold nothing: the row keeps its voiceprint and its
        # own figure, and the survivor's figure was never added to.
        ("[0.1, 0.2]", 8.0, 22.0, 22.0, 8.0),
        # An unmeasured survivor stays unmeasured.
        (None, 10.0, None, None, None),
    ],
)
async def test_unmerge_takes_the_merged_speech_back_out_of_the_survivors_figure(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    case: tuple[str | None, float, float | None, float | None, float | None],
) -> None:
    merged_embedding, added, survivor_before, survivor_after, kept = case
    await _seed_pipeline_merged_cluster(test_session_maker)
    async with test_session_maker() as session:
        await session.execute(
            text(
                "UPDATE recording_speakers SET embedding = :embedding, "
                "embedding_speech_seconds = :added WHERE id = 2"
            ),
            {"embedding": merged_embedding, "added": added},
        )
        await session.execute(
            text(
                "UPDATE recording_speakers SET embedding_speech_seconds = :seconds "
                "WHERE id = 1"
            ),
            {"seconds": survivor_before},
        )
        await session.commit()

    response = await client.post(
        "/api/v1/speakers/recordings/canon-rec/speakers/SPEAKER_01/unmerge"
    )

    assert response.status_code == 200
    async with test_session_maker() as session:
        seconds = dict(
            (
                await session.execute(
                    text("SELECT id, embedding_speech_seconds FROM recording_speakers")
                )
            ).all()
        )
    assert seconds == {1: survivor_after, 2: kept}


@pytest.mark.anyio
async def test_unmerge_refuses_a_speaker_that_is_not_merged(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_pipeline_merged_cluster(test_session_maker)

    not_merged = await client.post(
        "/api/v1/speakers/recordings/canon-rec/speakers/SPEAKER_00/unmerge"
    )
    assert not_merged.status_code == 409

    missing = await client.post(
        "/api/v1/speakers/recordings/canon-rec/speakers/SPEAKER_09/unmerge"
    )
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_finalize_keeps_the_diarisation_label_on_each_line(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    """The label survives the merge pass rewriting ``speaker`` to the survivor."""
    from backend.utils.canonical_pipeline import finalize_utterances_from_segments

    await _seed_processed_recording(test_session_maker)
    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync_session: finalize_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "SPEAKER_00",
                        "diarization_label": "SPEAKER_03",
                        "text": "hello there",
                    },
                    {
                        "start": 1.0,
                        "end": 2.0,
                        "speaker": "UNKNOWN",
                        "text": "and more",
                    },
                ],
                trigger_source="test",
            )
        )
        await session.commit()

    utterances = (await client.get("/api/v1/transcripts/canon-rec/utterances")).json()[
        "utterances"
    ]
    assert [u["diarization_label"] for u in utterances] == ["SPEAKER_03", None]
    assert [u["speaker"] for u in utterances] == ["SPEAKER_00", "UNKNOWN"]


# --- overlap labels after a merge -------------------------------------------


async def _seed_overlaps_on_a_speaker_with_one_line(
    session_maker: sessionmaker,
) -> None:
    """SPEAKER_01 has one line, and two other lines name it as overlapping.

    Neither overlaps that line in time: one starts where it ends, and the other
    comes from a diarisation turn that produced no words.
    """
    from backend.utils.canonical_pipeline import finalize_utterances_from_segments

    await _seed_processed_recording(session_maker)
    async with session_maker() as session:
        for pk, label in ((2, "SPEAKER_01"), (3, "SPEAKER_02")):
            await session.execute(
                text(
                    """
                    INSERT INTO recording_speakers (
                        id, created_at, updated_at, public_id, recording_id,
                        global_speaker_id, diarization_label, local_name, name,
                        embedding, merged_into_id, speaker_status, speaker_kind,
                        first_seen_ms, last_seen_ms, identity_confidence,
                        identity_locked
                    ) VALUES (
                        :pk, :now, :now, :public_id, 1,
                        NULL, :label, NULL, :name,
                        NULL, NULL, 'active', 'automated',
                        NULL, NULL, NULL, 0
                    )
                    """
                ),
                {
                    "pk": pk,
                    "now": "2026-05-19 00:00:00",
                    "public_id": f"speaker-public-{pk}",
                    "label": label,
                    "name": f"Speaker {pk}",
                },
            )

        def line(public_id, start, end, label, overlapping=()):
            return {
                "id": public_id,
                "start": start,
                "end": end,
                "speaker": label,
                "diarization_label": label,
                "overlapping_speakers": list(overlapping),
                "text": public_id,
                "segment_source": "finalize",
            }

        await session.run_sync(
            lambda sync_session: finalize_utterances_from_segments(
                sync_session,
                recording_id=1,
                segments=[
                    line("utt-first", 0.0, 1.0, "SPEAKER_00"),
                    line("utt-brief", 2.0, 3.0, "SPEAKER_01"),
                    line("utt-after", 3.0, 4.0, "SPEAKER_00", ["SPEAKER_01"]),
                    line("utt-far", 10.0, 12.0, "SPEAKER_02", ["SPEAKER_01"]),
                ],
                trigger_source="test",
            )
        )
        await session.commit()


async def _overlaps_by_line(
    client: AsyncClient, key: str = "id"
) -> dict[str, list[str]]:
    body = (await client.get("/api/v1/transcripts/canon-rec/utterances")).json()
    return {u[key]: u["overlapping_speakers"] for u in body["utterances"]}


async def _stored_overlaps_by_text(session_maker: sessionmaker) -> dict[str, list]:
    async with session_maker() as session:
        transcript = (
            await session.execute(
                select(Transcript).where(Transcript.recording_id == 1)
            )
        ).scalar_one()
        return {
            segment["text"]: segment.get("overlapping_speakers")
            for segment in transcript.segments or []
        }


@pytest.mark.anyio
async def test_a_merged_speaker_never_shows_as_an_overlapping_speaker(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_overlaps_on_a_speaker_with_one_line(test_session_maker)

    response = await client.post(
        "/api/v1/speakers/recordings/canon-rec/merge",
        json={
            "source_speaker_label": "SPEAKER_01",
            "target_speaker_label": "SPEAKER_00",
        },
    )
    assert response.status_code == 200

    # Finalising assigns its own ids, so each line is known by its text.
    overlaps = await _overlaps_by_line(client, key="text")
    # utt-after belongs to the speaker SPEAKER_01 joined, so it overlaps nobody.
    assert overlaps["utt-after"] == []
    assert overlaps["utt-far"] == ["SPEAKER_00"]
    # The projection keeps the label as diarised, for an unmerge.
    assert (await _stored_overlaps_by_text(test_session_maker))["utt-far"] == [
        "SPEAKER_01"
    ]


@pytest.mark.anyio
async def test_an_unmerge_brings_a_merged_speakers_overlap_labels_back(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_pipeline_merged_cluster(test_session_maker)
    # utt-own still names the merged cluster as overlapping it, as a line does
    # once a merge has left it out of the time-overlap rewrite.
    async with test_session_maker() as session:
        transcript = (
            await session.execute(
                select(Transcript).where(Transcript.recording_id == 1)
            )
        ).scalar_one()
        transcript.segments = [
            {**segment, "overlapping_speakers": ["SPEAKER_01"]}
            if segment.get("id") == "utt-own"
            else segment
            for segment in transcript.segments
        ]
        await session.commit()

    assert (await _overlaps_by_line(client))["utt-own"] == []

    response = await client.post(
        "/api/v1/speakers/recordings/canon-rec/speakers/SPEAKER_01/unmerge"
    )
    assert response.status_code == 200

    assert (await _overlaps_by_line(client))["utt-own"] == ["SPEAKER_01"]
