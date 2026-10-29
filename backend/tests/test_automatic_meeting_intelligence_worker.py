from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

import backend.worker.tasks as tasks_module
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.timeline_note import TimelineNote
from backend.models.transcript import Transcript
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.sqlite_schemas import (
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    SPEAKER_NAME_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.llm_config import ResolvedLLMConfig
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceResult

NO_NOTES = RecordingTimelineNotes()


def _note(kind: str, body: str, **anchor: int) -> TimelineNote:
    return TimelineNote(
        public_id=f"note-{body}",
        recording_id=1,
        created_by_user_id=1,
        kind=kind,
        body=body,
        **anchor,
    )


BASE_SCHEMA = """
CREATE TABLE recordings (
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
);

CREATE TABLE transcripts (
    notes_generation_metadata JSON,
    change_sequence BIGINT NOT NULL DEFAULT 0,
    settling_state JSON,
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL UNIQUE,
    text TEXT,
    segments JSON NOT NULL,
    language_detection JSON,
    notes TEXT,
    meeting_edge_focus TEXT,
    meeting_edge_payload JSON,
    meeting_edge_status VARCHAR NOT NULL DEFAULT 'idle',
    meeting_edge_error_message TEXT,
    meeting_edge_source_signature TEXT,
    speaker_name_suggestions JSON,
    notes_template_id INTEGER,
    notes_template_sections TEXT,
    notes_status VARCHAR NOT NULL,
    notes_stale_documents BOOLEAN DEFAULT 0,
    transcript_status VARCHAR NOT NULL,
    error_message TEXT,
    analytics_payload JSON,
    analytics_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    analytics_ai_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    analytics_ai_error_message TEXT,
    analytics_error_message TEXT
);

CREATE TABLE global_speakers (
    embedding_version INTEGER,
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    name VARCHAR NOT NULL,
    embedding JSON,
    is_voiceprint_locked BOOLEAN NOT NULL DEFAULT 0,
    color VARCHAR,
    title VARCHAR,
    company VARCHAR,
    email VARCHAR,
    phone_number VARCHAR,
    notes TEXT,
    user_id INTEGER
);

CREATE TABLE people_tags (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    global_speaker_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL
);

CREATE TABLE recording_speakers (
    embedding_version INTEGER,
    embedding_speech_seconds FLOAT,
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    global_speaker_id INTEGER,
    diarization_label VARCHAR NOT NULL,
    local_name VARCHAR,
    name VARCHAR,
    speaker_status VARCHAR NOT NULL DEFAULT 'active',
    speaker_kind VARCHAR NOT NULL DEFAULT 'automated',
    snippet_start FLOAT,
    snippet_end FLOAT,
    voice_snippet_path VARCHAR,
    embedding JSON,
    color VARCHAR,
    merged_into_id INTEGER,
    processing_run_id INTEGER,
    last_speaker_correction_event_id INTEGER,
    last_diarization_window_result_id INTEGER,
    first_seen_ms INTEGER,
    last_seen_ms INTEGER,
    identity_confidence FLOAT,
    identity_locked BOOLEAN NOT NULL DEFAULT 0,
    name_last_edit_source VARCHAR(32),
    name_source VARCHAR(16)
);

CREATE TABLE recording_speaker_aliases (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_speaker_id INTEGER NOT NULL,
    alias_type VARCHAR NOT NULL,
    alias_value VARCHAR(255) NOT NULL,
    source_run_id INTEGER,
    active BOOLEAN NOT NULL DEFAULT 1,
    valid_from_ms INTEGER,
    valid_to_ms INTEGER,
    confidence FLOAT
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
        connection.execute(text(RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA))
        for schema in (
            USERS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
            SPEAKER_NAME_EVENTS_SCHEMA,
        ):
            connection.execute(text(schema))

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
                    notes, notes_status, transcript_status, error_message
                ) VALUES (
                    1, :now, :now, 1, 'Transcript text', :segments,
                    NULL, 'pending', 'completed', NULL
                )
                """
            ),
            {"now": now, "segments": json.dumps(segments)},
        )
        connection.execute(
            text(
                """
                INSERT INTO global_speakers (
                    id, created_at, updated_at, name, embedding,
                    is_voiceprint_locked, color, user_id
                ) VALUES (
                    11, :now, :now, 'Dana', :embedding, 0, NULL, 1
                )
                """
            ),
            {"now": now, "embedding": json.dumps([1.0, 0.0, 0.0])},
        )
        connection.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id, global_speaker_id,
                    diarization_label, local_name, name, snippet_start,
                    snippet_end, voice_snippet_path, embedding, color,
                    merged_into_id
                ) VALUES (
                    1, :now, :now, 'recording-speaker-1', 1, NULL, 'SPEAKER_00', NULL, 'Speaker 1',
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
                    id, created_at, updated_at, public_id, recording_id, global_speaker_id,
                    diarization_label, local_name, name, snippet_start,
                    snippet_end, voice_snippet_path, embedding, color,
                    merged_into_id
                ) VALUES (
                    2, :now, :now, 'recording-speaker-2', 1, 11, 'SPEAKER_01', NULL, 'Dana',
                    NULL, NULL, NULL, :embedding, NULL, NULL
                )
                """
            ),
            {"now": now, "embedding": json.dumps([1.0, 0.0, 0.0])},
        )

    return engine


def _sample_llm_config(
    *, api_key: str | None = "sk-test", model: str | None = "gpt-test"
) -> ResolvedLLMConfig:
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


def test_build_automatic_meeting_intelligence_transcript_keeps_unresolved_labels_visible() -> (
    None
):
    transcript = tasks_module._build_automatic_meeting_intelligence_transcript(
        [
            {
                "start": 0.0,
                "end": 4.0,
                "speaker": "SPEAKER_00",
                "text": "Opening update.",
            },
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
        NO_NOTES,
    )

    assert "SPEAKER_00: Opening update." in transcript
    assert "Dana (with SPEAKER_00): Confirmed the rollout date." in transcript
    assert "Speaker 1" not in transcript


def test_build_automatic_meeting_intelligence_transcript_puts_notes_where_written() -> (
    None
):
    notes = RecordingTimelineNotes(
        notes=[
            _note("head", "Agenda: rollout."),
            _note("point", "Ask Dana for the date.", anchor_ms=5_000),
        ],
        author_names={1: "mira"},
    )

    transcript = tasks_module._build_automatic_meeting_intelligence_transcript(
        [
            {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00", "text": "Opening."},
            {"start": 4.0, "end": 8.0, "speaker": "SPEAKER_01", "text": "Rollout."},
            {"start": 8.0, "end": 9.0, "speaker": "SPEAKER_00", "text": "Thanks."},
        ],
        {"SPEAKER_01": "Dana"},
        ("SPEAKER_00",),
        notes,
    )

    # The head note is in the prompt's notes section, not the transcript.
    assert transcript.splitlines() == [
        "[00:00:00 - 00:00:04] SPEAKER_00: Opening.",
        "[00:00:04 - 00:00:08] Dana: Rollout.",
        "[Note — mira]: Ask Dana for the date.",
        "[00:00:08 - 00:00:09] SPEAKER_00: Thanks.",
    ]


def test_a_quoted_note_in_the_unified_prompt_tells_time_like_its_lines() -> None:
    notes = RecordingTimelineNotes(
        notes=[
            _note(
                "selection",
                "It is fourteen.",
                start_ms=3_912_000,
                end_ms=3_914_000,
                quoted_text="forty",
            )
        ],
        author_names={1: "mira"},
    )
    transcript = tasks_module._build_automatic_meeting_intelligence_transcript(
        [{"start": 3_910.0, "end": 3_915.0, "speaker": "S", "text": "Forty."}],
        {},
        (),
        notes,
    )
    assert transcript.splitlines()[-1] == (
        '[Note on "forty" at 01:05:12 — mira]: It is fourteen.'
    )


def test_notes_without_a_transcript_leave_the_unified_prompt_empty() -> None:
    # The stage skips the model on an empty transcript; a note typed during a
    # meeting nobody was heard in must not become a meeting to summarise.
    notes = RecordingTimelineNotes(
        notes=[_note("point", "Mic was muted", anchor_ms=0)],
    )
    assert (
        tasks_module._build_automatic_meeting_intelligence_transcript([], {}, (), notes)
        == ""
    )


def test_run_automatic_meeting_intelligence_stage_persists_suggestions_title_and_notes(
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
                # A user-notes section the model wrote itself is replaced.
                notes_markdown="# Meeting Notes\n\n## Summary\nGenerated notes.\n\n## User Notes\n- [User] Invented",
            )

    monkeypatch.setattr(
        tasks_module, "_llm_backend_from_config", lambda config: FakeLLM()
    )
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
                timeline_notes=RecordingTimelineNotes(
                    notes=[
                        _note("head", "Confirm the rollout date"),
                        _note("point", "Dana owns it", anchor_ms=4_500),
                    ]
                ),
            )

        with Session(engine) as verification_session:
            recording = verification_session.get(Recording, 1)
            transcript = verification_session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()
            speakers = verification_session.exec(
                select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
            ).all()
            names_by_label = {
                speaker.diarization_label: speaker.name for speaker in speakers
            }
            local_names_by_label = {
                speaker.diarization_label: speaker.local_name for speaker in speakers
            }
            suggestions = transcript.speaker_name_suggestions

        assert result is not None
        assert local_names_by_label["SPEAKER_00"] == "Alex"
        # The pending name shows; the row keeps its "Speaker N" for Not {name}.
        assert names_by_label["SPEAKER_00"] == "Speaker 1"
        assert names_by_label["SPEAKER_01"] == "Dana"
        assert len(suggestions) == 1
        assert suggestions[0]["diarization_label"] == "SPEAKER_00"
        assert suggestions[0]["suggested_name"] == "Alex"
        assert suggestions[0]["status"] == "pending"
        assert suggestions[0]["resolution_reason"] is None
        assert suggestions[0]["origin"] == "automatic_meeting_intelligence"
        assert recording.name == "Launch Readiness Review"
        assert recording.status == "PROCESSING"
        assert recording.processing_step == "Saving transcript..."
        assert recording.processing_progress == 85
        assert transcript.notes_status == "completed"
        assert transcript.error_message is None
        # Every note, in timeline order, the anchored one with its time.
        assert transcript.notes == (
            "## Summary\nGenerated notes.\n\n## User Notes\n"
            "- [User] Confirm the rollout date\n"
            "- [User] 00:04 Dana owns it"
        )
        assert captured["request"].unresolved_speakers == ("SPEAKER_00",)
        # The meeting-wide note is in the section, which also explains the
        # anchored notes the transcript carries.
        assert "Confirm the rollout date" in captured["request"].user_notes
        assert "[Note — name]" in captured["request"].user_notes
        assert captured["request"].prefer_short_titles is False
        assert (
            captured["timeout"]
            == tasks_module.AUTOMATIC_MEETING_INTELLIGENCE_TIMEOUT_SECONDS
        )
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
        raise AssertionError(
            "LLM backend should not be created when configuration is incomplete"
        )

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
                timeline_notes=NO_NOTES,
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


@pytest.mark.parametrize(
    ("line", "expected_local_name"),
    [
        # Upstream applied "Going To Share" here: the case-insensitive
        # self-introduction rule read "going to share" as a name.
        ("Okay, I'm going to share my screen now.", None),
        ("Hi everyone, I'm Priya from finance.", "Priya"),
    ],
)
def test_no_llm_final_processing_applies_only_real_self_introductions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    line: str,
    expected_local_name: str | None,
) -> None:
    engine = _create_worker_ai_database(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE transcripts SET segments = :segments WHERE recording_id = 1"),
            {
                "segments": json.dumps(
                    [{"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00", "text": line}]
                )
            },
        )

    def fail_if_called(_config):
        raise AssertionError("no LLM backend may be built without a model")

    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", fail_if_called)

    try:
        with Session(engine) as session:
            tasks_module._run_automatic_meeting_intelligence_stage(
                session=session,
                task=None,
                recording=session.get(Recording, 1),
                transcript=session.exec(
                    select(Transcript).where(Transcript.recording_id == 1)
                ).first(),
                speakers=session.exec(
                    select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
                ).all(),
                transcript_text=f"[00:00:00 - 00:00:04] SPEAKER_00: {line}",
                unresolved_speakers=("SPEAKER_00",),
                llm_config=_sample_llm_config(model=None),
                prefer_short_titles=True,
                device_suffix=" (CPU)",
                timeline_notes=NO_NOTES,
            )

        with Session(engine) as verification_session:
            speaker = verification_session.get(RecordingSpeaker, 1)
            transcript = verification_session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()

        assert speaker.local_name == expected_local_name
        applied_names = [
            item["suggested_name"]
            for item in transcript.speaker_name_suggestions or []
            if item["status"] == "pending"
        ]
        assert applied_names == ([expected_local_name] if expected_local_name else [])
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

    monkeypatch.setattr(
        tasks_module, "_llm_backend_from_config", lambda config: FakeLLM()
    )

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
                timeline_notes=NO_NOTES,
            )

        with Session(engine) as verification_session:
            recording = verification_session.get(Recording, 1)
            transcript = verification_session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()

        assert result is None
        assert recording.status == "PROCESSING"
        assert recording.processing_step == "Saving transcript..."
        assert recording.processing_progress == 85
        assert transcript.notes_status == "error"
        assert transcript.error_message == "Unified AI contract failed"
    finally:
        engine.dispose()


def test_a_meeting_too_long_for_one_call_gets_its_notes_in_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _create_worker_ai_database(tmp_path)
    refusal = ContextWindowExceededError("refused", prompt_tokens=40_000, window=8192)
    captured: dict[str, Any] = {}

    class FakeLLM:
        def generate_meeting_intelligence(self, request, timeout: int = 60):
            raise refusal

        def generate_meeting_intelligence_in_parts(self, request, *, overflow, timeout):
            captured["overflow"] = overflow
            captured["timeout"] = timeout
            return AutomaticMeetingIntelligenceResult(
                speaker_mapping={},
                title="Written in Parts",
                notes_markdown="## Summary\nCombined notes.",
            )

    monkeypatch.setattr(
        tasks_module, "_llm_backend_from_config", lambda config: FakeLLM()
    )

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
                timeline_notes=NO_NOTES,
            )

        with Session(engine) as verification_session:
            recording = verification_session.get(Recording, 1)
            transcript = verification_session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()

        assert result is not None
        assert captured["overflow"] is refusal
        assert recording.name == "Written in Parts"
        assert transcript.notes_status == "completed"
        assert transcript.notes == "## Summary\nCombined notes."
    finally:
        engine.dispose()


def _set_owner_settings(connection: Any, settings: dict[str, Any]) -> None:
    connection.execute(
        text(
            """
            INSERT INTO users (
                id, created_at, updated_at, username, hashed_password, is_active,
                is_superuser, force_password_change, role, token_version, settings,
                has_seen_demo_recording
            ) VALUES (1, :now, :now, 'owner', 'hash', 1, 1, 0, 'owner', 0, :settings, 0)
            """
        ),
        {"now": _utc_now_naive(), "settings": json.dumps(settings)},
    )


@pytest.mark.parametrize("confirm_suggested_names", [False, True])
def test_an_exact_people_name_links_only_when_names_are_confirmed_at_the_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    confirm_suggested_names: bool,
) -> None:
    engine = _create_worker_ai_database(tmp_path)

    with engine.begin() as connection:
        _set_owner_settings(
            connection, {"confirm_suggested_names": confirm_suggested_names}
        )
        connection.execute(
            text("UPDATE recording_speakers SET embedding = :embedding WHERE id = 1"),
            {"embedding": json.dumps([0.0, 1.0, 0.0])},
        )
        connection.execute(
            text("UPDATE global_speakers SET is_voiceprint_locked = 1 WHERE id = 11")
        )

    class FakeLLM:
        def generate_meeting_intelligence(self, request, timeout: int = 60):
            return AutomaticMeetingIntelligenceResult(
                speaker_mapping={"SPEAKER_00": "Dana"},
                title="Launch Readiness Review",
                notes_markdown="## Summary\nGenerated notes.",
            )

    monkeypatch.setattr(
        tasks_module, "_llm_backend_from_config", lambda config: FakeLLM()
    )

    try:
        with Session(engine) as session:
            recording = session.get(Recording, 1)
            transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()
            speakers = session.exec(
                select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
            ).all()

            tasks_module._run_automatic_meeting_intelligence_stage(
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
                timeline_notes=NO_NOTES,
            )

        with Session(engine) as verification_session:
            speaker_row = verification_session.exec(
                text(
                    "SELECT global_speaker_id, local_name, name_source FROM recording_speakers WHERE id = 1"
                )
            ).one()
            global_embedding = verification_session.exec(
                text("SELECT embedding FROM global_speakers WHERE id = 11")
            ).one()[0]
            transcript = verification_session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first()
            suggestions = transcript.speaker_name_suggestions

        # Either way the locked voiceprint is left untouched.
        assert json.loads(global_embedding) == [1.0, 0.0, 0.0]
        assert len(suggestions) == 1
        assert suggestions[0]["suggested_global_speaker_id"] == 11
        if confirm_suggested_names:
            # Taken as given: the exact People name links that person.
            assert tuple(speaker_row) == (11, None, "auto")
            assert suggestions[0]["status"] == "accepted"
            assert suggestions[0]["resolution_reason"] == "auto_applied"
        else:
            # Pending: the name shows, and nobody in People is linked.
            assert tuple(speaker_row) == (None, "Dana", "llm")
            assert suggestions[0]["status"] == "pending"
    finally:
        engine.dispose()


def test_the_stage_writes_notes_in_the_recordings_language(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Same as transcription" follows the recording's own language over the
    owner's (#671). The pipeline and the IO lane both run this stage."""
    engine = _create_worker_ai_database(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE recordings SET transcription_language = 'fr' WHERE id = 1")
        )
    captured: dict[str, Any] = {}

    class FakeLLM:
        def generate_meeting_intelligence(self, request, timeout: int = 60):
            captured["request"] = request
            return AutomaticMeetingIntelligenceResult(
                speaker_mapping={}, title="Revue", notes_markdown="## Résumé\nNotes."
            )

    monkeypatch.setattr(
        tasks_module, "_llm_backend_from_config", lambda config: FakeLLM()
    )
    config = _sample_llm_config()
    config.merged_config.update(
        {"notes_language": "same_as_transcription", "transcription_language": "de"}
    )

    try:
        with Session(engine) as session:
            tasks_module._run_automatic_meeting_intelligence_stage(
                session=session,
                task=None,
                recording=session.get(Recording, 1),
                transcript=session.exec(
                    select(Transcript).where(Transcript.recording_id == 1)
                ).first(),
                speakers=[],
                transcript_text="[00:00:00 - 00:00:04] SPEAKER_00: Bonjour.",
                unresolved_speakers=(),
                llm_config=config,
                prefer_short_titles=False,
                device_suffix="",
                timeline_notes=RecordingTimelineNotes(notes=[]),
                detected_transcription_language="de",
            )
    finally:
        engine.dispose()

    assert "in French." in captured["request"].output_language_instruction
