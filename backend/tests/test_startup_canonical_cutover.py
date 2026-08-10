"""Regression tests for the startup canonical cutover driver.

The driver previously bound its sessions to the same connection that held
the Postgres advisory lock. Acquiring the lock autobegan a connection-level
transaction, every session then joined it via savepoints, and the whole
sweep was rolled back when the connection closed on exit -- classifications
never persisted, so each boot re-ran the entire sweep. These tests run the
driver end to end against a file-backed SQLite database, patch the advisory
lock helper so it executes a statement on the lock connection exactly like
the Postgres implementation does, and assert durability from fresh
connections after the driver returns.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

import backend.startup_canonical_cutover as cutover
from backend.tests.sqlite_schemas import (
    DIARIZATION_WINDOW_RESULTS_SCHEMA,
    DIARIZATION_WINDOW_TURNS_SCHEMA,
    GLOBAL_SPEAKERS_SCHEMA,
    PROCESSING_RUNS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    SPEAKER_CORRECTION_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USER_TASKS_SCHEMA,
    USERS_SCHEMA,
)

SCHEMAS = (
    USERS_SCHEMA,
    USER_TASKS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    GLOBAL_SPEAKERS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    PROCESSING_RUNS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    SPEAKER_CORRECTION_EVENTS_SCHEMA,
    DIARIZATION_WINDOW_RESULTS_SCHEMA,
    DIARIZATION_WINDOW_TURNS_SCHEMA,
)


@contextmanager
def _statement_executing_advisory_lock(connection):
    # Mirror the Postgres helper: executing the lock statement is what used
    # to autobegin the doomed outer transaction on the shared connection.
    connection.execute(text("SELECT 1"))
    yield


def _build_engine(tmp_path: Path) -> Engine:
    # File-backed so the driver's lock connection and per-batch sessions use
    # genuinely independent connections, as they do against Postgres.
    engine = create_engine(f"sqlite:///{tmp_path / 'cutover.db'}")
    with engine.begin() as connection:
        for schema in SCHEMAS:
            connection.execute(text(schema))
    return engine


def _seed_admin_and_legacy_recording(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, created_at, updated_at, username, hashed_password,
                    is_active, is_superuser, force_password_change, role,
                    token_version, settings, has_seen_demo_recording
                ) VALUES (
                    1, :now, :now, 'owner', 'hash', 1, 1, 0, 'owner', 0,
                    '{}', 1
                )
                """
            ),
            {"now": "2026-05-19 00:00:00"},
        )
        connection.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (
                    1, :now, :now, 'Legacy meeting', 'legacy-rec',
                    'meeting-uid-1', '/tmp/legacy.wav', 'PROCESSED', 0, 100,
                    NULL, 0, 0, 1
                )
                """
            ),
            {"now": "2026-05-19 00:00:00"},
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    meeting_edge_status, notes_status, transcript_status
                ) VALUES (
                    1, :now, :now, 1, 'hello there', :segments, 'idle',
                    'pending', 'completed'
                )
                """
            ),
            {
                "now": "2026-05-19 00:00:00",
                "segments": (
                    '[{"start": 0.0, "end": 1.2, "speaker": "SPEAKER_00",'
                    ' "text": "hello there", "segment_source": "legacy"}]'
                ),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    speaker_status, speaker_kind, identity_locked
                ) VALUES (
                    1, :now, :now, 'speaker-public-1', 1, NULL, 'SPEAKER_00',
                    NULL, 'Speaker 1', 'active', 'automated', 0
                )
                """
            ),
            {"now": "2026-05-19 00:00:00"},
        )


def _patch_driver(monkeypatch, engine: Engine) -> None:
    monkeypatch.setattr(cutover, "sync_engine", engine)
    monkeypatch.setattr(cutover, "wait_for_database_connection", lambda: None)
    monkeypatch.setattr(cutover, "_advisory_lock", _statement_executing_advisory_lock)


def test_cutover_results_survive_connection_close(tmp_path, monkeypatch) -> None:
    engine = _build_engine(tmp_path)
    _seed_admin_and_legacy_recording(engine)
    _patch_driver(monkeypatch, engine)

    summary = cutover.run_startup_canonical_cutover()

    assert summary["backfilled"] == 1

    # Read back through fresh connections: the driver has fully returned, so
    # anything visible now genuinely committed.
    with engine.connect() as connection:
        generation = connection.execute(
            text("SELECT pipeline_generation FROM recordings WHERE id = 1")
        ).scalar_one()
        utterances = connection.execute(
            text("SELECT COUNT(*) FROM transcript_utterances WHERE recording_id = 1")
        ).scalar_one()

    assert generation == "legacy_backfilled"
    assert utterances == 1


def test_cutover_second_run_is_a_noop(tmp_path, monkeypatch) -> None:
    engine = _build_engine(tmp_path)
    _seed_admin_and_legacy_recording(engine)
    _patch_driver(monkeypatch, engine)

    first_summary = cutover.run_startup_canonical_cutover()
    second_summary = cutover.run_startup_canonical_cutover()

    assert first_summary["backfilled"] == 1
    assert second_summary["backfilled"] == 0
    assert second_summary["already_canonical"] == 0


def test_cutover_creates_no_tasks(tmp_path, monkeypatch) -> None:
    # The sweep used to hand admins a companion-retirement notice at every boot
    # until they had been marked as having seen it. That announcement is gone;
    # booting must not put anything in anybody's task list.
    engine = _build_engine(tmp_path)
    _seed_admin_and_legacy_recording(engine)
    _patch_driver(monkeypatch, engine)

    cutover.run_startup_canonical_cutover()
    cutover.run_startup_canonical_cutover()

    with engine.connect() as connection:
        tasks = connection.execute(text("SELECT COUNT(*) FROM user_tasks")).scalar_one()

    assert tasks == 0
