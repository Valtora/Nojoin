"""Destructive restores join Transcript order before cascade and foreign-key locks."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event, text
from sqlmodel import Session, select

from backend.core.backup import BackupManager
from backend.models.pipeline import ProcessingRunKind
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.test_backup_publication_state_postgres import engine as engine
from backend.utils.canonical_pipeline import append_utterances_from_segments
from backend.utils.canonical_pipeline.constants import _load_transcript


@pytest.mark.parametrize("strategy", ["clear", "overwrite"])
@pytest.mark.usefixtures("stub_celery_dispatch")
def test_destructive_restore_waits_before_recording_or_later_transcript_locks(
    engine: Engine,
    strategy: str,
) -> None:
    with Session(engine) as session:
        owner = User(username="restoring-owner", hashed_password="x")
        session.add(owner)
        session.flush()
        recordings = [
            Recording(
                name=f"Recording {index}",
                audio_path=f"/data/recordings/{index}.wav",
                user_id=owner.id,
                status=RecordingStatus.PROCESSED,
            )
            for index in range(2)
        ]
        session.add_all(recordings)
        session.flush()
        first_id, second_id = (row.id for row in recordings)
        # Heap order opposes recording order, as it can after an old reprocess.
        for rid in (second_id, first_id):
            session.add(Transcript(recording_id=rid, segments=[], text="Before"))
            session.flush()
        session.commit()

    archive_path, _ = BackupManager.create_backup_blocking(include_audio=False)
    restore_started = threading.Event()
    restore_pid: list[int] = []

    def before_restore_sql(
        connection: Any,
        cursor: Any,
        statement: str,
        *args: Any,
    ) -> None:
        if threading.current_thread() is threading.main_thread():
            return
        if (
            "FROM transcripts" in statement
            and "FOR UPDATE" in statement
            or statement.startswith("DELETE FROM transcripts")
            or statement.startswith("DELETE FROM recordings")
        ):
            restore_pid[:] = [connection.connection.driver_connection.info.backend_pid]
            restore_started.set()

    event.listen(engine, "before_cursor_execute", before_restore_sql)
    try:
        with (
            ThreadPoolExecutor(max_workers=1) as executor,
            Session(engine) as publisher,
        ):
            publisher.execute(text("SET lock_timeout = '1s'"))
            _load_transcript(publisher, first_id, lock=True)
            future = executor.submit(
                BackupManager.restore_backup_blocking,
                f"restore-order-{strategy}",
                archive_path,
                clear_existing=strategy == "clear",
                overwrite_existing=strategy == "overwrite",
            )
            try:
                assert restore_started.wait(5), (
                    "Restore never reached Transcript deletion"
                )
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    blocked = publisher.execute(
                        text("SELECT cardinality(pg_blocking_pids(:pid))"),
                        {"pid": restore_pid[0]},
                    ).scalar_one()
                    if blocked:
                        break
                    time.sleep(0.01)
                assert blocked, "Restore never waited for the publisher"
                _load_transcript(publisher, second_id, lock=True)
                append_utterances_from_segments(
                    publisher,
                    recording_id=first_id,
                    segments=[{"start": 0, "end": 1, "text": "Before replacement"}],
                    run_kind=ProcessingRunKind.LIVE,
                    source="live",
                )
                publisher.commit()
            finally:
                publisher.rollback()
            result = future.result(timeout=15)
            assert result["status"] == "completed", result
        with Session(engine) as session:
            assert len(session.exec(select(Recording)).all()) == 2
            assert {row.text for row in session.exec(select(Transcript)).all()} == {
                "Before"
            }
    finally:
        event.remove(engine, "before_cursor_execute", before_restore_sql)
        Path(archive_path).unlink(missing_ok=True)
