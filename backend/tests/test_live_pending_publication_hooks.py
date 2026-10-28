"""Production publication adapters emit pending state and committed metrics."""

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.processing import live_persistence, live_settling
from backend.processing.live_diarization import step as label_step
from backend.tests.test_hybrid_publication import _engine
from backend.tests.test_live_settling_publication import _seed
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.tests.test_live_settling_worker_postgres import worker_db as worker_db
from backend.utils.canonical_pipeline import live_unknown_metrics
from backend.utils.canonical_pipeline.core import serialize_canonical_utterances
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.config_manager import config_manager


@pytest.mark.parametrize(
    "device,mode,pending",
    [("cuda", "auto", True), ("cpu", "auto", False), ("cuda", "off", False)],
)
def test_whole_line_persistence_publishes_cuda_pending_and_preserves_cpu_or_off(
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    device: str,
    mode: str,
    pending: bool,
) -> None:
    from backend.core import db

    recording_id = _seed(settling_engine)
    monkeypatch.setattr(db, "get_sync_session", lambda: Session(settling_engine))
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: mode if key == "live_speaker_labels" else default,
    )
    result = live_persistence._persist_live_run(
        recording_id=recording_id,
        sequence=0,
        run=[0],
        new_segments=[
            {"start": 1.0, "end": 2.0, "speaker": "UNKNOWN", "text": "hello"}
        ],
        state={},
        live_config={"_settling_device": device},
        live_config_hash="test",
        live_model_name="test",
    )
    assert result.accepted
    with Session(settling_engine) as session:
        (line,) = serialize_canonical_utterances(session, recording_id)
        assert bool(line.get("diarisation_pending")) is pending
        assert line["speaker"] == "UNKNOWN"


def _metric_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        event
        for record in caplog.records
        if record.getMessage().startswith("pipeline_metric ")
        and (event := json.loads(record.getMessage().removeprefix("pipeline_metric ")))[
            "stage"
        ]
        == "live_covered_unknown_words"
    ]


def _publish_whole_line(recording_id: int, engine: Engine) -> None:
    with Session(engine) as session:
        transcript = session.execute(select(Transcript)).scalar_one()
        transcript.settling_state = {}
        session.add(transcript)
        session.commit()
    assert live_persistence._persist_live_run(
        recording_id=recording_id,
        sequence=0,
        run=[0],
        new_segments=[
            {"start": 0.1, "end": 0.5, "speaker": "UNKNOWN", "text": "hello there"},
            {"start": 1.0, "end": 2.0, "speaker": "UNKNOWN", "text": "known speaker"},
            {
                "start": 31.0,
                "end": 32.0,
                "speaker": "UNKNOWN",
                "text": "pending tail",
            },
        ],
        state={},
        live_config={"_settling_device": "cuda"},
        live_config_hash="test",
        live_model_name="test",
    ).accepted


@pytest.mark.parametrize("lane", ["text", "label", "whole"])
def test_accepted_publication_logs_committed_counter_and_rejected_work_does_not(
    worker_db: Any,
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    lane: str,
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: "auto" if key == "live_speaker_labels" else default,
    )
    evidence = SimpleNamespace(
        decide=lambda _start, _end: LiveLabelDecision("UNKNOWN", True, None),
        speech_spans=lambda: [],
    )
    monkeypatch.setattr(
        live_settling,
        "_speaker_evidence",
        lambda _: evidence if lane == "text" else None,
    )
    original_metric = live_unknown_metrics.record_pipeline_metric

    def committed_metric(**kwargs: Any) -> dict[str, Any]:
        with Session(settling_engine) as session:
            transcript = session.execute(
                select(Transcript).where(
                    Transcript.recording_id == worker_db.recording_id
                )
            ).scalar_one()
            assert "covered_unknown_words" not in (transcript.settling_state or {})
            lines = serialize_canonical_utterances(session, worker_db.recording_id)
            if kwargs["payload"]["covered_unknown_words"]:
                assert any(line.get("diarisation_pending") is False for line in lines)
        return original_metric(**kwargs)

    monkeypatch.setattr(
        live_unknown_metrics, "record_pipeline_metric", committed_metric
    )
    capture_step = worker_db.step(0)
    if lane == "whole":
        _publish_whole_line(worker_db.recording_id, settling_engine)
    else:
        assert live_settling.run_settling_step(capture_step, {}).accepted
    engine = _engine(capture_step.live_dir, "native")
    engine.posteriors[:] = 0
    if lane == "whole":
        engine.posteriors[100:200, 0] = 1
    if lane != "text":
        caplog.clear()
        assert label_step._write_labels(worker_db.recording_id, engine) is not None
    (event,) = _metric_events(caplog)
    assert event["recording_id"] == worker_db.recording_id
    assert event["payload"] == {"covered_unknown_words": 2 if lane == "whole" else 1}

    with Session(settling_engine) as session:
        recording = session.get(Recording, worker_db.recording_id)
        assert recording is not None
        recording.status = RecordingStatus.PROCESSING
        session.add(recording)
        session.commit()
    caplog.clear()
    if lane == "text":
        assert not live_settling.run_settling_step(worker_db.step(1), {}).accepted
    else:
        assert label_step._write_labels(worker_db.recording_id, engine) is None
    assert _metric_events(caplog) == []
