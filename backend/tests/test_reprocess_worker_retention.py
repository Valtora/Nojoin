"""An ordinary reprocess publishes fresh identities or keeps the previous result."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine
from sqlmodel import Session, select

from backend.core.db import driver_urls
from backend.models.chat import ChatMessage
from backend.models.context_chunk import ContextChunk
from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import (
    GlobalSpeaker,
    RecordingSpeaker,
    RecordingSpeakerDriftRefusal,
)
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing import embedding
from backend.processing.embedding_version import EMBEDDING_METHOD_VERSION
from backend.processing.text_embedding_version import TEXT_EMBEDDING_DIMENSIONS
from backend.tests.postgres_setup import fresh_schema
from backend.tests.test_pipeline_stages import _install_happy_path_modules, _run_task
from backend.utils.canonical_pipeline import finalize_utterances_from_segments
from backend.utils.config_manager import config_manager
from backend.utils.reprocess_publication import mark_reprocess_pending
from backend.utils.status_manager import update_recording_status
from backend.worker import tasks
from backend.worker.tasks import followups, pipeline, speaker_assignment


@pytest.fixture(params=["import", "browser"])
def worker_database(
    postgres_test_url: str,
    request: pytest.FixtureRequest,
) -> Iterator[tuple[Engine, int]]:
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={
            "options": "-csearch_path=reprocess_worker_retention_test,public"
        },
    )
    fresh_schema(engine, "reprocess_worker_retention_test")
    with Session(engine) as session:
        owner = User(
            username="worker-owner",
            hashed_password="x",
            settings={"voiceprint_learning": "automatic"},
        )
        session.add(owner)
        session.flush()
        recording = Recording(
            name="Previous title",
            audio_path="/tmp/old.wav",
            user_id=owner.id,
            status=RecordingStatus.QUEUED,
            duration_seconds=60,
            capture_epoch=2 if request.param == "browser" else 0,
        )
        session.add(recording)
        session.flush()
        recording_id = int(recording.id)
        session.add(
            Transcript(
                recording_id=recording_id,
                text="Previous words",
                segments=[],
                transcript_status="pending",
                settling_state={"reprocess_pending": True},
                notes="Previous notes",
                notes_status="completed",
                meeting_edge_focus="Keep my focus",
                meeting_edge_payload={"old": True},
                speaker_name_suggestions=[{"name": "Previous suggestion"}],
            )
        )
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": 0,
                    "end": 1,
                    "text": "Previous words",
                    "speaker": "SPEAKER_00",
                    "text_manually_edited": True,
                    "segment_source": "live"
                    if request.param == "browser"
                    else "finalize",
                }
            ],
        )
        old = session.exec(select(RecordingSpeaker)).first()
        old.local_name = "Wrong old identity"
        old.embedding = [0.0, 1.0]
        old.embedding_version = EMBEDDING_METHOD_VERSION
        session.add(old)
        session.add(
            RecordingSpeaker(
                recording_id=recording_id,
                diarization_label="SPEAKER_01",
                name="Wrong old merged identity",
                merged_into_id=old.id,
                embedding=[0.0, 1.0],
                embedding_version=EMBEDDING_METHOD_VERSION,
            )
        )
        session.add(
            SpeakerNameEvent(
                recording_id=recording_id,
                kind="name",
                name="Wrong anchored name",
                source="user",
                authority=100,
                recording_speaker_id=old.id,
                anchor_start_ms=0,
                anchor_end_ms=2000,
            )
        )
        session.add(
            ChatMessage(
                recording_id=recording_id,
                user_id=owner.id,
                role="user",
                content="Retain old chat",
            )
        )
        session.add(
            ContextChunk(
                recording_id=recording_id,
                content="Old transcript context",
                embedding=[0.0] * TEXT_EMBEDDING_DIMENSIONS,
            )
        )
        session.commit()
    try:
        yield engine, recording_id
    finally:
        engine.dispose()


def mark_for_retry(engine: Engine) -> None:
    """Mark the retained publication pending again, as Retry's requeue does."""
    with Session(engine) as session:
        transcript = session.exec(select(Transcript)).one()
        mark_reprocess_pending(transcript)
        transcript.transcript_status = "pending"
        session.add(transcript)
        session.commit()


def _view(engine: Engine, recording_id: int) -> dict[str, Any]:
    with Session(engine) as reader:
        transcript = reader.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).one()
        return deepcopy(
            {
                "text": transcript.text,
                "pending": (transcript.settling_state or {}).get("reprocess_pending")
                is True,
                "published": (transcript.settling_state or {}).get(
                    "reprocess_published"
                )
                is True,
                "transcript_status": transcript.transcript_status,
                "notes": transcript.notes,
                "focus": transcript.meeting_edge_focus,
                "edge": transcript.meeting_edge_payload,
                "suggestions": transcript.speaker_name_suggestions,
                "speakers": [
                    (s.diarization_label, s.local_name, s.embedding)
                    for s in reader.exec(
                        select(RecordingSpeaker).where(
                            RecordingSpeaker.recording_id == recording_id
                        )
                    ).all()
                ],
                "canonical": [
                    u.text
                    for u in reader.exec(
                        select(TranscriptUtterance).where(
                            TranscriptUtterance.recording_id == recording_id
                        )
                    ).all()
                ],
                "chat": [
                    row.content
                    for row in reader.exec(
                        select(ChatMessage).where(
                            ChatMessage.recording_id == recording_id
                        )
                    ).all()
                ],
                "context": [
                    row.content
                    for row in reader.exec(
                        select(ContextChunk).where(
                            ContextChunk.recording_id == recording_id
                        )
                    ).all()
                ],
                "names": [
                    row.name
                    for row in reader.exec(
                        select(SpeakerNameEvent).where(
                            SpeakerNameEvent.recording_id == recording_id
                        )
                    ).all()
                ],
                "status": reader.get(Recording, recording_id).status,
            }
        )


def _configure(
    monkeypatch: pytest.MonkeyPatch, canonical: bool, *, empty: bool = False
) -> None:
    segments = (
        []
        if empty
        else [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "Replacement words",
                "speaker": "SPEAKER_00",
            },
            {
                "start": 1.0,
                "end": 2.0,
                "text": "Changed cluster",
                "speaker": "SPEAKER_01",
            },
        ]
    )
    _install_happy_path_modules(monkeypatch, segments=segments)
    monkeypatch.setitem(sys.modules, "backend.processing.embedding", embedding)
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: (
            canonical if key == "enable_canonical_transcript_writes" else default
        ),
    )
    monkeypatch.setattr(
        pipeline, "combine_and_consolidate_segments", lambda *a, **k: deepcopy(segments)
    )
    monkeypatch.setattr(
        pipeline,
        "_run_final_asr_stage",
        lambda *a, **k: {
            "text": " ".join(s["text"] for s in segments),
            "segments": segments,
        },
    )
    monkeypatch.setattr(followups, "dispatch_post_processing_followups", lambda _: None)


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_replacement_is_fresh_and_committed_before_notes(
    worker_database: tuple[Engine, int],
    monkeypatch: pytest.MonkeyPatch,
    canonical: bool,
    empty: bool,
) -> None:
    engine, recording_id = worker_database
    _configure(monkeypatch, canonical, empty=empty)
    before = _view(engine, recording_id)
    observed: list[dict[str, Any]] = []
    original = speaker_assignment.assign_and_identify_speakers

    def assign(*args: Any, **kwargs: Any) -> None:
        observed.append(_view(engine, recording_id))
        original(*args, **kwargs)
        observed.append(_view(engine, recording_id))

    monkeypatch.setattr(pipeline, "assign_and_identify_speakers", assign)
    monkeypatch.setattr(
        tasks,
        "_run_automatic_meeting_intelligence_stage",
        lambda **k: observed.append(_view(engine, recording_id)),
    )
    monkeypatch.setattr(tasks, "update_recording_status", update_recording_status)
    with Session(engine) as session:
        _run_task(monkeypatch, session, recording_id=recording_id)
    after = _view(engine, recording_id)
    for view in observed[:2]:
        assert view.pop("status") == RecordingStatus.PROCESSING
        assert view == {key: value for key, value in before.items() if key != "status"}
    assert observed[2]["pending"] is False
    # Readable while notes run, then an ordinary transcript again.
    assert observed[2]["published"] is True
    assert after["published"] is False
    assert after["status"] == RecordingStatus.PROCESSED
    assert after["text"] == ("" if empty else "Replacement words Changed cluster")
    assert after["canonical"] == (
        [] if empty or not canonical else ["Replacement words", "Changed cluster"]
    )
    assert after["speakers"] == (
        []
        if empty
        else [
            ("SPEAKER_00", None, None),
            ("SPEAKER_01", None, None),
        ]
    )
    for field in ("notes", "focus", "edge", "suggestions"):
        assert after[field] == before[field]
    assert (after["chat"], after["context"], after["names"]) == ([], [], [])


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("failure", ["prepare", "speakers", "publication", "notes"])
def test_failure_boundary_and_retry(
    worker_database: tuple[Engine, int],
    monkeypatch: pytest.MonkeyPatch,
    canonical: bool,
    failure: str,
) -> None:
    engine, recording_id = worker_database
    _configure(monkeypatch, canonical)
    before = _view(engine, recording_id)
    failures: list[str] = []

    def fail(*args: Any, **kwargs: Any) -> None:
        failures.append(failure)
        raise RuntimeError("candidate failed")

    target, attribute = {
        "prepare": (pipeline, "_prepare_pipeline_run"),
        "speakers": (pipeline, "assign_and_identify_speakers"),
        "publication": (pipeline, "_persist_final_transcript"),
        "notes": (tasks, "_run_automatic_meeting_intelligence_stage"),
    }[failure]
    with monkeypatch.context() as patch:
        patch.setattr(target, attribute, fail)
        with Session(engine) as session:
            _run_task(patch, session, recording_id=recording_id)
    after = _view(engine, recording_id)
    assert failures == [failure]
    if failure == "notes":
        assert after["text"] == "Replacement words Changed cluster"
        assert not after["pending"]
        assert after["notes"] == before["notes"]
        return
    assert after.pop("status") == RecordingStatus.ERROR
    before.pop("status")
    # The retained publication is handed back as the completed transcript it was.
    assert after.pop("pending") is False
    assert after.pop("transcript_status") == "completed"
    before.pop("pending")
    before.pop("transcript_status")
    assert after == before
    # Regenerating notes on the failed recording ends with this status update.
    with Session(engine) as session:
        update_recording_status(session, recording_id)
    assert _view(engine, recording_id)["status"] == RecordingStatus.PROCESSED


@pytest.mark.parametrize("canonical", [False, True])
def test_vad_empty_replacement_retires_old_authority(
    worker_database: tuple[Engine, int],
    monkeypatch: pytest.MonkeyPatch,
    canonical: bool,
) -> None:
    engine, recording_id = worker_database
    _configure(monkeypatch, canonical)
    with Session(engine) as session:
        transcript = session.exec(select(Transcript)).one()
        transcript.language_detection = {
            "detected": "fr",
            "used": "en",
            "engine": "whisper",
        }
        session.add(transcript)
        session.commit()
    monkeypatch.setattr(
        sys.modules["backend.processing.vad"],
        "mute_non_speech_segments",
        lambda *a, **k: (True, 0.0),
    )
    with Session(engine) as session:
        _run_task(monkeypatch, session, recording_id=recording_id)
    after = _view(engine, recording_id)
    assert (after["text"], after["canonical"], after["speakers"], after["pending"]) == (
        "",
        [],
        [],
        False,
    )
    assert after["notes"] == "Previous notes"
    assert after["focus"] == "Keep my focus"
    with Session(engine) as session:
        assert session.exec(select(Transcript)).one().language_detection is None


def test_embedding_progress_commit_precedes_replacement_cleanup(
    worker_database: tuple[Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, recording_id = worker_database
    _configure(monkeypatch, True)
    extraction_views: list[dict[str, Any]] = []

    def extract(*args: Any, **kwargs: Any) -> dict[str, Any]:
        extraction_views.append(_view(engine, recording_id))
        return {}

    monkeypatch.setattr(
        sys.modules["backend.processing.embedding_core"], "extract_embeddings", extract
    )
    monkeypatch.setattr(
        pipeline, "_run_final_diarization_stage", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        pipeline,
        "transcription_audio_after_diarization",
        lambda *a, **k: "/tmp/voice.wav",
    )
    config = SimpleNamespace(
        provider="openai",
        merged_config={
            "enable_auto_voiceprints": True,
            "enable_vad": True,
            "enable_diarization": True,
        },
    )
    with Session(engine) as session:
        _run_task(monkeypatch, session, recording_id=recording_id, llm_config=config)
    assert len(extraction_views) == 1
    assert extraction_views[0]["text"] == "Previous words"
    assert extraction_views[0]["speakers"][0][1] == "Wrong old identity"
    assert not _view(engine, recording_id)["pending"]


def test_first_processing_keeps_immediate_raw_publication(
    worker_database: tuple[Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, existing_id = worker_database
    _configure(monkeypatch, False)
    with Session(engine) as session:
        existing = session.get(Recording, existing_id)
        fresh = Recording(
            name="Fresh",
            audio_path="/tmp/fresh.wav",
            duration_seconds=60,
            user_id=existing.user_id,
            status=RecordingStatus.QUEUED,
        )
        session.add(fresh)
        session.flush()
        recording_id = int(fresh.id)
        session.add(Transcript(recording_id=recording_id))
        session.commit()
    observations: list[str] = []

    def assign(*args: Any, **kwargs: Any) -> None:
        observations.append(_view(engine, recording_id)["text"])

    monkeypatch.setattr(pipeline, "assign_and_identify_speakers", assign)
    with Session(engine) as session:
        _run_task(monkeypatch, session, recording_id=recording_id)
    assert observations == ["Replacement words Changed cluster"]
    assert not _view(engine, recording_id)["pending"]


def test_failed_real_learning_and_drift_cleanup_roll_back(
    worker_database: tuple[Engine, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, recording_id = worker_database
    _configure(monkeypatch, True)
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        person = GlobalSpeaker(
            name="Fresh person",
            user_id=recording.user_id,
            embedding=[1.0, 0.0],
            embedding_version=EMBEDDING_METHOD_VERSION,
        )
        session.add(person)
        session.flush()
        person_id = int(person.id)
        old = session.exec(select(RecordingSpeaker)).first()
        old.drift_refusals.append(
            RecordingSpeakerDriftRefusal(global_speaker_id=person_id)
        )
        session.commit()

    def extracted(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"SPEAKER_00": SimpleNamespace(embedding=[0.98, 0.2], speech_seconds=30)}

    monkeypatch.setattr(speaker_assignment, "_extract_speaker_voiceprints", extracted)
    original = speaker_assignment.assign_and_identify_speakers
    mutations_observed: list[bool] = []

    def assign_then_fail(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        session = args[0].session
        assert session.get(GlobalSpeaker, person_id).embedding != [1.0, 0.0]
        assert session.exec(select(RecordingSpeakerDriftRefusal)).all() == []
        session.flush()
        with Session(engine) as reader:
            assert reader.get(GlobalSpeaker, person_id).embedding == [1.0, 0.0]
            assert len(reader.exec(select(RecordingSpeakerDriftRefusal)).all()) == 1
        mutations_observed.append(True)
        raise RuntimeError("failure after actual learning and drift cleanup")

    monkeypatch.setattr(pipeline, "assign_and_identify_speakers", assign_then_fail)
    with Session(engine) as session:
        _run_task(monkeypatch, session, recording_id=recording_id)
    with Session(engine) as reader:
        assert reader.get(GlobalSpeaker, person_id).embedding == [1.0, 0.0]
        assert len(reader.exec(select(RecordingSpeakerDriftRefusal)).all()) == 1
    assert mutations_observed == [True]
    assert _view(engine, recording_id)["transcript_status"] == "completed"
    monkeypatch.setattr(pipeline, "assign_and_identify_speakers", original)
    mark_for_retry(engine)
    with Session(engine) as session:
        _run_task(monkeypatch, session, recording_id=recording_id)
    with Session(engine) as reader:
        assert reader.get(GlobalSpeaker, person_id).embedding != [1.0, 0.0]
        assert reader.exec(select(RecordingSpeakerDriftRefusal)).all() == []
    assert not _view(engine, recording_id)["pending"]
