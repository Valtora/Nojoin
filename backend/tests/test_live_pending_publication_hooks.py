"""The whole-line persistence adapter publishes diarisation-pending lines."""

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.processing import live_persistence
from backend.tests.test_live_settling_publication import _seed
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline.core import serialize_canonical_utterances
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
