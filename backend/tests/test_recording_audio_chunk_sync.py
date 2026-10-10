"""Syncing an upload directory reads only the files it has not recorded yet.

Every segment upload syncs its whole directory. The sync used to hash and probe
every file again each time, so the cost of an upload grew with the parts
already received, and a 330-part video import froze the API for seconds at a
time. These tests record which files the sync hashed and which it probed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.tests.sqlite_schemas import (
    RECORDING_AUDIO_CHUNKS_SCHEMA,
    RECORDINGS_SCHEMA,
)
from backend.utils import recording_audio_sync
from backend.utils.recording_audio_sync import (
    IMPORT_PART_SOURCE_KIND,
    sync_recording_audio_chunks_from_directory,
)

RECORDING_ID = 7


@pytest.fixture
def session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'chunks.sqlite'}")
    with engine.begin() as connection:
        connection.execute(text(RECORDINGS_SCHEMA))
        connection.execute(text(RECORDING_AUDIO_CHUNKS_SCHEMA))
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {"hashed": [], "probed": []}
    real_hash = recording_audio_sync._sha256_for_path

    def _hash(path: Path) -> str:
        seen["hashed"].append(path.name)
        return real_hash(path)

    def _probe(path: str) -> float:
        seen["probed"].append(Path(path).name)
        return 1.5

    monkeypatch.setattr(recording_audio_sync, "_sha256_for_path", _hash)
    monkeypatch.setattr(recording_audio_sync, "get_audio_duration", _probe)
    return seen


@pytest.fixture
def upload_dir(tmp_path: Path) -> Path:
    path = tmp_path / "upload"
    path.mkdir()
    return path


def _sync(
    session: Session,
    upload_dir: Path,
    *,
    source_kind: str = IMPORT_PART_SOURCE_KIND,
    suffix: str = ".part",
):
    rows = sync_recording_audio_chunks_from_directory(
        session,
        recording_id=RECORDING_ID,
        source_kind=source_kind,
        suffix=suffix,
        temp_dir=upload_dir,
    )
    session.commit()
    return rows


def _row(session: Session, sequence: int) -> dict:
    return dict(
        session.execute(
            text(
                "SELECT byte_size, sha256, upload_status, duration_ms"
                " FROM recording_audio_chunks WHERE sequence_no = :sequence"
            ),
            {"sequence": sequence},
        )
        .mappings()
        .one()
    )


def test_each_upload_hashes_only_the_part_it_added(
    session: Session, reads: dict, upload_dir: Path
) -> None:
    for sequence in (1, 2, 3):
        (upload_dir / f"{sequence}.part").write_bytes(bytes([sequence]) * 1000)
        _sync(session, upload_dir)

    assert reads["hashed"] == ["1.part", "2.part", "3.part"]


def test_a_part_rewritten_with_other_bytes_is_hashed_again(
    session: Session, reads: dict, upload_dir: Path
) -> None:
    (upload_dir / "1.part").write_bytes(b"a" * 1000)
    (upload_dir / "2.part").write_bytes(b"b" * 1000)
    _sync(session, upload_dir)
    (upload_dir / "2.part").write_bytes(b"c" * 2000)
    _sync(session, upload_dir)

    assert reads["hashed"] == ["1.part", "2.part", "2.part"]
    assert _row(session, 2)["byte_size"] == 2000
    assert _row(session, 2)["sha256"] == hashlib.sha256(b"c" * 2000).hexdigest()


def test_a_row_marked_failed_is_rebuilt_from_its_file(
    session: Session, reads: dict, upload_dir: Path
) -> None:
    (upload_dir / "1.part").write_bytes(b"a" * 1000)
    _sync(session, upload_dir)
    session.execute(text("UPDATE recording_audio_chunks SET upload_status = 'failed'"))
    session.commit()
    _sync(session, upload_dir)

    assert reads["hashed"] == ["1.part", "1.part"]
    assert _row(session, 1)["upload_status"] == "received"


def test_import_parts_are_never_probed_for_a_duration(
    session: Session, reads: dict, upload_dir: Path
) -> None:
    (upload_dir / "1.part").write_bytes(b"a" * 1000)
    (upload_dir / "2.part").write_bytes(b"b" * 1000)
    _sync(session, upload_dir)

    assert reads["probed"] == []
    assert _row(session, 1)["duration_ms"] == 0


def test_other_chunks_are_still_probed_once(
    session: Session, reads: dict, upload_dir: Path
) -> None:
    (upload_dir / "1.webm").write_bytes(b"a" * 1000)
    _sync(session, upload_dir, source_kind="browser", suffix=".webm")
    _sync(session, upload_dir, source_kind="browser", suffix=".webm")

    assert reads["probed"] == ["1.webm"]
    assert _row(session, 1)["duration_ms"] == 1500
