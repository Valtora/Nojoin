"""Live and finished notes own their state, content and request ordering."""

from datetime import timedelta
from itertools import product
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import Session, select

from backend.api.v1.endpoints.transcripts import routes_notes
from backend.models.notes_output import NotesOutput
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.tests.test_notes_outputs import (
    PATH,
)
from backend.tests.test_notes_outputs import (
    configured_ai as configured_ai,
)
from backend.tests.test_notes_outputs import (
    meeting as meeting,
)
from backend.tests.test_notes_outputs import (
    reader_client as reader_client,
)
from backend.tests.test_notes_outputs import (
    reader_sessions as reader_sessions,
)
from backend.tests.test_notes_outputs import (
    worker as worker,
)
from backend.utils.notes_generation import claim_notes_generation
from backend.utils.time import utc_now

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.usefixtures("stub_text_embedding", "stub_celery_dispatch"),
]


@pytest.mark.parametrize(
    "case",
    list(
        product(
            [
                RecordingStatus.UPLOADING,
                RecordingStatus.PAUSED,
                RecordingStatus.PROCESSING,
                RecordingStatus.PROCESSED,
            ],
            [False, True],
            [False, True],
        )
    ),
)
async def test_outputs_generate_without_changing_recording(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    worker: Any,
    case: tuple[RecordingStatus, bool, bool],
) -> None:
    status, extra, fail = case
    async with reader_sessions() as db:
        recording = await db.get(Recording, meeting[0])
        recording.status = status
        recording.client_status = (
            ClientStatus.PAUSED
            if status == RecordingStatus.PAUSED
            else ClientStatus.RECORDING
        )
        recording.processing_step = "Capture progress"
        recording.processing_progress = 42
        transcript = (await db.execute(select(Transcript))).scalar_one()
        if status != RecordingStatus.PROCESSED:
            transcript.transcript_status = "processing"
        if status == RecordingStatus.PROCESSING:
            transcript.settling_state = {"reprocess_pending": True}
        # Another output's work must not block this one.
        if extra:
            transcript.notes_status = "generating"
        await db.commit()

    response = await reader_client.post(
        f"{PATH}/generate", json={"create_output": extra}
    )
    assert response.status_code == 200, response.text

    class LLM:
        def generate_meeting_notes(
            self, text: str, speakers: dict, **kwargs: Any
        ) -> str:
            assert "We agreed to ship." in text
            if fail:
                raise RuntimeError("Provider unavailable")
            return "## Summary\nSnapshot notes."

    worker(LLM())
    async with reader_sessions() as db:
        recording = await db.get(Recording, meeting[0])
        assert (
            recording.status,
            recording.processing_step,
            recording.processing_progress,
        ) == (status, "Capture progress", 42)
        assert recording.client_status == (
            ClientStatus.PAUSED
            if status == RecordingStatus.PAUSED
            else ClientStatus.RECORDING
        )
        transcript = (await db.execute(select(Transcript))).scalar_one()
        target = (
            (await db.execute(select(NotesOutput))).scalar_one()
            if extra
            else transcript
        )
        assert target.notes_status == ("error" if fail else "completed")
        if fail:
            assert "Provider unavailable" in target.error_message
            if not extra:
                assert target.notes == "## Minutes\nPrimary decision."
        else:
            assert target.notes.startswith("## Summary\nSnapshot notes.")
            assert target.notes_generation_metadata["generated_through_ms"] == 1000
        if extra:
            assert transcript.notes == "## Minutes\nPrimary decision."
            assert transcript.notes_status == "generating"


@pytest.mark.parametrize("case", list(product([False, True], repeat=2)))
async def test_older_notes_completion_cannot_replace_a_newer_result(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    worker: Any,
    case: tuple[bool, bool],
) -> None:
    extra, fail = case
    response = await reader_client.post(
        f"{PATH}/generate", json={"create_output": extra}
    )
    assert response.status_code == 200, response.text
    output_id = response.json()["output"]["id"] if extra else None

    class OlderLLM:
        def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
            # A newer generation finishes while the old provider is still running.
            from backend.worker import tasks

            with Session(tasks.generate_notes_task.session.get_bind()) as db:
                target = (
                    db.get(NotesOutput, output_id)
                    if extra
                    else db.exec(select(Transcript)).one()
                )
                claim_notes_generation(db, target)
                target.notes = "Newer result"
                target.notes_status = "completed"
                db.add(target)
                db.commit()
            if fail:
                raise RuntimeError("Old provider failed")
            return "Old result"

    worker(OlderLLM())
    async with reader_sessions() as db:
        target = (
            await db.get(NotesOutput, output_id)
            if extra
            else (await db.execute(select(Transcript))).scalar_one()
        )
        assert target.notes == "Newer result"
        assert target.notes_status == "completed"
        assert target.error_message is None


@pytest.mark.parametrize("case", list(product([False, True], repeat=2)))
async def test_a_run_superseded_while_the_newer_one_still_generates_is_discarded(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    worker: Any,
    case: tuple[bool, bool],
) -> None:
    """Stop mid-generation: finalisation claims request B while live run A is
    still with the provider. A must not write while B is generating."""
    extra, fail = case
    response = await reader_client.post(
        f"{PATH}/generate", json={"create_output": extra}
    )
    assert response.status_code == 200, response.text
    output_id = response.json()["output"]["id"] if extra else None
    newer: list[str] = []

    class SupersededLLM:
        def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
            from backend.worker import tasks

            with Session(tasks.generate_notes_task.session.get_bind()) as db:
                target = (
                    db.get(NotesOutput, output_id)
                    if extra
                    else db.exec(select(Transcript)).one()
                )
                newer.append(claim_notes_generation(db, target))
            if fail:
                raise RuntimeError("Old provider failed")
            return "Old result"

    worker(SupersededLLM())
    async with reader_sessions() as db:
        target = (
            await db.get(NotesOutput, output_id)
            if extra
            else (await db.execute(select(Transcript))).scalar_one()
        )
        assert target.notes_status == "generating"
        assert target.notes_generation_metadata["request_id"] == newer[0]
        assert target.notes == (None if extra else "## Minutes\nPrimary decision.")
        assert target.error_message is None


class _NotesLLM:
    def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
        return "## Summary\nSnapshot notes."


async def test_coverage_reaches_clients_without_the_request_id(
    reader_client: AsyncClient,
    meeting: tuple[int, int],
    worker: Any,
) -> None:
    # No canonical events yet, so the covered revision is 0; the one segment ends at 1 s.
    coverage = {"event_watermark": 0, "generated_through_ms": 1000}
    response = await reader_client.post(f"{PATH}/generate", json={})
    assert response.status_code == 200, response.text
    worker(_NotesLLM())
    recording = (await reader_client.get("/api/v1/recordings/meeting")).json()
    assert recording["transcript"]["notes_generation_metadata"] == coverage

    created = await reader_client.post(f"{PATH}/generate", json={"create_output": True})
    assert created.json()["output"]["notes_generation_metadata"] == {}
    worker(_NotesLLM())
    (listed,) = (await reader_client.get(f"{PATH}/outputs")).json()
    assert listed["notes_generation_metadata"] == coverage
    rerun = await reader_client.post(
        f"{PATH}/generate", json={"output_id": listed["id"]}
    )
    # The previous run's coverage stays until the new one replaces it.
    assert rerun.json()["output"]["notes_generation_metadata"] == coverage


@pytest.mark.parametrize("extra", [False, True])
async def test_a_claim_whose_worker_was_lost_can_be_generated_again(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    stub_celery_dispatch: list[tuple[str, object, object]],
    extra: bool,
) -> None:
    response = await reader_client.post(
        f"{PATH}/generate", json={"create_output": extra}
    )
    assert response.status_code == 200, response.text
    again = {"output_id": response.json()["output"]["id"]} if extra else {}
    assert (await reader_client.post(f"{PATH}/generate", json=again)).status_code == 409
    held = (await reader_client.get("/api/v1/recordings/meeting")).headers["ETag"]

    table = "notes_outputs" if extra else "transcripts"
    async with reader_sessions() as db:
        await db.execute(
            text(f"UPDATE {table} SET updated_at = :at"),
            {"at": utc_now() - timedelta(minutes=16)},
        )
        await db.commit()

    if extra:
        (output,) = (await reader_client.get(f"{PATH}/outputs")).json()
        assert (output["notes_status"], output["error_message"]) == (
            "error",
            "Generation did not finish. Try again.",
        )
    else:
        # A page polling with its held ETag sees the claim expire.
        polled = await reader_client.get(
            "/api/v1/recordings/meeting", headers={"If-None-Match": held}
        )
        assert polled.status_code == 200
        assert polled.json()["transcript"]["notes_status"] == "error"
    dispatched = len(stub_celery_dispatch)
    retried = await reader_client.post(f"{PATH}/generate", json=again)
    assert retried.status_code == 200, retried.text
    assert len(stub_celery_dispatch) == dispatched + 1


async def test_failed_dispatch_keeps_notes_and_allows_retry(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = routes_notes.dispatch_task_best_effort

    async def unavailable(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(routes_notes, "dispatch_task_best_effort", unavailable)
    response = await reader_client.post(f"{PATH}/generate", json={})
    assert response.status_code == 503
    notes = (await reader_client.get(PATH)).json()
    assert notes["notes"] == "## Minutes\nPrimary decision."
    async with reader_sessions() as db:
        assert (
            await db.execute(select(Transcript))
        ).scalar_one().notes_status == "error"
    monkeypatch.setattr(routes_notes, "dispatch_task_best_effort", original)
    assert (await reader_client.post(f"{PATH}/generate", json={})).status_code == 200
