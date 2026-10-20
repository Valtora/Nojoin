"""The daily sweep that reclaims document files no row references.

Runs the real sweep against a real directory and a documents table built from
the model. File ages are set with utime and the sweep is given its clock, so the
grace period is exercised without waiting one out.
"""

from __future__ import annotations

import logging
import os
import time

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session

from backend.models.document import Document
from backend.utils.document_storage import (
    ORPHAN_DOCUMENT_GRACE_HOURS,
    cleanup_orphaned_document_files,
)

NOW = time.time()
OLD = NOW - (ORPHAN_DOCUMENT_GRACE_HOURS + 1) * 3600
RECENT = NOW - 3600


@pytest.fixture
def documents_dir(tmp_path, monkeypatch):
    directory = tmp_path / "data" / "documents"
    directory.mkdir(parents=True)
    monkeypatch.setenv("DOCUMENTS_DIR", str(directory))
    return directory


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Document.metadata.create_all(engine, tables=[Document.__table__])
    with Session(engine) as session:
        yield session
    engine.dispose()


def _file(directory, name: str, *, mtime: float, size: int = 10):
    path = directory / name
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def _row(session, document_id: int, file_path: str, recording_id: int | None = 1):
    session.add(
        Document(
            id=document_id,
            recording_id=recording_id,
            title=f"doc {document_id}",
            file_path=file_path,
        )
    )
    session.commit()


def test_removes_old_orphans_and_keeps_recent_and_referenced_files(
    documents_dir, session, caplog
):
    orphan = _file(documents_dir, "orphan.pdf", mtime=OLD, size=700)
    stale_upload = _file(documents_dir, "x.pdf.abc.upload-tmp", mtime=OLD, size=300)
    recent = _file(documents_dir, "recent.pdf", mtime=RECENT)
    referenced = _file(documents_dir, "referenced.pdf", mtime=OLD)
    _row(session, 1, str(referenced))

    with caplog.at_level(logging.INFO):
        removed = cleanup_orphaned_document_files(
            session, logger=logging.getLogger("sweep"), now=NOW
        )

    assert removed == 2
    assert not orphan.exists() and not stale_upload.exists()
    assert recent.is_file() and referenced.is_file()
    assert "removed 2 file(s), reclaimed 1000 bytes" in caplog.text


def test_a_row_protects_its_file_however_its_path_is_rooted(documents_dir, session):
    """A restore can store a path relative to the working directory, and a
    legacy row a different absolute root; the stored name still matches."""
    relative = _file(documents_dir, "relative.md", mtime=OLD)
    rerooted = _file(documents_dir, "rerooted.md", mtime=OLD)
    _row(session, 1, "data/documents/relative.md")
    _row(session, 2, "/app/data/documents/rerooted.md")

    removed = cleanup_orphaned_document_files(
        session, logger=logging.getLogger("sweep"), now=NOW
    )

    assert removed == 0
    assert relative.is_file() and rerooted.is_file()


def test_a_row_without_a_recording_still_protects_its_file(documents_dir, session):
    # The column is nullable in the schema. No code path writes such a row, but
    # one that exists still references its file, so the file is not an orphan.
    unattached = _file(documents_dir, "unattached.md", mtime=OLD)
    _row(session, 1, str(unattached), recording_id=None)

    removed = cleanup_orphaned_document_files(
        session, logger=logging.getLogger("sweep"), now=NOW
    )

    assert removed == 0
    assert unattached.is_file()


def test_never_follows_links_or_enters_subdirectories(documents_dir, session, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("not ours")
    os.utime(outside, (OLD, OLD))
    link = documents_dir / "link.md"
    link.symlink_to(outside)
    nested = documents_dir / "nested"
    nested.mkdir()
    inner = _file(nested, "inner.md", mtime=OLD)

    removed = cleanup_orphaned_document_files(
        session, logger=logging.getLogger("sweep"), now=NOW
    )

    assert removed == 0
    assert link.is_symlink() and outside.read_text() == "not ours"
    assert inner.is_file()
