"""Additional notes survive the real archive and restore path."""

import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.core.backup import BackupManager
from backend.models.notes_output import NotesOutput
from backend.models.notes_template import NotesTemplate
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.test_backup_settling_state_postgres import engine as engine


@pytest.mark.parametrize("status", ["completed", "generating"])
@pytest.mark.usefixtures("stub_celery_dispatch")
def test_notes_outputs_round_trip(engine: Engine, status: str) -> None:
    with Session(engine) as db:
        user = User(username="notes-owner", hashed_password="x")
        db.add(user)
        db.flush()
        recording = Recording(
            name="Planning",
            audio_path="/tmp/notes-backup.wav",
            user_id=user.id,
            status=RecordingStatus.PROCESSED,
        )
        template = NotesTemplate(
            name="Task list", sections="## Tasks\nOwners.", user_id=user.id
        )
        db.add_all([recording, template])
        db.flush()
        db.add(
            Transcript(
                recording_id=recording.id,
                notes="Primary minutes.",
                transcript_status="completed",
                notes_status="completed",
            )
        )
        db.add(
            NotesOutput(
                recording_id=recording.id,
                name=template.name,
                notes="Extra tasks.",
                notes_template_id=template.id,
                notes_template_sections=template.sections,
                notes_status=status,
            )
        )
        db.commit()
    archive_path, _ = BackupManager.create_backup_blocking(include_audio=False)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert (
                json.loads(archive.read("notes_outputs.json"))[0]["notes"]
                == "Extra tasks."
            )
        result = BackupManager.restore_backup_blocking(
            "notes-outputs", archive_path, clear_existing=True
        )
        assert result["status"] == "completed", result
        with Session(engine) as db:
            output = db.exec(select(NotesOutput)).one()
            template = db.exec(select(NotesTemplate)).one()
            recording = db.exec(select(Recording)).one()
            assert output.notes == "Extra tasks."
            assert output.recording_id == recording.id
            assert output.notes_template_id == template.id
            assert output.notes_template_sections == "## Tasks\nOwners."
            assert output.notes_status == (
                "completed" if status == "completed" else "error"
            )
            assert db.exec(select(Transcript)).one().notes == "Primary minutes."
    finally:
        Path(archive_path).unlink(missing_ok=True)
