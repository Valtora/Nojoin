"""Publication-scoped hybrid projection and completed-native-prefix routing."""

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from backend.processing.live_diarization import step
from backend.processing.live_diarization.engine import LiveLabelEngine, Piece
from backend.processing.live_diarization.hybrid import HybridLiveState
from backend.processing.live_diarization.hybrid_lines import prepare_label_line
from backend.processing.live_diarization.lines import LabelTimeline
from backend.processing.live_diarization.state import new_engine
from backend.tests.test_live_speaker_labels_db import RECORDING_ID, _by_id, _database
from backend.utils.canonical_pipeline import serialize_canonical_utterances
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision


@pytest.fixture
def publication_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    from backend.core import db

    database = _database()
    monkeypatch.setattr(db, "get_sync_session", lambda: Session(database))
    yield database
    database.dispose()


def _engine(tmp_path: Path, route: str) -> HybridLiveState:
    engine = HybridLiveState(new_engine(tmp_path, step.CONFIG), None)
    engine.shadow.units.label = [0, 1, 2]
    engine.shadow.units.locked = [False, False, False]
    engine.shadow.pieces = [Piece(1, 9, 0), Piece(10, 14, 1), Piece(21, 29, 2)]
    engine.shadow.next_end_n = 30 * 16000 + 8000
    engine.translation = {0: 0, 1: 1, 2: 2}
    if route != "classic":
        engine.posteriors = np.zeros((3000, 8), np.float32)
        engine.posteriors[:, 2] = 0.9
        engine.native_complete_frame = 3000
        if route == "native":
            engine.route = "nemotron"
        else:
            engine.native_complete_frame = 1200
            engine.switch_emitted_at_n = 32000
            engine.translation = {0: 8, 1: 9, 2: 10}
    return engine


def _speakers(database: Engine) -> list[str]:
    with Session(database) as session:
        rows = _by_id(serialize_canonical_utterances(session, RECORDING_ID))
    return [rows[name]["speaker"] for name in ("line-a", "line-b", "line-c")]


@pytest.mark.parametrize(
    "case",
    [
        ("classic", [1, 2, 3], 1),
        ("fallback", [3, 10, 11], 1),
        ("native", [3, 3, 3], 0),
    ],
)
def test_publication_prepares_shadow_once_and_keeps_native_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publication_db: Engine,
    case: tuple[str, list[int], int],
) -> None:
    route, expected, conversions = case
    engine = _engine(tmp_path, route)
    original = LabelTimeline.from_engine
    calls: list[LiveLabelEngine] = []

    def from_engine(shadow: LiveLabelEngine) -> LabelTimeline:
        calls.append(shadow)
        return original(shadow)

    monkeypatch.setattr(LabelTimeline, "from_engine", from_engine)
    write = step._write_labels(RECORDING_ID, engine)
    assert write is not None and write.changed == 3
    assert len(calls) == conversions
    assert _speakers(publication_db) == [f"LIVE_PROVISIONAL_{i}" for i in expected]
    if route == "native":
        return
    # A later publication sees new evidence instead of reusing the first snapshot.
    engine.shadow.units.label[1] = 2
    second = step._write_labels(RECORDING_ID, engine)
    assert second is not None and second.changed == 1
    assert len(calls) == conversions * 2
    assert _speakers(publication_db)[1] == f"LIVE_PROVISIONAL_{expected[2]}"


def test_prepared_shadow_preserves_unknown_locks_and_majority_share(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "fallback")
    engine.native_complete_frame = 0
    engine.shadow.units.locked = [True, True, True]
    engine.shadow.units.label[1] = engine.shadow.held_slot
    del engine.translation[0]
    decide = prepare_label_line(engine)
    assert decide(1, 9) == LiveLabelDecision("UNKNOWN", locked=True, share=1.0)
    assert decide(10, 14) == LiveLabelDecision("UNKNOWN", locked=True, share=0.0)
    assert decide(15, 19) == LiveLabelDecision("UNKNOWN", locked=True, share=None)
    assert decide(21, 29) == LiveLabelDecision(
        "LIVE_PROVISIONAL_11", locked=True, share=1.0
    )


def test_prepared_unswitched_keeps_an_accepted_nonidentity_translation(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "classic")
    # Restored envelopes require complete mappings but also accept this value.
    engine.translation[0] = 8
    assert prepare_label_line(engine)(1, 9).speaker == "LIVE_PROVISIONAL_9"
