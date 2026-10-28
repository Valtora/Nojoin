"""Version-3 restore keeps durable transcript data and resets runtime state."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlmodel import Session, select

from backend.core.backup import BackupManager, runtime
from backend.core.backup.restore import stages
from backend.core.db import driver_urls
from backend.models.recording import Recording, RecordingStatus
from backend.models.recording_public import serialize_transcript
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline import (
    apply_compatibility_segment_replace,
    ensure_canonical_backfill,
    serialize_canonical_utterances,
)

SCHEMA = "backup_settling_state_test"
SEGMENTS = [
    {
        "id": "protected-line",
        "start": 0.0,
        "end": 1.0,
        "text": "Kept wording.",
        "speaker": "SPEAKER_00",
        "text_manually_edited": True,
        "confidence_payload": {"asr_segments": [{"words": [{"text": "Kept"}]}]},
    }
]


@pytest.fixture
def engine(
    postgres_test_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": f"-csearch_path={SCHEMA},public"},
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(engine, SCHEMA)
    for name in ("recordings", "documents"):
        (tmp_path / name).mkdir()
    paths = SimpleNamespace(
        user_data_directory=tmp_path,
        recordings_directory=tmp_path / "recordings",
        documents_directory=tmp_path / "documents",
        config_path=tmp_path / "config.json",
    )
    monkeypatch.setattr(runtime, "sync_engine", engine)
    monkeypatch.setattr(runtime, "PathManager", lambda: paths)
    monkeypatch.setattr(runtime, "get_app_version", lambda: "3.0.0")
    monkeypatch.setattr(
        stages, "_enqueue_recording_finalization", lambda *args, **kwargs: None
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("strategy", ["clear", "overwrite", "safe"])
@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("with_runtime", [False, True])
@pytest.mark.usefixtures("stub_celery_dispatch")
def test_version_three_round_trip_resets_only_imported_runtime_state(
    engine: Engine,
    strategy: str,
    interrupted: bool,
    with_runtime: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(engine) as session:
        user = User(username="alice", hashed_password="x")
        session.add(user)
        session.flush()
        recording = Recording(
            name="Capture",
            audio_path="/data/recordings/capture.wav",
            user_id=user.id,
            status=RecordingStatus.UPLOADING
            if interrupted
            else RecordingStatus.PROCESSED,
        )
        session.add(recording)
        session.flush()
        session.add(
            Transcript(
                recording_id=recording.id,
                text="Kept wording.",
                segments=SEGMENTS,
                transcript_status="processing" if interrupted else "completed",
                settling_state={"eligible": True, "epoch": 8, "sealed": False},
            )
        )
        session.commit()
    archive_path, _ = BackupManager.create_backup_blocking(include_audio=False)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert json.loads(archive.read("backup_info.json"))["format_version"] == 3
            exported = json.loads(archive.read("transcripts.json"))[0]
            assert exported["settling_state"]["epoch"] == 8
            assert exported["segments"] == SEGMENTS
        restore_path = archive_path if with_runtime else _old_archive(archive_path)
        # A safe merge must keep this newer target's runtime metadata intact.
        with engine.begin() as connection:
            connection.execute(
                Transcript.__table__.update().values(
                    settling_state={"eligible": True, "epoch": 9},
                )
            )
        result = BackupManager.restore_backup_blocking(
            f"publication-{strategy}-{interrupted}",
            restore_path,
            clear_existing=strategy == "clear",
            overwrite_existing=strategy == "overwrite",
        )
        assert result["status"] == "completed", result
        with Session(engine) as session:
            transcript = session.exec(select(Transcript)).one()
            recording = session.exec(select(Recording)).one()
            assert transcript.segments == SEGMENTS
            assert transcript.text == "Kept wording."
            if strategy == "safe":
                assert transcript.settling_state == {"eligible": True, "epoch": 9}
            else:
                assert transcript.settling_state is None
                assert recording.status == (
                    RecordingStatus.ERROR if interrupted else RecordingStatus.PROCESSED
                )
                public = serialize_transcript(
                    transcript, recording_public_id=recording.public_id
                )
                assert "settling_state" not in public.model_dump()
        if not interrupted and strategy != "safe":
            _finish_backfill(engine, monkeypatch)
    finally:
        Path(archive_path).unlink(missing_ok=True)


def _old_archive(archive_path: str) -> str:
    path = runtime.PathManager().user_data_directory / "older.zip"
    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(path, "w") as target:
        for name in source.namelist():
            payload = source.read(name)
            if name == "transcripts.json":
                rows = json.loads(payload)
                for row in rows:
                    row.pop("settling_state", None)
                payload = json.dumps(rows).encode()
            target.writestr(name, payload)
    return str(path)


def _finish_backfill(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.models.pipeline import TranscriptUtterance
    from backend.worker.tasks import finalize_restored_recording_task

    with Session(engine) as session:
        recording = session.exec(select(Recording)).one()
        recording_id = recording.id
        monkeypatch.setattr(finalize_restored_recording_task, "_session", session)
        finalize_restored_recording_task.run(recording_id, needs_proxy=False)
        transcript = session.exec(select(Transcript)).one()
        utterance = session.exec(select(TranscriptUtterance)).one()
        assert transcript.settling_state is None
        assert utterance.text == "Kept wording."
        assert utterance.manual_text_locked
        assert utterance.confidence_payload == SEGMENTS[0]["confidence_payload"]


@pytest.mark.usefixtures("stub_celery_dispatch")
def test_compatibility_rewrite_backup_and_restore_keeps_private_word_evidence(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(engine) as session:
        owner = User(username="rewrite-owner", hashed_password="x")
        session.add(owner)
        session.flush()
        recording = Recording(
            name="Rewritten capture",
            audio_path="/data/rewrite.wav",
            user_id=owner.id,
            status=RecordingStatus.PROCESSED,
        )
        session.add(recording)
        session.flush()
        session.add(Transcript(recording_id=recording.id, segments=SEGMENTS))
        session.commit()
        ensure_canonical_backfill(session, recording.id)
        session.commit()
        public_segments = serialize_canonical_utterances(session, recording.id)
        assert "confidence_payload" not in public_segments[0]
        public_segments[0]["speaker"] = "UNKNOWN"
        apply_compatibility_segment_replace(
            session, recording_id=recording.id, segments=public_segments
        )
        session.commit()
    archive_path, _ = BackupManager.create_backup_blocking(include_audio=False)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert json.loads(archive.read("backup_info.json"))["format_version"] == 3
            archived = json.loads(archive.read("transcripts.json"))[0]
            assert (
                archived["segments"][0]["confidence_payload"]
                == SEGMENTS[0]["confidence_payload"]
            )
        result = BackupManager.restore_backup_blocking(
            "rewritten-confidence", archive_path, clear_existing=True
        )
        assert result["status"] == "completed", result
        _finish_backfill(engine, monkeypatch)
    finally:
        Path(archive_path).unlink(missing_ok=True)
