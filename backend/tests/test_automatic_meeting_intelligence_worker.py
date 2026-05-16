from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.utils.llm_config import ResolvedLLMConfig
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceResult
import backend.worker.tasks as tasks_module


BASE_SCHEMA = """
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


def _create_worker_ai_database(tmp_path: Path) -> Any:
    db_path = tmp_path / "automatic-ai-worker.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    now = _utc_now_naive()
    segments = [
        {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00", "text": "Opening update."},
        {
            "start": 4.0,
            "end": 8.0,
            "speaker": "SPEAKER_01",
            "text": "Confirmed the rollout date.",
            "overlapping_speakers": ["SPEAKER_00"],
        },
    ]

    with engine.begin() as connection:
        for statement in BASE_SCHEMA.strip().split(";\n\n"):
            connection.execute(text(statement))

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
                    1024, 'PROCESSING', NULL, 100, 85, 'Saving transcript...',
                    NULL, NULL, 0, 0, 1
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
                    NULL, 'Confirm the rollout date', 'pending', 'completed', NULL
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
                    2, :now, :now, 1, 11, 'SPEAKER_01', NULL, 'Dana',
                    NULL, NULL, NULL, NULL, NULL, NULL
                )
                """
            ),
            {"now": now},
        )

    return engine


def _sample_llm_config(*, api_key: str | None = "sk-test", model: str | None = "gpt-test") -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider="openai",
        api_key=api_key,
        model=model,
        api_url=None,
        merged_config={"llm_provider": "openai", "prefer_short_titles": True},
    )


class _FakeTask:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def update_state(self, *, state: str, meta: dict[str, Any]) -> None:
        self.calls.append({"state": state, "meta": meta})


def test_build_automatic_meeting_intelligence_transcript_keeps_unresolved_labels_visible() -> None:
    transcript = tasks_module._build_automatic_meeting_intelligence_transcript(
        [
            {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00", "text": "Opening update."},
            {
                "start": 4.0,
                "end": 8.0,
                "speaker": "SPEAKER_01",
                "text": "Confirmed the rollout date.",
                "overlapping_speakers": ["SPEAKER_00"],
            },
        ],
        {"SPEAKER_00": "Speaker 1", "SPEAKER_01": "Dana"},
        ("SPEAKER_00",),
    )

    assert "SPEAKER_00: Opening update." in transcript
    assert "Dana (with SPEAKER_00): Confirmed the rollout date." in transcript
    assert "Speaker 1" not in transcript


def test_run_automatic_meeting_intelligence_stage_updates_speakers_title_and_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _create_worker_ai_database(tmp_path)
    captured: dict[str, Any] = {}

    class FakeLLM:
        def generate_meeting_intelligence(self, request, timeout: int = 60):
            captured["request"] = request
            captured["timeout"] = timeout
            return AutomaticMeetingIntelligenceResult(
                speaker_mapping={"SPEAKER_00": "Alex"},
                title="Launch Readiness Review",
                notes_markdown="# Meeting Notes\n\n## Summary\nGenerated notes.\n\n## User Notes\n- [User] Confirm the rollout date",
            )

    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda config: FakeLLM())
    task = _FakeTask()

    try:
        with Session(engine) as session:
            recording = session.get(Recording, 1)
            transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()
            speakers = session.exec(
                select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
            ).all()

            result = tasks_module._run_automatic_meeting_intelligence_stage(
                session=session,
                task=task,
                recording=recording,
                transcript=transcript,
                speakers=speakers,
                transcript_text="[00:00:00 - 00:00:04] SPEAKER_00: Opening update.\n[00:00:04 - 00:00:08] Dana (with SPEAKER_00): Confirmed the rollout date.",
                unresolved_speakers=("SPEAKER_00",),
                llm_config=_sample_llm_config(),
                prefer_short_titles=False,
                device_suffix=" (CPU)",
            )

        with Session(engine) as verification_session:
            recording = verification_session.get(Recording, 1)
            transcript = verification_session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()
            speakers = verification_session.exec(
                select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
            ).all()
            names_by_label = {speaker.diarization_label: speaker.name for speaker in speakers}

        assert result is not None
        assert names_by_label["SPEAKER_00"] == "Alex"
        assert names_by_label["SPEAKER_01"] == "Dana"
        assert recording.name == "Launch Readiness Review"
        assert recording.status == "PROCESSED"
        assert recording.processing_step == "Generating meeting notes... (CPU)"
        assert recording.processing_progress == 97
        assert transcript.notes_status == "completed"
        assert transcript.error_message is None
        assert transcript.notes == "# Meeting Notes\n\n## Summary\nGenerated notes.\n\n## User Notes\n- [User] Confirm the rollout date"
        assert captured["request"].unresolved_speakers == ("SPEAKER_00",)
        assert captured["request"].user_notes == "Confirm the rollout date"
        assert captured["request"].prefer_short_titles is False
        assert captured["timeout"] == tasks_module.AUTOMATIC_MEETING_INTELLIGENCE_TIMEOUT_SECONDS
        assert task.calls == [
            {
                "state": "PROCESSING",
                "meta": {
                    "progress": tasks_module.AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS,
                    "stage": tasks_module.AUTOMATIC_MEETING_INTELLIGENCE_STAGE,
                },
            }
        ]
    finally:
        engine.dispose()


def test_run_automatic_meeting_intelligence_stage_skips_when_llm_config_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _create_worker_ai_database(tmp_path)
    task = _FakeTask()

    def fail_if_called(_config):
        raise AssertionError("LLM backend should not be created when configuration is incomplete")

    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", fail_if_called)

    try:
        with Session(engine) as session:
            recording = session.get(Recording, 1)
            transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()
            speakers = session.exec(
                select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
            ).all()

            result = tasks_module._run_automatic_meeting_intelligence_stage(
                session=session,
                task=task,
                recording=recording,
                transcript=transcript,
                speakers=speakers,
                transcript_text="[00:00:00 - 00:00:04] SPEAKER_00: Opening update.",
                unresolved_speakers=("SPEAKER_00",),
                llm_config=_sample_llm_config(model=None),
                prefer_short_titles=True,
                device_suffix=" (CPU)",
            )

        with Session(engine) as verification_session:
            recording = verification_session.get(Recording, 1)
            transcript = verification_session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()

        assert result is None
        assert recording.name == "Planning"
        assert recording.processing_step == "Saving transcript..."
        assert transcript.notes_status == "pending"
        assert transcript.error_message is None
        assert task.calls == []
    finally:
        engine.dispose()


def test_run_automatic_meeting_intelligence_stage_marks_notes_error_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _create_worker_ai_database(tmp_path)

    class FakeLLM:
        def generate_meeting_intelligence(self, request, timeout: int = 60):
            raise RuntimeError("Unified AI contract failed")

    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda config: FakeLLM())

    try:
        with Session(engine) as session:
            recording = session.get(Recording, 1)
            transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()
            speakers = session.exec(
                select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
            ).all()

            result = tasks_module._run_automatic_meeting_intelligence_stage(
                session=session,
                task=None,
                recording=recording,
                transcript=transcript,
                speakers=speakers,
                transcript_text="[00:00:00 - 00:00:04] SPEAKER_00: Opening update.",
                unresolved_speakers=("SPEAKER_00",),
                llm_config=_sample_llm_config(),
                prefer_short_titles=True,
                device_suffix=" (CPU)",
            )

        with Session(engine) as verification_session:
            recording = verification_session.get(Recording, 1)
            transcript = verification_session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()

        assert result is None
        assert recording.status == "PROCESSED"
        assert recording.processing_step == "Error generating notes"
        assert recording.processing_progress == 97
        assert transcript.notes_status == "error"
        assert transcript.error_message == "Unified AI contract failed"
    finally:
        engine.dispose()