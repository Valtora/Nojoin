"""The language detection record and the mismatch notice (#671).

Whisper detects a language on VAD speech on every run. The pipeline stores what
it heard beside the language the transcript used, and the recording page asks
when they disagree, until the recording has a language of its own.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from backend.models.recording import Recording, RecordingStatus
from backend.models.recording_public import serialize_recording
from backend.models.transcript import Transcript
from backend.tests.test_pipeline_stages import (
    _FakeRecording,
    _FakeSession,
    _FakeTranscript,
    _install,
    _install_happy_path_modules,
    _run_task,
)
from backend.utils.language_notice import (
    LANGUAGE_NOTICE_MIN_PROBABILITY,
    language_detection_record,
    language_notice,
)

HEARD_GERMAN = {
    "detected": "de",
    "detected_label": "German",
    "probability": 0.93,
    "used": "en",
    "engine": "whisper",
}


def _notice(**overrides):
    arguments = {
        "processed": True,
        "recording_language": None,
        "detection": HEARD_GERMAN,
    }
    return language_notice(**{**arguments, **overrides})


def test_a_sure_detection_of_another_language_asks():
    assert _notice() == {
        "kind": "mismatch",
        "detected": "de",
        "detected_label": "German",
        "used": "en",
        "used_label": "English",
        "engine": "whisper",
        "reprocess_language": "de",
    }


def test_a_language_that_cant_be_chosen_reprocesses_on_auto():
    """Whisper hears ~99 languages; only the listed ones can be set. On auto it
    detects on the same speech, so it lands on the language it heard."""
    detection = {**HEARD_GERMAN, "detected": "he", "detected_label": "Hebrew"}

    notice = _notice(detection=detection)

    assert notice["detected_label"] == "Hebrew"
    assert notice["reprocess_language"] == "auto"


@pytest.mark.parametrize(
    ("probability", "asks"),
    [
        (LANGUAGE_NOTICE_MIN_PROBABILITY, True),
        (LANGUAGE_NOTICE_MIN_PROBABILITY - 0.01, False),
    ],
)
def test_only_a_sure_enough_detection_asks(probability, asks):
    """A bilingual meeting's coin toss doesn't nag."""
    notice = _notice(detection={**HEARD_GERMAN, "probability": probability})

    assert (notice is not None) is asks
    assert LANGUAGE_NOTICE_MIN_PROBABILITY == 0.5


@pytest.mark.parametrize(
    "overrides",
    [
        {"processed": False},  # queued or processing: wait for the transcript
        {"recording_language": "en"},  # answered: the recording has a language
        {"recording_language": "auto"},
        {"detection": None},  # VAD off, a failed detection, or not Whisper
        {"detection": {**HEARD_GERMAN, "detected": "en"}},  # it agreed
        {"detection": {**HEARD_GERMAN, "detected": "EN"}},
    ],
)
def test_no_notice_when_there_is_nothing_to_ask(overrides):
    assert _notice(**overrides) is None


def test_the_record_keeps_what_was_heard_beside_what_was_used():
    result = {
        "language": "en",
        "language_detection": {
            "detected": "de",
            "detected_label": "German",
            "probability": 0.93,
        },
    }

    config = {"transcription_backend": "whisper"}

    assert language_detection_record(result, config) == HEARD_GERMAN


def test_no_record_without_a_detection():
    assert language_detection_record({"language": "en"}, {}) is None


def _recording(status, language, detection) -> Recording:
    now = datetime(2026, 10, 23)
    recording = Recording(
        id=1,
        name="Board",
        audio_path="/tmp/board.wav",
        status=status,
        transcription_language=language,
        created_at=now,
        updated_at=now,
    )
    recording.transcript = Transcript(
        id=1,
        recording_id=1,
        segments=[],
        language_detection=detection,
        created_at=now,
        updated_at=now,
    )
    return recording


def test_the_recording_page_read_carries_the_notice():
    recording = _recording(RecordingStatus.PROCESSED, None, HEARD_GERMAN)

    read = serialize_recording(recording, include_transcript=True)

    assert read.language_notice is not None
    assert read.language_notice.reprocess_language == "de"
    assert read.language_notice.used == "en"


@pytest.mark.parametrize(
    ("status", "language"),
    [(RecordingStatus.PROCESSED, "en"), (RecordingStatus.PROCESSING, None)],
)
def test_the_read_carries_no_notice_once_answered_or_while_processing(status, language):
    read = serialize_recording(
        _recording(status, language, HEARD_GERMAN), include_transcript=True
    )

    assert read.language_notice is None


def _persisted_detection(monkeypatch, asr_result: dict, *, existing: bool = True):
    recording = _FakeRecording(741)
    transcript = _FakeTranscript(741)
    transcript.language_detection = {"detected": "stale"}
    session = _FakeSession(recording, transcript if existing else None)
    _install_happy_path_modules(monkeypatch)
    _install(
        monkeypatch,
        "backend.processing.transcribe",
        transcribe_audio=lambda path, config=None: dict(asr_result),
        release_model_cache=lambda: None,
    )
    _run_task(monkeypatch, session, recording_id=741)
    if existing:
        return transcript.language_detection
    # The new row is added again on later saves: it is one row.
    created = {id(row): row for row in session.added if isinstance(row, Transcript)}
    [row] = created.values()
    return row.language_detection


@pytest.mark.parametrize("existing", [True, False])
def test_the_pipeline_stores_each_runs_detection(monkeypatch, existing):
    """Also on the transcript a reprocess creates afresh, having deleted the old."""
    detection = {"detected": "de", "detected_label": "German", "probability": 0.93}
    stored = _persisted_detection(
        monkeypatch,
        {
            "text": "hallo",
            "language": "en",
            "segments": [],
            "language_detection": detection,
        },
        existing=existing,
    )

    assert stored == HEARD_GERMAN


def test_a_run_without_detection_clears_the_last_one(monkeypatch):
    stored = _persisted_detection(
        monkeypatch, {"text": "hello", "language": "en", "segments": []}
    )

    assert stored is None
