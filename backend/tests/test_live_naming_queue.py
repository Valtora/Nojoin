"""When the live task queues a naming check, run through the real live lane.

In the lane, only the label step is replaced: the stand-in writes its answer
through the real label projection and reports whether lines changed, which the
last test shows the real step does. Celery dispatch and Redis are stubbed.
"""

from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

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
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)
from backend.worker.tasks.live_naming_stage import LIVE_NAMING_TASK


def _label_every_line(lane: ResumeLane, monkeypatch: pytest.MonkeyPatch) -> None:
    def label(run: Any) -> bool:
        with Session(lane.engine) as session:
            write = apply_live_speaker_labels(
                session,
                recording_id=run.recording_id,
                settled_ms=0,
                decide=lambda _start, _end: LiveLabelDecision(
                    "LIVE_PROVISIONAL_1", False, 1.0
                ),
            )
            session.commit()
        return write.changed > 0

    monkeypatch.setattr(labels_worker, "run_live_speaker_labels", label)


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
