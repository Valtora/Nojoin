"""A backup carries every drift guard refusal a recording speaker holds.

The refusals are a set per link (``recording_speaker_drift_refusals``), keyed by
the link and the person, so a restore has to remap both sides onto the rows it
inserts. Runs on the surrogate tables in ``backup_harness``.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from sqlmodel import Session, select

from backend.core.backup import BackupManager
from backend.tests.backup_harness import (
    TestGlobalSpeaker,
    TestRecording,
    TestRecordingSpeaker,
    TestRecordingSpeakerDriftRefusal,
    TestUser,
    build_test_context,
    patch_backup_manager,
    seed_source_data,
)

REFUSALS_MEMBER = "recording_speaker_drift_refusals.json"


async def _seed_refusals(session_maker) -> None:
    """Dana's speaker (80) refused for Dana (30) and for Eli (31)."""
    async with session_maker() as session:
        session.add(TestGlobalSpeaker(id=31, name="Eli Okafor", user_id=1))
        await session.flush()
        for person_id in (30, 31):
            session.add(
                TestRecordingSpeakerDriftRefusal(
                    recording_speaker_id=80, global_speaker_id=person_id
                )
            )
        await session.commit()


async def _export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    source = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source)
    await seed_source_data(source.async_session_maker)
    await _seed_refusals(source.async_session_maker)
    zip_path, _ = await BackupManager.create_backup(include_audio=False)
    await source.async_engine.dispose()
    return zip_path


async def _restore(zip_path: str, job_id: str) -> str:
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }
    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=False, overwrite_existing=False
    )
    return BackupManager.restore_jobs[job_id]["status"]


@pytest.mark.anyio
async def test_a_round_trip_keeps_every_refusal_of_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zip_path = await _export(tmp_path, monkeypatch)
    with zipfile.ZipFile(zip_path) as archive:
        archived = json.loads(archive.read(REFUSALS_MEMBER))
    assert sorted(
        (row["recording_speaker_id"], row["global_speaker_id"]) for row in archived
    ) == [(80, 30), (80, 31)]

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    target = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target)
    with Session(target.sync_engine) as session:
        # Taken ids, so a restore that kept the source's ids would be caught.
        session.add(TestUser(id=1, username="bob", hashed_password="x"))
        session.flush()
        session.add(TestGlobalSpeaker(id=30, name="Someone else", user_id=1))
        session.add(TestGlobalSpeaker(id=31, name="Another", user_id=1))
        session.commit()

    assert await _restore(zip_path, "refusals-round-trip") == "completed"

    with Session(target.sync_engine) as session:
        speaker = session.exec(
            select(TestRecordingSpeaker).where(
                TestRecordingSpeaker.diarization_label == "SPEAKER_00"
            )
        ).one()
        people = {
            person.name: person.id
            for person in session.exec(select(TestGlobalSpeaker)).all()
        }
        restored = session.exec(select(TestRecordingSpeakerDriftRefusal)).all()

    assert sorted((r.recording_speaker_id, r.global_speaker_id) for r in restored) == (
        sorted(
            [
                (speaker.id, people["Dana Mercer"]),
                (speaker.id, people["Eli Okafor"]),
            ]
        )
    )
    assert speaker.global_speaker_id == people["Dana Mercer"]
    await target.async_engine.dispose()


@pytest.mark.anyio
async def test_a_refusal_for_a_person_the_restore_skips_is_reported_with_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Robin has no owner, so the restore skips her as unresolved.

    Dana's speaker was refused for Robin too. That refusal names a person the
    target does not have, so it is skipped for the same reason, and the
    restore report says so rather than that the database rejected it.
    """
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    source = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source)
    await seed_source_data(source.async_session_maker)
    await _seed_refusals(source.async_session_maker)
    async with source.async_session_maker() as session:
        session.add(TestGlobalSpeaker(id=32, name="Robin Vale", user_id=None))
        await session.flush()
        session.add(
            TestRecordingSpeakerDriftRefusal(
                recording_speaker_id=80, global_speaker_id=32
            )
        )
        await session.commit()
    zip_path, _ = await BackupManager.create_backup(include_audio=False)
    await source.async_engine.dispose()

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    target = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target)

    assert await _restore(zip_path, "refusals-orphan") == "completed_with_warnings"
    skipped = BackupManager.restore_jobs["refusals-orphan"]["warnings"]["skipped"]
    assert skipped["global_speakers"] == {"unresolved_owner": 1}
    assert skipped["recording_speaker_drift_refusals"] == {"unresolved_owner": 1}
    with Session(target.sync_engine) as session:
        people = {p.id: p.name for p in session.exec(select(TestGlobalSpeaker)).all()}
        restored = session.exec(select(TestRecordingSpeakerDriftRefusal)).all()
    assert sorted(people[r.global_speaker_id] for r in restored) == [
        "Dana Mercer",
        "Eli Okafor",
    ]
    await target.async_engine.dispose()


@pytest.mark.anyio
async def test_a_safe_merge_skips_the_refusals_of_a_skipped_recording_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recording already present keeps its own speakers, and their refusals.

    The archive's speakers for it are skipped, so their refusals are too, and
    without counting as rows the restore failed to bring across.
    """
    zip_path = await _export(tmp_path, monkeypatch)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    target = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target)
    with Session(target.sync_engine) as session:
        session.add(TestUser(id=101, username="alice", hashed_password="x"))
        session.flush()
        session.add(
            TestRecording(
                id=102,
                name="Existing quarterly planning",
                audio_path="data/recordings/quarterly-planning.wav",
                status="PROCESSED",
                user_id=101,
            )
        )
        session.commit()

    assert await _restore(zip_path, "refusals-safe-merge") == "completed"

    with Session(target.sync_engine) as session:
        assert session.exec(select(TestRecordingSpeaker)).all() == []
        assert session.exec(select(TestRecordingSpeakerDriftRefusal)).all() == []
    await target.async_engine.dispose()
