"""A Discard during inference, through the real live lane on PostgreSQL (#869).

The engine call is most of a run, so it is where a Discard most often lands.
The recording row is deleted, as Discard does, and the capture's folder
removed. Neither the legacy lane nor the settling lane logs an error or a
traceback for it, and nothing writes the folder back.
"""

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete
from sqlmodel import Session

from backend.models.recording import Recording
from backend.processing import live_settling, transcribe
from backend.processing.live_settling import _decode_words as real_decode_words
from backend.processing.transcribe import transcribe_audio as real_transcribe_audio
from backend.tests.test_live_discard_race import (
    _engine_that_discards,
    _loud,
    _register_engine,
)
from backend.tests.test_live_resume_lane import ResumeLane
from backend.tests.test_live_resume_lane import resume_lane as resume_lane
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)


@pytest.mark.parametrize("resume_lane", [("on", False), ("off", False)], indirect=True)
def test_a_discard_during_inference_logs_nothing_loud(
    resume_lane: ResumeLane,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lane = resume_lane
    monkeypatch.setattr(live_settling, "_decode_words", real_decode_words)
    monkeypatch.setattr(transcribe, "transcribe_audio", real_transcribe_audio)
    private: Path = tmp_path_factory.mktemp("engine-input")
    monkeypatch.setattr(tempfile, "tempdir", str(private))

    def discard() -> None:
        with Session(lane.engine) as session:
            session.execute(delete(Recording).where(Recording.id == lane.recording_id))
            session.commit()
        shutil.rmtree(lane.path, ignore_errors=True)

    calls: list[Any] = []

    def counted() -> None:
        if not calls:
            discard()
        calls.append(None)

    _register_engine(monkeypatch, _engine_that_discards(counted))
    lane.upload(1)
    caplog.set_level(logging.INFO)

    lane.run(1)

    assert calls, "the engine was not called"
    assert _loud(caplog) == []
    assert not lane.path.exists()
    assert list(private.iterdir()) == []
    with Session(lane.engine) as session:
        assert session.get(Recording, lane.recording_id) is None
