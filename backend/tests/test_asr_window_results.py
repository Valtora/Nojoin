from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from backend.models.pipeline import (
    RecordingAsrWindowResult,
    RecordingAsrWindowResultStatus,
)
from backend.utils.asr_window_results import (
    complete_recording_asr_window_result,
    fail_recording_asr_window_result,
    get_recording_asr_window_result,
    get_reusable_catch_up_segments,
    get_transcription_model_name,
    start_recording_asr_window_result,
)

RECORDING_ASR_WINDOW_RESULTS_SCHEMA = """
CREATE TABLE recording_asr_window_results (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    processing_run_id INTEGER,
    source_kind VARCHAR(255) NOT NULL,
    span_start_ms INTEGER NOT NULL,
    span_end_ms INTEGER NOT NULL,
    chunk_start_sequence INTEGER,
    chunk_end_sequence INTEGER,
    transcription_backend VARCHAR(255) NOT NULL,
    model_name VARCHAR(255),
    config_hash VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    error_summary TEXT,
    error_payload JSON,
    result_payload JSON,
    produced_utterance_public_ids JSON,
    started_at DATETIME,
    completed_at DATETIME,
    UNIQUE(recording_id, idempotency_key)
)
"""


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(RECORDING_ASR_WINDOW_RESULTS_SCHEMA))
    return Session(engine)


def test_get_transcription_model_name_uses_backend_specific_config():
    assert (
        get_transcription_model_name(
            {"transcription_backend": "whisper", "whisper_model_size": "base"}
        )
        == "base"
    )
    assert (
        get_transcription_model_name(
            {"transcription_backend": "parakeet", "parakeet_model": "parakeet-v3"}
        )
        == "parakeet-v3"
    )
    assert (
        get_transcription_model_name(
            {"transcription_backend": "canary", "canary_model": "canary-v2"}
        )
        == "canary-v2"
    )


def test_asr_window_result_reuses_same_row_across_start_and_complete():
    with _make_session() as session:
        started = start_recording_asr_window_result(
            session,
            recording_id=7,
            source_kind="live",
            span_start_ms=1000,
            span_end_ms=2500,
            chunk_start_sequence=2,
            chunk_end_sequence=2,
            config={
                "transcription_backend": "whisper",
                "whisper_model_size": "base",
                "processing_device": "cpu",
            },
        )
        session.commit()

        completed = complete_recording_asr_window_result(
            session,
            recording_id=7,
            source_kind="live",
            span_start_ms=1000,
            span_end_ms=2500,
            chunk_start_sequence=2,
            chunk_end_sequence=2,
            config={
                "transcription_backend": "whisper",
                "whisper_model_size": "base",
                "processing_device": "cpu",
            },
            result_payload={"segment_count": 1, "text_chars": 12},
        )
        session.commit()

        rows = session.exec(select(RecordingAsrWindowResult)).all()
        assert len(rows) == 1
        assert started is not None
        assert completed is not None
        assert rows[0].id == started.id == completed.id
        assert rows[0].status == RecordingAsrWindowResultStatus.COMPLETED
        assert rows[0].transcription_backend == "whisper"
        assert rows[0].model_name == "base"
        assert rows[0].result_payload == {"segment_count": 1, "text_chars": 12}


def test_asr_window_result_can_transition_from_failed_to_completed_retry():
    with _make_session() as session:
        failed = fail_recording_asr_window_result(
            session,
            recording_id=11,
            source_kind="catch_up",
            span_start_ms=5000,
            span_end_ms=9000,
            chunk_start_sequence=5,
            chunk_end_sequence=7,
            config={
                "transcription_backend": "parakeet",
                "parakeet_model": "parakeet-tdt-0.6b-v3",
                "processing_device": "cuda",
            },
            error_summary="ASR invocation failed.",
            error_payload={"error_type": "RuntimeError"},
        )
        session.commit()

        completed = complete_recording_asr_window_result(
            session,
            recording_id=11,
            source_kind="catch_up",
            span_start_ms=5000,
            span_end_ms=9000,
            chunk_start_sequence=5,
            chunk_end_sequence=7,
            config={
                "transcription_backend": "parakeet",
                "parakeet_model": "parakeet-tdt-0.6b-v3",
                "processing_device": "cuda",
            },
            result_payload={"segment_count": 3, "text_chars": 48},
        )
        session.commit()

        row = session.exec(select(RecordingAsrWindowResult)).one()
        assert failed is not None
        assert completed is not None
        assert row.id == failed.id == completed.id
        assert row.status == RecordingAsrWindowResultStatus.COMPLETED
        assert row.error_summary is None
        assert row.error_payload is None
        assert row.result_payload == {"segment_count": 3, "text_chars": 48}


def test_get_recording_asr_window_result_rehydrates_reusable_catch_up_segments():
    with _make_session() as session:
        complete_recording_asr_window_result(
            session,
            recording_id=12,
            source_kind="catch_up",
            span_start_ms=5000,
            span_end_ms=9000,
            chunk_start_sequence=5,
            chunk_end_sequence=7,
            config={
                "transcription_backend": "whisper",
                "whisper_model_size": "base",
                "processing_device": "cpu",
            },
            result_payload={
                "segment_count": 2,
                "text_chars": 22,
                "segments": [
                    {"start": 0.0, "end": 0.8, "speaker": "LIVE_01", "text": "hello"},
                    {"start": 1.1, "end": 1.6, "speaker": "LIVE_01", "text": "again"},
                ],
            },
        )
        session.commit()

        row = get_recording_asr_window_result(
            session,
            recording_id=12,
            source_kind="catch_up",
            span_start_ms=5000,
            span_end_ms=9000,
            chunk_start_sequence=5,
            chunk_end_sequence=7,
            config={
                "transcription_backend": "whisper",
                "whisper_model_size": "base",
                "processing_device": "cpu",
            },
        )

        assert row is not None
        assert get_reusable_catch_up_segments(row) == [
            {
                "start": 5.0,
                "end": 5.8,
                "speaker": "LIVE_01",
                "text": "hello",
                "segment_source": "catch_up",
            },
            {
                "start": 6.1,
                "end": 6.6,
                "speaker": "LIVE_01",
                "text": "again",
                "segment_source": "catch_up",
            },
        ]
