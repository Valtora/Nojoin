"""Worker-side tests for meeting_chat_task (CLI OAuth chat in the io lane).

Verifies the streaming publish sequence, that the assistant turn is persisted
exactly once (the worker is the single writer), and that a backend failure
publishes a friendly error and persists nothing. The backend, resolver and
publisher are faked, so no Claude Agent SDK / redis is required.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

import backend.worker.tasks as tasks_module
from backend.models.chat import ChatMessage

_SCHEMA = """
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
    has_seen_companion_retirement_notice BOOLEAN NOT NULL DEFAULT 0,
    invitation_id INTEGER
);
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
    analytics_error_message TEXT
);
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role VARCHAR NOT NULL,
    content TEXT NOT NULL
);
"""

_NOW = "2026-07-07 00:00:00"


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    with engine.begin() as conn:
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(text(statement))
        conn.execute(
            text(
                "INSERT INTO users (id, created_at, updated_at, username, "
                "hashed_password, is_active, is_superuser, force_password_change, "
                "role, token_version, settings, has_seen_demo_recording) VALUES "
                "(1, :n, :n, 'alex', 'x', 1, 0, 0, 'owner', 0, '{}', 0)"
            ),
            {"n": _NOW},
        )
        conn.execute(
            text(
                "INSERT INTO recordings (id, created_at, updated_at, name, public_id, "
                "meeting_uid, audio_path, status, upload_progress, processing_progress, "
                "is_archived, is_deleted, user_id) VALUES "
                "(1, :n, :n, 'Rec', 'p1', 'm1', '/a.wav', 'PROCESSED', 100, 100, 0, 0, 1)"
            ),
            {"n": _NOW},
        )
        conn.execute(
            text(
                "INSERT INTO transcripts (id, created_at, updated_at, recording_id, "
                "segments, notes, meeting_edge_status, notes_status, transcript_status) "
                "VALUES (1, :n, :n, 1, '[]', 'Meeting notes here', 'idle', 'complete', "
                "'complete')"
            ),
            {"n": _NOW},
        )
    return engine


class _FakeBackend:
    def __init__(self, chunks=None, raise_exc=None):
        self.chunks = list(chunks or [])
        self.raise_exc = raise_exc
        self.received: dict = {}

    def ask_question_streaming(
        self,
        *,
        user_question,
        meeting_notes,
        diarized_transcript,
        conversation_history,
        recording_id,
    ):
        self.received = {
            "user_question": user_question,
            "meeting_notes": meeting_notes,
            "recording_id": recording_id,
            "conversation_history": conversation_history,
        }
        if self.raise_exc:
            raise self.raise_exc
        yield from self.chunks


class _FakePublisher:
    instances: list["_FakePublisher"] = []

    def __init__(self, task_id):
        self.task_id = task_id
        self.events: list[tuple] = []
        _FakePublisher.instances.append(self)

    def publish_token(self, text):
        self.events.append(("tok", text))

    def publish_done(self):
        self.events.append(("done", None))

    def publish_error(self, message):
        self.events.append(("err", message))

    def close(self):
        self.events.append(("close", None))


def _wire(monkeypatch, engine, backend):
    tasks_module.meeting_chat_task._session = None  # fresh session per run
    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(
        "backend.worker.tasks.chat.resolve_llm_config", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "backend.worker.tasks.chat._llm_backend_from_config", lambda cfg: backend
    )
    _FakePublisher.instances.clear()
    monkeypatch.setattr("backend.worker.tasks.chat.ChatStreamPublisher", _FakePublisher)


def _chat_rows(engine):
    with Session(engine) as session:
        return session.exec(
            select(ChatMessage).where(ChatMessage.recording_id == 1)
        ).all()


def test_streams_tokens_persists_single_assistant_message(monkeypatch):
    engine = _engine()
    backend = _FakeBackend(chunks=["Hel", "lo"])
    _wire(monkeypatch, engine, backend)

    tasks_module.meeting_chat_task.run(
        1, "What happened?", [{"role": "user", "parts": [{"text": "hi"}]}]
    )

    pub = _FakePublisher.instances[0]
    assert [e for e in pub.events if e[0] != "close"] == [
        ("tok", "Hel"),
        ("tok", "lo"),
        ("done", None),
    ]
    assert pub.events[-1] == ("close", None)  # always closed
    assert backend.received["user_question"] == "What happened?"
    assert backend.received["meeting_notes"] == "Meeting notes here"
    assert backend.received["recording_id"] == 1

    rows = _chat_rows(engine)
    assert len(rows) == 1
    assert rows[0].role == "assistant"
    assert rows[0].content == "Hello"


def test_backend_failure_publishes_error_and_persists_nothing(monkeypatch):
    engine = _engine()
    backend = _FakeBackend(raise_exc=RuntimeError("HTTP 429 rate limit exceeded"))
    _wire(monkeypatch, engine, backend)

    tasks_module.meeting_chat_task.run(1, "q", None)

    pub = _FakePublisher.instances[0]
    kinds = [e[0] for e in pub.events]
    assert "err" in kinds
    err_message = next(e[1] for e in pub.events if e[0] == "err")
    assert "rate limit" in err_message.lower()
    assert ("close", None) in pub.events
    assert _chat_rows(engine) == []


def test_no_text_response_persists_nothing(monkeypatch):
    engine = _engine()
    backend = _FakeBackend(chunks=[])  # model returned nothing
    _wire(monkeypatch, engine, backend)

    tasks_module.meeting_chat_task.run(1, "q", None)

    pub = _FakePublisher.instances[0]
    assert ("done", None) in pub.events
    assert _chat_rows(engine) == []
