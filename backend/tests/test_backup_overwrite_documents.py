"""An Overwrite restore removes the files of the documents it replaces, and only those.

The recording the archive replaces is deleted, and its document rows go by cascade.
Their files are unlinked after the commit, apart from any a row still names: a
restored document can take the name of the copy it replaced.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

import backend.core.backup.restore.stages as backup_stages
from backend.core.backup import BackupManager
from backend.tests.backup_harness import (
    TestContext,
    TestDocument,
    build_test_context,
    patch_backup_manager,
    seed_source_data,
)
from backend.tests.test_backup_manager import seed_existing_target_recording


async def build_overwrite_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, archive_has_file: bool
) -> str:
    """A backup whose recording replaces the target's, with one document, agenda.pdf."""
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    incoming = source_context.path_manager.documents_directory / "agenda.pdf"
    if archive_has_file:
        incoming.write_bytes(b"backup agenda")
    await seed_source_data(source_context.async_session_maker)
    async with source_context.async_session_maker() as session:
        session.add(
            TestDocument(id=1, recording_id=40, title="Agenda", file_path=str(incoming))
        )
        await session.commit()
    zip_path, _ = await BackupManager.create_backup(include_audio=False)
    await source_context.async_engine.dispose()
    return zip_path


async def seed_replaced_documents(
    target_context: TestContext,
    monkeypatch: pytest.MonkeyPatch,
    file_paths: dict[int, str],
) -> None:
    """The target's recording, which the archive replaces, with these documents."""
    patch_backup_manager(monkeypatch, target_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    await seed_existing_target_recording(target_context.async_session_maker)
    with Session(target_context.sync_engine) as session:
        for doc_id, file_path in file_paths.items():
            session.add(
                TestDocument(
                    id=doc_id,
                    recording_id=102,
                    title=Path(file_path).name,
                    file_path=file_path,
                )
            )
        session.commit()


async def restore_overwriting(zip_path: str, job_id: str) -> dict[str, Any]:
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }
    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=False, overwrite_existing=True
    )
    return BackupManager.restore_jobs[job_id]


def restored_document(target_context: TestContext) -> TestDocument:
    with Session(target_context.sync_engine) as session:
        return session.exec(select(TestDocument)).one()


@pytest.mark.anyio
@pytest.mark.parametrize("archive_has_file", [True, False], ids=["file", "no-file"])
@pytest.mark.parametrize("preflight", [True, False], ids=["preflight", "fallback"])
async def test_overwrite_removes_the_replaced_recordings_document_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preflight: bool,
    archive_has_file: bool,
) -> None:
    # The replaced document that shares the incoming one's name must keep a file,
    # whether the archive carries one or not.
    zip_path = await build_overwrite_archive(
        tmp_path, monkeypatch, archive_has_file=archive_has_file
    )
    target_context = build_test_context(tmp_path / "target")
    documents_dir = target_context.path_manager.documents_directory
    (documents_dir / "old-notes.pdf").write_bytes(b"old notes")
    (documents_dir / "agenda.pdf").write_bytes(b"old agenda")
    await seed_replaced_documents(
        target_context,
        monkeypatch,
        {7: str(documents_dir / "old-notes.pdf"), 8: str(documents_dir / "agenda.pdf")},
    )
    if not preflight:
        # The insert loop's own delete, for a match the pre-delete missed.
        monkeypatch.setattr(
            backup_stages, "_restore_preflight_overwrite", lambda *args: None
        )

    job = await restore_overwriting(zip_path, "overwrite-documents-job")

    assert job["status"] == "completed"
    restored = restored_document(target_context)
    assert (
        Path(restored.file_path).resolve() == (documents_dir / "agenda.pdf").resolve()
    )
    assert sorted(os.listdir(documents_dir)) == ["agenda.pdf"]
    expected = b"backup agenda" if archive_has_file else b"old agenda"
    assert Path(restored.file_path).read_bytes() == expected

    await target_context.async_engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("alias", ["home", "hard-link"])
async def test_overwrite_keeps_a_replaced_name_for_the_file_a_restored_row_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    # The replaced row spells the restored row's file another way. Comparing the
    # spellings as strings, even through realpath, called them different files
    # and the unlink removed the one the restored row needs. A hard link stands
    # in for a case-insensitive mount, where /Data/x and /data/x are one entry:
    # two names for one inode, which a case-sensitive test filesystem can hold.
    zip_path = await build_overwrite_archive(
        tmp_path, monkeypatch, archive_has_file=False
    )
    target_context = build_test_context(tmp_path / "target")
    documents_dir = target_context.path_manager.documents_directory
    agenda = documents_dir / "agenda.pdf"
    agenda.write_bytes(b"old agenda")
    (documents_dir / "old-notes.pdf").write_bytes(b"old notes")
    if alias == "home":
        monkeypatch.setenv("HOME", str(tmp_path / "target"))
        replaced_spelling = "~/documents/agenda.pdf"
    else:
        os.link(agenda, documents_dir / "Agenda.pdf")
        replaced_spelling = str(documents_dir / "Agenda.pdf")
    await seed_replaced_documents(
        target_context,
        monkeypatch,
        {7: str(documents_dir / "old-notes.pdf"), 8: replaced_spelling},
    )

    job = await restore_overwriting(zip_path, f"overwrite-alias-{alias}-job")

    assert job["status"] == "completed"
    assert (
        Path(restored_document(target_context).file_path).resolve() == agenda.resolve()
    )
    assert agenda.read_bytes() == b"old agenda"
    assert Path(replaced_spelling).expanduser().exists()
    assert not (documents_dir / "old-notes.pdf").exists()

    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_overwrite_unlinks_the_replaced_files_before_moving_files_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The check that keeps a named file cannot see a replaced name that does not
    # exist yet but aliases a destination, as on a case-insensitive mount. Only
    # the order covers that case, so the check is bypassed here: with it gone,
    # an unlink after the moves would remove the restored document's new file.
    zip_path = await build_overwrite_archive(
        tmp_path, monkeypatch, archive_has_file=True
    )
    target_context = build_test_context(tmp_path / "target")
    documents_dir = target_context.path_manager.documents_directory
    (documents_dir / "agenda.pdf").write_bytes(b"old agenda")
    await seed_replaced_documents(
        target_context, monkeypatch, {8: str(documents_dir / "agenda.pdf")}
    )
    monkeypatch.setattr(
        backup_stages, "_keep_document_files_still_named", lambda *args: None
    )

    job = await restore_overwriting(zip_path, "overwrite-order-job")

    assert job["status"] == "completed"
    restored = Path(restored_document(target_context).file_path)
    assert restored.read_bytes() == b"backup agenda"

    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_overwrite_leaves_no_file_for_a_restored_document_that_fails_to_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The staged file's move is queued before the insert. When the insert fails,
    # the move used to run anyway, onto the replaced name just unlinked, leaving
    # a file no row names.
    zip_path = await build_overwrite_archive(
        tmp_path, monkeypatch, archive_has_file=True
    )
    target_context = build_test_context(tmp_path / "target")
    documents_dir = target_context.path_manager.documents_directory
    (documents_dir / "agenda.pdf").write_bytes(b"old agenda")
    await seed_replaced_documents(
        target_context, monkeypatch, {8: str(documents_dir / "agenda.pdf")}
    )
    with target_context.sync_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER refuse_agenda BEFORE INSERT ON backup_test_documents "
                "WHEN NEW.title = 'Agenda' "
                "BEGIN SELECT RAISE(ABORT, 'refused by the test'); END"
            )
        )

    job = await restore_overwriting(zip_path, "overwrite-failed-insert-job")

    assert job["status"] == "completed_with_warnings"
    assert job["warnings"]["skipped"] == {"documents": {"insert_failed": 1}}
    with Session(target_context.sync_engine) as session:
        assert session.exec(select(TestDocument)).all() == []
    assert os.listdir(documents_dir) == []

    await target_context.async_engine.dispose()
