"""A live format-3 backup carries canonical words before snapshot closure."""

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.core.backup import BackupManager
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.test_backup_publication_state_postgres import _old_archive
from backend.tests.test_backup_publication_state_postgres import engine as engine
from backend.tests.test_live_settling_finalisation import _final_segment
from backend.tests.test_live_settling_publication import _request, _seed, _words
from backend.utils.canonical_pipeline import (
    build_transcript_segments_for_read,
    build_transcript_text_for_read,
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
    serialize_canonical_utterances,
)
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    publish_live_settling,
)
from backend.utils.config_manager import config_manager
from backend.worker.tasks import finalize_restored_recording_task


@pytest.mark.usefixtures("stub_celery_dispatch")
@pytest.mark.parametrize("retracted", [False, True])
def test_live_backup_projects_current_words_without_writing_snapshot(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, retracted: bool
) -> None:
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: default)
    recording_id = _seed(engine)
    with Session(engine) as session:
        assert publish_live_settling(
            session, request=_request(session, recording_id, 0), words=_words()
        ).accepted
        session.commit()
        if retracted:
            refresh_transcript_projection_from_canonical(session, recording_id)
            session.commit()
            assert publish_live_settling(
                session, request=_request(session, recording_id, 1), words=[]
            ).accepted
            session.commit()
        transcript = session.exec(select(Transcript)).one()
        sequence = transcript.change_sequence
        snapshot = transcript.segments
        assert bool(snapshot) is retracted
        assert build_transcript_text_for_read(session, recording_id) == (
            "" if retracted else "Hello world"
        )
        assert (
            bool(build_transcript_segments_for_read(session, recording_id))
            is not retracted
        )
    archive_path, _ = BackupManager.create_backup_blocking(include_audio=False)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert json.loads(archive.read("backup_info.json"))["format_version"] == 3
            assert "transcript_utterances.json" not in archive.namelist()
            exported = json.loads(archive.read("transcripts.json"))[0]
            assert exported["text"] == ("" if retracted else "Hello world")
            if retracted:
                assert exported["segments"] == []
            else:
                line = exported["segments"][0]
                assert line["text"] == "Hello world"
                words = line["confidence_payload"]["asr_segments"][0]["words"]
                assert [word["word"] for word in words] == ["Hello", " world"]
                assert all(word["id"] and not word["locked"] for word in words)
        with Session(engine) as session:
            transcript = session.exec(select(Transcript)).one()
            assert transcript.segments == snapshot
            assert transcript.change_sequence == sequence
    finally:
        Path(archive_path).unlink(missing_ok=True)


def _provenance_archive(
    engine: Engine, *, finalized: bool
) -> tuple[str, list[dict[str, Any]]]:
    recording_id = _seed(engine)
    with Session(engine) as session:
        assert publish_live_settling(
            session, request=_request(session, recording_id, 0), words=_words()
        ).accepted
        assert close_live_settling(session, recording_id=recording_id)
        if finalized:
            finalize_utterances_from_segments(
                session,
                recording_id=recording_id,
                segments=[_final_segment("whisper")],
            )
            refresh_transcript_projection_from_canonical(session, recording_id)
        transcript = session.exec(select(Transcript)).one()
        # Isolate restore preservation from the publication-marker regression.
        transcript.settling_state = {**transcript.settling_state, "settled": True}
        owner = User(username="settled-owner", hashed_password="x")
        session.add(owner)
        session.flush()
        recording = session.exec(select(Recording)).one()
        recording.user_id = owner.id
        recording.status = RecordingStatus.PROCESSED
        session.add(recording)
        session.add(transcript)
        session.commit()
    archive_path, _ = BackupManager.create_backup_blocking(include_audio=False)
    with zipfile.ZipFile(archive_path) as archive:
        exported = json.loads(archive.read("transcripts.json"))[0]
        assert exported["settling_state"]["settled"] is True
        assert exported["settling_state"]["generation"] == "episode-a"
        assert exported["change_sequence"] > 0
        return archive_path, exported["segments"]


@pytest.mark.usefixtures("stub_celery_dispatch")
@pytest.mark.parametrize("strategy", ["clear", "overwrite"])
@pytest.mark.parametrize("finalized", [False, True])
def test_settled_restore_rebackup_and_reprocess_keep_durable_provenance(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
    finalized: bool,
) -> None:
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: default)
    archive_path, segments = _provenance_archive(engine, finalized=finalized)
    archives = [archive_path]
    try:
        result = BackupManager.restore_backup_blocking(
            f"settled-{strategy}-{finalized}",
            archive_path,
            clear_existing=strategy == "clear",
            overwrite_existing=strategy == "overwrite",
        )
        assert result["status"] == "completed", result
        with Session(engine) as session:
            transcript = session.exec(select(Transcript)).one()
            assert transcript.settling_state == {"settled": True}
            assert transcript.change_sequence == 0
            assert transcript.segments == segments
        rebackup, _ = BackupManager.create_backup_blocking(include_audio=False)
        archives.append(rebackup)
        with zipfile.ZipFile(rebackup) as archive:
            exported = json.loads(archive.read("transcripts.json"))[0]
            assert exported["segments"] == segments
            assert exported["settling_state"] == {"settled": True}
        with Session(engine) as session:
            recording_id = session.exec(select(Recording.id)).one()
            monkeypatch.setattr(finalize_restored_recording_task, "_session", session)
            finalize_restored_recording_task.run(recording_id, needs_proxy=False)
            assert session.exec(select(Transcript)).one().settling_state == {
                "settled": True
            }
            for run, first in enumerate(("Restored", "Reprocessed", "Reprocessed")):
                final = _final_segment("whisper", first)
                if run == 2:
                    final.pop("words")
                finalize_utterances_from_segments(
                    session,
                    recording_id=recording_id,
                    segments=[final],
                    idempotency_key=f"restored-final-{run}",
                )
                refresh_transcript_projection_from_canonical(session, recording_id)
                session.commit()
                session.expunge_all()
                line = serialize_canonical_utterances(
                    session, recording_id, include_confidence_payload=True
                )[0]
                assert line["text"] == first + " world"
                payload = line["confidence_payload"]
                assert "live_text_settling" not in payload
                words = [
                    word
                    for source in payload["asr_segments"]
                    for word in source.get("words", [])
                ]
                assert [word["word"] for word in words] == (
                    [first, "world"] if run < 2 else []
                )
                assert session.exec(select(Transcript)).one().settling_state == {
                    "settled": True
                }
    finally:
        for path in archives:
            Path(path).unlink(missing_ok=True)


@pytest.mark.usefixtures("stub_celery_dispatch")
@pytest.mark.parametrize("legacy_archive", [False, True])
def test_provenance_restore_keeps_safe_target_and_accepts_old_archives(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, legacy_archive: bool
) -> None:
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: default)
    archive_path, _ = _provenance_archive(engine, finalized=True)
    try:
        restore_path = _old_archive(archive_path) if legacy_archive else archive_path
        target = {"settled": False, "generation": "target", "eligible": False}
        with Session(engine) as session:
            transcript = session.exec(select(Transcript)).one()
            transcript.settling_state = target
            transcript.change_sequence = 999
            session.add(transcript)
            session.commit()
        result = BackupManager.restore_backup_blocking(
            f"settled-legacy-{legacy_archive}",
            restore_path,
            clear_existing=legacy_archive,
        )
        assert result["status"] == "completed", result
        with Session(engine) as session:
            transcript = session.exec(select(Transcript)).one()
            assert transcript.settling_state == (None if legacy_archive else target)
            assert transcript.change_sequence == (0 if legacy_archive else 999)
    finally:
        Path(archive_path).unlink(missing_ok=True)
