"""Storage hygiene at the size of a real library, and as the process an operator
starts. Both need PostgreSQL, so both skip unless NOJOIN_TEST_POSTGRES_URL is set.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

import backend.utils.storage_hygiene as hygiene
from backend.models.document import Document
from backend.models.recording import Recording, RecordingStatus
from backend.tests.storage_hygiene_harness import (
    POSTGRES_SCHEMA,
    Layout,
    add_referenced_audio,
    make_layout,
    postgres_engine,
    uuid_name,
    write_file,
)
from backend.utils.storage_hygiene import AUDIO, RULE_RESTORE_LOCK

REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Layout:
    return make_layout(tmp_path, monkeypatch)


@pytest.fixture
def pg(postgres_test_url: str) -> Iterator[Engine]:
    with postgres_engine(postgres_test_url) as engine:
        yield engine


def _library(layout: Layout, engine: Engine, recordings: int) -> None:
    """``recordings`` recordings, each with audio, a proxy and a document."""
    rows = []
    for _ in range(recordings):
        base = uuid.uuid4()
        write_file(layout.recordings / f"{base}.flac", size=1)
        write_file(layout.recordings / f"{base}.mp3", size=1)
        rows.append(
            Recording(
                name="m",
                audio_path=f"data/recordings/{base}.flac",
                proxy_path=f"data/recordings/{base}.mp3",
                status=RecordingStatus.PROCESSED,
            )
        )
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()
        documents = [
            Document(
                recording_id=row.id,
                title="d",
                file_path=str(write_file(layout.documents / uuid_name(".pdf"), size=1)),
            )
            for row in rows
        ]
        session.add_all(documents)
        session.commit()


def test_a_library_of_2000_recordings(
    layout: Layout, pg: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    _library(layout, pg, 2000)
    orphans = [
        write_file(layout.recordings / uuid_name(".flac"), size=1) for _ in range(60)
    ]

    started = time.perf_counter()
    with caplog.at_level(logging.INFO, logger=hygiene.__name__):
        report = hygiene.run_storage_hygiene(
            lambda: Session(pg),
            hygiene.HygieneOptions(redis_client=lambda: _FreeLock()),
        )
    elapsed = time.perf_counter() - started

    assert elapsed < 30
    assert not report.tripped
    assert report.categories[AUDIO].scanned == 2060
    assert report.categories[AUDIO].unreferenced == 60
    listed = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("Storage hygiene would remove ")
    ]
    assert len(listed) == 51
    assert listed[-1] == "Storage hygiene would remove 10 more files"
    assert all(path.exists() for path in orphans)


class _FreeLock:
    def exists(self, key: str) -> int:
        return 0

    def close(self) -> None:
        pass


def test_the_cli_as_a_process_writes_nothing_and_fails_closed(
    layout: Layout, pg: Engine, postgres_test_url: str, tmp_path: Path
) -> None:
    """``python -m backend.storage_hygiene --delete`` with Redis unreachable."""
    add_referenced_audio(pg, layout, 10)
    orphan = write_file(layout.recordings / uuid_name(".flac"))
    before = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")}
    separator = "&" if "?" in postgres_test_url else "?"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(layout.app),
        "PYTHONPATH": str(REPOSITORY),
        # Bytecode too: the process must not write into the checkout's backend/.
        "PYTHONDONTWRITEBYTECODE": "1",
        "NOJOIN_CONTAINERIZED": "1",
        "NOJOIN_DATA_DIR": str(layout.data),
        "DATABASE_URL": (
            f"{postgres_test_url}{separator}options=-csearch_path%3D{POSTGRES_SCHEMA}"
        ),
        "REDIS_URL": "redis://127.0.0.1:1/0",
    }

    process = subprocess.run(
        [sys.executable, "-m", "backend.storage_hygiene", "--delete"],
        cwd=layout.app,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert process.returncode == 1, process.stderr[-2000:]
    assert (
        f"  - {RULE_RESTORE_LOCK}: the restore lock could not be read" in process.stdout
    )
    assert any(
        line.startswith("  audio ") and line.endswith(str(orphan))
        for line in process.stdout.splitlines()
    )
    assert orphan.exists()
    after = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")}
    assert after == before
