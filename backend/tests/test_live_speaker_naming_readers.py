"""Readers during capture show the name the user gave a live speaker.

The live lines are ``live_naming_fixtures.LIVE_LINES``. ``LIVE_PROVISIONAL_1``
is named "Alex" as the naming endpoint names it; ``LIVE_PROVISIONAL_2`` is
not, so it still reads "Speaker 2". The live panel, the live read, exports and
the MCP tools are covered in ``test_live_speaker_naming_api.py`` and
``test_mcp_tools.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

import backend.worker.tasks as tasks_module
from backend.models.recording import Recording
from backend.services.meeting_analytics.query import compute_recording_analytics
from backend.tests.live_naming_fixtures import ONE, TWO, seed_live_lines
from backend.tests.sqlite_schemas import (
    CALENDAR_EVENTS_SCHEMA,
    GLOBAL_SPEAKERS_SCHEMA,
    PROCESSING_RUNS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    SPEAKER_CORRECTION_EVENTS_SCHEMA,
    SPEAKER_NAME_EVENTS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.llm_transcript import render_transcript_for_llm
from backend.utils.speaker_names.events import UserNameAct
from backend.utils.speaker_names.live import (
    live_speaker_names,
    record_live_speaker_name,
)
from backend.worker.tasks.meeting_edge_stage import _refresh_meeting_edge


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
            RECORDING_SPEAKER_ALIASES_SCHEMA,
            PROCESSING_RUNS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
            SPEAKER_CORRECTION_EVENTS_SCHEMA,
            SPEAKER_NAME_EVENTS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
            CALENDAR_EVENTS_SCHEMA,
        ):
            connection.execute(text(schema))
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, created_at, updated_at, username, hashed_password,
                    is_active, is_superuser, force_password_change, role,
                    token_version, settings, has_seen_demo_recording
                ) VALUES (1, :now, :now, 'alice', 'x', 1, 0, 0, 'user', 0,
                          '{"enable_meeting_edge": true}', 0)
                """
            ),
            {"now": _now()},
        )
        connection.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (1, :now, :now, 'Live meeting', 'live-rec', 'uid-live',
                          '/tmp/live.wav', 'UPLOADING', 50, 10, 'unified', 0, 0, 1)
                """
            ),
            {"now": _now()},
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    meeting_edge_status, notes_status, transcript_status
                ) VALUES (1, :now, :now, 1, '', '[]', 'idle', 'pending',
                          'processing')
                """
            ),
            {"now": _now()},
        )
    with Session(engine) as db:
        seed_live_lines(db)
        record_live_speaker_name(
            db, 1, ONE, UserNameAct("Alex", None, actor_user_id=1, surface="live")
        )
        db.commit()
        yield db
    engine.dispose()


def test_only_live_labels_have_live_names(session) -> None:
    # A row a rename wrote for a final speaker is not a live name, even while
    # a current line carries that speaker's label.
    session.execute(
        text(
            """
            INSERT INTO transcript_utterances (
                created_at, updated_at, public_id, recording_id, sort_key,
                start_ms, end_ms, text, speaker_label, state, source_kind,
                revision, overlap_rank, manual_text_locked, manual_speaker_locked,
                speaker_assignment_source, speaker_assignment_authority
            ) VALUES (:now, :now, 'final-1', 1, 'zzzz', 40000, 41000, 'Done.',
                      'SPEAKER_00', 'finalized', 'final', 1, 0, 0, 0, 'final',
                      'final')
            """
        ),
        {"now": _now()},
    )
    session.execute(
        text(
            """
            INSERT INTO speaker_name_events (
                created_at, updated_at, public_id, recording_id, kind, name,
                source, authority, surface, anchor_start_ms, anchor_end_ms,
                anchor_label
            ) VALUES (:now, :now, 'ev-final', 1, 'name', 'Dana', 'user', 40,
                      'api', 0, 1000, 'SPEAKER_00')
            """
        ),
        {"now": _now()},
    )

    assert live_speaker_names(session, 1) == {ONE: "Alex"}


def test_meeting_chat_names_the_live_speaker(session) -> None:
    rendered = render_transcript_for_llm(session, 1)

    speakers = [
        line.split("] ", 1)[1].split(":", 1)[0] for line in rendered.splitlines()
    ]
    assert speakers[:4] == ["Alex", "Alex", "Speaker 2", "Alex"]


def test_meeting_edge_names_the_live_speaker(session, monkeypatch) -> None:
    requests = []

    class _LLM:
        def generate_meeting_edge(self, request, timeout):
            requests.append(request)
            raise RuntimeError("stop after the prompt is built")

    monkeypatch.setattr(
        tasks_module,
        "resolve_llm_config",
        lambda *args, **kwargs: SimpleNamespace(
            provider="test",
            model="m",
            api_url=None,
            merged_config={},
            missing_configuration_message=lambda: None,
        ),
    )
    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda config: _LLM())

    _refresh_meeting_edge(SimpleNamespace(session=session), 1)

    (request,) = requests
    assert "Alex: live line 0" in request.recent_transcript
    assert "Speaker 2: live line 2" in request.recent_transcript
    assert "LIVE_" not in request.recent_transcript


def test_analytics_name_the_live_speaker(session) -> None:
    analytics = compute_recording_analytics(session, session.get(Recording, 1))

    names = {
        speaker["diarization_label"]: (speaker["name"], speaker["is_named"])
        for speaker in analytics["speakers"]
    }
    assert names[ONE] == ("Alex", True)
    assert names[TWO] == ("Speaker 2", False)
