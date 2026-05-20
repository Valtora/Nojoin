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
    segment_id: str | None = None,
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    is_archived, is_deleted, user_id
                ) VALUES (
                    1, :now, :now, 'Canonical meeting', :public_id, 'meeting-uid-1',
                    '/tmp/canon.wav', 'PROCESSED', 0, 100, 0, 0, 1
                )
                """
            ),
            {"now": "2026-05-19 00:00:00", "public_id": public_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    notes, user_notes, meeting_edge_status, notes_status,
                    transcript_status
                ) VALUES (
                    1, :now, :now, 1, 'hello there', :segments,
                    NULL, NULL, 'idle', 'pending', 'completed'
                )
                """
            ),
            {
                "now": "2026-05-19 00:00:00",
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
                    1, :now, :now, 'speaker-public-1', 1,
                    NULL, 'SPEAKER_00', NULL, 'Speaker 1',
                    NULL, NULL, 'active', 'automated',
                    NULL, NULL, NULL, 0
                )
                """
            ),
            {"now": "2026-05-19 00:00:00"},
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
                    is_archived, is_deleted, user_id
                ) VALUES (
                    1, :now, :now, 'Live meeting', :public_id, 'meeting-uid-live',
                    '/tmp/live.wav', 'UPLOADING', 50, 10, 0, 0, 1
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
        assert len(transcript_segments) == 1
        assert transcript_segments[0]["id"] == "live-utt-1"


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