"""Immutable diariser snapshots used by rolling ASR's independent speech gate."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from backend.processing.live_diarization import evidence
from backend.processing.live_diarization.checkpoint import new_checkpoint_filename
from backend.processing.live_diarization.engine import LiveLabelConfig
from backend.processing.live_diarization.files import BANK_FILENAME
from backend.processing.live_diarization.state import (
    encode_shadow,
    new_engine,
    write_snapshot,
)


def _snapshot(*, native: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    arrays: dict[str, Any] = {
        "active": np.zeros(128, bool),
        "piece_unit": np.array([0, 1]),
        "unit_label": np.array([0, 1]),
        "unit_locked": np.array([True, False]),
        "piece_start": np.array([1.0, 2.0]),
        "piece_end": np.array([2.0, 3.0]),
        "hybrid_posteriors": np.zeros((300, 8), np.float32),
    }
    arrays["hybrid_posteriors"][100:200, 3] = 0.8
    meta = {
        "next_end_n": 56000,
        "hybrid": {
            "route": "nemotron" if native else "clustering",
            "translation": [[0, 5], [1, 6]],
            "posterior_first_frame": 0,
            "native_complete_frame": 300,
        },
    }
    return meta, arrays


def test_classic_snapshot_translates_and_preserves_word_lock_semantics() -> None:
    snapshot = evidence._from_snapshot(*_snapshot())
    assert snapshot.decide(1000, 1800).speaker == "LIVE_PROVISIONAL_6"
    assert snapshot.decide(1000, 1800).locked
    assert snapshot.decide(2000, 2800).speaker == "LIVE_PROVISIONAL_7"
    assert not snapshot.decide(2000, 2800).locked
    assert snapshot.speech_spans() == [(1.0, 2.0), (2.0, 3.0)]


def test_native_snapshot_respects_frame_origin_and_silent_posteriors() -> None:
    meta, arrays = _snapshot(native=True)
    meta["hybrid"]["posterior_first_frame"] = 100
    meta["hybrid"]["native_complete_frame"] = 400
    snapshot = evidence._from_snapshot(meta, arrays)
    assert snapshot.decide(2100, 2800).speaker == "LIVE_PROVISIONAL_4"
    assert snapshot.decide(2100, 2800).locked
    assert snapshot.decide(1100, 1800).speaker == "UNKNOWN"
    assert not snapshot.decide(4100, 4500).locked
    spans = snapshot.speech_spans()
    assert (2.0, 2.01) in spans and (2.99, 3.0) in spans


def test_fallback_snapshot_keeps_native_prefix_then_uses_translated_shadow() -> None:
    meta, arrays = _snapshot()
    meta["hybrid"]["switch_emitted_at_n"] = 48000
    meta["hybrid"]["native_complete_frame"] = 200
    snapshot = evidence._from_snapshot(meta, arrays)
    assert snapshot.decide(1100, 1800).speaker == "LIVE_PROVISIONAL_4"
    assert snapshot.decide(2100, 2800).speaker == "LIVE_PROVISIONAL_7"


@pytest.mark.parametrize("pruned", [False, True])
def test_accepted_checkpoint_read_never_restores_or_truncates_bank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pruned: bool
) -> None:
    from backend.core import db
    from backend.processing.live_diarization import checkpoint, state

    config = LiveLabelConfig()
    engine = new_engine(tmp_path, config)
    meta, arrays = encode_shadow(engine)
    filename = new_checkpoint_filename()
    write_snapshot(tmp_path, meta, arrays, filename=filename)
    bank = tmp_path / BANK_FILENAME
    bank.write_bytes(b"unaccepted suffix owned by a concurrent label step")
    original = bank.read_bytes()

    class Session:
        def __enter__(self) -> "Session":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def execute(self, _query: Any) -> Any:
            run = SimpleNamespace(model_metadata={"checkpoint": filename})
            return SimpleNamespace(scalar_one_or_none=lambda: run)

    monkeypatch.setattr(db, "get_sync_session", Session)
    monkeypatch.setattr(
        checkpoint,
        "load_checkpoint",
        lambda *_args, **_kwargs: pytest.fail("restored engine"),
    )
    monkeypatch.setattr(
        state, "_restore", lambda *_args, **_kwargs: pytest.fail("restored unit bank")
    )
    if pruned:
        (tmp_path / filename).unlink()
    result = evidence.available_speaker_evidence(tmp_path, recording_id=42)
    assert (result is None) is pruned
    assert bank.read_bytes() == original
