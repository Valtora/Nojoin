"""Notes failures follow ordinary status handling after final publication."""

from concurrent.futures import CancelledError
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import Engine
from sqlmodel import Session

from backend.core.exceptions import AudioProcessingError
from backend.models.recording import Recording, RecordingStatus
from backend.tests.canonical_engine import _transcript
from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.tests.test_live_settling_finalisation import _closed_line, _final_segment
from backend.tests.test_pipeline_stages import _install_happy_path_modules, _run_task
from backend.utils.canonical_pipeline import serialize_canonical_utterances
from backend.utils.config_manager import config_manager
from backend.utils.notes_generation import claim_notes_generation
from backend.utils.status_manager import update_recording_status
from backend.worker import tasks
from backend.worker.tasks import followups, pipeline


def _snapshot(engine: Engine, recording_id: int) -> dict[str, Any]:
    """Observe committed state through a reader independent of the worker."""
    with Session(engine) as reader:
        transcript = _transcript(reader, recording_id)
        recording = reader.get(Recording, recording_id)
        return deepcopy(
            {
                "text": transcript.text,
                "segments": [segment["text"] for segment in transcript.segments],
                "transcript_status": transcript.transcript_status,
                "notes_status": transcript.notes_status,
                "notes": transcript.notes,
                "error_message": transcript.error_message,
                "status": recording.status,
                "step": recording.processing_step,
                "progress": recording.processing_progress,
                "started": recording.processing_started_at is not None,
                "completed": recording.processing_completed_at is not None,
                "client_status": recording.client_status,
            }
        )


def _configure_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    canonical_enabled: bool,
) -> None:
    _install_happy_path_modules(monkeypatch, segments=[_final_segment("whisper")])
    monkeypatch.setattr(pipeline, "claim_notes_generation", claim_notes_generation)
    monkeypatch.setattr(
        pipeline,
        "_run_final_asr_stage",
        lambda *args, **kwargs: {
            "text": "Goodbye world",
            "language": "en",
            "segments": [_final_segment("whisper")],
        },
    )
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: (
            canonical_enabled
            if key == "enable_canonical_transcript_writes"
            else default
        ),
    )
    monkeypatch.setattr(
        followups, "dispatch_meeting_analysis_if_opted_in", lambda _: False
    )


def _pipeline_outcome(result: Any, recording_id: int) -> str | None:
    if result is None:
        return None
    assert result == {"status": "success", "recording_id": recording_id}
    return result["status"]


def _exercise_failure(
    monkeypatch: pytest.MonkeyPatch,
    engine: Engine,
    *,
    marked: bool,
    scenario: str,
    notes_status: str = "pending",
) -> dict[str, Any]:
    with Session(engine) as session:
        recording_id, _ = _closed_line(session)
        transcript = _transcript(session, recording_id)
        if not marked:
            transcript.settling_state = None
        transcript.transcript_status = "processing"
        transcript.notes_status = notes_status
        transcript.notes = "Previous notes"
        recording = session.get(Recording, recording_id)
        recording.audio_path = f"/tmp/recording-{recording_id}.wav"
        recording.duration_seconds = 60
        session.add(recording)
        session.add(transcript)
        session.commit()
        recomputations: list[dict[str, Any]] = []
        failure_views: list[dict[str, Any]] = []

        def recompute(worker_session: Session, target_id: int) -> None:
            update_recording_status(worker_session, target_id)
            recomputations.append(_snapshot(engine, target_id))

        monkeypatch.setattr(tasks, "update_recording_status", recompute)
        failures = {
            "context": RuntimeError("notes context failed"),
            "enqueue": ConnectionError("notes queue unavailable"),
            "timeout": SoftTimeLimitExceeded("notes timed out"),
            "cancel": CancelledError("notes cancelled"),
            "audio": AudioProcessingError("notes context failed"),
            "inline": RuntimeError("inline notes failed"),
            "speakers": RuntimeError("speaker assignment failed"),
            "canonical": RuntimeError("canonical publication failed"),
        }
        failure = failures[scenario]

        def fail(*args: Any, **kwargs: Any) -> None:
            failure_views.append(_snapshot(engine, recording_id))
            raise failure

        monkeypatch.setattr(
            pipeline, "assign_and_identify_speakers", lambda *args, **kwargs: None
        )
        if scenario in {"speakers", "canonical"}:
            monkeypatch.setattr(
                pipeline,
                "assign_and_identify_speakers"
                if scenario == "speakers"
                else "finalize_utterances_from_segments",
                fail,
            )
        elif scenario in {"context", "audio"}:
            monkeypatch.setattr(
                tasks, "_build_automatic_meeting_intelligence_transcript", fail
            )
        else:
            monkeypatch.setattr(
                tasks,
                "_build_automatic_meeting_intelligence_transcript",
                lambda *args, **kwargs: "Goodbye world",
            )
            monkeypatch.setattr(
                pipeline,
                "_meeting_intelligence_runs_on_io",
                lambda _: scenario == "enqueue",
            )
            if scenario == "enqueue":

                def dispatch(name: str, *args: Any, **kwargs: Any) -> Any:
                    if (
                        name
                        == "backend.worker.tasks.generate_meeting_intelligence_task"
                    ):
                        fail(*args, **kwargs)
                    return SimpleNamespace(id="followup-task")

                monkeypatch.setattr(tasks.celery_app, "send_task", dispatch)
            else:
                monkeypatch.setattr(
                    tasks, "_run_automatic_meeting_intelligence_stage", fail
                )

        propagated = None
        result = None
        try:
            result = _run_task(monkeypatch, session, recording_id=recording_id)
        except (SoftTimeLimitExceeded, CancelledError) as exc:
            assert exc is failure
            propagated = type(exc).__name__
        return {
            "final": _snapshot(engine, recording_id),
            "failure_views": failure_views,
            "recomputations": recomputations,
            "propagated": propagated,
            "result": _pipeline_outcome(result, recording_id),
            "canonical_text": [
                row["text"]
                for row in serialize_canonical_utterances(session, recording_id)
            ],
        }


@pytest.mark.parametrize("canonical_enabled", [False, True])
@pytest.mark.parametrize(
    "scenario", ["context", "enqueue", "timeout", "cancel", "audio", "inline"]
)
def test_post_publication_notes_failure_matches_unmarked(
    monkeypatch: pytest.MonkeyPatch,
    publication_engine: Engine,
    canonical_enabled: bool,
    scenario: str,
) -> None:
    with monkeypatch.context() as patch:
        _configure_pipeline(patch, canonical_enabled)
        expected = _exercise_failure(
            patch,
            publication_engine,
            marked=False,
            scenario=scenario,
        )
    with monkeypatch.context() as patch:
        _configure_pipeline(patch, canonical_enabled)
        actual = _exercise_failure(
            patch,
            publication_engine,
            marked=True,
            scenario=scenario,
        )
    # Settled provenance always requires canonical publication, even with writes off.
    assert actual.pop("canonical_text") == ["Goodbye world"]
    assert expected.pop("canonical_text") == [
        "Goodbye world" if canonical_enabled else "Hello world"
    ]
    assert actual == expected
    final = actual["final"]
    assert final["text"] == "Goodbye world"
    assert final["segments"] == ["Goodbye world"]
    assert final["transcript_status"] == "completed"
    assert final["notes_status"] == ("error" if scenario == "enqueue" else "pending")
    assert final["error_message"] == (
        "notes queue unavailable" if scenario == "enqueue" else None
    )
    assert final["notes"] == "Previous notes"
    assert final["completed"] is (scenario == "enqueue")
    if scenario == "enqueue":
        assert final["step"] == "Completed"
        assert final["progress"] == 100
    interrupted = scenario in {"timeout", "cancel"}
    # An unavailable notes queue cannot keep a published transcript processing.
    assert final["status"] == (
        RecordingStatus.ERROR if interrupted else RecordingStatus.PROCESSED
    )
    assert len(actual["failure_views"]) == 1
    assert actual["failure_views"][0]["text"] == "Goodbye world"
    assert len(actual["recomputations"]) == (0 if interrupted else 1)
    assert actual["result"] == ("success" if scenario == "enqueue" else None)
    assert actual["propagated"] == (
        "SoftTimeLimitExceeded"
        if scenario == "timeout"
        else "CancelledError"
        if scenario == "cancel"
        else None
    )


@pytest.mark.parametrize("canonical_enabled", [False, True])
@pytest.mark.parametrize("scenario", ["speakers", "canonical"])
@pytest.mark.parametrize("notes_status", ["pending", "generating"])
def test_pre_publication_failure_keeps_previous_view(
    monkeypatch: pytest.MonkeyPatch,
    publication_engine: Engine,
    canonical_enabled: bool,
    scenario: str,
    notes_status: str,
) -> None:
    _configure_pipeline(monkeypatch, canonical_enabled)
    observed = _exercise_failure(
        monkeypatch,
        publication_engine,
        marked=True,
        scenario=scenario,
        notes_status=notes_status,
    )
    assert observed["final"]["text"] == "Hello world"
    assert observed["final"]["segments"] == ["Hello world"]
    assert observed["canonical_text"] == ["Hello world"]
    assert observed["final"]["transcript_status"] == "processing"
    assert observed["final"]["notes_status"] == notes_status
    assert observed["final"]["status"] == RecordingStatus.ERROR
    assert observed["final"]["completed"] is False
    assert observed["recomputations"] == []
    assert observed["failure_views"][0]["text"] == "Hello world"
