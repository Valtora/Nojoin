"""Extra notes use the existing generator without replacing primary notes."""

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import Session, select

from backend.api.v1.endpoints import transcripts
from backend.api.v1.endpoints.transcripts import routes_chat, routes_notes
from backend.core.db import driver_urls
from backend.models.context_chunk import ContextChunk
from backend.models.notes_output import NotesOutput
from backend.models.notes_template import NotesTemplate
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import TRANSCRIPTION_FAILED_NOTES_MESSAGE, Transcript
from backend.processing.text_embedding_version import TEXT_EMBEDDING_DIMENSIONS
from backend.tests.test_live_settling_readers_postgres import (
    reader_client as reader_client,
)
from backend.tests.test_live_settling_readers_postgres import (
    reader_sessions as reader_sessions,
)
from backend.utils.canonical_pipeline.notes_propagation import (
    propagate_speaker_rename_to_notes,
)
from backend.utils.reprocess_publication import REPROCESS_RUNNING_MESSAGE
from backend.worker import tasks

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.usefixtures("stub_text_embedding", "stub_celery_dispatch"),
]
SECTIONS = "## Tasks\nList agreed tasks and their owners."
PATH = "/api/v1/transcripts/meeting/notes"


@pytest.fixture(autouse=True)
def configured_ai(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    config = SimpleNamespace(
        missing_configuration_message=lambda: None,
        provider="ollama",
        api_key=None,
        merged_config={"llm_provider": "ollama"},
    )

    async def resolve(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return config

    monkeypatch.setattr(routes_notes, "resolve_llm_config_async", resolve)
    monkeypatch.setattr(routes_chat, "resolve_llm_config_async", resolve)
    monkeypatch.setattr(tasks, "resolve_llm_config", lambda *args, **kwargs: config)
    return config


@pytest.fixture
async def meeting(reader_sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with reader_sessions() as db:
        recording = Recording(
            name="Planning",
            public_id="meeting",
            user_id=1,
            audio_path="/tmp/meeting.wav",
            status=RecordingStatus.PROCESSED,
        )
        template = NotesTemplate(name="Task list", sections=SECTIONS, user_id=1)
        db.add_all([recording, template])
        await db.flush()
        assert recording.id is not None and template.id is not None
        db.add(
            Transcript(
                recording_id=recording.id,
                text="We agreed to ship.",
                segments=[
                    {
                        "text": "We agreed to ship.",
                        "start": 0,
                        "end": 1,
                        "speaker": "SPEAKER_00",
                    }
                ],
                notes="## Minutes\nPrimary decision.",
                notes_status="completed",
                transcript_status="completed",
                notes_template_sections="## Minutes\nDecisions.",
            )
        )
        await db.commit()
        return recording.id, template.id


def run_worker(postgres_test_url: str, args: list[Any], kwargs: dict[str, Any]) -> None:
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": "-csearch_path=live_settling_readers_test,public"},
    )
    with Session(engine) as session:
        tasks.generate_notes_task._session = session
        try:
            tasks.generate_notes_task.run(*args, **kwargs)
        finally:
            tasks.generate_notes_task._session = None
    engine.dispose()


@pytest.fixture
def worker(
    postgres_test_url: str,
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: list[tuple[str, Any, Any]],
) -> Callable[[Any], None]:
    def run(llm: Any) -> None:
        monkeypatch.setattr(tasks, "_llm_backend_from_config", lambda _: llm)
        name, args, kwargs = stub_celery_dispatch[-1]
        assert name == "backend.worker.tasks.generate_notes_task"
        run_worker(postgres_test_url, args, kwargs or {})

    return run


async def create_extra(client: AsyncClient, template_id: int) -> int:
    response = await client.post(
        f"{PATH}/generate",
        json={"create_output": True, "notes_template_id": template_id},
    )
    assert response.status_code == 200, response.text
    return int(response.json()["output"]["id"])


async def test_extra_lifecycle_preserves_primary_and_other_output(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    worker: Callable[[Any], None],
    stub_celery_dispatch: list[tuple[str, object, object]],
) -> None:
    seen: list[str] = []

    class LLM:
        def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
            seen.append(kwargs["notes_context"].notes_sections)
            return f"## Tasks\nExtra run {len(seen)}."

    recording_id, template_id = meeting
    async with reader_sessions() as db:
        db.add(
            ContextChunk(
                recording_id=recording_id,
                content="Primary indexed notes",
                embedding=[0.0] * TEXT_EMBEDDING_DIMENSIONS,
                meta={"source": "notes"},
            )
        )
        second_template = NotesTemplate(name="Owners", sections=SECTIONS, user_id=1)
        db.add(second_template)
        await db.commit()
        second_template_id = second_template.id
    first_extra = await create_extra(reader_client, template_id)
    assert stub_celery_dispatch[-1][1] == [recording_id, None, first_extra]
    worker(LLM())
    second_extra = await create_extra(reader_client, second_template_id)
    worker(LLM())
    response = await reader_client.post(
        f"{PATH}/generate",
        json={"output_id": first_extra, "notes_template_id": template_id},
    )
    assert response.status_code == 400, response.text
    response = await reader_client.post(
        f"{PATH}/generate", json={"output_id": first_extra}
    )
    assert response.status_code == 200, response.text
    worker(LLM())
    outputs = (await reader_client.get(f"{PATH}/outputs")).json()
    assert [output["notes"] for output in outputs] == [
        "## Tasks\nExtra run 3.",
        "## Tasks\nExtra run 2.",
    ]
    assert seen == [SECTIONS] * 3
    assert (await reader_client.get(PATH)).json()[
        "notes"
    ] == "## Minutes\nPrimary decision."
    async with reader_sessions() as db:
        recording = await db.get(Recording, recording_id)
        assert recording.status == RecordingStatus.PROCESSED
        assert [
            chunk.content
            for chunk in (await db.execute(select(ContextChunk))).scalars()
        ] == ["Primary indexed notes"]
    for output_id in (first_extra, second_extra):
        assert (
            await reader_client.delete(f"{PATH}/outputs/{output_id}")
        ).status_code == 204
    assert (await reader_client.get(f"{PATH}/outputs")).json() == []
    assert (await reader_client.get(PATH)).json()[
        "notes"
    ] == "## Minutes\nPrimary decision."


def without_timestamps(output: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in output.items() if k not in ("created_at", "updated_at")}


async def test_extra_set_generates_then_saves_an_edit(
    reader_client: AsyncClient,
    meeting: tuple[int, int],
    worker: Callable[[Any], None],
) -> None:
    class LLM:
        def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
            return "## Tasks\nGenerated."

    template_id = meeting[1]
    response = await reader_client.post(
        f"{PATH}/generate",
        json={"create_output": True, "notes_template_id": template_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["notes_status"], body["error_message"]) == ("generating", None)
    output_id = body["output"]["id"]
    generating = {
        "id": output_id,
        "kind": "notes",
        "name": "Task list",
        "notes": None,
        "notes_generation_metadata": {},
        "notes_template_id": template_id,
        "notes_template_sections": SECTIONS,
        "notes_status": "generating",
        "error_message": None,
    }
    assert without_timestamps(body["output"]) == generating
    listed = (await reader_client.get(f"{PATH}/outputs")).json()
    assert [without_timestamps(output) for output in listed] == [generating]
    worker(LLM())

    edited = "## Tasks\nEdited by hand."
    response = await reader_client.put(
        f"{PATH}/outputs/{output_id}", json={"notes": edited}
    )
    assert response.status_code == 200, response.text
    # The run records how much of the meeting it covered (one 1 s segment).
    saved = {
        **generating,
        "notes": edited,
        "notes_generation_metadata": {
            "event_watermark": 0,
            "generated_through_ms": 1000,
        },
        "notes_status": "completed",
    }
    assert without_timestamps(response.json()) == saved
    listed = (await reader_client.get(f"{PATH}/outputs")).json()
    assert [without_timestamps(output) for output in listed] == [saved]
    assert (await reader_client.get(PATH)).json()[
        "notes"
    ] == "## Minutes\nPrimary decision."

    # A rerun keeps the edited text on screen until the new run lands, and
    # refuses a save that the run would overwrite.
    response = await reader_client.post(
        f"{PATH}/generate", json={"output_id": output_id}
    )
    assert response.status_code == 200, response.text
    assert without_timestamps(response.json()["output"]) == {
        **saved,
        "notes_status": "generating",
    }
    response = await reader_client.put(
        f"{PATH}/outputs/{output_id}", json={"notes": "Lost edit"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Meeting notes are already generating."
    assert (await reader_client.get(f"{PATH}/outputs")).json()[0]["notes"] == edited


@pytest.mark.parametrize("state", ["reprocess", "failed", "extra_busy"])
async def test_generation_conflicts(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    stub_celery_dispatch: list[tuple[str, object, object]],
    state: str,
) -> None:
    output_id = await create_extra(reader_client, meeting[1])
    async with reader_sessions() as db:
        transcript = (await db.execute(select(Transcript))).scalar_one()
        # Only the guard under test may refuse: the set is idle unless it is
        # the one generating.
        if state != "extra_busy":
            output = await db.get(NotesOutput, output_id)
            output.notes_status = "completed"
        if state == "reprocess":
            transcript.transcript_status = "pending"
            transcript.settling_state = {"reprocess_pending": True}
        elif state == "failed":
            transcript.fail_transcription("ASR failed")
        await db.commit()
    stub_celery_dispatch.clear()
    reason = {
        "reprocess": REPROCESS_RUNNING_MESSAGE,
        "failed": TRANSCRIPTION_FAILED_NOTES_MESSAGE,
    }.get(state, "Meeting notes are already generating.")
    response = await reader_client.post(
        f"{PATH}/generate", json={"output_id": output_id}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == reason
    assert stub_celery_dispatch == []
    if state != "extra_busy":
        response = await reader_client.post(
            f"{PATH}/generate",
            json={"create_output": True, "notes_template_id": meeting[1]},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == reason
    if state == "extra_busy":
        assert (
            await reader_client.delete(f"{PATH}/outputs/{output_id}")
        ).status_code == 409


async def test_owner_and_recording_checks(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    stub_celery_dispatch: list[tuple[str, object, object]],
) -> None:
    async with reader_sessions() as db:
        other = Recording(
            name="Private", public_id="private", user_id=2, audio_path="x"
        )
        own = Recording(
            name="Other", public_id="own", user_id=1, audio_path="other.wav"
        )
        private_template = NotesTemplate(name="Private", sections=SECTIONS, user_id=2)
        db.add_all([other, own, private_template])
        await db.flush()
        extra = NotesOutput(
            recording_id=other.id,
            name="Private",
            notes="Secret",
            notes_template_sections=SECTIONS,
        )
        db.add(extra)
        await db.commit()
        extra_id, private_template_id = extra.id, private_template.id
    for recording in ("private", "meeting", "own"):
        path = f"/api/v1/transcripts/{recording}/notes/outputs/{extra_id}"
        assert (
            await reader_client.put(path, json={"notes": "Changed"})
        ).status_code == 404
        assert (await reader_client.delete(path)).status_code == 404
    assert (
        await reader_client.get("/api/v1/transcripts/private/notes/outputs")
    ).status_code == 404
    assert (
        await reader_client.post(
            f"{PATH}/generate",
            json={"create_output": True, "notes_template_id": private_template_id},
        )
    ).status_code == 404
    assert (
        await reader_client.post(f"{PATH}/generate", json={"output_id": extra_id})
    ).status_code == 404
    assert (
        await reader_client.get(
            "/api/v1/transcripts/meeting/export",
            params={"sections": "notes", "notes_output_id": extra_id},
        )
    ).status_code == 404
    assert stub_celery_dispatch == []


async def test_extra_export_and_chat_still_default_to_primary(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with reader_sessions() as db:
        extra = NotesOutput(
            recording_id=meeting[0],
            name="Tasks",
            notes="Extra secret tasks.",
            notes_template_sections=SECTIONS,
            notes_status="completed",
        )
        db.add(extra)
        await db.commit()
        extra_id = extra.id
    default = await reader_client.get(
        "/api/v1/transcripts/meeting/export", params={"sections": "notes"}
    )
    selected = await reader_client.get(
        "/api/v1/transcripts/meeting/export",
        params={"sections": "notes", "notes_output_id": extra_id},
    )
    assert default.status_code == selected.status_code == 200
    assert "Primary decision" in default.text and "Extra secret" not in default.text
    assert "Extra secret" in selected.text and "Primary decision" not in selected.text
    assert "_Notes_Tasks." in selected.headers["content-disposition"]
    seen: list[str] = []

    class LLM:
        def ask_question_streaming(self, **kwargs: Any) -> Iterator[str]:
            seen.append(kwargs["meeting_notes"])
            yield "Answer."

    monkeypatch.setattr(transcripts, "get_llm_backend_with_secondary", lambda _: LLM())
    monkeypatch.setattr(routes_chat, "async_session_maker", reader_sessions)
    response = await reader_client.post(
        "/api/v1/transcripts/meeting/chat", json={"message": "What happened?"}
    )
    assert response.status_code == 200, response.text
    assert seen == ["## Minutes\nPrimary decision."]


@pytest.mark.parametrize("fail", [False, True])
async def test_saved_template_and_worker_failure_are_isolated(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    worker: Callable[[Any], None],
    fail: bool,
) -> None:
    await create_extra(reader_client, meeting[1])
    async with reader_sessions() as db:
        await db.execute(
            text("DELETE FROM notes_templates WHERE id = :id"), {"id": meeting[1]}
        )
        await db.commit()

    class LLM:
        def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
            assert kwargs["notes_context"].notes_sections == SECTIONS
            if fail:
                raise RuntimeError("Provider unavailable")
            return "## Tasks\nStill uses its saved structure."

    worker(LLM())
    output = (await reader_client.get(f"{PATH}/outputs")).json()[0]
    assert output["notes_status"] == ("error" if fail else "completed")
    assert output["notes_template_id"] is None
    assert output["name"] == "Task list"
    if fail:
        assert "Provider unavailable" in output["error_message"]
    async with reader_sessions() as db:
        transcript = (await db.execute(select(Transcript))).scalar_one()
        assert transcript.notes_status == "completed"
        assert transcript.notes == "## Minutes\nPrimary decision."


EXTRA_NOTES = "## Tasks\nAlice ships.\n\n## User Notes\nAsk Alice."


@pytest.fixture
async def extra_set(
    reader_sessions: async_sessionmaker[AsyncSession], meeting: tuple[int, int]
) -> None:
    async with reader_sessions() as db:
        db.add(
            NotesOutput(
                recording_id=meeting[0],
                name="Tasks",
                notes=EXTRA_NOTES,
                notes_template_sections=SECTIONS,
                notes_status="completed",
            )
        )
        await db.commit()


@pytest.mark.usefixtures("extra_set")
@pytest.mark.parametrize("body", [{}, {"notes_template_id": None}])
async def test_primary_regeneration_uses_current_default_and_preserves_extra(
    reader_client: AsyncClient,
    meeting: tuple[int, int],
    worker: Callable[[Any], None],
    configured_ai: SimpleNamespace,
    body: dict[str, None],
) -> None:
    # The user changed their default since the notes were generated; an
    # explicit null (as MCP sends) means the same as no choice.
    configured_ai.merged_config["notes_template_id"] = meeting[1]

    class LLM:
        def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
            assert kwargs["notes_context"].notes_sections == SECTIONS
            return "## Minutes\nRegenerated primary."

    response = await reader_client.post(f"{PATH}/generate", json=body)
    assert response.status_code == 200, response.text
    worker(LLM())
    assert (await reader_client.get(PATH)).json()[
        "notes"
    ] == "## Minutes\nRegenerated primary."
    assert (await reader_client.get(f"{PATH}/outputs")).json()[0][
        "notes"
    ] == EXTRA_NOTES


@pytest.mark.usefixtures("extra_set")
async def test_renames_and_find_replace_reach_extra_sets(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
) -> None:
    async with reader_sessions() as db:
        await db.run_sync(
            lambda session: propagate_speaker_rename_to_notes(
                session, recording_id=meeting[0], old_names=["Alice"], new_name="Ana"
            )
        )
        await db.commit()
    response = await reader_client.post(
        "/api/v1/transcripts/meeting/replace",
        json={"find_text": "ships", "replace_text": "launches"},
    )
    assert response.status_code == 200, response.text
    assert (await reader_client.get(f"{PATH}/outputs")).json()[0]["notes"] == (
        "## Tasks\nAna launches.\n\n## User Notes\nAsk Alice."
    )


async def test_new_set_needs_a_template_not_already_on_the_meeting(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    stub_celery_dispatch: list[tuple[str, object, object]],
) -> None:
    recording_id, task_list_id = meeting
    async with reader_sessions() as db:
        minutes = NotesTemplate(name="Minutes", sections=SECTIONS, user_id=1)
        brief = NotesTemplate(name="Brief", sections=SECTIONS, user_id=1)
        db.add_all([minutes, brief])
        await db.flush()
        transcript = (await db.execute(select(Transcript))).scalar_one()
        transcript.notes_template_id = minutes.id
        await db.commit()
        minutes_id, brief_id = minutes.id, brief.id
    # The picker reads the first notes' template from here.
    assert (await reader_client.get(PATH)).json()["notes_template_id"] == minutes_id

    async def create(**body: Any) -> Any:
        return await reader_client.post(
            f"{PATH}/generate", json={"create_output": True, **body}
        )

    for body in ({}, {"notes_template_id": None}):
        response = await create(**body)
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "Choose a notes template for the new set."
    response = await create(notes_template_id=minutes_id)
    assert response.status_code == 409, response.text
    assert "already has a set from that template" in response.json()["detail"]
    assert stub_celery_dispatch == []

    first_extra = await create_extra(reader_client, task_list_id)
    response = await create(notes_template_id=task_list_id)
    assert response.status_code == 409, response.text
    response = await create(notes_template_id=brief_id)
    assert response.status_code == 200, response.text
    assert response.json()["output"]["notes_status"] == "generating"
    second_extra = response.json()["output"]["id"]
    assert [call[1] for call in stub_celery_dispatch] == [
        [recording_id, None, first_extra],
        [recording_id, None, second_extra],
    ]
    outputs = (await reader_client.get(f"{PATH}/outputs")).json()
    assert [output["notes_template_id"] for output in outputs] == [
        task_list_id,
        brief_id,
    ]
