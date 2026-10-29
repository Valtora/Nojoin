"""HEAD-derived publication contracts for ordinary and settled recordings."""

import sys
from concurrent.futures import CancelledError
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from backend.core.exceptions import AudioProcessingError
from backend.models.recording import RecordingStatus
from backend.tests.test_pipeline_stages import (
    _FakeRecording,
    _FakeSession,
    _FakeTranscript,
    _install_happy_path_modules,
    _run_task,
)
from backend.utils import canonical_pipeline
from backend.utils.status_manager import update_recording_status
from backend.worker import tasks
from backend.worker.tasks import followups, pipeline


def _exercise_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    marked: bool,
    scenario: str,
    notes_status: str = "pending",
    canonical_enabled: bool = False,
) -> dict[str, Any]:
    recording = _FakeRecording(740)
    transcript = _FakeTranscript(740)
    transcript.text = "Previous accepted view"
    transcript.segments = [{"text": transcript.text}]
    transcript.transcript_status = "processing"
    transcript.notes_status = notes_status
    transcript.settling_state = {"settled": True} if marked else None
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch)
    monkeypatch.setattr(
        followups, "dispatch_meeting_analysis_if_opted_in", lambda recording_id: False
    )
    monkeypatch.setattr(tasks, "update_recording_status", update_recording_status)
    monkeypatch.setattr(
        tasks.config_manager,
        "get",
        lambda key, default=None: (
            canonical_enabled
            if key == "enable_canonical_transcript_writes"
            else default
        ),
    )
    events: list[tuple[Any, ...]] = []
    previous_commit = session.commit

    def commit() -> None:
        events.append(
            (
                "commit",
                transcript.text,
                transcript.transcript_status,
                deepcopy(transcript.segments),
            )
        )
        previous_commit()

    session.commit = commit
    failures = {
        "asr_failure": RuntimeError("decoder failed"),
        "speaker_failure": RuntimeError("speaker failed"),
        "speaker_audio_failure": AudioProcessingError("speaker failed"),
        "speaker_timeout": SoftTimeLimitExceeded("worker interrupted"),
        "speaker_cancel": CancelledError("worker interrupted"),
        "asr_timeout": SoftTimeLimitExceeded("worker interrupted"),
        "asr_cancel": CancelledError("worker interrupted"),
    }
    failure = failures.get(scenario)
    if scenario.startswith("asr_"):
        transcribe = sys.modules["backend.processing.transcribe"]

        def asr(*args: Any, **kwargs: Any) -> dict[str, Any]:
            if failure:
                raise failure
            return {"text": "", "segments": [], "language": "en"}

        monkeypatch.setattr(transcribe, "transcribe_audio", asr)
    if scenario == "vad_empty":
        monkeypatch.setattr(
            sys.modules["backend.processing.vad"],
            "mute_non_speech_segments",
            lambda *args, **kwargs: (True, 0.0),
        )

    def speakers(*args: Any, **kwargs: Any) -> None:
        events.append(("speakers", transcript.text, transcript.transcript_status))
        if scenario.startswith("speaker_"):
            raise failure

    def refresh(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        transcript.text = (
            "" if scenario in {"vad_empty", "asr_empty"} else "hello world"
        )
        transcript.segments = [] if not transcript.text else [{"text": transcript.text}]
        return transcript.segments

    monkeypatch.setattr(pipeline, "assign_and_identify_speakers", speakers)
    monkeypatch.setattr(
        tasks, "finalize_utterances_from_segments", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(tasks, "refresh_transcript_projection_from_canonical", refresh)
    monkeypatch.setattr(
        canonical_pipeline,
        "finalize_utterances_from_segments",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        canonical_pipeline, "refresh_transcript_projection_from_canonical", refresh
    )
    propagated = None
    try:
        result = _run_task(monkeypatch, session, recording_id=740)
    except (SoftTimeLimitExceeded, CancelledError) as exc:
        assert exc is failure
        propagated = type(exc).__name__
        result = None
    return {
        "events": events,
        "text": transcript.text,
        "transcript_status": transcript.transcript_status,
        "status": recording.status,
        "completed": recording.processing_completed_at is not None,
        "result": result,
        "propagated": propagated,
    }


@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize("canonical_enabled", [False, True])
@pytest.mark.parametrize("notes_status", ["pending", "generating"])
@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "vad_empty",
        "asr_empty",
        "asr_failure",
        "speaker_failure",
        "speaker_audio_failure",
        "speaker_timeout",
        "speaker_cancel",
        "asr_timeout",
        "asr_cancel",
    ],
)
def test_publication_and_status_follow_the_recording_contract(
    monkeypatch: pytest.MonkeyPatch,
    marked: bool,
    canonical_enabled: bool,
    notes_status: str,
    scenario: str,
) -> None:
    observed = _exercise_pipeline(
        monkeypatch,
        marked=marked,
        scenario=scenario,
        notes_status=notes_status,
        canonical_enabled=canonical_enabled,
    )
    interrupted = scenario.endswith("timeout") or scenario == "speaker_cancel"
    failed = "failure" in scenario or interrupted
    asr_failed = scenario in {"asr_failure", "asr_cancel"}
    failed = failed or asr_failed
    reached_speakers = scenario.startswith("speaker_") or scenario in {
        "success",
        "asr_empty",
    }
    fresh_text = "" if scenario in {"vad_empty", "asr_empty"} else "hello world"
    previous_view = failed and (marked or not scenario.startswith("speaker_"))
    assert observed["text"] == (
        "Previous accepted view" if previous_view else fresh_text
    )
    assert observed["transcript_status"] == (
        "error" if asr_failed else "processing" if previous_view else "completed"
    )
    # Notes activity cannot hide ASR failure or keep a final transcript processing.
    if interrupted or (failed and marked) or asr_failed:
        expected_status = RecordingStatus.ERROR
    else:
        expected_status = RecordingStatus.PROCESSED
    assert observed["status"] == expected_status
    assert observed["completed"] is (not failed)
    assert observed["propagated"] == (
        "SoftTimeLimitExceeded"
        if scenario.endswith("timeout")
        else "CancelledError"
        if scenario == "speaker_cancel"
        else None
    )
    assert observed["result"] == (
        {"status": "success", "recording_id": 740}
        if scenario in {"success", "asr_empty"}
        else None
    )
    speaker_events = [event for event in observed["events"] if event[0] == "speakers"]
    assert speaker_events == (
        [
            ("speakers", "Previous accepted view", "processing")
            if marked
            else ("speakers", fresh_text, "completed")
        ]
        if reached_speakers
        else []
    )
    if reached_speakers:
        before_speakers = observed["events"][
            : observed["events"].index(speaker_events[0])
        ]
        assert any(
            event[1] == fresh_text and event[2] == "completed"
            for event in before_speakers
        ) is (not marked)


@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize(
    "dispatch_name",
    [
        "index_transcript_task",
        "compute_delivery_analytics_task",
        "compute_overlap_analytics_task",
    ],
)
@pytest.mark.parametrize("failure_type", [OSError, ConnectionError])
def test_optional_followup_failure_preserves_completed_result(
    monkeypatch: pytest.MonkeyPatch,
    marked: bool,
    dispatch_name: str,
    failure_type: type[Exception],
) -> None:
    original_install = _install_happy_path_modules

    def install(*args: Any, **kwargs: Any) -> Any:
        result = original_install(*args, **kwargs)

        def fail(*args: Any, **kwargs: Any) -> None:
            raise failure_type("broker temporarily unreachable")

        monkeypatch.setattr(tasks, dispatch_name, SimpleNamespace(delay=fail))
        return result

    monkeypatch.setattr(sys.modules[__name__], "_install_happy_path_modules", install)
    observed = _exercise_pipeline(monkeypatch, marked=marked, scenario="success")
    assert observed["status"] == RecordingStatus.PROCESSED
    assert observed["text"] == "hello world"
    assert observed["transcript_status"] == "completed"
    assert observed["completed"] is True
    assert observed["result"] == {"status": "success", "recording_id": 740}
