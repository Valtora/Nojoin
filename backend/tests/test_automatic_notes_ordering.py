"""Automatic notes settle only the request that still owns the output."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

import backend.worker.tasks as tasks_module
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.test_automatic_meeting_intelligence_worker import (
    _create_worker_ai_database,
    _sample_llm_config,
)
from backend.utils.llm_config import ResolvedLLMConfig
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceResult
from backend.utils.notes_generation import claim_notes_generation


@pytest.fixture
def automatic_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = _create_worker_ai_database(tmp_path)
    with Session(engine) as session:
        recording = session.get(Recording, 1)
        recording.status = RecordingStatus.PROCESSED
        recording.processing_step = "Completed"
        recording.processing_progress = 100
        transcript = session.exec(select(Transcript)).one()
        transcript.notes = "Previous notes"
        session.add(recording)
        session.add(transcript)
        session.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def _recording_state(engine: Engine) -> tuple[Any, ...]:
    with Session(engine) as reader:
        recording = reader.get(Recording, 1)
        return (
            recording.name,
            recording.status,
            recording.processing_step,
            recording.processing_progress,
            recording.processing_started_at,
            recording.processing_completed_at,
        )


def _run_stage(
    session: Session,
    config: ResolvedLLMConfig,
    request_id: str | None,
    transcript_text: str = "SPEAKER_00: Opening update.",
) -> AutomaticMeetingIntelligenceResult | None:
    return tasks_module._run_automatic_meeting_intelligence_stage(
        session=session,
        task=None,
        recording=session.get(Recording, 1),
        transcript=session.exec(select(Transcript)).one(),
        speakers=session.exec(select(RecordingSpeaker)).all(),
        transcript_text=transcript_text,
        unresolved_speakers=("SPEAKER_00",),
        llm_config=config,
        prefer_short_titles=True,
        device_suffix="",
        timeline_notes=RecordingTimelineNotes(),
        update_processing_status=False,
        request_id=request_id,
    )


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("preclaimed", [False, True])
def test_older_automatic_result_preserves_newer_manual_notes(
    automatic_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
    preclaimed: bool,
) -> None:
    before = _recording_state(automatic_engine)
    manual_requests: list[str] = []

    class LLM:
        def generate_meeting_intelligence(
            self, *args: Any, **kwargs: Any
        ) -> AutomaticMeetingIntelligenceResult:
            with Session(automatic_engine) as newer:
                transcript = newer.exec(select(Transcript)).one()
                manual_requests.append(claim_notes_generation(newer, transcript))
                transcript.notes = "Newer manual notes"
                transcript.notes_status = "completed"
                transcript.notes_generation_metadata = {
                    **transcript.notes_generation_metadata,
                    "generated_through_ms": 5_000,
                }
                newer.add(transcript)
                newer.commit()
            if fails:
                raise RuntimeError("Older automatic request failed")
            return AutomaticMeetingIntelligenceResult(
                title="Obsolete automatic title",
                notes_markdown="## Summary\nObsolete automatic notes.",
                speaker_mapping={"SPEAKER_00": "Obsolete speaker"},
            )

    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda _: LLM())
    with Session(automatic_engine) as session:
        transcript = session.exec(select(Transcript)).one()
        request_id = claim_notes_generation(session, transcript) if preclaimed else None
        assert _run_stage(session, _sample_llm_config(), request_id) is None

    assert len(manual_requests) == 1
    assert _recording_state(automatic_engine) == before
    with Session(automatic_engine) as reader:
        transcript = reader.exec(select(Transcript)).one()
        assert transcript.notes == "Newer manual notes"
        assert transcript.notes_status == "completed"
        assert transcript.error_message is None
        assert transcript.notes_generation_metadata == {
            "request_id": manual_requests[0],
            "generated_through_ms": 5_000,
        }
        assert not transcript.speaker_name_suggestions
        assert reader.get(RecordingSpeaker, 1).name == "Speaker 1"


@pytest.mark.parametrize("missing_input", ["configuration", "transcript"])
def test_deferred_request_with_unusable_inputs_settles_only_notes_error(
    automatic_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    missing_input: str,
) -> None:
    before = _recording_state(automatic_engine)

    def no_llm(config: ResolvedLLMConfig) -> Any:
        pytest.fail("An unusable deferred request must not construct an LLM backend")

    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", no_llm)
    config = _sample_llm_config(
        model=None if missing_input == "configuration" else "gpt-test"
    )
    with Session(automatic_engine) as session:
        transcript = session.exec(select(Transcript)).one()
        request_id = claim_notes_generation(session, transcript)
        result = _run_stage(
            session,
            config,
            request_id,
            transcript_text="" if missing_input == "transcript" else "Opening update.",
        )
        assert result is None

    assert _recording_state(automatic_engine) == before
    with Session(automatic_engine) as reader:
        transcript = reader.exec(select(Transcript)).one()
        assert transcript.notes_status == "error"
        assert transcript.notes == "Previous notes"
        assert transcript.error_message == (
            "Transcript is empty"
            if missing_input == "transcript"
            else "No model selected for openai"
        )
        assert transcript.notes_generation_metadata["request_id"] == request_id


def test_automatic_notes_cutoff_describes_input_before_a_midrun_transcript_change(
    automatic_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LLM:
        def generate_meeting_intelligence(
            self, *args: Any, **kwargs: Any
        ) -> AutomaticMeetingIntelligenceResult:
            with Session(automatic_engine) as newer:
                transcript = newer.exec(select(Transcript)).one()
                transcript.segments = [
                    *transcript.segments,
                    {"start": 8.0, "end": 80.0, "text": "Not in the notes input."},
                ]
                newer.add(transcript)
                newer.commit()
            return AutomaticMeetingIntelligenceResult(
                title="Original meeting",
                notes_markdown="## Summary\nNotes from the original eight seconds.",
                speaker_mapping={},
            )

    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda _: LLM())
    with Session(automatic_engine) as session:
        result = _run_stage(session, _sample_llm_config(), request_id=None)
        assert result is not None

    with Session(automatic_engine) as reader:
        transcript = reader.exec(select(Transcript)).one()
        assert transcript.segments[-1]["end"] == 80.0
        assert transcript.notes_status == "completed"
        assert transcript.notes == "## Summary\nNotes from the original eight seconds."
        assert transcript.notes_generation_metadata["generated_through_ms"] == 8_000
