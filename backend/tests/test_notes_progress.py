"""The step a long meeting's notes are on, from the parts run to the page.

The parts run reports each step before the call it makes; the worker writes it
to the transcript (and, inside the pipeline, to the recording's step) where the
page polls for it; and it is gone once the notes finish or fail. The model is
the fake Ollama server of the long-meeting tests, so the real backend and the
real parts run take every step; each request it answers notes what the
database said at that moment.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, select

import backend.worker.tasks as tasks_module
import backend.worker.tasks.intelligence as intelligence_module
from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.services.timeline_note_service import load_recording_timeline_notes
from backend.tests.sqlite_schemas import (
    DOCUMENTS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
)
from backend.tests.test_automatic_meeting_intelligence_worker import (
    NO_NOTES,
    _create_worker_ai_database,
)
from backend.tests.test_generate_notes_task import (
    _create_notes_task_database,
    _run_generate_notes_task,
)
from backend.tests.test_long_meeting_notes import (
    _budget,
    _context,
    _Model,
    _request,
    _TaggedModel,
    _transcript,
)
from backend.tests.test_ollama_context_window import _backend, _Response
from backend.tests.test_ollama_long_meeting import TRANSCRIPT, _Ollama
from backend.tests.test_recording_pause_resume import (
    RECORDING_SPEAKERS_SCHEMA,
    RECORDING_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.generation_claim import GENERATION_CLAIM_EXPIRY
from backend.utils.llm_config import ResolvedLLMConfig
from backend.utils.long_meeting_notes import (
    generate_meeting_intelligence_in_parts,
    generate_notes_in_parts,
)
from backend.utils.notes_progress import reporting_notes_progress
from backend.utils.time import utc_now
from backend.worker.tasks.notes_progress import notes_progress_writer

# Regenerated notes are indexed for search, which embeds them (#689).
pytestmark = pytest.mark.usefixtures("stub_text_embedding")

WINDOW = 8192


def _expected_step(prompt: str) -> str:
    """The step a prompt of the parts run belongs to, read from the prompt."""
    if "# Transcript Part" in prompt:
        part, total = re.search(r"This is part (\d+) of (\d+)", prompt).groups()
        return f"Drafting notes, part {part} of {total}"
    stretch = re.search(r"\(parts (\d+) to (\d+) of (\d+)", prompt)
    if "this stretch of the meeting" in prompt and stretch:
        first, last, total = stretch.groups()
        return f"Combining notes, parts {first} to {last} of {total}"
    return "Combining notes"


# --- The parts run ---------------------------------------------------------


def test_each_call_of_a_long_meeting_is_reported_before_it_runs():
    steps: list[str] = []
    seen: list[tuple[str, str]] = []
    model = _Model(draft="## Summary\nDraft for {part}. " + "detail " * 200)

    def complete(prompt: str) -> str:
        seen.append((steps[-1] if steps else "", prompt))
        return model(prompt)

    with reporting_notes_progress(steps.append):
        generate_notes_in_parts(_transcript(150), _context(), complete, _budget())

    # One step per call, reported before it, and in the order the calls ran.
    assert [step for step, _ in seen] == steps
    assert steps == [_expected_step(prompt) for _, prompt in seen]
    drafts = [step for step in steps if step.startswith("Drafting")]
    total = len(model.drafts())
    assert drafts == [
        f"Drafting notes, part {n} of {total}" for n in range(1, total + 1)
    ]
    # Drafts too long for one combining call are combined in groups first.
    assert any(step.startswith("Combining notes, parts 1 to ") for step in steps)
    assert steps[-1] == "Combining notes"


def test_a_call_asked_again_says_so():
    steps: list[str] = []
    model = _TaggedModel([])
    answers = iter([""])

    def unusable_first_draft(prompt: str) -> str:
        if "This is part 1 of" in prompt and next(answers, None) is not None:
            return ""
        return model(prompt)

    with reporting_notes_progress(steps.append):
        generate_meeting_intelligence_in_parts(
            _request(_transcript(60)), unusable_first_draft, _budget()
        )

    total = len(model.drafts())
    assert steps[:2] == [
        f"Drafting notes, part 1 of {total}",
        f"Drafting notes, part 1 of {total} (retrying)",
    ]


def test_a_combined_answer_asked_for_at_half_length_says_so():
    steps: list[str] = []
    overran: list[bool] = []
    model = _Model()

    def overrunning_once(prompt: str) -> str:
        if "# Notes of the Parts" in prompt and not overran:
            overran.append(True)
            raise ContextWindowExceededError(
                "stopped", window=16_000, answer_incomplete=True
            )
        return model(prompt)

    with reporting_notes_progress(steps.append):
        generate_notes_in_parts(
            _transcript(60), _context(), overrunning_once, _budget()
        )

    assert steps[-2:] == [
        "Combining notes",
        "Combining notes (retrying a shorter answer)",
    ]


# --- Regenerate Notes --------------------------------------------------------


class _ObservedOllama(_Ollama):
    """The fake Ollama, noting the page's view of the run at each request."""

    def __init__(
        self,
        database_url: str,
        *,
        fail_final: bool = False,
        while_answering: str | None = None,
        **kwargs,
    ):
        super().__init__(window=WINDOW, **kwargs)
        self.observer = create_engine(database_url, future=True)
        self.fail_final = fail_final
        # SQL another session runs while the model answers its first request.
        self.while_answering = while_answering
        self.observed: list[tuple[str, Any, Any]] = []

    def post(self, url, **kwargs):
        if url.endswith("/api/chat"):
            prompt = kwargs["json"]["messages"][-1]["content"]
            if self.while_answering is not None:
                with self.observer.begin() as connection:
                    connection.execute(text(self.while_answering))
                self.while_answering = None
            with self.observer.connect() as connection:
                progress, step = connection.execute(
                    text(
                        "SELECT t.notes_progress, r.processing_step FROM transcripts t "
                        "JOIN recordings r ON r.id = t.recording_id"
                    )
                ).one()
            self.observed.append((prompt, progress, step))
            final = "the whole meeting" in prompt and "# Notes of the Parts" in prompt
            if self.fail_final and final:
                return _Response({"error": "model crashed"}, status_code=400)
        return super().post(url, **kwargs)


def _leave_a_lost_runs_step(engine: Any) -> None:
    """The step a run lost with its worker would leave behind."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE transcripts SET notes_progress = 'Drafting notes, part 9 of 9'"
            )
        )


def _long_segments() -> list[dict[str, Any]]:
    return [
        {
            "start": minute * 60.0,
            "end": minute * 60.0 + 59.0,
            "speaker": "SPEAKER_00",
            "text": " ".join(f"m{minute}w{word}" for word in range(60)) + ".",
        }
        for minute in range(90)
    ]


def _regenerate_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **fake_options: Any
) -> tuple[_ObservedOllama, tuple[Any, ...]]:
    monkeypatch.setattr(tasks_module.config_manager, "get_all", lambda: {})
    engine = _create_notes_task_database(
        tmp_path,
        owner_settings={"llm_provider": "ollama", "ollama_model": "qwen3:14b"},
        transcript_segments=_long_segments(),
    )
    _leave_a_lost_runs_step(engine)
    fake = _ObservedOllama(str(engine.url), **fake_options)
    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(
        "backend.processing.llm_backends.factory.get_llm_backend",
        lambda *args, **kwargs: _backend(fake, context_window=WINDOW),
    )
    try:
        _run_generate_notes_task(engine)
        with fake.observer.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT t.notes_status, t.notes_progress, t.notes, "
                    "r.processing_step FROM transcripts t "
                    "JOIN recordings r ON r.id = t.recording_id"
                )
            ).one()
    finally:
        fake.observer.dispose()
    return fake, tuple(row)


def test_regenerate_notes_shows_each_step_of_a_long_meeting_and_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, (status, progress, notes, step) = _regenerate_with(tmp_path, monkeypatch)

    whole, *parts = fake.observed
    # The whole meeting is tried first, with nothing to show beyond
    # "generating": not even the step an earlier, lost run left.
    assert whole[1] is None
    assert len(parts) >= 3
    assert [seen for _, seen, _ in parts] == [
        _expected_step(prompt) for prompt, _, _ in parts
    ]
    # Regenerate never writes the finished recording's step (#828).
    assert {seen_step for _, _, seen_step in fake.observed} == {"Completed"}
    assert status == "completed"
    assert notes.startswith("## Summary\nAll of it.")
    assert progress is None
    assert step == "Completed"


def test_regenerate_notes_clears_the_step_when_the_notes_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, (status, progress, notes, _) = _regenerate_with(
        tmp_path, monkeypatch, fail_final=True
    )

    assert fake.observed[-1][1] == "Combining notes"
    assert status == "error"
    assert notes == "Existing notes"
    assert progress is None


def test_a_step_that_cannot_be_written_does_not_fail_the_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unreachable = create_engine(
        f"sqlite:///{tmp_path / 'missing' / 'gone.sqlite'}", future=True
    )
    real_writer = notes_progress_writer

    def writer_on_unreachable_database(session, recording_id, **kwargs):
        return real_writer(Session(unreachable), recording_id, **kwargs)

    monkeypatch.setattr(
        intelligence_module, "notes_progress_writer", writer_on_unreachable_database
    )

    fake, (status, progress, notes, _) = _regenerate_with(tmp_path, monkeypatch)

    assert len(fake.observed) >= 4
    assert {seen for _, seen, _ in fake.observed} == {None}
    assert status == "completed"
    assert notes.startswith("## Summary\nAll of it.")
    assert progress is None


# A note the user deletes, or edits, while the model is answering.
NOTE_CHANGES = {
    "deleted": "DELETE FROM timeline_notes WHERE id = 1",
    "edited": "UPDATE timeline_notes SET body = 'Remember the launch date and the budget' "
    "WHERE id = 1",
}


@pytest.mark.parametrize("change", list(NOTE_CHANGES))
def test_regenerated_notes_keep_the_notes_the_model_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    fake, (status, _, notes, _) = _regenerate_with(
        tmp_path, monkeypatch, while_answering=NOTE_CHANGES[change]
    )

    assert "Remember the launch date" in fake.observed[0][0]
    assert status == "completed"
    appendix = notes.split("## User Notes", 1)[1]
    assert "Remember the launch date" in appendix
    assert "the budget" not in appendix


# --- Automatic notes -------------------------------------------------------


def _ollama_config() -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider="ollama",
        api_key=None,
        model="qwen3:14b",
        api_url="http://ollama.local",
        merged_config={"llm_provider": "ollama", "prefer_short_titles": True},
    )


def _with_timeline_notes(engine: Any) -> None:
    """The two notes the Regenerate fixture has: a head note and a point."""
    now = utc_now()
    with engine.begin() as connection:
        connection.execute(text(TIMELINE_NOTES_SCHEMA))
        for note_id, kind, body, anchor_ms in (
            (1, "head", "Remember the launch date", None),
            (2, "point", "Ask about QA", 1_000),
        ):
            connection.execute(
                text(
                    "INSERT INTO timeline_notes (id, created_at, updated_at, "
                    "public_id, recording_id, created_by_user_id, kind, body, "
                    "anchor_ms, revision) VALUES (:id, :now, :now, :public_id, 1, "
                    "1, :kind, :body, :anchor_ms, 1)"
                ),
                {
                    "id": note_id,
                    "now": now,
                    "public_id": f"note-{note_id}",
                    "kind": kind,
                    "body": body,
                    "anchor_ms": anchor_ms,
                },
            )


def _run_automatic_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    inline: bool = True,
    with_notes: bool = False,
    **fake_options: Any,
) -> tuple[Any, _ObservedOllama, tuple[Any, ...]]:
    engine = _create_worker_ai_database(tmp_path)
    _leave_a_lost_runs_step(engine)
    if with_notes:
        _with_timeline_notes(engine)
    fake = _ObservedOllama(
        str(engine.url), mapping={"SPEAKER_00": "Ana"}, **fake_options
    )
    monkeypatch.setattr(
        tasks_module,
        "_llm_backend_from_config",
        lambda config: _backend(fake, context_window=WINDOW),
    )

    try:
        with Session(engine) as session:
            recording = session.get(Recording, 1)
            transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).one()
            speakers = session.exec(
                select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
            ).all()
            result = tasks_module._run_automatic_meeting_intelligence_stage(
                session=session,
                task=None,
                recording=recording,
                transcript=transcript,
                speakers=speakers,
                transcript_text=TRANSCRIPT,
                unresolved_speakers=("SPEAKER_00",),
                llm_config=_ollama_config(),
                prefer_short_titles=True,
                device_suffix=" (CPU)",
                timeline_notes=(
                    load_recording_timeline_notes(session, 1)
                    if with_notes
                    else NO_NOTES
                ),
                update_processing_status=inline,
            )
        with fake.observer.connect() as connection:
            row = connection.execute(
                text("SELECT notes_status, notes_progress, notes FROM transcripts")
            ).one()
    finally:
        fake.observer.dispose()
        engine.dispose()
    return result, fake, tuple(row)


@pytest.mark.parametrize("inline", [True, False], ids=["inline", "deferred"])
def test_automatic_notes_show_each_step_of_a_long_meeting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool
) -> None:
    result, fake, (status, progress, _) = _run_automatic_stage(
        tmp_path, monkeypatch, inline=inline
    )

    assert result is not None
    whole, *parts = fake.observed
    assert whole[1] is None
    assert len(parts) >= 3
    expected = [_expected_step(prompt) for prompt, _, _ in parts]
    assert [seen for _, seen, _ in parts] == expected
    # Inside the pipeline the stage owns the recording's step, which the
    # processing view and the dashboard show; deferred, the recording is done.
    assert [seen_step for _, _, seen_step in parts] == (
        [f"{step}... (CPU)" for step in expected]
        if inline
        else ["Saving transcript..."] * len(parts)
    )
    assert status == "completed"
    assert progress is None


@pytest.mark.parametrize("change", list(NOTE_CHANGES))
def test_automatic_notes_keep_the_notes_the_model_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    result, fake, (status, _, notes) = _run_automatic_stage(
        tmp_path, monkeypatch, with_notes=True, while_answering=NOTE_CHANGES[change]
    )

    assert result is not None
    assert "Remember the launch date" in fake.observed[0][0]
    assert status == "completed"
    appendix = notes.split("## User Notes", 1)[1]
    assert "Remember the launch date" in appendix
    assert "the budget" not in appendix


# --- What the page reads ---------------------------------------------------


@pytest.fixture
async def session_maker() -> AsyncIterator[sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
            TAGS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
            DOCUMENTS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
        ):
            await connection.execute(text(schema))
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(session_maker: sessionmaker) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


async def _seed(session_maker: sessionmaker, *, notes_status: str, claimed) -> None:
    async with session_maker() as session:
        session.add(
            Recording(
                id=1,
                public_id="rec-1",
                meeting_uid="meeting-1",
                name="Weekly sync",
                audio_path="/nonexistent/meeting.webm",
                status="PROCESSED",
                processing_step="Completed",
                user_id=1,
            )
        )
        session.add(
            Transcript(
                id=1,
                recording_id=1,
                segments=[],
                notes_status=notes_status,
                notes_progress="Drafting notes, part 2 of 5",
                transcript_status="completed",
                updated_at=claimed,
            )
        )
        await session.commit()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("notes_status", "lost", "shown"),
    [
        ("generating", False, "Drafting notes, part 2 of 5"),
        ("generating", True, None),
        ("completed", False, None),
    ],
    ids=["running", "lost-with-its-worker", "finished"],
)
async def test_the_page_reads_the_step_only_while_the_notes_generate(
    client: AsyncClient,
    session_maker: sessionmaker,
    notes_status: str,
    lost: bool,
    shown: str | None,
) -> None:
    claimed = utc_now() - (GENERATION_CLAIM_EXPIRY * 2 if lost else timedelta(0))
    await _seed(session_maker, notes_status=notes_status, claimed=claimed)

    read = await client.get("/api/v1/recordings/rec-1")

    assert read.status_code == 200, read.text
    assert read.json()["transcript"]["notes_progress"] == shown
