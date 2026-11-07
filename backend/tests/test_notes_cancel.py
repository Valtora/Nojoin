"""Cancel at the real API/worker boundary, with Postgres serialising writers.

Only the model and Celery dispatch are replaced. Worker sessions, public reads,
independent edits and row locks all use the migrated production schema.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import requests
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import Session, select

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services.timeline_note_service import load_recording_timeline_notes
from backend.tests.postgres_setup import migrate_empty_database
from backend.tests.test_ollama_context_window import (
    FITS,
    _answer,
    _llama_server_overflow,
    _refusal,
    _Response,
)
from backend.tests.test_timeline_notes_migration_postgres import (
    _alembic,
)
from backend.tests.test_timeline_notes_migration_postgres import (
    scratch_database as scratch_database,
)
from backend.utils.generation_claim import NOTES_RUN
from backend.utils.llm_config import resolve_llm_config
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceResult
from backend.utils.status_manager import update_recording_status
from backend.utils.time import utc_now
from backend.worker import tasks
from backend.worker.tasks.final_publication import persist_final_transcript
from backend.worker.tasks.notes_progress import notes_progress_writer
from backend.worker.tasks.side_runs import renew_transcript_claim

BASE = "/api/v1/transcripts/cancel-recording/notes"
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.usefixtures("stub_text_embedding", "stub_celery_dispatch"),
]


@pytest.fixture
def database(postgres_test_url: str) -> Iterator[str]:
    name = f"nojoin_notes_cancel_{uuid4().hex[:12]}"
    admin = make_url(postgres_test_url).set(drivername="postgresql")
    with psycopg.connect(
        admin.render_as_string(hide_password=False), autocommit=True
    ) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = admin.set(database=name).render_as_string(hide_password=False)
    try:
        migrate_empty_database(url, "head")
        yield url
    finally:
        with psycopg.connect(
            admin.render_as_string(hide_password=False), autocommit=True
        ) as conn:
            conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def engine(database: str) -> Iterator[Engine]:
    bind = create_engine(make_url(database).set(drivername="postgresql+psycopg"))
    with Session(bind) as session:
        owner = User(
            id=1,
            username="cancel-owner",
            hashed_password="x",
            role="owner",
            settings={"llm_provider": "ollama", "ollama_model": "test-model"},
        )
        session.add(owner)
        session.commit()
        session.add(
            Recording(
                id=1,
                public_id="cancel-recording",
                meeting_uid="cancel-meeting",
                name="Original title",
                audio_path="/tmp/notes-cancel.wav",
                user_id=1,
                status="PROCESSED",
                processing_step="Completed",
                processing_progress=100,
                first_processed_at=utc_now(),
                title_user_edited=False,
            )
        )
        session.commit()
        session.add(
            Transcript(
                recording_id=1,
                text="We agreed on the budget.",
                segments=[
                    {
                        "start": 0,
                        "end": 4,
                        "speaker": "SPEAKER_00",
                        "text": "We agreed on the budget.",
                    }
                ],
                transcript_status="completed",
                notes="Existing notes",
                notes_status="completed",
                notes_template_sections="Original sections",
                notes_stale_documents=True,
            )
        )
        session.add(
            RecordingSpeaker(
                recording_id=1,
                diarization_label="SPEAKER_00",
                name="Speaker 1",
            )
        )
        session.commit()
    try:
        yield bind
    finally:
        bind.dispose()


@pytest.fixture
async def maker(
    database: str, engine: Engine
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    bind = create_async_engine(make_url(database).set(drivername="postgresql+asyncpg"))
    try:
        yield async_sessionmaker(bind, expire_on_commit=False)
    finally:
        await bind.dispose()


@pytest.fixture
async def client(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def db() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    async def owner() -> User:
        async with maker() as session:
            return await session.get(User, 1)

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = owner
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as api:
        yield api


async def generate(client: AsyncClient) -> str:
    response = await client.post(f"{BASE}/generate")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notes_status"] == "generating"
    assert body["notes_progress"] is None
    assert UUID(body["notes_run_id"])
    return body["notes_run_id"]


async def cancel(client: AsyncClient, token: str) -> dict[str, Any]:
    response = await client.post(f"{BASE}/cancel", json={"notes_run_id": token})
    assert response.status_code == 200, response.text
    return response.json()


def state(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text(
                    "SELECT t.notes, t.notes_status, t.notes_run_id, t.notes_progress, "
                    "t.updated_at, t.error_message, t.notes_template_sections, "
                    "t.notes_stale_documents, t.speaker_name_suggestions, t.change_sequence, "
                    "r.status, r.name, r.processing_step, s.local_name "
                    "FROM transcripts t JOIN recordings r ON r.id=t.recording_id "
                    "LEFT JOIN recording_speakers s ON s.recording_id=r.id"
                )
            )
            .mappings()
            .one()
        )


@dataclass
class BlockingModel:
    entered: threading.Event
    release: threading.Event
    failure: bool = False
    calls: int = 0

    def answer(self) -> None:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(15), "test never released the model"
        if self.failure:
            raise RuntimeError("old model failed")

    def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
        self.answer()
        return "Replacement notes"

    def generate_meeting_intelligence(
        self, *args: Any, **kwargs: Any
    ) -> AutomaticMeetingIntelligenceResult:
        self.answer()
        return AutomaticMeetingIntelligenceResult(
            speaker_mapping={"SPEAKER_00": "Alex"},
            title="Replacement title",
            notes_markdown="## Summary\nReplacement notes",
        )


def worker(engine: Engine, token: str | None, automatic: bool = False) -> None:
    task = (
        tasks.generate_meeting_intelligence_task
        if automatic
        else tasks.generate_notes_task
    )
    with Session(engine) as session:
        task._session = session
        try:
            if automatic:
                task.run(1, token)
            else:
                task.run(1, None, token)
        finally:
            task._session = None


def use_model(monkeypatch: pytest.MonkeyPatch, model: BlockingModel) -> None:
    monkeypatch.setattr(
        "backend.processing.llm_backends.factory.get_llm_backend",
        lambda *args, **kwargs: model,
    )


@pytest.mark.parametrize("notes", [None, " \n\t", "Existing notes"])
async def test_cancel_restores_current_content_and_preserves_independent_edits(
    client: AsyncClient,
    engine: Engine,
    stub_celery_dispatch: list,
    notes: str | None,
) -> None:
    token = await generate(client)
    response = await client.put(BASE, json={"notes": notes or ""})
    assert response.status_code == 200
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE transcripts SET notes_progress='Drafting', error_message='old notes error'"
            )
        )
    before = state(engine)

    result = await cancel(client, token)

    assert result["notes_status"] == (
        "completed" if notes and notes.strip() else "pending"
    )
    assert result["notes"] == (notes or "")
    assert result["notes_run_id"] is None
    assert result["notes_progress"] is None
    assert result["error_message"] is None
    after = state(engine)
    assert after["change_sequence"] == before["change_sequence"] + 1
    for field in (
        "notes",
        "notes_template_sections",
        "notes_stale_documents",
        "status",
        "name",
        "local_name",
    ):
        assert after[field] == before[field]
    assert await cancel(client, token) == result
    assert state(engine) == after


async def test_stale_cancel_conflicts_with_b_and_public_reads_report_only_its_token(
    client: AsyncClient,
    engine: Engine,
    stub_celery_dispatch: list,
) -> None:
    a = await generate(client)
    await cancel(client, a)
    b = await generate(client)
    assert a != b
    before = state(engine)

    conflict = await client.post(f"{BASE}/cancel", json={"notes_run_id": a})

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["notes_run_id"] == b
    assert conflict.json()["detail"]["notes_status"] == "generating"
    assert conflict.json()["detail"]["notes"] == "Existing notes"
    assert state(engine) == before
    read = await client.get("/api/v1/recordings/cancel-recording")
    assert read.status_code == 200, read.text
    assert read.json()["transcript"]["notes_run_id"] == b
    await cancel(client, b)
    read = await client.get("/api/v1/recordings/cancel-recording")
    assert read.json()["transcript"]["notes_run_id"] is None


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
async def test_cancelled_queued_worker_cannot_reclaim_b(
    client: AsyncClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    automatic: bool,
    legacy: bool,
) -> None:
    a = await generate(client)
    await cancel(client, a)
    b = await generate(client)
    model = BlockingModel(threading.Event(), threading.Event())
    model.release.set()
    use_model(monkeypatch, model)
    before = state(engine)

    await asyncio.to_thread(worker, engine, None if legacy else a, automatic)

    assert state(engine) == before
    assert model.calls == 0
    assert str(before["notes_run_id"]) == b


@pytest.mark.parametrize(
    "scenario",
    [
        (automatic, failure, replacement)
        for automatic in (False, True)
        for failure in (False, True)
        for replacement in ("cancel", "reclaim")
    ],
)
async def test_old_success_and_failure_cannot_publish_over_b(
    client: AsyncClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    scenario: tuple[bool, bool, str],
) -> None:
    automatic, failure, replacement = scenario
    a = await generate(client)
    model = BlockingModel(threading.Event(), threading.Event(), failure=failure)
    use_model(monkeypatch, model)
    running = asyncio.create_task(asyncio.to_thread(worker, engine, a, automatic))
    try:
        assert await asyncio.to_thread(model.entered.wait, 10), (
            "worker never reached model"
        )
        if replacement == "cancel":
            await cancel(client, a)
        else:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE transcripts SET updated_at=now()-interval '20 minutes'"
                    )
                )
        await generate(client)
        before = state(engine)
    finally:
        model.release.set()
        await running

    # This includes title, speakers, provenance, document freshness and the step.
    assert state(engine) == before
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM context_chunks")).scalar_one() == 0
        )


async def test_two_generate_requests_acquire_once_under_a_postgres_row_lock(
    client: AsyncClient,
    engine: Engine,
    maker: async_sessionmaker[AsyncSession],
    stub_celery_dispatch: list,
) -> None:
    reached = asyncio.Event()
    count = 0
    bind = maker.kw["bind"].sync_engine

    def at_lock(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        nonlocal count
        if "transcripts" in statement and "FOR UPDATE" in statement:
            count += 1
            if count == 2:
                reached.set()

    event.listen(bind, "before_cursor_execute", at_lock)
    first = second = None
    with engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            text("SELECT id FROM transcripts WHERE recording_id=1 FOR UPDATE")
        )
        try:
            first = asyncio.create_task(client.post(f"{BASE}/generate"))
            second = asyncio.create_task(client.post(f"{BASE}/generate"))
            await asyncio.wait_for(reached.wait(), 10)
            assert not first.done() and not second.done()
        finally:
            transaction.commit()
    try:
        responses = await asyncio.gather(first, second)
    finally:
        event.remove(bind, "before_cursor_execute", at_lock)
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(stub_celery_dispatch) == 1
    winner = next(
        response for response in responses if response.status_code == 200
    ).json()
    assert stub_celery_dispatch[0][1][2] == winner["notes_run_id"]


async def test_cancel_respects_the_owned_recording_boundary(
    client: AsyncClient,
    engine: Engine,
    stub_celery_dispatch: list,
) -> None:
    token = await generate(client)
    with Session(engine) as session:
        session.add(User(id=2, username="other", hashed_password="x"))
        session.commit()
        recording = session.get(Recording, 1)
        recording.user_id = 2
        session.add(recording)
        session.commit()
    before = state(engine)
    response = await client.post(f"{BASE}/cancel", json={"notes_run_id": token})
    assert response.status_code == 404
    assert state(engine) == before


async def test_old_heartbeat_and_progress_cannot_renew_or_relabel_b(
    client: AsyncClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: list,
) -> None:
    import backend.core.db as db_module

    monkeypatch.setattr(db_module, "sync_engine", engine)
    a = await generate(client)
    await cancel(client, a)
    b = await generate(client)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE recordings SET status='PROCESSING', processing_step='B is running'"
            )
        )
        conn.execute(
            text("UPDATE transcripts SET updated_at=now()-interval '2 minutes'")
        )
    old_time = state(engine)["updated_at"]
    renew_transcript_claim(1, NOTES_RUN, run_id=b)
    assert state(engine)["updated_at"] > old_time
    with Session(engine) as session:
        current = notes_progress_writer(
            session, 1, notes_run_id=b, processing_step_suffix=" (CPU)"
        )
        current("B draft")
        before = state(engine)
        assert before["processing_step"] == "B draft... (CPU)"
        late = notes_progress_writer(
            session, 1, notes_run_id=a, processing_step_suffix=" (CPU)"
        )
        late("A draft")
        renew_transcript_claim(1, NOTES_RUN, run_id=a)
    assert state(engine) == before


@pytest.mark.parametrize("automatic", [False, True])
async def test_completion_wins_its_row_lock_before_cancel(
    client: AsyncClient,
    engine: Engine,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    automatic: bool,
) -> None:
    token = await generate(client)
    model = BlockingModel(threading.Event(), threading.Event())
    use_model(monkeypatch, model)
    published_lock = threading.Event()
    release_publication = threading.Event()
    cancel_waiting = asyncio.Event()

    def after_lock(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if (
            model.release.is_set()
            and "transcripts" in statement
            and "FOR UPDATE" in statement
        ):
            published_lock.set()
            assert release_publication.wait(15), "publication not released"

    def before_cancel_lock(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "transcripts" in statement and "FOR UPDATE" in statement:
            cancel_waiting.set()

    async_bind = maker.kw["bind"].sync_engine
    event.listen(engine, "after_cursor_execute", after_lock)
    event.listen(async_bind, "before_cursor_execute", before_cancel_lock)
    running = asyncio.create_task(asyncio.to_thread(worker, engine, token, automatic))
    cancelling = None
    try:
        assert await asyncio.to_thread(model.entered.wait, 10)
        model.release.set()
        assert await asyncio.to_thread(published_lock.wait, 10)
        cancelling = asyncio.create_task(cancel(client, token))
        await asyncio.wait_for(cancel_waiting.wait(), 10)
        assert not cancelling.done()
    finally:
        model.release.set()
        release_publication.set()
        await running
        event.remove(engine, "after_cursor_execute", after_lock)
        event.remove(async_bind, "before_cursor_execute", before_cancel_lock)
    result = await cancelling
    assert result["notes_status"] == "completed"
    assert result["notes_run_id"] is None
    assert "Replacement notes" in result["notes"]
    after = state(engine)
    assert after["notes_stale_documents"] is False
    assert after["notes_template_sections"] != "Original sections"
    if automatic:
        assert after["name"] == "Replacement title"
        assert after["local_name"] == "Alex"
    assert await cancel(client, token) == result
    assert state(engine) == after


@pytest.mark.parametrize("retained", [False, True])
async def test_reprocess_retires_an_expired_token_before_timestamp_writes(
    client: AsyncClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: list,
    retained: bool,
) -> None:
    a = await generate(client)
    model = BlockingModel(threading.Event(), threading.Event())
    use_model(monkeypatch, model)
    running = asyncio.create_task(asyncio.to_thread(worker, engine, a))
    try:
        assert await asyncio.to_thread(model.entered.wait, 10)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE transcripts SET updated_at=now()-interval '20 minutes', "
                    "notes_progress='Old draft', transcript_status=:status"
                ),
                {"status": "completed" if retained else "pending"},
            )
        response = await client.post(
            "/api/v1/recordings/cancel-recording/reprocess",
            json={"transcription_backend": "whisper"},
        )
        assert response.status_code == 200, response.text
        before = state(engine)
        assert before["notes_run_id"] is None
        assert before["notes_progress"] is None
        assert before["notes_status"] != "generating"
    finally:
        model.release.set()
        await running
    assert state(engine) == before


def test_migration_retires_legacy_claims_and_round_trips(scratch_database: str) -> None:
    migrate_empty_database(scratch_database, "e3c9a7b15d42")
    with psycopg.connect(scratch_database) as conn:
        conn.execute(
            "INSERT INTO users (id, username, hashed_password, role, created_at, updated_at, "
            "is_active, is_superuser, force_password_change) "
            "VALUES (1,'migration-owner','x','owner',now(),now(),true,true,false)"
        )
        for row_id, notes in ((1, "Existing notes"), (2, " \n\t"), (3, None)):
            conn.execute(
                "INSERT INTO recordings (id, name, public_id, meeting_uid, audio_path, "
                "status, upload_progress, processing_progress, is_archived, is_deleted, "
                "user_id, created_at, updated_at) "
                "VALUES (%s,'Meeting',%s,%s,%s,'PROCESSED',100,100,false,false,1,now(),now())",
                (row_id, f"legacy-{row_id}", f"legacy-{row_id}", f"/tmp/{row_id}.wav"),
            )
            conn.execute(
                "INSERT INTO transcripts (recording_id, notes, notes_status, notes_progress, "
                "transcript_status, error_message, meeting_edge_status, speaker_name_suggestions, created_at, updated_at) "
                "VALUES (%s,%s,'generating','Old draft',%s,'Failure','idle','[]'::jsonb,now(),now())",
                (row_id, notes, "error" if row_id == 3 else "completed"),
            )
    _alembic(scratch_database, "upgrade", "head")
    with psycopg.connect(scratch_database) as conn:
        assert conn.execute(
            "SELECT notes_status, notes_run_id, notes_progress, error_message "
            "FROM transcripts ORDER BY recording_id"
        ).fetchall() == [
            ("completed", None, None, None),
            ("pending", None, None, None),
            ("pending", None, None, "Failure"),
        ]
        assert conn.execute(
            "SELECT notes FROM transcripts WHERE recording_id=1"
        ).fetchone() == ("Existing notes",)
    _alembic(scratch_database, "downgrade", "e3c9a7b15d42")
    with psycopg.connect(scratch_database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='transcripts' AND column_name='notes_run_id'"
        ).fetchone() == (0,)
    _alembic(scratch_database, "upgrade", "head")


@pytest.mark.parametrize("lost", [False, True])
async def test_final_asr_preserves_live_notes_and_retires_lost_ownership(
    client: AsyncClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: list,
    lost: bool,
) -> None:
    a = await generate(client)
    model = BlockingModel(threading.Event(), threading.Event())
    use_model(monkeypatch, model)
    running = asyncio.create_task(asyncio.to_thread(worker, engine, a))
    try:
        assert await asyncio.to_thread(model.entered.wait, 10)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE transcripts SET notes_progress='Current draft', error_message='Current notes warning'"
                )
            )
            if lost:
                conn.execute(
                    text(
                        "UPDATE transcripts SET updated_at=now()-interval '20 minutes'"
                    )
                )
        with Session(engine) as session:
            recording = session.get(Recording, 1)
            persist_final_transcript(
                SimpleNamespace(session=session, merged_config={}),
                recording,
                [
                    {
                        "start": 0,
                        "end": 5,
                        "speaker": "SPEAKER_00",
                        "text": "Final transcript",
                    }
                ],
                {"text": "Final transcript"},
            )
        after = state(engine)
        assert after["notes_run_id"] == (None if lost else UUID(a))
        assert after["notes_progress"] == (None if lost else "Current draft")
        assert after["notes_status"] == ("completed" if lost else "generating")
        assert after["error_message"] == (None if lost else "Current notes warning")
        if not lost:
            await cancel(client, a)
        before = state(engine)
    finally:
        model.release.set()
        await running
    assert state(engine) == before


async def test_inline_cancel_is_neutral_and_allows_immediate_generate(
    client: AsyncClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: list,
) -> None:
    model = BlockingModel(threading.Event(), threading.Event())
    use_model(monkeypatch, model)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE recordings SET status='PROCESSING', processing_step='Saving transcript...'"
            )
        )

    def inline() -> Any:
        with Session(engine) as session:
            recording = session.get(Recording, 1)
            transcript = session.exec(select(Transcript)).one()
            speakers = session.exec(select(RecordingSpeaker)).all()
            user = session.get(User, 1)
            return tasks._run_automatic_meeting_intelligence_stage(
                session=session,
                task=None,
                recording=recording,
                transcript=transcript,
                speakers=speakers,
                transcript_text=transcript.text,
                unresolved_speakers=("SPEAKER_00",),
                llm_config=resolve_llm_config(session, user.settings, user_id=1),
                prefer_short_titles=True,
                device_suffix="",
                timeline_notes=load_recording_timeline_notes(session, 1),
            )

    running = asyncio.create_task(asyncio.to_thread(inline))
    try:
        assert await asyncio.to_thread(model.entered.wait, 10)
        read = await client.get("/api/v1/recordings/cancel-recording")
        assert read.status_code == 200
        a = read.json()["transcript"]["notes_run_id"]
        assert a
        cancelled = await cancel(client, a)
        assert cancelled["notes_status"] == "completed"
        assert state(engine)["status"] == "PROCESSING"
        b = await generate(client)
        before = state(engine)
    finally:
        model.release.set()
        result = await running
    assert result is None
    assert state(engine) == before
    # The production status recomputation remains driven by completed ASR,
    # even with B generating; cancellation never produces a transcription error.
    with Session(engine) as session:
        update_recording_status(session, 1)
    after = state(engine)
    assert after["status"] == "PROCESSED"
    assert str(after["notes_run_id"]) == b


async def test_public_read_hides_an_expired_runs_identity_and_progress(
    client: AsyncClient,
    engine: Engine,
    stub_celery_dispatch: list,
) -> None:
    await generate(client)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE transcripts SET updated_at=now()-interval '20 minutes', "
                "notes_progress='Old draft'"
            )
        )
    read = await client.get("/api/v1/recordings/cancel-recording")
    assert read.status_code == 200, read.text
    transcript = read.json()["transcript"]
    assert transcript["notes_status"] == "error"
    assert transcript["notes_run_id"] is None
    assert transcript["notes_progress"] is None


@pytest.mark.parametrize("automatic", [False, True])
async def test_cancel_endpoint_stops_the_real_providers_next_part_or_repair(
    client: AsyncClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    automatic: bool,
) -> None:
    entered, release = threading.Event(), threading.Event()
    outbound: list[str] = []
    with Session(engine) as session:
        owner = session.get(User, 1)
        owner.settings = {**owner.settings, "ollama_api_url": "http://8.8.8.8:11434"}
        session.add(owner)
        transcript = session.exec(select(Transcript)).one()
        transcript.text = "A long meeting. " * 6000
        transcript.segments = [
            {
                "start": 0,
                "end": 600,
                "speaker": "SPEAKER_00",
                "text": transcript.text,
            }
        ]
        session.add(transcript)
        session.commit()

    def post(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/api/show"):
            return _Response({"capabilities": ["completion"]})
        outbound.append(url)
        entered.set()
        assert release.wait(15)
        if automatic:
            return _answer(FITS, "")
        return _refusal(_llama_server_overflow(40_000, 8192))

    monkeypatch.setattr(requests, "post", post)
    a = await generate(client)
    running = asyncio.create_task(asyncio.to_thread(worker, engine, a, automatic))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        await cancel(client, a)
        await generate(client)
        before = state(engine)
    finally:
        release.set()
        await running
    assert len(outbound) == 1
    assert state(engine) == before
