"""When the live task queues a naming check, run through the real live lane.

The lane runs the real label step; only the label device, the speaker model
(which hears every audible sample as one voice), Celery dispatch and Redis are
replaced.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlmodel import Session

from backend.models.recording import Recording
from backend.models.user import User
from backend.processing.live_diarization import step as labels_worker
from backend.tests.live_label_fakes import (
    SAMPLE_RATE,
    SpeakerTrackModel,
    speaker_audio,
)
from backend.tests.test_live_resume_lane import ResumeLane
from backend.tests.test_live_resume_lane import resume_lane as resume_lane
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.tests.test_live_speaker_labels_db import (
    RECORDING_ID,
    _database,
    _patch_step,
)
from backend.worker.tasks.live_naming_stage import LIVE_NAMING_TASK

# Captured before the lane fixture swaps the step out for its own recorder.
REAL_LABEL_STEP = labels_worker.run_live_speaker_labels


class _OneVoice(SpeakerTrackModel):
    """The speaker model: every audible sample in a window is one speaker."""

    def __call__(self, windows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return super().__call__((np.abs(windows) > 0.01).astype(np.float32))


def _label_every_line(lane: ResumeLane, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the real label step, with only the device and model replaced."""
    model = _OneVoice()
    monkeypatch.setattr(labels_worker, "run_live_speaker_labels", REAL_LABEL_STEP)
    monkeypatch.setattr(labels_worker, "live_speaker_labels_device", lambda: "cpu")
    monkeypatch.setattr(labels_worker, "_load_model", lambda device, config: model)


def _naming_checks(dispatched: list[Any]) -> int:
    return sum(name == LIVE_NAMING_TASK for name, _, _ in dispatched)


@pytest.mark.parametrize("resume_lane", [("off", True)], indirect=True)
def test_a_line_labelled_after_its_publication_gets_a_naming_check(
    resume_lane: ResumeLane,
    monkeypatch: pytest.MonkeyPatch,
    fake_single_flight: Any,
    stub_celery_dispatch: list[Any],
) -> None:
    lane = resume_lane
    _label_every_line(lane, monkeypatch)
    lane.upload(1)

    # The publication finds only unlabelled lines; the label write after it
    # gives them a label nobody has named.
    lane.run(1)

    assert _naming_checks(stub_celery_dispatch) == 1


@pytest.mark.parametrize("resume_lane", [("off", True)], indirect=True)
def test_with_live_suggestions_off_no_naming_check_is_queued(
    resume_lane: ResumeLane,
    monkeypatch: pytest.MonkeyPatch,
    fake_single_flight: Any,
    stub_celery_dispatch: list[Any],
) -> None:
    lane = resume_lane
    with Session(lane.engine) as session:
        owner = User(
            username="owner",
            hashed_password="x",
            settings={"suggest_live_speaker_names": False},
        )
        session.add(owner)
        session.commit()
        recording = session.get(Recording, lane.recording_id)
        assert recording is not None
        recording.user_id = owner.id
        session.commit()
    _label_every_line(lane, monkeypatch)

    lane.upload(1)
    lane.run(1)  # The label write gives the lines a label.
    lane.upload(2)
    lane.run(2)  # The publication now carries a label nobody has named.

    assert _naming_checks(stub_celery_dispatch) == 0


def test_the_label_step_reports_whether_it_changed_a_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = _database()
    _patch_step(monkeypatch, engine, SpeakerTrackModel())
    audio = speaker_audio([(0, 10, 1), (10, 12, 0)], 12)

    def run(start_s: float, end_s: float) -> bool:
        chunk = audio[int(start_s * SAMPLE_RATE) : int(end_s * SAMPLE_RATE)]
        return labels_worker.run_live_speaker_labels(
            labels_worker.LiveLabelRun(
                recording_id=RECORDING_ID,
                sequence=0,
                live_dir=tmp_path,
                audio=chunk,
                audio_start_s=start_s,
                next_text_start_s=end_s,
            )
        )

    assert run(0.0, 10.0) is True  # The opening line gets its label.
    assert run(10.0, 12.0) is False  # Silence: no line changes.
