"""Persist and inspect every dependency of an ordinary published transcript."""

from copy import deepcopy
from typing import Any

from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from backend.models.chat import ChatMessage
from backend.models.context_chunk import ContextChunk
from backend.models.document import Document
from backend.models.pipeline import (
    DiarizationWindowResult,
    DiarizationWindowTurn,
    ProcessingRun,
    RecordingSpeakerAlias,
    SpeakerCorrectionEvent,
    TranscriptUtterance,
    TranscriptUtteranceEvent,
)
from backend.models.recording import Recording
from backend.models.speaker import (
    GlobalSpeaker,
    RecordingSpeaker,
    RecordingSpeakerDriftRefusal,
)
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.transcript import Transcript
from backend.processing.embedding_version import EMBEDDING_METHOD_VERSION
from backend.processing.text_embedding_version import TEXT_EMBEDDING_DIMENSIONS

RETIRED_MODELS = (
    ChatMessage,
    ContextChunk,
    SpeakerCorrectionEvent,
    SpeakerNameEvent,
    TranscriptUtterance,
    RecordingSpeaker,
    ProcessingRun,
    DiarizationWindowResult,
    RecordingSpeakerAlias,
    RecordingSpeakerDriftRefusal,
    TranscriptUtteranceEvent,
    DiarizationWindowTurn,
)
Snapshot = dict[str, dict[tuple[Any, ...], dict[str, Any]]]


def seed_dependencies(engine: Engine, recording_id: int) -> int:
    """Include independent cleanup rows, cascades, and protected document context."""
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        person = GlobalSpeaker(
            name="Replacement person",
            user_id=recording.user_id,
            embedding=[1.0, 0.0],
            embedding_version=EMBEDDING_METHOD_VERSION,
        )
        run = ProcessingRun(recording_id=recording_id, idempotency_key="old-detached")
        document = Document(
            recording_id=recording_id,
            title="Keep attached document",
            file_path="/tmp/publication-context.txt",
        )
        session.add_all([person, run, document])
        session.flush()
        old = session.exec(
            select(RecordingSpeaker).order_by(RecordingSpeaker.id)
        ).first()
        utterance = session.exec(select(TranscriptUtterance)).first()
        window = DiarizationWindowResult(
            recording_id=recording_id,
            processing_run_id=run.id,
            window_start_ms=0,
            window_end_ms=1000,
            raw_payload={"old": True},
        )
        session.add(window)
        session.flush()
        session.add_all(
            [
                SpeakerCorrectionEvent(
                    recording_id=recording_id,
                    utterance_id=utterance.id,
                    source_recording_speaker_id=old.id,
                    payload={"old": True},
                ),
                RecordingSpeakerAlias(
                    recording_speaker_id=old.id,
                    alias_value="old-independent-alias",
                    source_run_id=run.id,
                ),
                TranscriptUtteranceEvent(
                    recording_id=recording_id,
                    utterance_id=utterance.id,
                    processing_run_id=run.id,
                    event_type="old-publication-event",
                ),
                DiarizationWindowTurn(
                    window_result_id=window.id,
                    local_speaker_key="SPEAKER_00",
                    start_ms=0,
                    end_ms=1000,
                    matched_recording_speaker_id=old.id,
                ),
                ContextChunk(
                    recording_id=recording_id,
                    document_id=document.id,
                    content="Keep document context",
                    embedding=[0.0] * TEXT_EMBEDDING_DIMENSIONS,
                ),
            ]
        )
        old.drift_refusals.append(
            RecordingSpeakerDriftRefusal(global_speaker_id=person.id)
        )
        session.add(old)
        session.commit()
        return int(person.id)


def publication_snapshot(engine: Engine) -> Snapshot:
    """Read actual persisted columns and primary keys using a separate session."""
    result: Snapshot = {}
    with Session(engine) as reader:
        for model in (*RETIRED_MODELS, Transcript, GlobalSpeaker, Document):
            mapper = inspect(model)
            rows = {}
            for row in reader.exec(select(model)).all():
                values = {}
                for column in mapper.columns:
                    value = getattr(row, column.key)
                    values[column.key] = deepcopy(
                        value.tolist() if hasattr(value, "tolist") else value
                    )
                key = tuple(values[column.key] for column in mapper.primary_key)
                rows[key] = values
            result[model.__tablename__] = rows
        result["recordings"] = {
            (row.id,): {"name": row.name}
            for row in reader.exec(select(Recording)).all()
        }
    return result


def assert_retired(before: Snapshot, after: Snapshot) -> None:
    """Old IDs must disappear even when canonical publication creates new rows."""
    for model in RETIRED_MODELS:
        table = model.__tablename__
        old = before[table]
        if model is ContextChunk:
            old = {key: row for key, row in old.items() if row["document_id"] is None}
        assert old, f"Missing seed for {table}"
        assert old.keys().isdisjoint(after[table]), (
            f"Old {table} IDs survived publication"
        )
    for table in ("documents", "context_chunks"):
        protected = {
            key: row
            for key, row in before[table].items()
            if table == "documents" or row["document_id"] is not None
        }
        assert protected
        assert {key: after[table][key] for key in protected} == protected
