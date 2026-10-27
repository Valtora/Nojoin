"""Format 3 retains timeline span snapshots independently of canonical lines."""

import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.core.backup import BackupManager
from backend.models.timeline_note import TimelineNote
from backend.tests.test_backup_publication_state_postgres import engine as engine
from backend.tests.test_live_actions_backup import _accepted_capture
from backend.utils.config_manager import config_manager


@pytest.mark.usefixtures("stub_celery_dispatch")
def test_timeline_span_round_trip_keeps_time_snapshot_without_quote(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: default)
    _accepted_capture(engine, interrupted=False)
    with Session(engine) as session:
        source_note = session.exec(select(TimelineNote)).one()
        session.add(
            TimelineNote(
                recording_id=source_note.recording_id,
                created_by_user_id=source_note.created_by_user_id,
                kind="point",
                body="Line timeline snapshot",
                anchor_ms=100,
                start_ms=100,
                end_ms=850,
            )
        )
        session.commit()
    archive_path, _ = BackupManager.create_backup_blocking(include_audio=False)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert json.loads(archive.read("backup_info.json"))["format_version"] == 3
            notes = json.loads(archive.read("timeline_notes.json"))
            point = next(note for note in notes if note["kind"] == "point")
            assert (
                point["anchor_ms"],
                point["start_ms"],
                point["end_ms"],
                point["quoted_text"],
            ) == (
                100,
                100,
                850,
                None,
            )
        result = BackupManager.restore_backup_blocking(
            "timeline-span",
            archive_path,
            clear_existing=True,
        )
        assert result["status"] == "completed", result
        with Session(engine) as session:
            point = session.exec(
                select(TimelineNote).where(TimelineNote.kind == "point")
            ).one()
            assert (
                point.anchor_ms,
                point.start_ms,
                point.end_ms,
                point.quoted_text,
            ) == (
                100,
                100,
                850,
                None,
            )
    finally:
        Path(archive_path).unlink(missing_ok=True)
