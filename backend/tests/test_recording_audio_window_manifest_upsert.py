from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, event, text
from sqlmodel import Session

from backend.tests.sqlite_schemas import RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA
from backend.utils.audio_windows import (
    WINDOW_ASR_STATUS_PENDING,
    WINDOW_DIARIZATION_STATUS_PENDING,
    WINDOW_STATUS_PENDING,
)
from backend.utils.db_batching import MAX_BIND_PARAMS
from backend.utils.recording_audio_sync import _upsert_window_manifests
from backend.utils.time import utc_now

RECORDING_ID = 121


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA))
    return Session(engine)


def _payload(
    window_index: int, *, status: str = WINDOW_STATUS_PENDING
) -> dict[str, Any]:
    now = utc_now()
    return {
        "recording_id": RECORDING_ID,
        "window_index": window_index,
        "source_kind": "browser",
        "target_window_ms": 20_000,
        "hop_ms": 5_000,
        "window_start_ms": window_index * 5_000,
        "window_end_ms": window_index * 5_000 + 20_000,
        "chunk_start_sequence": window_index,
        "chunk_end_sequence": window_index + 4,
        "is_partial": False,
        "is_sealed": False,
        "created_at": now,
        "updated_at": now,
        "public_id": f"window-{window_index:05d}",
        "status": status,
        "asr_status": WINDOW_ASR_STATUS_PENDING,
        "asr_processing_run_id": None,
        "asr_last_error": None,
        "diarization_status": WINDOW_DIARIZATION_STATUS_PENDING,
        "diarization_processing_run_id": None,
        "diarization_config_hash": None,
        "diarization_window_result_id": None,
        "diarization_last_error": None,
        "processing_run_id": None,
        "last_error": None,
    }


def _record_bind_counts(session: Session) -> list[int]:
    counts: list[int] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, *_):
        if parameters is None:
            return
        if "INSERT INTO recording_audio_window_manifests" in statement:
            counts.append(len(parameters))

    return counts


def test_upsert_window_manifests_persists_a_two_hour_recording():
    # A two-hour meeting at the default 20s window and 5s hop produces roughly
    # 1440 windows, which overflowed the 32767 bind parameter ceiling when the
    # upsert was issued as a single statement.
    window_count = 1600
    session = _make_session()
    bind_counts = _record_bind_counts(session)

    rows = _upsert_window_manifests(
        session,
        recording_id=RECORDING_ID,
        manifest_payloads=[_payload(index) for index in range(window_count)],
    )
    session.commit()

    assert len(rows) == window_count
    assert [int(row.window_index) for row in rows] == list(range(window_count))
    assert len(bind_counts) > 1, "expected the upsert to be split across statements"
    assert max(bind_counts) <= MAX_BIND_PARAMS


def test_upsert_window_manifests_updates_rows_across_batch_boundaries():
    window_count = 1600
    session = _make_session()

    _upsert_window_manifests(
        session,
        recording_id=RECORDING_ID,
        manifest_payloads=[_payload(index) for index in range(window_count)],
    )
    session.commit()

    rows = _upsert_window_manifests(
        session,
        recording_id=RECORDING_ID,
        manifest_payloads=[
            _payload(index, status="complete") for index in range(window_count)
        ],
    )
    session.commit()

    assert len(rows) == window_count
    assert {row.status for row in rows} == {"complete"}


def test_upsert_window_manifests_returns_empty_for_no_payloads():
    session = _make_session()

    assert (
        _upsert_window_manifests(
            session, recording_id=RECORDING_ID, manifest_payloads=[]
        )
        == []
    )
