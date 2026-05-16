from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

import backend.worker.tasks as tasks_module


BASE_SCHEMA = """
CREATE TABLE invitations (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    code VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    created_by_id INTEGER,
    expires_at DATETIME,
    max_uses INTEGER,
    used_count INTEGER NOT NULL,
    is_revoked BOOLEAN NOT NULL
);

CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    username VARCHAR NOT NULL,
    hashed_password VARCHAR NOT NULL,
    is_active BOOLEAN NOT NULL,
    is_superuser BOOLEAN NOT NULL,
    force_password_change BOOLEAN NOT NULL,
    role VARCHAR NOT NULL,
    token_version INTEGER NOT NULL,
    settings JSON,
    has_seen_demo_recording BOOLEAN NOT NULL,
    invitation_id INTEGER
);

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
    user_id INTEGER
);

CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL UNIQUE,
    text TEXT,
    segments JSON NOT NULL,
    notes TEXT,
    user_notes TEXT,
    notes_status VARCHAR NOT NULL,
    transcript_status VARCHAR NOT NULL,
    error_message TEXT
);

CREATE TABLE recording_speakers (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL,
    global_speaker_id INTEGER,
    diarization_label VARCHAR NOT NULL,
    local_name VARCHAR,
    name VARCHAR,
    snippet_start FLOAT,
    snippet_end FLOAT,
    voice_snippet_path VARCHAR,
    embedding JSON,
    color VARCHAR,
    merged_into_id INTEGER
);
"""


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _create_notes_task_database(
    tmp_path: Path,
    *,
    owner_settings: dict[str, Any],
    transcript_segments: list[dict[str, Any]] | None = None,
) -> Any:
    db_path = tmp_path / "notes-task.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    now = _utc_now_naive()
    segments = transcript_segments or [
        {"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00", "text": "Hello team."}
    ]

    with engine.begin() as connection:
        for statement in BASE_SCHEMA.strip().split(";\n\n"):
            connection.execute(text(statement))

        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, created_at, updated_at, username, hashed_password,
                    is_active, is_superuser, force_password_change, role,
                    token_version, settings, has_seen_demo_recording,
                    invitation_id
                ) VALUES (
                    1, :now, :now, 'owner', 'hash', 1, 1, 0, 'owner',
                    0, :settings, 0, NULL
                )
                """
            ),
            {"now": now, "settings": json.dumps(owner_settings)},
        )
        connection.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, proxy_path, celery_task_id, duration_seconds,
                    file_size_bytes, status, client_status, upload_progress,
                    processing_progress, processing_step, processing_started_at,
                    processing_completed_at, is_archived, is_deleted, user_id
                ) VALUES (
                    1, :now, :now, 'Planning', 'public-recording-1',
                    'meeting-uid-1', '/tmp/audio.wav', NULL, NULL, 10.0,
                    1024, 'PROCESSED', NULL, 100, 100, 'Completed',
                    NULL, :now, 0, 0, 1
                )
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    notes, user_notes, notes_status, transcript_status,
                    error_message
                ) VALUES (
                    1, :now, :now, 1, 'SPEAKER_00: Hello team.',
                    :segments, 'Existing notes', 'Remember the launch date',
                    'pending', 'completed', 'Previous failure'
                )
                """
            ),
            {"now": now, "segments": json.dumps(segments)},
        )

    return engine


def _run_generate_notes_task(engine: Any) -> None:
    tasks_module.generate_notes_task._session = None
    try:
        tasks_module.generate_notes_task.run(1)
    finally:
        if tasks_module.generate_notes_task._session is not None:
            tasks_module.generate_notes_task._session.close()
        tasks_module.generate_notes_task._session = None
        engine.dispose()


def test_generate_notes_task_completes_with_saved_provider_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _create_notes_task_database(
        tmp_path,
        owner_settings={
            "llm_provider": "anthropic",
            "anthropic_api_key": "sk-ant-valid",
            "anthropic_model": "claude-test",
        },
    )
    captured: dict[str, Any] = {}

    class FakeLLM:
        def generate_meeting_notes(
            self,
            transcript: str,
            speaker_mapping: dict[str, str],
            prompt_template: str | None = None,
            timeout: int = 60,
            user_notes: str | None = None,
        ) -> str:
            captured["transcript"] = transcript
            captured["speaker_mapping"] = speaker_mapping
            captured["timeout"] = timeout
            captured["user_notes"] = user_notes
            return "# Meeting Notes\n\n## Summary\nGenerated notes."

    def fake_get_llm_backend(provider: str, api_key=None, model=None, api_url=None):
        captured["provider"] = provider
        captured["api_key"] = api_key
        captured["model"] = model
        captured["api_url"] = api_url
        return FakeLLM()

    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr("backend.processing.llm_services.get_llm_backend", fake_get_llm_backend)

    verification_engine = create_engine(str(engine.url), future=True)
    try:
        _run_generate_notes_task(engine)

        with Session(verification_engine) as session:
            row = session.exec(
                text("SELECT notes, notes_status, error_message, processing_step, processing_progress FROM transcripts JOIN recordings ON transcripts.recording_id = recordings.id WHERE transcripts.id = 1")
            ).one()

        assert row[0] == "# Meeting Notes\n\n## Summary\nGenerated notes."
        assert row[1] == "completed"
        assert row[2] is None
        assert row[3] == "Completed"
        assert row[4] == 100
        assert captured["provider"] == "anthropic"
        assert captured["api_key"] == "sk-ant-valid"
        assert captured["model"] == "claude-test"
        assert captured["timeout"] == 300
        assert captured["user_notes"] == "Remember the launch date"
    finally:
        verification_engine.dispose()


def test_generate_notes_task_marks_missing_model_as_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _create_notes_task_database(
        tmp_path,
        owner_settings={
            "llm_provider": "anthropic",
            "anthropic_api_key": "sk-ant-valid",
        },
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("LLM backend should not be created when the model is missing")

    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr("backend.processing.llm_services.get_llm_backend", fail_if_called)

    verification_engine = create_engine(str(engine.url), future=True)
    try:
        _run_generate_notes_task(engine)

        with Session(verification_engine) as session:
            row = session.exec(
                text("SELECT notes, notes_status, error_message FROM transcripts WHERE id = 1")
            ).one()

        assert row[0] == "Existing notes"
        assert row[1] == "error"
        assert row[2] == "No model selected for anthropic"
    finally:
        verification_engine.dispose()
