"""Finalisation waits from Edge's eligibility read through its publication commit."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlmodel import Session, select

from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.tests.test_canonical_publication_fence import Database
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.utils.status_manager import update_recording_status
from backend.worker.tasks import meeting_edge_stage as stage
from backend.worker.tasks.intelligence_source import load_intelligence_source


def _assert_finalisation_blocked(
    engine: Any, finalising: Future[None], pids: dict[str, int]
) -> None:
    """Observe PostgreSQL's actual blocker, rather than assume a thread ran."""
    deadline = time.monotonic() + 5
    with Session(engine) as observer:
        while time.monotonic() < deadline:
            if finalising.done():
                finalising.result()
                pytest.fail("Finalisation committed before Edge publication committed")
            if "finaliser" in pids:
                blockers = observer.execute(
                    text("SELECT pg_blocking_pids(:pid)"),
                    {"pid": pids["finaliser"]},
                ).scalar_one()
                if pids["publisher"] in blockers:
                    return
            threading.Event().wait(0.01)
    pytest.fail("Finalisation never waited on the Edge publication transaction")


def _assert_unpublished(engine: Any, recording_id: int) -> None:
    with Session(engine) as observer:
        assert (
            observer.get(Recording, recording_id).status == RecordingStatus.PROCESSING
        )
        transcript = observer.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).one()
        assert transcript.meeting_edge_status == "updating"
        assert transcript.meeting_edge_payload == {"summary": "Previous guidance"}


def _seed_edge_publication(engine: Any, recording_id: int) -> int:
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        recording.status = RecordingStatus.PROCESSING
        transcript = load_intelligence_source(session, recording_id)
        transcript.transcript_status = "completed"
        transcript.notes_status = "completed"
        transcript.meeting_edge_status = "updating"
        transcript.meeting_edge_payload = {"summary": "Previous guidance"}
        transcript.meeting_edge_source_signature = "in-flight"
        session.add(recording)
        session.add(transcript)
        session.commit()
        session.refresh(transcript)
        session.refresh(recording)
        assert recording.status == RecordingStatus.PROCESSING
        assert transcript.meeting_edge_source_signature == "in-flight"
        return transcript.change_sequence


def _finalise(engine: Any, recording_id: int, pids: dict[str, int]) -> None:
    with Session(engine) as session:
        session.execute(text("SET lock_timeout = '10s'"))
        pids["finaliser"] = session.execute(
            text("SELECT pg_backend_pid()")
        ).scalar_one()
        update_recording_status(session, recording_id)
        assert session.get(Recording, recording_id).status == RecordingStatus.PROCESSED


@pytest.mark.parametrize("fails", [False, True], ids=["result", "error"])
def test_finalisation_waits_after_edge_eligibility_until_publication_commits(
    publication_database: Database, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    engine, recording_id, _ = publication_database
    revision = _seed_edge_publication(engine, recording_id)

    eligible, release_write = threading.Event(), threading.Event()
    committing, release_commit = threading.Event(), threading.Event()
    pids: dict[str, int] = {}
    validate = stage.meeting_edge_source_is_current

    def check_eligibility(*args: Any, **kwargs: Any) -> bool:
        current = validate(*args, **kwargs)
        assert current, "The real publication eligibility check refused the fixture"
        eligible.set()
        assert release_write.wait(10), "Publication was never released"
        return current

    monkeypatch.setattr(stage, "meeting_edge_source_is_current", check_eligibility)

    def publish() -> bool:
        with Session(engine) as session:
            pids["publisher"] = session.execute(
                text("SELECT pg_backend_pid()")
            ).scalar_one()
            # Do not take a fence before eligibility: that would mask its omission.
            transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == recording_id)
            ).one()

            def before_commit(session: Session) -> None:
                committing.set()
                assert release_commit.wait(10), "Publication commit was never released"

            event.listen(session, "before_commit", before_commit)
            return stage._set_meeting_edge_state(
                session,
                transcript,
                status="error" if fails else "ready",
                error_message="Late failure" if fails else None,
                payload=None if fails else {"summary": "New guidance"},
                source_revision=revision,
                expected_signature="in-flight",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        publication = executor.submit(publish)
        try:
            if not eligible.wait(5):
                publication.result(timeout=1)
                pytest.fail("Publication never read its eligibility")
            finalisation = executor.submit(_finalise, engine, recording_id, pids)
            _assert_finalisation_blocked(engine, finalisation, pids)
            release_write.set()
            assert committing.wait(5), "Publication never reached commit"
            _assert_finalisation_blocked(engine, finalisation, pids)
            _assert_unpublished(engine, recording_id)
        finally:
            release_write.set()
            release_commit.set()
        assert publication.result(timeout=10) is True
        finalisation.result(timeout=10)

    with Session(engine) as session:
        assert session.get(Recording, recording_id).status == RecordingStatus.PROCESSED
        transcript = load_intelligence_source(session, recording_id)
        assert transcript.meeting_edge_payload == {
            "summary": "Previous guidance" if fails else "New guidance"
        }
        assert transcript.meeting_edge_status == "ready"
        assert transcript.meeting_edge_error_message is None
        assert transcript.meeting_edge_source_signature is None
