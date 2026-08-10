"""Selection tests for the transcript index backfill sweep.

An embedding-version cutover deletes every context chunk, and ordinary
transcript indexing runs only at processing time, so the sweep is what
brings an existing library back into semantic search. These tests pin the
selection: only PROCESSED, non-deleted recordings with transcript text and
no current-version transcript chunks qualify — notes and document chunks
do not count as coverage.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlmodel import Session, create_engine

from backend.celery_app import celery_app
from backend.models import registry  # noqa: F401 - registers all model mappers
from backend.models.context_chunk import ContextChunk
from backend.processing.text_embedding_version import TEXT_EMBEDDING_VERSION
from backend.worker.tasks.intelligence import recording_ids_missing_transcript_index

_SCHEMA = """
CREATE TABLE recordings (
    max_speakers INTEGER,
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    name VARCHAR(255) NOT NULL, public_id VARCHAR(36) NOT NULL, meeting_uid VARCHAR(36) NOT NULL,
    audio_path VARCHAR(1024) NOT NULL, proxy_path VARCHAR(1024), celery_task_id VARCHAR(255),
    duration_seconds FLOAT, file_size_bytes INTEGER, status VARCHAR(32) NOT NULL,
    client_status VARCHAR(32), upload_progress INTEGER NOT NULL, processing_progress INTEGER NOT NULL,
    processing_step VARCHAR(255), processing_started_at TIMESTAMP, processing_completed_at TIMESTAMP,
    pipeline_generation VARCHAR(32) DEFAULT 'unified', is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL, last_activity_at TIMESTAMP, user_id INTEGER, calendar_event_id INTEGER
);
CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    recording_id INTEGER NOT NULL UNIQUE, text TEXT, segments JSON, notes TEXT,
    user_notes TEXT, meeting_edge_focus TEXT, meeting_edge_payload JSON,
    meeting_edge_status VARCHAR DEFAULT 'idle', meeting_edge_error_message TEXT,
    meeting_edge_source_signature TEXT, speaker_name_suggestions JSON,
    notes_template_id INTEGER, notes_template_sections TEXT, notes_status VARCHAR,
    notes_stale_documents BOOLEAN DEFAULT 0, transcript_status VARCHAR, error_message TEXT,
    analytics_payload JSON,
    analytics_status VARCHAR NOT NULL DEFAULT 'pending',
    analytics_ai_status VARCHAR NOT NULL DEFAULT 'pending',
    analytics_ai_error_message TEXT,
    analytics_error_message TEXT
);
CREATE TABLE context_chunks (
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    recording_id INTEGER NOT NULL, document_id INTEGER, document_page_id INTEGER,
    content TEXT NOT NULL, embedding BLOB, meta JSON, embedding_version INTEGER
);
"""

_NOW = datetime(2026, 8, 1, 12, 0, 0)


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(text(statement))
    return Session(engine)


def _seed_recording(
    session: Session,
    *,
    recording_id: int,
    status: str = "PROCESSED",
    is_deleted: bool = False,
    transcript_text: str = "we discussed the roadmap",
) -> None:
    session.execute(
        text(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id, "
            "meeting_uid, audio_path, status, upload_progress, processing_progress, "
            "is_archived, is_deleted, user_id) VALUES (:id, :n, :n, :name, :pid, "
            ":uid, :path, :status, 100, 100, 0, :deleted, 1)"
        ),
        {
            "id": recording_id,
            "n": _NOW,
            "name": f"Rec {recording_id}",
            "pid": f"p{recording_id}",
            "uid": f"m{recording_id}",
            "path": f"/a{recording_id}.wav",
            "status": status,
            "deleted": is_deleted,
        },
    )
    session.execute(
        text(
            "INSERT INTO transcripts (id, created_at, updated_at, recording_id, "
            "text, segments, notes_status, transcript_status) VALUES "
            "(:id, :n, :n, :id, :text, '[]', 'completed', 'completed')"
        ),
        {"id": recording_id, "n": _NOW, "text": transcript_text},
    )


def test_sweep_selects_only_unindexed_processed_recordings():
    session = _make_session()

    # 1: no chunks at all -> selected.
    _seed_recording(session, recording_id=1)
    # 2: current-version transcript chunk -> covered, not selected.
    _seed_recording(session, recording_id=2)
    session.add(
        ContextChunk(
            recording_id=2,
            content="covered",
            meta={"source": "transcript"},
            embedding_version=TEXT_EMBEDDING_VERSION,
        )
    )
    # 3: notes chunk only -> transcript still unindexed, selected.
    _seed_recording(session, recording_id=3)
    session.add(
        ContextChunk(
            recording_id=3,
            content="notes",
            meta={"source": "notes"},
            embedding_version=TEXT_EMBEDDING_VERSION,
        )
    )
    # 4: stale-version transcript chunk -> incomparable vectors, selected.
    _seed_recording(session, recording_id=4)
    session.add(
        ContextChunk(
            recording_id=4,
            content="old",
            meta={"source": "transcript"},
            embedding_version=TEXT_EMBEDDING_VERSION - 1,
        )
    )
    # 5: binned -> never selected.
    _seed_recording(session, recording_id=5, is_deleted=True)
    # 6: still processing -> not selected.
    _seed_recording(session, recording_id=6, status="PROCESSING")
    # 7: empty transcript text -> nothing to index, not selected.
    _seed_recording(session, recording_id=7, transcript_text="")
    session.commit()

    missing = recording_ids_missing_transcript_index(session, 10)
    assert sorted(missing) == [1, 3, 4]

    # The batch bound is respected.
    assert len(recording_ids_missing_transcript_index(session, 2)) == 2


def test_sweep_is_registered_on_the_io_lane_and_beat():
    routes = celery_app.conf.task_routes
    assert routes["backend.worker.tasks.index_missing_transcripts_task"] == {
        "queue": "io"
    }
    beat = celery_app.conf.beat_schedule["index-missing-transcripts-every-15m"]
    assert beat["task"] == "backend.worker.tasks.index_missing_transcripts_task"
    assert beat["schedule"] == 900.0
