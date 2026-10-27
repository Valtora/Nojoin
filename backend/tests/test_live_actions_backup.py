"""Format 3 carries accepted words, boundaries, notes and live naming anchors."""

import copy
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.core.backup import BackupManager
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.timeline_note import TimelineNote
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.test_backup_publication_state_postgres import engine as engine
from backend.tests.test_live_settling_publication import _request, _seed, _words
from backend.utils.canonical_pipeline import (
    serialize_canonical_utterances,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.split import SplitCommand, split_utterance
from backend.utils.config_manager import config_manager
from backend.utils.speaker_names.events import UserNameAct
from backend.utils.speaker_names.live import (
    live_speaker_names,
    record_live_speaker_name,
)
from backend.worker.tasks import finalize_restored_recording_task


def _accepted_capture(
    engine: Engine, *, interrupted: bool
) -> tuple[list[dict[str, Any]], str]:
    recording_id = _seed(engine)
    with Session(engine) as session:
        owner = User(username="accepted-owner", hashed_password="x")
        session.add(owner)
        session.flush()
        person = GlobalSpeaker(name="Alice", user_id=owner.id)
        session.add(person)
        session.flush()
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(("Hello", " world", " again", " today")),
            decide_speaker=lambda start, end: LiveLabelDecision(
                "LIVE_PROVISIONAL_0", True, 1.0
            ),
        ).accepted
        assert close_live_settling(session, recording_id=recording_id)
        line = serialize_canonical_utterances(session, recording_id)[0]
        children = split_utterance(
            session,
            SplitCommand(
                recording_id, line["id"], line["words"][2]["id"], line["revision"]
            ),
        )
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=children[0].public_id,
            text="Human correction",
        )
        name = record_live_speaker_name(
            session,
            recording_id,
            "LIVE_PROVISIONAL_0",
            UserNameAct("Alice", person.id, owner.id, "live"),
        )
        assert name is not None
        session.add(
            TimelineNote(
                recording_id=recording_id,
                created_by_user_id=owner.id,
                kind="selection",
                body="Keep this quote",
                start_ms=100,
                end_ms=850,
                quoted_text="Human correction",
            )
        )
        transcript = session.exec(select(Transcript)).one()
        transcript.transcript_status = "processing" if interrupted else "completed"
        recording = session.get(Recording, recording_id)
        recording.user_id = owner.id
        recording.status = (
            RecordingStatus.UPLOADING if interrupted else RecordingStatus.PROCESSED
        )
        session.add(recording)
        session.add(transcript)
        session.commit()
        expected = copy.deepcopy(
            serialize_canonical_utterances(
                session,
                recording_id,
                include_confidence_payload=True,
                resolve_merged_overlaps=False,
            )
        )
        return expected, name.public_id


@pytest.mark.usefixtures("stub_celery_dispatch")
@pytest.mark.parametrize("strategy", ["clear", "overwrite"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_accepted_capture_round_trip_and_backfill(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
    interrupted: bool,
) -> None:
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: default)
    expected, name_public_id = _accepted_capture(engine, interrupted=interrupted)
    archive_path, _ = BackupManager.create_backup_blocking(include_audio=False)
    archives = [archive_path]
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert json.loads(archive.read("backup_info.json"))["format_version"] == 3
            assert "speaker_name_events.json" not in archive.namelist()
            exported = json.loads(archive.read("transcripts.json"))[0]
            assert exported["segments"] == expected
            assert (
                exported["settling_state"]["name_anchors"][0]["public_id"]
                == name_public_id
            )
        result = BackupManager.restore_backup_blocking(
            f"accepted-{strategy}-{interrupted}",
            archive_path,
            clear_existing=strategy == "clear",
            overwrite_existing=strategy == "overwrite",
        )
        assert result["status"] == "completed", result
        with Session(engine) as session:
            transcript = session.exec(select(Transcript)).one()
            assert transcript.change_sequence == 0
            assert set(transcript.settling_state) == {"settled", "name_anchors"}
            assert transcript.segments == expected
            recording = session.exec(select(Recording)).one()
            assert recording.status == (
                RecordingStatus.ERROR if interrupted else RecordingStatus.PROCESSED
            )
            person = session.exec(
                select(GlobalSpeaker).where(GlobalSpeaker.name == "Alice")
            ).one()
            name = session.exec(select(SpeakerNameEvent)).one()
            assert name.public_id == name_public_id
            assert name.global_speaker_id == person.id
            assert (name.anchor_start_ms, name.anchor_end_ms) == (100, 1750)
            note = session.exec(select(TimelineNote)).one()
            assert (note.start_ms, note.end_ms, note.quoted_text) == (
                100,
                850,
                "Human correction",
            )
            monkeypatch.setattr(finalize_restored_recording_task, "_session", session)
            finalize_restored_recording_task.run(recording.id, needs_proxy=False)
            lines = serialize_canonical_utterances(
                session, recording.id, include_confidence_payload=True
            )
            assert [line["text"] for line in lines] == [
                "Human correction",
                "again today",
            ]
            assert lines[0]["text_manually_edited"] is True
            payloads = [line["confidence_payload"] for line in lines]
            assert [payload["human_protection"] for payload in payloads] == [
                segment["confidence_payload"]["human_protection"]
                for segment in expected
            ]
            assert [payload["asr_segments"] for payload in payloads] == [
                segment["confidence_payload"]["asr_segments"] for segment in expected
            ]
            assert live_speaker_names(session, recording.id) == {
                "LIVE_PROVISIONAL_0": "Alice"
            }
        rebackup, _ = BackupManager.create_backup_blocking(include_audio=False)
        archives.append(rebackup)
        with zipfile.ZipFile(rebackup) as archive:
            exported = json.loads(archive.read("transcripts.json"))[0]
            assert (
                exported["settling_state"]["name_anchors"][0]["public_id"]
                == name_public_id
            )
            assert [segment["text"] for segment in exported["segments"]] == [
                "Human correction",
                "again today",
            ]
    finally:
        for path in archives:
            Path(path).unlink(missing_ok=True)
