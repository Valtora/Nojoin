"""Tests for the AI-analytics worker task.

The evidence rules themselves are covered in test_meeting_analysis_contract.
What is pinned here is the task's contract with the rest of the system: that an
install with no AI provider settles as available-but-unconfigured rather than
broken, that a failure never strands the transcript mid-run, that the secondary
provider chain covers this call like every other, and that writing this tier
does not destroy the measured delivery tier sharing its column.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

import backend.worker.tasks.analytics_ai as analytics_ai
from backend.processing.llm_backends.factory import SecondaryLLMBackend
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.llm_config import ResolvedLLMConfig
from backend.utils.meeting_analysis import MEETING_ANALYSIS_METHOD_VERSION

# (recording_speaker_id, text, start_ms, end_ms)
UTTERANCES = [
    (1, "Let's take it to two customers first and see what breaks.", 0, 6_000),
    (2, "I think the approach is right, but not by March.", 6_000, 12_000),
    (2, "Who owns the data migration once we cut over?", 12_000, 18_000),
]

MODEL_RESPONSE = json.dumps(
    {
        "topics": [
            {
                "title": "Rollout plan",
                "start_seconds": 0,
                "end_seconds": 18,
                "summary": "How widely to ship the pilot.",
                "led_by": "Priya Patel",
                "leadership_basis": "Proposed the pilot.",
            }
        ],
        "sentiment": [
            {
                "speaker": "Alex Johnson",
                "tone": "mixed",
                "summary": "Backed the approach, doubted the date.",
                "citations": [
                    {
                        "quote": "I think the approach is right, but not by March.",
                        "start_seconds": 6,
                    }
                ],
            }
        ],
        "questions": [
            {
                "question": "Who owns the data migration?",
                "asked_by": "Alex Johnson",
                "asked_at_seconds": 12,
                "answered_by": None,
                "answered_at_seconds": None,
                "answer_summary": None,
            }
        ],
        "decisions": [
            {
                "decision": "Pilot with two customers first.",
                "proposed_by": "Priya Patel",
                "agreed_by": [],
                "objected_by": [],
                "consensus": "assumed",
                "citations": [
                    {
                        "quote": "Let's take it to two customers first and see what breaks.",
                        "start_seconds": 0,
                        "speaker": "Priya Patel",
                    }
                ],
            }
        ],
    }
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class FakeBackend:
    """A provider that returns canned text through the real parser."""

    def __init__(self, response: str | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = 0
        self.model = "fake-model"

    def generate_meeting_analysis(self, request, prompt_template=None, timeout=300):
        from backend.processing.llm_backends.base import LLMBackend

        self.calls += 1
        if self.error is not None:
            raise self.error
        self.last_request = request
        return LLMBackend.parse_meeting_analysis_result(self.response, request)


def _make_engine(tmp_path: Path, name: str) -> Any:
    engine = create_engine(f"sqlite:///{tmp_path / name}.sqlite", future=True)
    with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
        ):
            connection.execute(text(schema))
    return engine


def _seed(connection, *, analytics_payload: str | None = None) -> None:
    connection.execute(
        text(
            "INSERT INTO users (id, created_at, updated_at, username, "
            "hashed_password, is_active, is_superuser, force_password_change, "
            "role, token_version, has_seen_demo_recording) VALUES "
            "(1, :ts, :ts, 'alice', 'x', 1, 0, 0, 'user', 0, 0)"
        ),
        {"ts": _now()},
    )
    connection.execute(
        text(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id, "
            "meeting_uid, audio_path, status, upload_progress, processing_progress, "
            "is_archived, is_deleted, user_id, duration_seconds) VALUES "
            "(10, :ts, :ts, 'Sync', 'rec-10', 'uid-10', '/tmp/x.wav', 'PROCESSED', "
            "100, 100, 0, 0, 1, 60.0)"
        ),
        {"ts": _now()},
    )
    connection.execute(
        text(
            "INSERT INTO transcripts (id, created_at, updated_at, recording_id, "
            "segments, notes_status, transcript_status, analytics_status, "
            "analytics_ai_status, analytics_payload) VALUES "
            "(1, :ts, :ts, 10, '[]', 'completed', 'completed', 'pending', "
            "'pending', :payload)"
        ),
        {"ts": _now(), "payload": analytics_payload},
    )
    for speaker_id, label, name in (
        (1, "SPEAKER_00", "Priya Patel"),
        (2, "SPEAKER_01", "Alex Johnson"),
    ):
        connection.execute(
            text(
                "INSERT INTO recording_speakers (id, created_at, updated_at, "
                "public_id, recording_id, diarization_label, local_name, name, "
                "speaker_status, speaker_kind, identity_locked) VALUES "
                "(:id, :ts, :ts, :pid, 10, :label, :name, :name, 'confirmed', "
                "'human', 0)"
            ),
            {
                "id": speaker_id,
                "pid": f"sp-{speaker_id}",
                "label": label,
                "name": name,
                "ts": _now(),
            },
        )
    for index, (speaker_id, body, start, end) in enumerate(UTTERANCES):
        connection.execute(
            text(
                "INSERT INTO transcript_utterances (id, created_at, updated_at, "
                "public_id, recording_id, sort_key, start_ms, end_ms, text, "
                "speaker_label, recording_speaker_id, state, source_kind, revision, "
                "manual_text_locked, manual_speaker_locked, speaker_assignment_source, "
                "speaker_assignment_authority, overlap_rank) VALUES "
                "(:id, :ts, :ts, :pid, 10, :sort, :start, :end, :body, :label, "
                ":speaker, 'stable', 'final', 1, 0, 0, 'final', 'final', 0)"
            ),
            {
                "id": index + 1,
                "pid": f"u-{index}",
                "sort": f"{index:04d}",
                "start": start,
                "end": end,
                "body": body,
                "label": "SPEAKER_00" if speaker_id == 1 else "SPEAKER_01",
                "speaker": speaker_id,
                "ts": _now(),
            },
        )
    connection.execute(
        text(
            "INSERT INTO transcript_utterance_events (id, created_at, updated_at, "
            "recording_id, utterance_id, event_type, source, resulting_revision) "
            "VALUES (9, :ts, :ts, 10, 1, 'create', 'system', 1)"
        ),
        {"ts": _now()},
    )


def _configured() -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider="gemini",
        api_key="key",
        model="model",
        api_url=None,
        merged_config={},
    )


def _unconfigured() -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider="gemini",
        api_key=None,
        model=None,
        api_url=None,
        merged_config={},
    )


def _run(engine, monkeypatch, *, config, backend) -> dict:
    monkeypatch.setattr(analytics_ai, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(
        "backend.utils.llm_config.resolve_llm_config",
        lambda *args, **kwargs: config,
    )
    monkeypatch.setattr(
        "backend.processing.llm_services.get_llm_backend_with_secondary",
        lambda *args, **kwargs: backend,
    )
    return analytics_ai.compute_meeting_analysis_task.run(10)


def _row(engine) -> Any:
    with engine.begin() as connection:
        return connection.execute(
            text(
                "SELECT analytics_ai_status, analytics_ai_error_message, "
                "analytics_payload, analytics_status FROM transcripts "
                "WHERE recording_id = 10"
            )
        ).one()


def _payload(raw: Any) -> dict:
    return json.loads(raw) if isinstance(raw, str) else raw


def test_no_ai_provider_is_unavailable_rather_than_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An install with no AI provider is working correctly. Reporting it as an
    error would tell the user something is broken when nothing is."""
    engine = _make_engine(tmp_path, "no-provider")
    with engine.begin() as connection:
        _seed(connection)

    backend = FakeBackend(MODEL_RESPONSE)
    result = _run(engine, monkeypatch, config=_unconfigured(), backend=backend)

    assert result["status"] == "unavailable"
    status, message, payload, _ = _row(engine)
    assert status == "unavailable"
    assert message
    assert payload is None
    # Nothing was spent finding this out.
    assert backend.calls == 0


def test_a_successful_run_stores_the_tier_with_its_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _make_engine(tmp_path, "ok")
    with engine.begin() as connection:
        _seed(connection)

    backend = FakeBackend(MODEL_RESPONSE)
    result = _run(engine, monkeypatch, config=_configured(), backend=backend)

    assert result["status"] == "success"
    status, message, payload, _ = _row(engine)
    assert status == "completed"
    assert message is None

    block = _payload(payload)["ai"]
    assert block["method_version"] == MEETING_ANALYSIS_METHOD_VERSION
    # The cursor this was analysed against, which is what makes staleness
    # detectable without a second column.
    assert block["event_watermark"] == 9
    assert block["topics"][0]["led_by"] == "rs:1"
    assert block["sentiment"][0]["speaker_key"] == "rs:2"
    assert block["questions"][0]["answered_by"] is None
    assert block["decisions"][0]["consensus"] == "assumed"
    assert block["transcript_truncated"] is False


def test_the_model_is_given_only_the_meetings_own_speakers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _make_engine(tmp_path, "allowlist")
    with engine.begin() as connection:
        _seed(connection)

    backend = FakeBackend(MODEL_RESPONSE)
    _run(engine, monkeypatch, config=_configured(), backend=backend)

    assert set(backend.last_request.allowlist.names) == {"Priya Patel", "Alex Johnson"}
    assert "Who owns the data migration once we cut over?" in (
        backend.last_request.transcript
    )


def test_writing_the_ai_tier_preserves_the_measured_delivery_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two lanes write one JSONB column, so the AI task must merge into it
    rather than assign it."""
    engine = _make_engine(tmp_path, "merge")
    existing = json.dumps(
        {
            "delivery": {"speakers": {"rs:1": {"words_per_minute": 140}}},
            "method_version": 1,
            "event_watermark": 4,
        }
    )
    with engine.begin() as connection:
        _seed(connection, analytics_payload=existing)

    _run(engine, monkeypatch, config=_configured(), backend=FakeBackend(MODEL_RESPONSE))

    payload = _payload(_row(engine)[2])
    assert payload["delivery"]["speakers"]["rs:1"]["words_per_minute"] == 140
    assert payload["ai"]["topics"]
    # The delivery tier's own status is untouched by an AI run.
    assert _row(engine)[3] == "pending"


def test_a_failure_mid_run_does_not_leave_the_transcript_generating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generating is the one state the interface cannot recover from on its own."""
    engine = _make_engine(tmp_path, "boom")
    with engine.begin() as connection:
        _seed(connection)

    backend = FakeBackend(error=RuntimeError("provider exploded"))
    result = _run(engine, monkeypatch, config=_configured(), backend=backend)

    assert result["status"] == "error"
    status, message, payload, _ = _row(engine)
    assert status == "error"
    assert message
    assert payload is None


def test_an_unparseable_response_is_an_error_not_an_empty_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A meeting with no decisions and a provider that returned nothing usable
    must not look the same to the user."""
    engine = _make_engine(tmp_path, "garbage")
    with engine.begin() as connection:
        _seed(connection)

    result = _run(
        engine,
        monkeypatch,
        config=_configured(),
        backend=FakeBackend("I'm afraid I can't do that"),
    )

    assert result["status"] == "error"
    assert _row(engine)[0] == "error"


def test_the_secondary_provider_covers_this_call_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fallback is wired per method on SecondaryLLMBackend, so a new call that
    forgets to forward silently loses it."""
    engine = _make_engine(tmp_path, "fallback")
    with engine.begin() as connection:
        _seed(connection)

    primary = FakeBackend(error=RuntimeError("primary down"))
    secondary = FakeBackend(MODEL_RESPONSE)
    chain = SecondaryLLMBackend(primary=primary, secondary=secondary)

    result = _run(engine, monkeypatch, config=_configured(), backend=chain)

    assert result["status"] == "success"
    assert primary.calls == 1
    assert secondary.calls == 1
    assert _row(engine)[0] == "completed"


def test_a_meeting_with_no_attributed_speech_is_not_sent_to_a_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _make_engine(tmp_path, "silent")
    with engine.begin() as connection:
        _seed(connection)
        connection.execute(text("DELETE FROM transcript_utterances"))

    backend = FakeBackend(MODEL_RESPONSE)
    result = _run(engine, monkeypatch, config=_configured(), backend=backend)

    assert result["status"] == "skipped"
    assert backend.calls == 0
    assert _row(engine)[0] == "error"


def test_an_over_long_transcript_is_truncated_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Analysing the first part of a meeting and presenting it as the meeting
    is exactly the quiet wrongness this surface must not produce."""
    engine = _make_engine(tmp_path, "long")
    with engine.begin() as connection:
        _seed(connection)
    monkeypatch.setattr(analytics_ai, "MEETING_ANALYSIS_MAX_TRANSCRIPT_CHARS", 90)

    backend = FakeBackend(MODEL_RESPONSE)
    _run(engine, monkeypatch, config=_configured(), backend=backend)

    block = _payload(_row(engine)[2])["ai"]
    assert block["transcript_truncated"] is True
    assert block["analysed_through_ms"] < UTTERANCES[-1][3]


def test_a_missing_recording_is_skipped_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _make_engine(tmp_path, "gone")

    result = _run(
        engine, monkeypatch, config=_configured(), backend=FakeBackend(MODEL_RESPONSE)
    )

    assert result == {"status": "skipped", "reason": "recording_not_found"}
