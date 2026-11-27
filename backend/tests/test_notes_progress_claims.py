"""A new notes claim starts without the step a lost run left behind.

Claiming renews the claim, which makes the API report the transcript's step
again (backend.models.recording_public), so a claim that kept a lost run's
step would show it until a worker picked the run up. The worker's own claims
are covered with the runs in test_notes_progress; these are the claims made
before a worker has the run: Regenerate Notes' API call and the pipeline
handing notes to the IO lane.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, text
from sqlmodel import Session

from backend.models.recording import Recording
from backend.tests.canonical_engine import _transcript
from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.tests.test_live_settling_finalisation import _closed_line
from backend.tests.test_lost_side_run_claims import (
    LOST,
    _ago,
)
from backend.tests.test_lost_side_run_claims import client as client
from backend.tests.test_lost_side_run_claims import (
    llm_configured as llm_configured,
)
from backend.tests.test_lost_side_run_claims import owner as owner
from backend.tests.test_lost_side_run_claims import seed as seed
from backend.tests.test_lost_side_run_claims import (
    session_maker as session_maker,
)
from backend.tests.test_pipeline_notes_publication_parity import (
    _configure_pipeline,
)
from backend.tests.test_pipeline_stages import _run_task
from backend.worker import tasks
from backend.worker.tasks import pipeline

LOST_STEP = "Drafting notes, part 9 of 9"


@pytest.mark.anyio
async def test_regenerate_notes_claims_without_a_lost_runs_step(
    client: AsyncClient, seed, session_maker, stub_celery_dispatch: list
) -> None:
    await seed(claimed=_ago(LOST), notes_status="generating")
    async with session_maker() as session:
        await session.execute(
            text("UPDATE transcripts SET notes_progress = :step"), {"step": LOST_STEP}
        )
        await session.commit()

    response = await client.post("/api/v1/transcripts/rec-1/notes/generate")

    assert response.status_code == 200, response.text
    assert stub_celery_dispatch, "the run was not queued"
    async with session_maker() as session:
        claimed = (
            await session.execute(
                text("SELECT notes_status, notes_progress FROM transcripts")
            )
        ).one()
    # Reported while the run waits in the queue: generating, with no step.
    assert tuple(claimed) == ("generating", None)


@pytest.mark.parametrize("canonical_enabled", [False, True])
def test_the_pipeline_hands_notes_to_the_io_lane_without_a_lost_runs_step(
    monkeypatch: pytest.MonkeyPatch,
    publication_engine: Engine,
    canonical_enabled: bool,
) -> None:
    _configure_pipeline(monkeypatch, canonical_enabled)
    monkeypatch.setattr(
        pipeline, "assign_and_identify_speakers", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        tasks,
        "_build_automatic_meeting_intelligence_transcript",
        lambda *args, **kwargs: "Goodbye world",
    )
    monkeypatch.setattr(pipeline, "_meeting_intelligence_runs_on_io", lambda _: True)
    with Session(publication_engine) as session:
        recording_id, _ = _closed_line(session)
        transcript = _transcript(session, recording_id)
        transcript.transcript_status = "processing"
        transcript.notes_status = "error"
        transcript.notes_progress = LOST_STEP
        recording = session.get(Recording, recording_id)
        recording.audio_path = f"/tmp/recording-{recording_id}.wav"
        recording.duration_seconds = 60
        session.add(recording)
        session.add(transcript)
        session.commit()

        seen_at_dispatch: list[tuple[Any, Any]] = []

        def dispatch(name: str, args: Any = None, **kwargs: Any) -> None:
            # What the page reads while the run waits in the IO queue.
            with Session(publication_engine) as reader:
                claimed = _transcript(reader, recording_id)
                seen_at_dispatch.append((claimed.notes_status, claimed.notes_progress))

        monkeypatch.setattr(tasks.celery_app, "send_task", dispatch)
        _run_task(monkeypatch, session, recording_id=recording_id)

    assert seen_at_dispatch == [("generating", None)]
