"""export_timeline.sql against the migrated schema, read-only, on PostgreSQL.

The Screen study (#653) exports each study meeting's speech timeline from
live with this one SELECT. It must run on the schema Alembic builds, in a
read-only transaction, and return numbers and ids only: no transcript text,
no speaker or person names, no titles. Skips unless NOJOIN_TEST_POSTGRES_URL
names a server; CI sets it for the backend suite.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlmodel import Session

from backend.core.db import driver_urls
from backend.models.pipeline import (
    RecordingAudioChunk,
    TranscriptUtterance,
    TranscriptUtteranceEvent,
    TranscriptUtteranceState,
)
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.tag import RecordingTag, Tag
from backend.models.user import User
from backend.tests.postgres_setup import migrate_empty_database

SQL = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "speaker_frames"
    / "export_timeline.sql"
)
SECRET_TEXT = "the budget is confidential"
PERSON = "Alice Example"
TITLE = "Quarterly planning"


def _psycopg_url(url: str) -> str:
    return (
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


@pytest.fixture
def database(postgres_test_url: str) -> Iterator[str]:
    """A database of its own at Alembic's head."""
    admin = _psycopg_url(postgres_test_url)
    name = f"nojoin_frames_{uuid4().hex[:12]}"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = make_url(admin).set(database=name).render_as_string(hide_password=False)
    try:
        migrate_empty_database(url, "head")
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _line(
    session: Session, recording: int, span: tuple[int, int], **fields: object
) -> int:
    line = TranscriptUtterance(
        recording_id=recording,
        sort_key=f"{span[0]:012d}",
        start_ms=span[0],
        end_ms=span[1],
        text=SECRET_TEXT,
        state=fields.pop("state", TranscriptUtteranceState.FINALIZED),
        **fields,
    )
    session.add(line)
    session.flush()
    return int(line.id)  # type: ignore[arg-type]


def _finalize(session: Session, recording: int, line: int, speaker: str) -> None:
    session.add(
        TranscriptUtteranceEvent(
            recording_id=recording,
            utterance_id=line,
            event_type="finalize",
            source="finalize",
            new_values={"speaker": speaker, "text": SECRET_TEXT},
        )
    )
    session.flush()


def _chunk(session: Session, recording: int, sequence: int) -> None:
    session.add(
        RecordingAudioChunk(
            recording_id=recording,
            sequence_no=sequence,
            absolute_start_ms=sequence * 10_000,
            absolute_end_ms=sequence * 10_000 + 10_000,
            duration_ms=10_000,
            sample_rate_hz=16_000,
            channel_count=2,
            byte_size=1,
            sha256="0" * 64,
            storage_path=f"/data/chunks/{sequence}.flac",
        )
    )


def _seed(url: str) -> SimpleNamespace:
    """One study meeting, reviewed; an untagged one and a binned one."""
    importlib.import_module("backend.models.registry")
    engine = create_engine(driver_urls(url)[1])
    with Session(engine) as session:
        user = User(username="owner", hashed_password="x")
        session.add(user)
        session.flush()
        study, plain, binned = (
            Recording(
                name=TITLE,
                audio_path=f"/data/r{k}.wav",
                user_id=user.id,
                is_deleted=k == 2,
            )
            for k in range(3)
        )
        person = GlobalSpeaker(name=PERSON, user_id=user.id)
        tag = Tag(name="study-teams", user_id=user.id)
        session.add_all([study, plain, binned, person, tag])
        session.flush()
        session.add_all(
            [RecordingTag(recording_id=r.id, tag_id=tag.id) for r in (study, binned)]
        )
        alice = RecordingSpeaker(
            recording_id=study.id,
            diarization_label="SPEAKER_00",
            global_speaker_id=person.id,
        )
        other = RecordingSpeaker(
            recording_id=study.id,
            diarization_label="SPEAKER_01",
            local_name="Bob Example",
        )
        fixed = RecordingSpeaker(
            recording_id=study.id,
            diarization_label="MANUAL_ab12cd34",
            global_speaker_id=person.id,
        )
        session.add_all([alice, other, fixed])
        session.flush()
        first = _line(
            session,
            study.id,
            (0, 2_000),
            diarization_label="SPEAKER_00",
            speaker_label="SPEAKER_00",
            recording_speaker_id=alice.id,
        )
        # Finalized twice; the export takes the latest.
        _finalize(session, study.id, first, "SPEAKER_01")
        _finalize(session, study.id, first, "SPEAKER_00")
        # The pipeline said speaker 1; review moved the line to Alice.
        second = _line(
            session,
            study.id,
            (2_000, 5_000),
            diarization_label="SPEAKER_01",
            speaker_label="MANUAL_ab12cd34",
            recording_speaker_id=fixed.id,
            manual_speaker_locked=True,
        )
        _finalize(session, study.id, second, "SPEAKER_01")
        # No speaker row on the line: found by its label.
        third = _line(
            session,
            study.id,
            (5_000, 6_000),
            diarization_label="SPEAKER_01",
            speaker_label="SPEAKER_01",
            overlap_rank=1,
        )
        _finalize(session, study.id, third, "SPEAKER_01")
        _line(
            session,
            study.id,
            (6_000, 7_000),
            diarization_label="SPEAKER_00",
            state=TranscriptUtteranceState.SUPERSEDED,
        )
        _line(session, plain.id, (0, 1_000), diarization_label="SPEAKER_00")
        _line(session, binned.id, (0, 1_000), diarization_label="SPEAKER_00")
        for sequence in (1, 0):
            _chunk(session, study.id, sequence)
        session.commit()
        ids = SimpleNamespace(study=study.public_id, person=person.id, other=other.id)
    engine.dispose()
    return ids


def _export(url: str) -> str:
    with psycopg.connect(
        _psycopg_url(url), options="-c default_transaction_read_only=on"
    ) as conn:
        rows = conn.execute(SQL.read_text(encoding="utf-8")).fetchall()
    return "\n".join(json.dumps(row[0]) for row in rows)


def test_the_export_is_one_select_that_changes_nothing() -> None:
    sql = re.sub(r"--[^\n]*", "", SQL.read_text(encoding="utf-8"))
    statements = [s for s in sql.split(";") if s.strip()]
    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("WITH")
    assert not re.search(
        r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|GRANT|COPY|CALL)\b",
        sql,
        re.I,
    )


def test_the_export_reads_the_migrated_schema_and_leaves_out_text_and_names(
    database: str,
) -> None:
    ids = _seed(database)
    exported = _export(database)

    for secret in (SECRET_TEXT, PERSON, "Bob Example", TITLE, "SPEAKER_", "MANUAL_"):
        assert secret not in exported
    rows = [json.loads(line) for line in exported.splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["recording"] == ids.study
    assert row["platform"] == "teams"
    assert row["platform_order"] == 1
    assert row["chunks"] == [[0, 0], [1, 10_000]]
    # Labels are numbered within the recording: SPEAKER_00 is 1, SPEAKER_01 is 2.
    assert row["utterances"] == [
        [0, 2_000, 1, 1, ids.person, 0],
        [2_000, 5_000, 2, 2, ids.person, 0],
        [5_000, 6_000, 2, 2, -ids.other, 1],
    ]
