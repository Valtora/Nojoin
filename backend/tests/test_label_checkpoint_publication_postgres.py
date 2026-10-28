"""Lifecycle never waits for durable label evidence; rejected evidence is ignored."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from backend.core import db
from backend.models.pipeline import ProcessingRun, TranscriptUtteranceEvent
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.processing.live_diarization import checkpoint, state, step
from backend.processing.live_diarization.files import BANK_FILENAME, STATE_FILENAME
from backend.processing.live_diarization.hybrid import HybridLiveState
from backend.tests.test_canonical_publication_fence import (
    Database,
    publication_database,
)
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.canonical_pipeline.live_labels import LiveLabelWrite
from backend.utils.transcript_settling import capture_publication_token

__all__ = ["publication_database"]


@pytest.fixture
def label_database(
    publication_database: Database, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Database]:
    """Keep a physical worker connection separate from the lifecycle connection."""
    engine, _, _ = publication_database
    with engine.connect() as connection:
        monkeypatch.setattr(db, "get_sync_session", lambda: Session(connection))
        yield publication_database


def _run(recording_id: int, live_dir: Path, token: Any) -> step.LiveLabelRun:
    return step.LiveLabelRun(recording_id, 0, live_dir, None, 0.0, 0.0, token)


def _labels(recording_id: int, live_dir: Path) -> HybridLiveState:
    labels = HybridLiveState(
        state.new_engine(live_dir, step.CONFIG), None, speaker_engine="classic"
    )
    labels.recording_id = recording_id
    labels.bank.path.touch()
    labels.bank.stage_appends()
    return labels


def _advance_lifecycle(engine: Any, recording_id: int, transition: str) -> None:
    with Session(engine) as lifecycle, db.get_sync_session() as worker:
        worker_pid = worker.get_bind().connection.driver_connection.info.backend_pid
        lifecycle_pid = (
            lifecycle.connection().connection.driver_connection.info.backend_pid
        )
        assert worker_pid != lifecycle_pid
        lifecycle.execute(text("SET lock_timeout = '200ms'"))
        transcript = _load_transcript(lifecycle, recording_id, lock=True)
        recording = lifecycle.get(Recording, recording_id)
        if transition == "resume":
            recording.capture_epoch += 1
        elif transition == "generation":
            transcript.settling_state = {"generation": "replacement", "sealed": False}
        else:
            recording.status = RecordingStatus.QUEUED
        lifecycle.commit()


def _facts(session: Session, recording_id: int) -> tuple[Any, ...]:
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).one()
    events = session.exec(
        select(TranscriptUtteranceEvent.id).where(
            TranscriptUtteranceEvent.recording_id == recording_id
        )
    ).all()
    return transcript.segments, events


@pytest.mark.parametrize(
    "checkpoint_case",
    list(product(["publication", "selection", "recovery", "progress"], [1, 2, 3])),
)
@pytest.mark.parametrize("transition", ["resume", "generation", "stop"])
def test_lifecycle_commits_during_checkpoint_storage_and_rejects_stale_evidence(
    label_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    checkpoint_case: tuple[str, int],
    transition: str,
) -> None:
    operation, sync_number = checkpoint_case
    engine, recording_id, _ = label_database
    labels = _labels(recording_id, tmp_path)
    with Session(engine) as session:
        token = capture_publication_token(session.get(Recording, recording_id), None)
    run = _run(recording_id, tmp_path, token)
    step._checkpoint_labels(run, labels)
    accepted = (tmp_path / STATE_FILENAME).read_bytes()
    with Session(engine) as session:
        before = _facts(session, recording_id)
    labels.bank.append(np.ones(step.CONFIG.embedding_dim, dtype=np.float32))
    entered, release = threading.Event(), threading.Event()
    original_fsync = state.os.fsync
    syncs = 0

    def stall(fd: int) -> None:
        nonlocal syncs
        syncs += 1
        if syncs == sync_number:
            entered.set()
            assert release.wait(5), "Lifecycle did not release the label writer"
        original_fsync(fd)

    monkeypatch.setattr(state.os, "fsync", stall)

    def publish() -> None:
        with pytest.raises(step._ObsoleteLabelRun):
            if operation == "publication":
                step._publish_run_labels(run, labels)
            else:
                write = LiveLabelWrite(0, 0, None) if operation == "progress" else None
                step._checkpoint_labels(run, labels, write)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(publish)
        try:
            assert entered.wait(5), "Writer never reached durable storage"
            _advance_lifecycle(engine, recording_id, transition)
        finally:
            release.set()
        future.result(timeout=10)
    assert (tmp_path / STATE_FILENAME).read_bytes() == accepted
    with Session(engine) as session:
        assert _facts(session, recording_id) == before
        assert (
            len(
                session.exec(
                    select(ProcessingRun).where(
                        ProcessingRun.idempotency_key == checkpoint.CHECKPOINT_KEY
                    )
                ).all()
            )
            == 1
        )
    restored = checkpoint.load_checkpoint(
        tmp_path, step.CONFIG, recording_id=recording_id
    )
    assert restored is not None and restored.bank.rows == 0
    assert (tmp_path / BANK_FILENAME).stat().st_size == 0


def test_accepted_checkpoint_recovers_without_the_compatibility_mirror(
    label_database: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, recording_id, _ = label_database
    labels = _labels(recording_id, tmp_path)
    run = _run(recording_id, tmp_path, None)

    def failed_mirror(*args: Any) -> None:
        raise OSError("mirror unavailable")

    monkeypatch.setattr(step, "mirror_checkpoint", failed_mirror)
    with pytest.raises(OSError, match="mirror unavailable"):
        step._checkpoint_labels(run, labels)
    assert not (tmp_path / STATE_FILENAME).exists()
    restored = checkpoint.load_checkpoint(
        tmp_path, step.CONFIG, recording_id=recording_id
    )
    assert restored is not None
    assert restored.capture_generation == labels.capture_generation


def test_lifecycle_commits_while_accepted_checkpoint_mirror_waits_on_storage(
    label_database: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine, recording_id, _ = label_database
    labels = _labels(recording_id, tmp_path)
    run = _run(recording_id, tmp_path, None)
    entered, release = threading.Event(), threading.Event()
    original_link = checkpoint.os.link

    def stall_link(source: Path, target: Path) -> None:
        entered.set()
        assert release.wait(5)
        original_link(source, target)

    monkeypatch.setattr(checkpoint.os, "link", stall_link)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(step._checkpoint_labels, run, labels)
        try:
            assert entered.wait(5)
            _advance_lifecycle(engine, recording_id, "resume")
        finally:
            release.set()
        future.result(timeout=10)
    restored = checkpoint.load_checkpoint(
        tmp_path, step.CONFIG, recording_id=recording_id
    )
    assert restored is not None
    assert restored.capture_generation == labels.capture_generation
    with Session(engine) as session:
        assert _load_transcript(session, recording_id).settling_state is None
