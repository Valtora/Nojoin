from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

import backend.utils.llm_config as llm_config_module
import backend.worker.tasks as tasks_module


BASE_SCHEMA = """
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


def _create_infer_speakers_task_database(
    tmp_path: Path,
    *,
    owner_settings: dict[str, Any],
    transcript_segments: list[dict[str, Any]] | None = None,
) -> Any:
    db_path = tmp_path / "infer-speakers-task.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    now = _utc_now_naive()
    segments = transcript_segments or [
        {"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00", "text": "Hello team."},
        {"start": 1.5, "end": 4.0, "speaker": "SPEAKER_01", "text": "The rollout is on Friday."},
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
                    1024, 'PROCESSING', NULL, 100, 100, 'Inferring speakers...',
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
                    1, :now, :now, 1, 'Transcript text', :segments,
                    NULL, 'Remember the launch date', 'completed', 'completed', NULL
                )
                """
            ),
            {"now": now, "segments": json.dumps(segments)},
        )
        connection.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, recording_id, global_speaker_id,
                    diarization_label, local_name, name, snippet_start,
                    snippet_end, voice_snippet_path, embedding, color,
                    merged_into_id
                ) VALUES (
                    1, :now, :now, 1, NULL, 'SPEAKER_00', NULL, 'Speaker 1',
                    NULL, NULL, NULL, NULL, NULL, NULL
                )
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, recording_id, global_speaker_id,
                    diarization_label, local_name, name, snippet_start,
                    snippet_end, voice_snippet_path, embedding, color,
                    merged_into_id
                ) VALUES (
                    2, :now, :now, 1, NULL, 'SPEAKER_01', NULL, 'Dana',
                    NULL, NULL, NULL, NULL, NULL, NULL
                )
                """
            ),
            {"now": now},
        )

    return engine


def _run_infer_speakers_task(engine: Any) -> None:
    tasks_module.infer_speakers_task._session = None
    try:
        tasks_module.infer_speakers_task.run(1)
    finally:
        if tasks_module.infer_speakers_task._session is not None:
            tasks_module.infer_speakers_task._session.close()
        tasks_module.infer_speakers_task._session = None
        engine.dispose()


def test_infer_speakers_task_updates_speakers_and_restores_recording_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _create_infer_speakers_task_database(
        tmp_path,
        owner_settings={
            "llm_provider": "openai",
            "openai_api_key": "sk-openai-valid",
            "openai_model": "gpt-test",
        },
    )
    captured: dict[str, Any] = {}

    class FakeLLM:
        def infer_speakers(
            self,
            transcript: str,
            prompt_template: str | None = None,
            timeout: int = 60,
            user_notes: str | None = None,
        ) -> dict[str, str]:
            captured["transcript"] = transcript
            captured["timeout"] = timeout
            captured["user_notes"] = user_notes
            return {"SPEAKER_00": "Alex", "SPEAKER_01": "Dana"}

    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(tasks_module.config_manager, "reload", lambda: None)
    monkeypatch.setattr(llm_config_module.config_manager, "get_all", lambda: {})
    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda config: FakeLLM())

    verification_engine = create_engine(str(engine.url), future=True)
    try:
        _run_infer_speakers_task(engine)

        with Session(verification_engine) as session:
            row = session.exec(
                text(
                    "SELECT status, processing_step, processing_progress, name FROM recordings WHERE id = 1"
                )
            ).one()
            speaker_rows = session.exec(
                text(
                    "SELECT diarization_label, name FROM recording_speakers WHERE recording_id = 1 ORDER BY diarization_label"
                )
            ).all()

        assert row[0] == "PROCESSED"
        assert row[1] == "Completed"
        assert row[2] == 100
        assert dict(speaker_rows) == {
            "SPEAKER_00": "Alex",
            "SPEAKER_01": "Dana",
        }
        assert "SPEAKER_00 - Hello team." in captured["transcript"]
        assert "SPEAKER_01 - The rollout is on Friday." in captured["transcript"]
        assert captured["user_notes"] == "Remember the launch date"
        assert captured["timeout"] == 60
    finally:
        verification_engine.dispose()


def test_infer_speakers_task_skips_without_complete_llm_configuration_and_restores_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _create_infer_speakers_task_database(
        tmp_path,
        owner_settings={
            "llm_provider": "openai",
            "openai_api_key": "sk-openai-valid",
        },
    )

    def fail_if_called(_config):
        raise AssertionError("LLM backend should not be created when model configuration is missing")

    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(tasks_module.config_manager, "reload", lambda: None)
    monkeypatch.setattr(llm_config_module.config_manager, "get_all", lambda: {})
    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", fail_if_called)

    verification_engine = create_engine(str(engine.url), future=True)
    try:
        _run_infer_speakers_task(engine)

        with Session(verification_engine) as session:
            recording_row = session.exec(
                text(
                    "SELECT status, processing_step FROM recordings WHERE id = 1"
                )
            ).one()
            speaker_rows = session.exec(
                text(
                    "SELECT diarization_label, name FROM recording_speakers WHERE recording_id = 1 ORDER BY diarization_label"
                )
            ).all()

        assert recording_row[0] == "PROCESSED"
        assert recording_row[1] == "Completed"
        assert dict(speaker_rows) == {
            "SPEAKER_00": "Speaker 1",
            "SPEAKER_01": "Dana",
        }
    finally:
        verification_engine.dispose()