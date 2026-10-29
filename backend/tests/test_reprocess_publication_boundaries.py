"""Late publication failures preserve the complete previous committed result."""

from collections.abc import Callable
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeakerDriftRefusal
from backend.models.transcript import Transcript
from backend.tests.reprocess_publication_helpers import (
    Snapshot,
    assert_retired,
    publication_snapshot,
    seed_dependencies,
)
from backend.tests.test_pipeline_stages import _run_task
from backend.tests.test_reprocess_worker_retention import _configure, mark_for_retry
from backend.tests.test_reprocess_worker_retention import (
    worker_database as worker_database,
)
from backend.worker.tasks import pipeline, speaker_assignment


def _enable_real_learning(monkeypatch: pytest.MonkeyPatch) -> None:
    def extracted(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"SPEAKER_00": SimpleNamespace(embedding=[0.98, 0.2], speech_seconds=30)}

    monkeypatch.setattr(speaker_assignment, "_extract_speaker_voiceprints", extracted)


@pytest.mark.parametrize("canonical", [False, True])
def test_success_retires_every_old_dependency_id(
    worker_database: tuple[Engine, int],
    monkeypatch: pytest.MonkeyPatch,
    canonical: bool,
) -> None:
    engine, recording_id = worker_database
    _configure(monkeypatch, canonical)
    seed_dependencies(engine, recording_id)
    before = publication_snapshot(engine)
    with Session(engine) as session:
        _run_task(monkeypatch, session, recording_id=recording_id)
    assert_retired(before, publication_snapshot(engine))


@pytest.mark.parametrize(
    ("failure", "canonical"),
    [
        ("canonical_finalisation", True),
        ("projection_refresh", True),
        ("final_commit", True),
        ("final_commit", False),
    ],
)
def test_late_failure_rolls_back_complete_publication_and_real_learning(
    worker_database: tuple[Engine, int],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    canonical: bool,
) -> None:
    engine, recording_id = worker_database
    _configure(monkeypatch, canonical)
    person_id = seed_dependencies(engine, recording_id)
    _enable_real_learning(monkeypatch)
    before = publication_snapshot(engine)
    reached: list[str] = []
    learning_changed: list[bool] = []
    reader_observations: list[Snapshot] = []

    def fail_after_mutations(session: Session) -> None:
        session.flush()
        learning_changed.append(
            session.get(GlobalSpeaker, person_id).embedding != [1.0, 0.0]
            and session.exec(select(RecordingSpeakerDriftRefusal)).all() == []
        )
        # Flush the real changes, then prove other readers still see every old
        # publication column and dependency while the transaction is in flight.
        reader_observations.append(publication_snapshot(engine))
        reached.append(failure)
        raise RuntimeError(f"injected {failure} failure")

    with monkeypatch.context() as patch, Session(engine) as session:
        if failure == "final_commit":
            _fail_publication_commit(patch, session, fail_after_mutations)
        else:
            attribute = {
                "canonical_finalisation": "finalize_utterances_from_segments",
                "projection_refresh": "refresh_transcript_projection_from_canonical",
            }[failure]
            original = getattr(pipeline, attribute)

            def run_then_fail(*args: Any, **kwargs: Any) -> None:
                original(*args, **kwargs)
                fail_after_mutations(session)

            patch.setattr(pipeline, attribute, run_then_fail)
        _run_task(patch, session, recording_id=recording_id)

    assert reached == [failure], (
        "The worker never reached the intended failure boundary"
    )
    assert learning_changed == [True]
    assert reader_observations == [before]
    assert _settled(publication_snapshot(engine)) == _settled(before, restored=True)
    with Session(engine) as reader:
        assert reader.get(Recording, recording_id).status == RecordingStatus.ERROR
    # Retry in a new worker session: committed dependencies disappear together
    # with the old words, and the previously rolled-back learning now persists.
    mark_for_retry(engine)
    with Session(engine) as session:
        _run_task(monkeypatch, session, recording_id=recording_id)
    after = publication_snapshot(engine)
    assert_retired(before, after)
    assert after["global_speakers"][(person_id,)]["embedding"] != [1.0, 0.0]
    with Session(engine) as reader:
        transcript = reader.exec(select(Transcript)).one()
        assert transcript.text == "Replacement words Changed cluster"
        assert (transcript.settling_state or {}).get("reprocess_pending") is not True


def _settled(snapshot: Snapshot, *, restored: bool = False) -> Snapshot:
    """The snapshot without write bookkeeping; `restored` applies the hand-back."""
    settled = deepcopy(snapshot)
    for row in settled["transcripts"].values():
        row.pop("updated_at")
        row.pop("change_sequence")
        if restored:
            row.update(transcript_status="completed", settling_state=None)
    return settled


def _fail_publication_commit(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    fail: Callable[[Session], None],
) -> None:
    """Fail only the commit armed by the actual projection finalisation."""
    finish = pipeline.finish_reprocess_projection
    commit = session.commit
    armed = False

    def finish_then_arm(*args: Any, **kwargs: Any) -> None:
        nonlocal armed
        finish(*args, **kwargs)
        armed = True

    def commit_or_fail() -> None:
        nonlocal armed
        if armed:
            armed = False
            fail(session)
        commit()

    monkeypatch.setattr(pipeline, "finish_reprocess_projection", finish_then_arm)
    monkeypatch.setattr(session, "commit", commit_or_fail)
