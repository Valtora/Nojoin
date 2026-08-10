"""Tests for the delivery-analytics worker task.

The DSP itself is covered in test_delivery_descriptors. What is pinned here is
the task's contract with the rest of the system: that it records a watermark
so staleness is detectable, that it never leaves a transcript stuck mid-run,
and that a recording whose audio is gone reaches a terminal state rather than
being retried forever.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

import backend.worker.tasks.analytics as analytics_task
from backend.processing.delivery_descriptors import DELIVERY_METHOD_VERSION
from backend.tests.sqlite_schemas import (
    RECORDINGS_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)

SAMPLE_RATE = 16_000

RECORDING_AUDIO_CHUNKS_SCHEMA = """
CREATE TABLE recording_audio_chunks (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36),
    recording_id INTEGER NOT NULL,
    sequence_no INTEGER NOT NULL,
    source_kind VARCHAR(32) NOT NULL DEFAULT 'browser',
    absolute_start_ms INTEGER NOT NULL,
    absolute_end_ms INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    sample_rate_hz INTEGER NOT NULL,
    channel_count INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 VARCHAR(128) NOT NULL,
    storage_path TEXT NOT NULL,
    upload_status VARCHAR(32) NOT NULL DEFAULT 'received',
    idempotency_key VARCHAR(255),
    received_at DATETIME,
    cleanup_eligible_at DATETIME
)
"""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def voiced_tone(f0_hz: float, duration_s: float) -> np.ndarray:
    t = np.arange(int(SAMPLE_RATE * duration_s)) / SAMPLE_RATE
    signal = np.zeros_like(t)
    for harmonic in range(1, 12):
        signal += np.sin(2 * math.pi * f0_hz * harmonic * t) / harmonic
    return (signal / (np.max(np.abs(signal)) or 1.0) * 0.3).astype(np.float32)


def _make_engine(tmp_path: Path, name: str) -> Any:
    engine = create_engine(f"sqlite:///{tmp_path / name}.sqlite", future=True)
    with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
            RECORDING_AUDIO_CHUNKS_SCHEMA,
        ):
            connection.execute(text(schema))
    return engine


def _seed(connection, *, audio_path: str, utterance_count: int = 6) -> None:
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
            "(10, :ts, :ts, 'Sync', 'rec-10', 'uid-10', :audio, 'PROCESSED', 100, "
            "100, 0, 0, 1, 60.0)"
        ),
        {"ts": _now(), "audio": audio_path},
    )
    connection.execute(
        text(
            "INSERT INTO transcripts (id, created_at, updated_at, recording_id, "
            "segments, notes_status, transcript_status, analytics_status) VALUES "
            "(1, :ts, :ts, 10, '[]', 'completed', 'completed', 'pending')"
        ),
        {"ts": _now()},
    )
    for index in range(utterance_count):
        connection.execute(
            text(
                "INSERT INTO transcript_utterances (id, created_at, updated_at, "
                "public_id, recording_id, sort_key, start_ms, end_ms, text, "
                "speaker_label, recording_speaker_id, state, source_kind, revision, "
                "manual_text_locked, manual_speaker_locked, speaker_assignment_source, "
                "speaker_assignment_authority, overlap_rank) VALUES "
                "(:id, :ts, :ts, :pid, 10, :sort, :start, :end, 'one two three four "
                "five six', 'SPEAKER_00', 1, 'stable', 'final', 1, 0, 0, 'final', "
                "'final', 0)"
            ),
            {
                "id": index + 1,
                "pid": f"u-{index}",
                "sort": f"{index:04d}",
                "start": index * 3_000,
                "end": index * 3_000 + 2_000,
                "ts": _now(),
            },
        )
    connection.execute(
        text(
            "INSERT INTO transcript_utterance_events (id, created_at, updated_at, "
            "recording_id, utterance_id, event_type, source, resulting_revision) "
            "VALUES (7, :ts, :ts, 10, 1, 'create', 'system', 1)"
        ),
        {"ts": _now()},
    )


def _run(engine, monkeypatch) -> dict:
    monkeypatch.setattr(analytics_task, "get_sync_session", lambda: Session(engine))
    return analytics_task.compute_delivery_analytics_task.run(10)


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    import soundfile as sf

    path = tmp_path / "meeting.wav"
    sf.write(str(path), voiced_tone(150.0, 40.0), SAMPLE_RATE)
    return path


def _transcript_row(engine) -> Any:
    with engine.begin() as connection:
        return connection.execute(
            text(
                "SELECT analytics_status, analytics_payload, analytics_error_message "
                "FROM transcripts WHERE recording_id = 10"
            )
        ).one()


def test_measures_delivery_and_records_the_watermark(
    tmp_path: Path, audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The watermark is what makes staleness detectable without a second column."""
    engine = _make_engine(tmp_path, "ok")
    with engine.begin() as connection:
        _seed(connection, audio_path=str(audio_file))

    result = _run(engine, monkeypatch)

    assert result["status"] == "success"
    status, payload, error = _transcript_row(engine)
    assert status == "completed"
    assert error is None

    import json

    stored = json.loads(payload) if isinstance(payload, str) else payload
    assert stored["event_watermark"] == 7
    # Stamped so a payload produced by an older extraction procedure is
    # identifiable rather than silently mixed with current ones.
    assert stored["method_version"] == DELIVERY_METHOD_VERSION
    speaker = stored["delivery"]["speakers"]["rs:1"]
    assert abs(speaker["median_f0_hz"] - 150) / 150 < 0.05


def test_missing_audio_is_terminal_rather_than_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recording whose audio is gone can never produce these figures, so the
    state has to say so instead of leaving the interface waiting."""
    engine = _make_engine(tmp_path, "no-audio")
    with engine.begin() as connection:
        _seed(connection, audio_path=str(tmp_path / "does-not-exist.wav"))

    result = _run(engine, monkeypatch)

    assert result["status"] == "skipped"
    status, payload, error = _transcript_row(engine)
    assert status == "error"
    assert "no longer available" in error
    assert payload is None


def test_a_failure_mid_run_does_not_leave_the_transcript_generating(
    tmp_path: Path, audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generating is the one state the interface cannot recover from on its own."""
    engine = _make_engine(tmp_path, "boom")
    with engine.begin() as connection:
        _seed(connection, audio_path=str(audio_file))

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("analysis blew up")

    monkeypatch.setattr(
        "backend.processing.delivery_descriptors.analyse_delivery", _explode
    )

    result = _run(engine, monkeypatch)

    assert result["status"] == "error"
    status, _, error = _transcript_row(engine)
    assert status == "error"
    assert error


def test_a_missing_recording_is_skipped_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _make_engine(tmp_path, "gone")

    result = _run(engine, monkeypatch)

    assert result == {"status": "skipped", "reason": "recording_not_found"}
