"""Notes and the search index name a live speaker as the user named it.

Both read a transcript that may still be made of live lines: notes generated
on a recording whose processing failed, and the index rebuilt when a document
is attached during capture. The database is ``test_generate_notes_task``'s,
with its one line given to the live label ``LIVE_PROVISIONAL_2``, still current.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session

import backend.worker.tasks as tasks_module
from backend.tests.sqlite_schemas import TRANSCRIPT_UTTERANCES_SCHEMA
from backend.tests.test_generate_notes_task import (
    _create_notes_task_database,
    _run_generate_notes_task,
    _utc_now_naive,
)

# Notes and the index both embed text (#689).
pytestmark = pytest.mark.usefixtures("stub_text_embedding")

LIVE_LINE = {
    "start": 0.0,
    "end": 1.5,
    "speaker": "LIVE_PROVISIONAL_2",
    "text": "Hello team.",
}


def _database(tmp_path: Path) -> Any:
    engine = _create_notes_task_database(
        tmp_path,
        owner_settings={"llm_provider": "anthropic", "anthropic_model": "claude-test"},
        transcript_segments=[LIVE_LINE],
    )
    with engine.begin() as connection:
        # The live line itself, still current: processing has not replaced it.
        connection.execute(text(TRANSCRIPT_UTTERANCES_SCHEMA))
        connection.execute(
            text(
                "INSERT INTO transcript_utterances (id, created_at, updated_at, "
                "public_id, recording_id, sort_key, start_ms, end_ms, text, "
                "speaker_label, state, source_kind, revision, overlap_rank, "
                "manual_text_locked, manual_speaker_locked, "
                "speaker_assignment_source, speaker_assignment_authority) VALUES "
                "(1, :now, :now, 'live-1', 1, '000000000000', 0, 1500, "
                "'Hello team.', 'LIVE_PROVISIONAL_2', 'provisional', 'live', 1, 0, "
                "0, 0, 'live', 'provisional')"
            ),
            {"now": _utc_now_naive()},
        )
        connection.execute(
            text(
                "INSERT INTO speaker_name_events (created_at, updated_at, public_id, "
                "recording_id, kind, name, source, authority, surface, "
                "anchor_start_ms, anchor_end_ms, anchor_label) VALUES (:now, :now, "
                "'ev-live', 1, 'name', 'Alex', 'user', 40, 'live', 0, 1500, "
                "'LIVE_PROVISIONAL_2')"
            ),
            {"now": _utc_now_naive()},
        )
    return engine


def test_notes_name_the_live_speaker(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tasks_module.config_manager, "get_all", lambda: {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-valid")
    engine = _database(tmp_path)
    transcripts: list[str] = []

    class FakeLLM:
        def generate_meeting_notes(self, transcript: str, *args, **kwargs) -> str:
            transcripts.append(transcript)
            return "# Meeting Notes"

    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(
        "backend.processing.llm_backends.factory.get_llm_backend",
        lambda *args, **kwargs: FakeLLM(),
    )

    _run_generate_notes_task(engine)

    (transcript,) = transcripts
    assert "[00:00 - 00:01] Alex: Hello team." in transcript.splitlines()


def test_the_search_index_names_the_live_speaker(tmp_path, monkeypatch) -> None:
    engine = _database(tmp_path)
    url = str(engine.url)
    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    tasks_module.index_transcript_task._session = None
    try:
        tasks_module.index_transcript_task.run(1)
    finally:
        if tasks_module.index_transcript_task._session is not None:
            tasks_module.index_transcript_task._session.close()
        tasks_module.index_transcript_task._session = None
        engine.dispose()

    from sqlalchemy import create_engine

    reader = create_engine(url)
    with reader.connect() as connection:
        rows = connection.execute(
            text("SELECT content, meta FROM context_chunks")
        ).all()
    reader.dispose()
    assert [row[0] for row in rows] == ["Alex: Hello team.\n"]
    assert json.loads(rows[0][1])["speakers"] == ["Alex"]
