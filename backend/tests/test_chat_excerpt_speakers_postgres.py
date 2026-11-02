"""Speaker names in Meeting Chat's excerpts of other meetings, on a real PostgreSQL.

A tag widens chat retrieval to other meetings' indexed transcripts. Their
chunks keep the labels they were indexed with, so the chat route names each
line's speaker as it reads them: the recording's own name for a diarisation
label, "Unidentified speaker" for UNKNOWN, and spoken text left verbatim. The
pgvector ranking needs Postgres; skips unless NOJOIN_TEST_POSTGRES_URL names a
server, which CI sets for the backend suite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.transcripts import routes_chat
from backend.celery_app import celery_app
from backend.models.context_chunk import ContextChunk
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import RecordingSpeaker
from backend.models.tag import RecordingTag, Tag
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.text_embedding_version import TEXT_EMBEDDING_DIMENSIONS
from backend.tests.postgres_setup import migrate_empty_database
from backend.tests.test_timeline_notes_api import _session_user
from backend.tests.test_timeline_notes_migration_postgres import _psycopg_url

OWNER_ID = 1
THIS, OTHER, TAG = 10, 11, 5
VECTOR = [1.0] * TEXT_EMBEDDING_DIMENSIONS


@pytest.fixture
def database(postgres_test_url: str) -> Iterator[str]:
    """A database of its own, migrated to head."""
    admin_url = _psycopg_url(postgres_test_url)
    name = f"nojoin_chat_excerpts_{uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        migrate_empty_database(url, "head")
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def maker(database: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        make_url(database).set(drivername="postgresql+asyncpg")
    )
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(maker: async_sessionmaker[AsyncSession], chunk_text: str) -> None:
    async with maker() as session:
        session.add(
            User(
                id=OWNER_ID,
                username="mira",
                hashed_password="x",
                role="owner",
                settings={
                    "llm_provider": "ollama",
                    "ollama_api_url": "http://ollama.invalid",
                    "ollama_model": "qwen3",
                },
            )
        )
        await session.flush()
        for row_id, name in ((THIS, "This meeting"), (OTHER, "Planning")):
            session.add(
                Recording(
                    id=row_id,
                    name=name,
                    public_id=f"rec-{row_id}",
                    meeting_uid=f"m-{row_id}",
                    audio_path=f"/data/{row_id}.wav",
                    status=RecordingStatus.PROCESSED,
                    user_id=OWNER_ID,
                )
            )
        session.add(Tag(id=TAG, name="Roadmap", user_id=OWNER_ID))
        await session.flush()
        session.add(
            Transcript(recording_id=THIS, notes="", transcript_status="completed")
        )
        session.add(RecordingTag(recording_id=THIS, tag_id=TAG))
        session.add(RecordingTag(recording_id=OTHER, tag_id=TAG))
        session.add(
            RecordingSpeaker(
                recording_id=OTHER, diarization_label="SPEAKER_00", local_name="Dana"
            )
        )
        session.add(
            ContextChunk(
                recording_id=OTHER,
                content=chunk_text,
                embedding=VECTOR,
                meta={"source": "transcript", "start": 0.0, "end": 9.0},
            )
        )
        await session.commit()


class _Provider:
    """The LLM edge: records the question chat sends and answers once."""

    def __init__(self) -> None:
        self.questions: list[str] = []

    def ask_question_streaming(self, **kwargs):
        self.questions.append(kwargs["user_question"])
        yield "Noted."


def _send_task(name, args=None, **kwargs):
    assert name.endswith("get_text_embedding_task"), name
    return SimpleNamespace(get=lambda timeout=None: [VECTOR])


async def _chat(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> str:
    provider = _Provider()
    monkeypatch.setattr(celery_app, "send_task", _send_task)
    monkeypatch.setattr(routes_chat, "async_session_maker", maker)
    monkeypatch.setattr(
        "backend.api.v1.endpoints.transcripts.get_llm_backend_with_secondary",
        lambda config: provider,
    )
    application = FastAPI()
    application.include_router(api_router, prefix="/api/v1")

    async def _db() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    application.dependency_overrides[get_db] = _db
    application.dependency_overrides[get_current_user] = _session_user(OWNER_ID)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/api/v1/transcripts/rec-{THIS}/chat",
            json={"message": "What did Planning decide?", "tag_ids": [TAG]},
        )
    assert response.status_code == 200
    assert "[DONE]" in response.text
    (question,) = provider.questions
    return question


@pytest.mark.anyio
async def test_other_meeting_excerpts_name_their_speakers_and_keep_spoken_labels(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(
        maker,
        "SPEAKER_00: We ship the context layer in Q3.\n"
        "UNKNOWN: Agreed, and say UNKNOWN: and SPEAKER_00: as written.",
    )

    question = await _chat(maker, monkeypatch)

    assert (
        "--- From Planning ---\n"
        "Dana: We ship the context layer in Q3.\n"
        "Unidentified speaker: Agreed, and say UNKNOWN: and SPEAKER_00: as written."
    ) in question
