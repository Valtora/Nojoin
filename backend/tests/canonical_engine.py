"""A canonical-transcript engine on SQLite and PostgreSQL, with small row helpers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlmodel import Session, select

from backend.core.db import driver_urls
from backend.models.pipeline import ProcessingRunKind
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.tests import test_canonical_transcript_phase1 as schemas
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline import append_utterances_from_segments

SCHEMA = "canonical_engine_test"


@pytest.fixture(params=["sqlite", "postgres"])
def publication_engine(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[Engine]:
    """Run identical transaction semantics on SQLite and the worker PG driver."""
    if request.param == "postgres":
        url = request.getfixturevalue("postgres_test_url")
        engine = create_engine(
            driver_urls(url)[1],
            connect_args={"options": f"-csearch_path={SCHEMA},public"},
        )
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        fresh_schema(engine, SCHEMA)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'publication.db'}")
        with engine.begin() as connection:
            for name, schema in vars(schemas).items():
                if name.endswith("_SCHEMA"):
                    connection.execute(text(schema))
    try:
        yield engine
    finally:
        engine.dispose()


def _recording(session: Session) -> int:
    recording = Recording(
        name="Capture",
        audio_path="/tmp/publication.wav",
        status=RecordingStatus.UPLOADING,
    )
    session.add(recording)
    session.flush()
    assert recording.id is not None
    session.add(Transcript(recording_id=recording.id, text="", segments=[]))
    session.commit()
    return recording.id


def _transcript(session: Session, recording_id: int) -> Transcript:
    return session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).one()


def _append(
    session: Session, recording_id: int, *, prefix: str = "line", count: int = 3
) -> list[Any]:
    return append_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[
            {
                "id": f"{prefix}-{index}",
                "start": float(index),
                "end": float(index + 1),
                "text": f"{prefix} {index}",
                "speaker": "UNKNOWN",
                "segment_source": "live",
            }
            for index in range(count)
        ],
        run_kind=ProcessingRunKind.LIVE,
        source="live",
        idempotency_key=prefix,
    )
