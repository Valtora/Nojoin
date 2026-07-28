"""Endpoint test for the chat CLI-OAuth dispatch branch.

Invokes the chat handler directly (bypassing the ASGI layer) with an async
SQLite session. The pgvector RAG call and the LLM resolver are patched so the
handler reaches the provider branch; the CLI path then drives the *real* relay
against a fake redis, proving the response is byte-identical to the inline
contract and that a non-CLI provider is never dispatched to the worker.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.services.chat_relay as chat_relay
from backend.api.v1.endpoints.transcripts import routes_chat
from backend.api.v1.endpoints.transcripts.helpers import ChatRequest
from backend.celery_app import celery_app

# Register every ORM model so mappers configure via the recording/user FKs.
from backend.models import registry  # noqa: F401
from backend.utils.llm_config import ResolvedLLMConfig

_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    username VARCHAR NOT NULL, hashed_password VARCHAR NOT NULL, is_active BOOLEAN NOT NULL,
    is_superuser BOOLEAN NOT NULL, force_password_change BOOLEAN NOT NULL, role VARCHAR NOT NULL,
    token_version INTEGER NOT NULL, settings JSON, has_seen_demo_recording BOOLEAN NOT NULL,
    has_seen_companion_retirement_notice BOOLEAN NOT NULL DEFAULT 0,
    invitation_id INTEGER
);
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
    recording_id INTEGER NOT NULL UNIQUE, text TEXT, segments JSON NOT NULL, notes TEXT,
    user_notes TEXT, meeting_edge_focus TEXT, meeting_edge_payload JSON,
    meeting_edge_status VARCHAR NOT NULL DEFAULT 'idle', meeting_edge_error_message TEXT,
    meeting_edge_source_signature TEXT, speaker_name_suggestions JSON,
    notes_template_id INTEGER, notes_template_sections TEXT, notes_status VARCHAR NOT NULL,
    transcript_status VARCHAR NOT NULL, error_message TEXT
);
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    recording_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role VARCHAR NOT NULL, content TEXT NOT NULL
);
"""

_NOW = "2026-07-07 00:00:00"


async def _make_session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                await conn.execute(text(statement))
        await conn.execute(
            text(
                "INSERT INTO users (id, created_at, updated_at, username, hashed_password, "
                "is_active, is_superuser, force_password_change, role, token_version, settings, "
                "has_seen_demo_recording) VALUES (1, :n, :n, 'alex', 'x', 1, 0, 0, 'owner', 0, "
                "'{}', 0)"
            ),
            {"n": _NOW},
        )
        await conn.execute(
            text(
                "INSERT INTO recordings (id, created_at, updated_at, name, public_id, meeting_uid, "
                "audio_path, status, upload_progress, processing_progress, is_archived, is_deleted, "
                "user_id) VALUES (1, :n, :n, 'Rec', 'p1', 'm1', '/a.wav', 'PROCESSED', 100, 100, 0, "
                "0, 1)"
            ),
            {"n": _NOW},
        )
        await conn.execute(
            text(
                "INSERT INTO transcripts (id, created_at, updated_at, recording_id, segments, notes, "
                "meeting_edge_status, notes_status, transcript_status) VALUES (1, :n, :n, 1, '[]', "
                "'Notes', 'idle', 'complete', 'complete')"
            ),
            {"n": _NOW},
        )
    return engine, sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class _FakeAsyncRedis:
    def __init__(self, store):
        self.store = store

    async def blpop(self, key, timeout=0):
        items = self.store.get(key)
        if items:
            return (key, items.pop(0))
        return None

    async def aclose(self):
        pass


def _cli_config():
    return ResolvedLLMConfig(
        provider="cli",
        api_key=None,
        model="claude-sonnet-5",
        api_url=None,
        merged_config={},
        cli_user_id=1,
    )


def _rag_and_dispatch_stubs(dispatched):
    """A fake celery send_task: RAG embedding fails fast; chat returns an id."""

    def _send_task(name, args=None, **kwargs):
        if name.endswith("get_text_embedding_task"):
            return SimpleNamespace(
                get=lambda timeout=None: (_ for _ in ()).throw(RuntimeError("no rag"))
            )
        dispatched.append((name, args))
        return SimpleNamespace(id="task-1")

    return _send_task


def test_cli_provider_dispatches_and_relays_byte_identical(monkeypatch):
    async def _run():
        engine, maker = await _make_session_maker()
        dispatched: list = []
        monkeypatch.setattr(
            routes_chat,
            "resolve_llm_config_async",
            lambda *a, **k: _fake_async(_cli_config()),
        )
        monkeypatch.setattr(
            celery_app, "send_task", _rag_and_dispatch_stubs(dispatched)
        )
        monkeypatch.setattr(
            "backend.models.task.register_task_ownership",
            lambda *a, **k: _fake_async(None),
        )
        store = {
            "nojoin:cli_chat:task-1": [
                '{"t": "tok", "v": "Hi"}',
                '{"t": "done"}',
            ]
        }
        monkeypatch.setattr(
            chat_relay.aioredis, "from_url", lambda *a, **k: _FakeAsyncRedis(store)
        )

        user = SimpleNamespace(id=1, settings={"usage_model": "cli_oauth"})
        async with maker() as session:
            response = await routes_chat.chat_with_meeting(
                "p1", ChatRequest(message="Hello?"), db=session, current_user=user
            )
            frames = [chunk async for chunk in response.body_iterator]

        assert frames == ['data: {"token": "Hi"}\n\n', "data: [DONE]\n\n"]
        # Dispatched exactly the chat task with (recording_id, augmented_message, history).
        assert dispatched == [
            ("backend.worker.tasks.meeting_chat_task", [1, "Hello?", []])
        ]
        await engine.dispose()

    asyncio.run(_run())


def test_non_cli_provider_is_not_dispatched(monkeypatch):
    async def _run():
        engine, maker = await _make_session_maker()
        dispatched: list = []
        ollama = ResolvedLLMConfig(
            provider="ollama",
            api_key=None,
            model="llama3",
            api_url="http://x",
            merged_config={},
        )
        monkeypatch.setattr(
            routes_chat, "resolve_llm_config_async", lambda *a, **k: _fake_async(ollama)
        )
        monkeypatch.setattr(
            celery_app, "send_task", _rag_and_dispatch_stubs(dispatched)
        )
        monkeypatch.setattr(routes_chat, "async_session_maker", maker)

        class _FakeBackend:
            def ask_question_streaming(self, **kwargs):
                yield "answer"

        monkeypatch.setattr(
            "backend.api.v1.endpoints.transcripts.get_llm_backend_with_secondary",
            lambda cfg: _FakeBackend(),
        )

        user = SimpleNamespace(id=1, settings={})
        async with maker() as session:
            response = await routes_chat.chat_with_meeting(
                "p1", ChatRequest(message="Hi"), db=session, current_user=user
            )
            frames = [chunk async for chunk in response.body_iterator]

        # Inline (non-CLI) path streamed, and no chat task was dispatched.
        assert any('"token": "answer"' in f for f in frames)
        assert not any(name.endswith("meeting_chat_task") for name, _ in dispatched)
        await engine.dispose()

    asyncio.run(_run())


async def _fake_async(value):
    return value
