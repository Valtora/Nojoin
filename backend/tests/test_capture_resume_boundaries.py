"""Capture resume geometry survives asynchronous ingestion and runtime resets."""

from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session, select

from backend.models.transcript import Transcript
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.utils import reprocess_publication
from backend.utils.capture_resume import (
    RESUME_BOUNDARIES,
    RESUME_SEQUENCES,
    persist_resume_boundaries,
    resolve_resume_boundaries,
)
from backend.utils.transcript_settling import (
    durable_settling_state,
    resolve_capture_settling_policy,
    update_capture_settling_state,
)


def test_transcode_finishing_first_does_not_move_the_resume_cut() -> None:
    state = {RESUME_SEQUENCES: [5]}
    late = SimpleNamespace(sequence_no=6, absolute_start_ms=0)
    first = SimpleNamespace(sequence_no=5, absolute_start_ms=0)
    assert resolve_resume_boundaries(state, [late, first], through_sequence=4) == []
    first.absolute_start_ms = 10_250
    late.absolute_start_ms = 12_250
    assert resolve_resume_boundaries(state, [late, first], through_sequence=6) == [
        10_250
    ]


def test_final_offsets_outlive_chunks_and_runtime_reset() -> None:
    transcript = Transcript(recording_id=1, settling_state={RESUME_SEQUENCES: [3, 5]})
    chunks = [
        SimpleNamespace(sequence_no=4, absolute_start_ms=6500),
        SimpleNamespace(sequence_no=5, absolute_start_ms=8500),
    ]
    # A corrupt first resumed chunk has no audio; the next valid chunk begins there.
    persist_resume_boundaries(transcript, chunks)
    assert transcript.settling_state[RESUME_BOUNDARIES] == [6500, 8500]
    transcript.settling_state = durable_settling_state(transcript.settling_state)
    assert resolve_resume_boundaries(transcript.settling_state, []) == [6500, 8500]


@pytest.mark.parametrize("settled", [False, True])
def test_geometry_survives_both_reprocess_reset_paths(
    settled: bool, publication_database: Any
) -> None:
    engine, recording_id, _ = publication_database
    state = {RESUME_BOUNDARIES: [4200], "eligible": True, "policy": {"enabled": True}}
    if settled:
        state["settled"] = True
    durable = durable_settling_state(state)
    assert durable[RESUME_BOUNDARIES] == [4200]
    assert "eligible" not in durable and "policy" not in durable
    with Session(engine) as session:
        transcript = session.exec(select(Transcript)).one()
        transcript.settling_state = {**state, "reprocess_pending": True}
        session.add(transcript)
        session.commit()
    with Session(engine) as session:
        reprocess_publication.clear_previous_reprocess_state(session, recording_id)
        session.commit()
    with Session(engine) as session:
        assert session.exec(select(Transcript)).one().settling_state == {
            RESUME_BOUNDARIES: [4200],
            "reprocess_pending": True,
            "reprocess_published": True,
        }


def test_resume_metadata_does_not_enable_settling_or_require_a_policy() -> None:
    transcript = Transcript(recording_id=1, settling_state={RESUME_SEQUENCES: [2]})
    update_capture_settling_state(
        transcript, capture_epoch=1, sealed=False, phase="live"
    )
    resolve_capture_settling_policy(transcript, device="cpu")
    assert transcript.settling_state == {RESUME_SEQUENCES: [2]}
