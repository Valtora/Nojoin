from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

import backend.worker.tasks as tasks
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.test_automatic_meeting_intelligence_worker import (
    _create_worker_ai_database,
    _sample_llm_config,
)
from backend.tests.test_generate_notes_task import _create_notes_task_database
from backend.tests.test_infer_speakers_task import _create_infer_speakers_task_database
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceResult
from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceResult,
    SpeakerInferenceSuggestion,
)

pytestmark = pytest.mark.usefixtures("stub_text_embedding")


def _replace_publication(engine: Any) -> None:
    """A distinct transaction publishes newer words and derived products."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE transcripts SET change_sequence = change_sequence + 1, "
                "text = 'Replacement words', notes = 'Replacement notes', "
                "segments = :segments, "
                "notes_status = 'completed', error_message = NULL, "
                "speaker_name_suggestions = '[]', meeting_edge_status = 'ready', "
                'meeting_edge_payload = \'{"summary": "Replacement Edge"}\', '
                "meeting_edge_error_message = NULL WHERE recording_id = 1"
            ),
            {
                "segments": '[{"start": 0, "end": 2, "speaker": "SPEAKER_00", "text": "Replacement words"}]'
            },
        )
        connection.execute(
            text(
                "UPDATE recordings SET name = 'Replacement title', "
                "processing_step = 'Replacement completed' WHERE id = 1"
            )
        )


def _assert_replacement(engine: Any) -> None:
    with Session(engine) as session:
        transcript = session.get(Transcript, 1)
        recording = session.get(Recording, 1)
        assert transcript.notes == "Replacement notes"
        assert transcript.notes_status == "completed"
        assert transcript.error_message is None
        assert transcript.speaker_name_suggestions == []
        assert transcript.meeting_edge_payload == {"summary": "Replacement Edge"}
        assert transcript.meeting_edge_status == "ready"
        assert transcript.meeting_edge_error_message is None
        assert recording.name == "Replacement title"
        assert recording.processing_step == "Replacement completed"


@pytest.mark.parametrize("fails", [False, True])
def test_automatic_result_and_error_cannot_overwrite_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    engine = _create_worker_ai_database(tmp_path)

    class Model:
        def generate_meeting_intelligence(self, *args: Any, **kwargs: Any) -> Any:
            _replace_publication(engine)
            if fails:
                raise RuntimeError("Old model failed")
            return AutomaticMeetingIntelligenceResult(
                title="Old title",
                notes_markdown="## Summary\nOld notes",
                speaker_mapping={"SPEAKER_00": "Alex"},
            )

    monkeypatch.setattr(tasks, "_llm_backend_from_config", lambda config: Model())
    with Session(engine) as session:
        tasks._run_automatic_meeting_intelligence_stage(
            session=session,
            task=None,
            recording=session.get(Recording, 1),
            transcript=session.get(Transcript, 1),
            speakers=session.exec(select(RecordingSpeaker)).all(),
            transcript_text="SPEAKER_00: Opening update.",
            unresolved_speakers=["SPEAKER_00"],
            llm_config=_sample_llm_config(),
            prefer_short_titles=True,
            device_suffix="",
            timeline_notes=RecordingTimelineNotes(),
            update_processing_status=False,
        )
    _assert_replacement(engine)
    with Session(engine) as session:
        speaker = session.exec(
            select(RecordingSpeaker).where(
                RecordingSpeaker.diarization_label == "SPEAKER_00"
            )
        ).first()
        assert speaker.local_name is None


@pytest.mark.parametrize("fails", [False, True])
def test_manual_notes_result_and_error_cannot_overwrite_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    engine = _create_notes_task_database(
        tmp_path,
        owner_settings={
            "llm_provider": "ollama",
            "ollama_model": "test",
        },
    )

    class Model:
        def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
            _replace_publication(engine)
            if fails:
                raise RuntimeError("Old notes failed")
            return "Old notes"

    monkeypatch.setattr(tasks.config_manager, "get_all", lambda: {})
    monkeypatch.setattr(tasks, "_llm_backend_from_config", lambda config: Model())
    monkeypatch.setattr(tasks, "get_sync_session", lambda: Session(engine))
    task = tasks.generate_notes_task
    task._session = None
    try:
        task.run(1)
    finally:
        if task._session is not None:
            task._session.close()
        task._session = None
    _assert_replacement(engine)


@pytest.mark.parametrize("replacement", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_meeting_edge_result_and_error_cannot_overwrite_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool, replacement: bool
) -> None:
    from backend.worker.tasks import meeting_edge_stage as stage

    engine = _create_worker_ai_database(tmp_path)
    with engine.begin() as connection:
        connection.execute(text("UPDATE recordings SET status = 'PROCESSING'"))

    class Model:
        def generate_meeting_edge(self, *args: Any, **kwargs: Any) -> Any:
            if replacement:
                _replace_publication(engine)
            if fails:
                raise RuntimeError("Old Edge failed")
            return SimpleNamespace()

    monkeypatch.setattr(stage, "is_meeting_edge_enabled", lambda settings: True)
    monkeypatch.setattr(
        stage, "load_recording_timeline_notes", lambda *args: RecordingTimelineNotes()
    )
    monkeypatch.setattr(tasks, "_has_meeting_edge_signal", lambda **kwargs: True)
    monkeypatch.setattr(
        stage, "resolve_llm_config", lambda *args, **kwargs: _sample_llm_config()
    )
    monkeypatch.setattr(tasks, "_llm_backend_from_config", lambda config: Model())
    monkeypatch.setattr(
        tasks, "serialize_meeting_edge_result", lambda result: {"summary": "Old Edge"}
    )
    with Session(engine) as session:
        stage._refresh_meeting_edge(SimpleNamespace(session=session), 1)
    if replacement:
        _assert_replacement(engine)
    else:
        with Session(engine) as session:
            transcript = session.get(Transcript, 1)
            assert transcript.meeting_edge_status == ("error" if fails else "ready")
            if fails:
                assert transcript.meeting_edge_error_message == "Old Edge failed"
            else:
                assert transcript.meeting_edge_payload["summary"] == "Old Edge"


@pytest.mark.parametrize("fails", [False, True])
def test_manual_speaker_inference_cannot_write_names_or_status_after_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    engine = _create_infer_speakers_task_database(
        tmp_path,
        owner_settings={
            "llm_provider": "ollama",
            "ollama_model": "test",
        },
    )

    class Model:
        def infer_speaker_suggestions(self, *args: Any, **kwargs: Any) -> Any:
            _replace_publication(engine)
            if fails:
                raise RuntimeError("Old inference failed")
            return SpeakerInferenceResult(
                (
                    SpeakerInferenceSuggestion(
                        diarization_label="SPEAKER_00",
                        suggested_name="Alex",
                        confidence=0.9,
                        rationale="Old introduction",
                    ),
                )
            )

    monkeypatch.setattr(tasks.config_manager, "get_all", lambda: {})
    monkeypatch.setattr(tasks, "_llm_backend_from_config", lambda config: Model())
    monkeypatch.setattr(tasks, "get_sync_session", lambda: Session(engine))
    _run_task(tasks.infer_speakers_task)
    _assert_replacement(engine)
    with Session(engine) as session:
        assert (
            session.exec(
                select(RecordingSpeaker).where(
                    RecordingSpeaker.diarization_label == "SPEAKER_00"
                )
            )
            .first()
            .local_name
            is None
        )


def _run_task(task: Any) -> None:
    task._session = None
    try:
        task.run(1)
    finally:
        if task._session is not None:
            task._session.close()
        task._session = None


@pytest.mark.parametrize("source", ["notes", "transcript"])
def test_old_embedding_cannot_replace_new_publication_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    engine = _create_notes_task_database(
        tmp_path,
        owner_settings={
            "llm_provider": "ollama",
            "ollama_model": "test",
        },
    )

    def embed(chunks: list[str]) -> list[list[float]]:
        _replace_publication(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO context_chunks (created_at, updated_at, recording_id, "
                    "content, meta) VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, "
                    "'Replacement chunk', :meta)"
                ),
                {"meta": '{"source": "' + source + '"}'},
            )
        return [[0.1]] * len(chunks)

    monkeypatch.setattr(tasks.config_manager, "get_all", lambda: {})
    monkeypatch.setattr(
        tasks,
        "_llm_backend_from_config",
        lambda config: SimpleNamespace(
            generate_meeting_notes=lambda *args, **kwargs: "Old notes"
        ),
    )
    monkeypatch.setattr(tasks, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(
        "backend.processing.text_embedding.get_text_embedding_service",
        lambda: SimpleNamespace(embed=embed),
    )
    _run_task(
        tasks.generate_notes_task if source == "notes" else tasks.index_transcript_task
    )
    _assert_replacement(engine)
    with Session(engine) as session:
        assert session.exec(text("SELECT content FROM context_chunks")).all() == [
            ("Replacement chunk",)
        ]


@pytest.mark.parametrize("empty", [False, True])
def test_stale_automatic_fallback_cannot_apply_self_introductions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, empty: bool
) -> None:
    engine = _create_worker_ai_database(tmp_path)
    with Session(engine, expire_on_commit=False) as session:
        transcript = session.get(Transcript, 1)
        transcript.segments = [
            {
                "start": 0.0,
                "end": 2.0,
                "speaker": "SPEAKER_00",
                "text": "Hi everyone, I'm Alex from finance.",
            }
        ]
        session.add(transcript)
        recording = session.get(Recording, 1)
        speakers = session.exec(select(RecordingSpeaker)).all()
        session.flush()
        revision = transcript.change_sequence
        session.commit()
        _replace_publication(engine)
        tasks._run_automatic_meeting_intelligence_stage(
            session=session,
            task=None,
            recording=recording,
            transcript=transcript,
            speakers=speakers,
            transcript_text="" if empty else "Hi, I'm Alex.",
            unresolved_speakers=["SPEAKER_00"],
            llm_config=_sample_llm_config(model=None),
            prefer_short_titles=True,
            device_suffix="",
            timeline_notes=RecordingTimelineNotes(),
            source_revision=revision,
        )
    _assert_replacement(engine)


def test_pipeline_rebuilds_notes_inputs_if_publication_changes_after_final_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.worker.tasks import pipeline
    from backend.worker.tasks.intelligence_source import load_intelligence_source

    engine = _create_worker_ai_database(tmp_path)
    captured: dict[str, Any] = {}

    def concurrent_publication(session: Session, recording_id: int) -> Transcript:
        _replace_publication(engine)
        return load_intelligence_source(session, recording_id)

    def capture_stage(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(pipeline, "load_intelligence_source", concurrent_publication)
    monkeypatch.setattr(
        pipeline, "canonical_finalization_required", lambda transcript: False
    )
    monkeypatch.setattr(
        pipeline,
        "load_recording_timeline_notes",
        lambda *args: RecordingTimelineNotes(),
    )
    monkeypatch.setattr(
        pipeline, "_meeting_intelligence_runs_on_io", lambda config: False
    )
    monkeypatch.setattr(
        tasks, "_run_automatic_meeting_intelligence_stage", capture_stage
    )
    with Session(engine) as session:
        transcript = session.get(Transcript, 1)
        transcript.settling_state = {"reprocess_pending": True}
        session.add(transcript)
        session.commit()
        ctx = pipeline._PipelineRunContext(
            task=SimpleNamespace(update_state=lambda **kwargs: None),
            session=session,
            recording_id=1,
            device_suffix="",
            temp_files=[],
            merged_config={},
        )
        pipeline._finalize_transcript_and_notes(
            ctx,
            session.get(Recording, 1),
            transcript,
            [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "speaker": "SPEAKER_00",
                    "text": "Old candidate words",
                }
            ],
            _sample_llm_config(),
            {"text": "Old candidate words"},
        )
        assert "Replacement words" in captured["transcript_text"]
        assert "Old candidate words" not in captured["transcript_text"]
        assert captured["source_revision"] == transcript.change_sequence


def test_deferred_task_outer_error_cannot_mark_new_publication_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _create_worker_ai_database(tmp_path)

    def fail_stage(**kwargs: Any) -> None:
        kwargs["session"].commit()
        _replace_publication(engine)
        raise RuntimeError("Old task failed outside stage")

    monkeypatch.setattr(
        "backend.worker.tasks.intelligence.load_recording_timeline_notes",
        lambda *args: RecordingTimelineNotes(),
    )
    monkeypatch.setattr(
        tasks, "resolve_llm_config", lambda *args, **kwargs: _sample_llm_config()
    )
    monkeypatch.setattr(tasks, "_run_automatic_meeting_intelligence_stage", fail_stage)
    monkeypatch.setattr(tasks, "get_sync_session", lambda: Session(engine))
    _run_task(tasks.generate_meeting_intelligence_task)
    _assert_replacement(engine)
