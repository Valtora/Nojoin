"""Final publication has one visible cutover and terminal failure statuses."""

from concurrent.futures import CancelledError
from types import SimpleNamespace
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import Engine, event
from sqlmodel import Session

from backend.core.exceptions import AudioProcessingError
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import RecordingSpeaker
from backend.tests.test_live_settling_finalisation import _closed_line, _final_segment
from backend.tests.test_pipeline_stages import (
    _FakeRecording,
    _FakeSession,
    _FakeTranscript,
    _install_happy_path_modules,
    _run_task,
)
from backend.tests.test_transcript_publication import _transcript
from backend.tests.test_transcript_publication import (
    publication_engine as publication_engine,
)
from backend.utils.canonical_pipeline import (
    serialize_canonical_utterances,
    update_utterance_text,
)
from backend.utils.config_manager import config_manager
from backend.utils.status_manager import update_recording_status
from backend.worker import tasks
from backend.worker.tasks import pipeline, vad_stage


@pytest.mark.parametrize("stage", ["configuration", "speakers"])
@pytest.mark.parametrize(
    "component_states",
    [
        (transcript, notes)
        for transcript in ("pending", "processing", "completed")
        for notes in ("pending", "generating")
    ],
)
@pytest.mark.parametrize(
    "failure",
    [RuntimeError, AudioProcessingError, SoftTimeLimitExceeded, CancelledError],
)
def test_speaker_stage_failure_keeps_previous_view_and_terminal_error(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    component_states: tuple[str, str],
    failure: type[Exception],
) -> None:
    component_status, notes_status = component_states
    recording = _FakeRecording(740)
    transcript = _FakeTranscript(740)
    transcript.text = "Previous accepted view"
    transcript.segments = [{"text": transcript.text}]
    transcript.transcript_status = component_status
    transcript.notes_status = notes_status
    transcript.settling_state = {"settled": True}
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch)
    monkeypatch.setattr(tasks, "update_recording_status", update_recording_status)

    def fail_after_asr(*args: Any, **kwargs: Any) -> None:
        assert transcript.text == "Previous accepted view"
        assert transcript.segments == [{"text": "Previous accepted view"}]
        raise failure("speaker stage failed")

    monkeypatch.setattr(pipeline, "assign_and_identify_speakers", fail_after_asr)

    class BrokenConfiguration:
        @property
        def merged_config(self) -> Any:
            fail_after_asr()

    configuration = BrokenConfiguration() if stage == "configuration" else None
    if failure in {SoftTimeLimitExceeded, CancelledError}:
        with pytest.raises(failure):
            _run_task(monkeypatch, session, recording_id=740, llm_config=configuration)
    else:
        _run_task(monkeypatch, session, recording_id=740, llm_config=configuration)
    assert transcript.text == "Previous accepted view"
    assert transcript.segments == [{"text": "Previous accepted view"}]
    assert transcript.transcript_status == component_status
    assert recording.status == RecordingStatus.ERROR
    assert recording.processing_completed_at is None


def _context(session: Session, recording_id: int) -> Any:
    return SimpleNamespace(
        session=session,
        task=SimpleNamespace(update_state=lambda **kwargs: None),
        recording_id=recording_id,
        device_suffix="",
        temp_files=[],
        merged_config={},
    )


@pytest.mark.parametrize("canonical_failure", [False, True])
def test_final_result_is_published_only_with_canonical_projection(
    publication_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    canonical_failure: bool,
) -> None:
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: default)
    with Session(publication_engine) as session:
        recording_id, _ = _closed_line(session)
        transcript = _transcript(session, recording_id)
        transcript.transcript_status = "processing"
        session.add(transcript)
        speaker = RecordingSpeaker(
            recording_id=recording_id,
            diarization_label="SPEAKER_00",
            local_name="Previous name",
        )
        session.add(speaker)
        session.commit()
        speaker.local_name = "Final name"
        session.add(speaker)
        commits: list[str] = []

        def observe_commit(committing: Session) -> None:
            with Session(publication_engine) as reader:
                commits.append(_transcript(reader, recording_id).text or "")

        event.listen(session, "after_commit", observe_commit)

        def fail_canonical(*args: Any, **kwargs: Any) -> None:
            assert transcript.text == "Hello world"
            assert transcript.segments[0]["text"] == "Hello world"
            raise RuntimeError("canonical failed")

        def after_publication(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("notes context failed")

        if canonical_failure:
            monkeypatch.setattr(
                pipeline, "finalize_utterances_from_segments", fail_canonical
            )
        monkeypatch.setattr(
            pipeline,
            "_build_automatic_meeting_intelligence_transcript",
            after_publication,
        )
        recording = session.get(Recording, recording_id)
        with pytest.raises(RuntimeError, match="failed"):
            pipeline._finalize_transcript_and_notes(
                _context(session, recording_id),
                recording,
                transcript,
                [_final_segment("whisper")],
                None,
                {"text": "Goodbye world", "language": "en"},
            )
        session.rollback()
        expected = "Hello world" if canonical_failure else "Goodbye world"
        assert _transcript(session, recording_id).text == expected
        assert [
            line["text"]
            for line in serialize_canonical_utterances(session, recording_id)
        ] == [expected]
        assert commits == ([] if canonical_failure else ["Goodbye world"])
        assert session.get(RecordingSpeaker, speaker.id).local_name == (
            "Previous name" if canonical_failure else "Final name"
        )
        assert _transcript(session, recording_id).transcript_status == (
            "processing" if canonical_failure else "completed"
        )


@pytest.mark.parametrize(
    "failure_type",
    [RuntimeError, AudioProcessingError, SoftTimeLimitExceeded, CancelledError],
)
def test_unmarked_preparation_failure_keeps_head_exception_and_state(
    monkeypatch: pytest.MonkeyPatch, failure_type: type[Exception]
) -> None:
    recording = _FakeRecording(741)
    transcript = _FakeTranscript(741)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch)
    failure = failure_type("configuration failed")

    class BrokenConfiguration:
        @property
        def merged_config(self) -> Any:
            raise failure

    with pytest.raises(failure_type) as raised:
        _run_task(
            monkeypatch, session, recording_id=741, llm_config=BrokenConfiguration()
        )
    assert raised.value is failure
    assert recording.status == RecordingStatus.PROCESSED
    assert session.committed == 0
    assert transcript.transcript_status == "pending"


@pytest.mark.parametrize("failure_stage", [None, "speakers", "canonical"])
def test_unmarked_canonical_enabled_pipeline_publishes_asr_before_speakers(
    publication_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str | None,
) -> None:
    _install_happy_path_modules(
        monkeypatch,
        segments=[
            {"start": 0.0, "end": 1.0, "speaker": "UNKNOWN", "text": "hello world"}
        ],
    )
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: default)
    monkeypatch.setattr(tasks, "update_recording_status", update_recording_status)
    from backend.worker.tasks import followups

    monkeypatch.setattr(
        followups, "dispatch_meeting_analysis_if_opted_in", lambda recording_id: False
    )
    with Session(publication_engine) as session:
        recording_id, _ = _closed_line(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = None
        transcript.transcript_status = "processing"
        recording = session.get(Recording, recording_id)
        recording.audio_path = "/tmp/recording.wav"
        recording.duration_seconds = 60.0
        session.add(transcript)
        session.add(recording)
        session.commit()
        seen: list[str] = []

        def speakers(*args: Any, **kwargs: Any) -> None:
            with Session(publication_engine) as reader:
                visible = _transcript(reader, recording_id)
                assert visible.transcript_status == "completed"
                assert visible.text == "hello world"
                seen.append(visible.text)
            if failure_stage == "speakers":
                raise RuntimeError("speaker failed")

        def fail_canonical(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("canonical failed")

        monkeypatch.setattr(pipeline, "assign_and_identify_speakers", speakers)
        if failure_stage == "canonical":
            monkeypatch.setattr(
                pipeline, "finalize_utterances_from_segments", fail_canonical
            )
        result = _run_task(monkeypatch, session, recording_id=recording_id)
        session.expire_all()
        assert seen == ["hello world"]
        assert _transcript(session, recording_id).text == "hello world"
        assert _transcript(session, recording_id).transcript_status == "completed"
        assert session.get(Recording, recording_id).status == RecordingStatus.PROCESSED
        assert result == (
            {"status": "success", "recording_id": recording_id}
            if failure_stage is None
            else None
        )


@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize("protected", [False, True])
def test_no_speech_reconciles_settled_lines_and_preserves_unmarked_policy(
    publication_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    marked: bool,
    protected: bool,
) -> None:
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: default)
    monkeypatch.setattr(vad_stage, "auto_link_recording", lambda *args: None)
    monkeypatch.setattr(
        vad_stage,
        "mark_recording_audio_chunks_ready_for_cleanup",
        lambda *args, **kwargs: None,
    )
    with Session(publication_engine) as session:
        recording_id, line = _closed_line(session)
        transcript = _transcript(session, recording_id)
        if not marked:
            transcript.settling_state = None
            session.add(transcript)
        if protected:
            update_utterance_text(
                session,
                recording_id=recording_id,
                utterance_public_id=line["id"],
                text="Accepted correction",
            )
        session.commit()
        recording = session.get(Recording, recording_id)
        vad_stage._complete_without_speech(session, recording)
        session.expire_all()
        transcript = _transcript(session, recording_id)
        assert recording.status == RecordingStatus.PROCESSED
        assert transcript.transcript_status == "completed"
        assert transcript.text == (
            "Accepted correction" if marked and protected else ""
        )
        active = serialize_canonical_utterances(session, recording_id)
        assert [row["text"] for row in active] == (
            (["Accepted correction"] if protected else [])
            if marked
            else ["Accepted correction" if protected else "Hello world"]
        )


@pytest.mark.parametrize("marked", [False, True])
def test_canonical_rollback_switch_keeps_settled_final_protection_and_unmarked_parity(
    publication_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    marked: bool,
) -> None:
    with Session(publication_engine) as session:
        recording_id, line = _closed_line(session)
        transcript = _transcript(session, recording_id)
        if not marked:
            transcript.settling_state = None
            session.add(transcript)
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=line["id"],
            text="Accepted correction",
        )
        session.commit()
        monkeypatch.setattr(
            config_manager,
            "get",
            lambda key, default=None: (
                False if key == "enable_canonical_transcript_writes" else default
            ),
        )

        def stop_after_commit(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("notes context failed")

        monkeypatch.setattr(
            pipeline,
            "_build_automatic_meeting_intelligence_transcript",
            stop_after_commit,
        )
        with pytest.raises(RuntimeError, match="notes context failed"):
            if not marked:
                pipeline._persist_final_transcript(
                    _context(session, recording_id),
                    session.get(Recording, recording_id),
                    [_final_segment("whisper")],
                    {"text": "Goodbye world"},
                )
            pipeline._finalize_transcript_and_notes(
                _context(session, recording_id),
                session.get(Recording, recording_id),
                transcript,
                [_final_segment("whisper")],
                None,
                {"text": "Goodbye world"},
            )
        session.rollback()
        expected = "Accepted correction" if marked else "Goodbye world"
        assert _transcript(session, recording_id).text == expected
        assert _transcript(session, recording_id).segments[0]["text"] == expected
        assert (
            serialize_canonical_utterances(session, recording_id)[0]["text"]
            == "Accepted correction"
        )
