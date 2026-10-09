"""Meeting chat must read the same transcript the notes and Meeting Edge read.

``LLMBackend.get_mapped_transcript_for_llm`` builds the transcript every chat
backend sends. The notes and Meeting Edge paths read canonical utterances and
resolve speaker names through ``build_recording_speaker_map``; these tests pin
chat to that same view, against real rows in SQLite.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import Session

from backend.processing.llm_backends.base import LLMBackend
from backend.tests.test_canonical_transcript_phase1 import (
    GLOBAL_SPEAKERS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)

NOW = "2026-05-19 00:00:00"
RECORDING_ID = 1


@pytest.fixture
def engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'chat-transcript.sqlite'}")
    with engine.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            PEOPLE_TAGS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
        ):
            connection.execute(text(schema))
        connection.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    is_archived, is_deleted, user_id
                ) VALUES (
                    :id, :now, :now, 'Planning', 'rec-public', 'meeting-uid',
                    '/tmp/planning.wav', 'PROCESSED', 0, 100, 0, 0, 1
                )
                """
            ),
            {"id": RECORDING_ID, "now": NOW},
        )
    monkeypatch.setattr("backend.core.db.get_sync_session", lambda: Session(engine))
    try:
        yield engine
    finally:
        engine.dispose()


def _insert_transcript(engine, segments: list[dict] | None) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    meeting_edge_status, notes_status, transcript_status
                ) VALUES (
                    :id, :now, :now, :id, '', :segments, 'idle', 'completed',
                    'completed'
                )
                """
            ),
            {
                "id": RECORDING_ID,
                "now": NOW,
                "segments": None if segments is None else json.dumps(segments),
            },
        )


def _insert_speaker(
    engine,
    label: str,
    *,
    local_name: str | None = None,
    name: str | None = None,
    global_name: str | None = None,
) -> None:
    speaker_id = int(label.removeprefix("SPEAKER_")) + 1
    with engine.begin() as connection:
        global_id = None
        if global_name is not None:
            global_id = speaker_id
            connection.execute(
                text(
                    "INSERT INTO global_speakers (id, created_at, updated_at, user_id, name) "
                    "VALUES (:id, :now, :now, 1, :name)"
                ),
                {"id": global_id, "now": NOW, "name": global_name},
            )
        connection.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    speaker_status, speaker_kind, identity_locked
                ) VALUES (
                    :id, :now, :now, :public_id, :recording_id, :global_id,
                    :label, :local_name, :name, 'active', 'automated', 0
                )
                """
            ),
            {
                "id": speaker_id,
                "now": NOW,
                "public_id": f"speaker-{speaker_id}",
                "recording_id": RECORDING_ID,
                "global_id": global_id,
                "label": label,
                "local_name": local_name,
                "name": name,
            },
        )


def _insert_utterance(
    engine, utterance_id: int, span_ms: tuple[int, int], label: str, words: str
) -> None:
    start_ms, end_ms = span_ms
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO transcript_utterances (
                    id, created_at, updated_at, public_id, recording_id, sort_key,
                    start_ms, end_ms, text, speaker_label, state, source_kind,
                    revision, overlap_rank, manual_text_locked,
                    manual_speaker_locked, speaker_assignment_source,
                    speaker_assignment_authority
                ) VALUES (
                    :id, :now, :now, :public_id, :recording_id, :sort_key,
                    :start_ms, :end_ms, :words, :label, 'finalized', 'final',
                    1, 0, 0, 0, 'diarization', 'automatic'
                )
                """
            ),
            {
                "id": utterance_id,
                "now": NOW,
                "public_id": f"utt-{utterance_id}",
                "recording_id": RECORDING_ID,
                "sort_key": f"{start_ms:012d}",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "words": words,
                "label": label,
            },
        )


def test_chat_reads_canonical_utterances_when_the_projection_is_empty(engine):
    # The projection is a cache of the canonical rows; when it is empty the
    # notes path still reads the utterances, so chat must not report "no
    # transcript" for the same recording.
    _insert_transcript(engine, segments=None)
    _insert_speaker(engine, "SPEAKER_00", name="Speaker 1")
    _insert_utterance(engine, 1, (0, 2500), "SPEAKER_00", "We ship on Friday.")
    _insert_utterance(engine, 2, (65000, 67000), "SPEAKER_00", "Docs are done.")

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript == (
        "[00:00] Speaker 1: We ship on Friday.\n[01:05] Speaker 1: Docs are done."
    )


def test_chat_prefers_canonical_text_over_a_stale_projection(engine):
    _insert_transcript(
        engine,
        segments=[
            {
                "id": "utt-1",
                "start": 0.0,
                "end": 2.5,
                "speaker": "SPEAKER_00",
                "text": "We ship on Monday.",
            }
        ],
    )
    _insert_speaker(engine, "SPEAKER_00", name="Speaker 1")
    _insert_utterance(engine, 1, (0, 2500), "SPEAKER_00", "We ship on Friday.")

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert "We ship on Friday." in transcript
    assert "Monday" not in transcript


def test_chat_names_speakers_the_way_the_notes_do(engine):
    # A rename stores the new name in local_name and clears the deprecated
    # name column; a link to a global speaker clears both. Chat used to read
    # only the deprecated column, so both speakers came out as "None".
    _insert_transcript(
        engine,
        segments=[
            {"id": "utt-1", "start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
            {"id": "utt-2", "start": 2.0, "end": 4.0, "speaker": "SPEAKER_01"},
        ],
    )
    _insert_speaker(engine, "SPEAKER_00", local_name="Priya")
    _insert_speaker(engine, "SPEAKER_01", global_name="Dana")
    _insert_utterance(engine, 1, (0, 2000), "SPEAKER_00", "Is the budget final?")
    _insert_utterance(engine, 2, (2000, 4000), "SPEAKER_01", "Yes, signed off.")

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript == (
        "[00:00] Priya: Is the budget final?\n[00:02] Dana: Yes, signed off."
    )


def test_chat_falls_back_to_the_projection_without_canonical_rows(engine):
    # A legacy recording that predates the canonical cutover has only the
    # projection; it must stay readable in chat.
    _insert_transcript(
        engine,
        segments=[
            {"start": 3.0, "end": 4.0, "speaker": "SPEAKER_00", "text": "Hello."}
        ],
    )
    _insert_speaker(engine, "SPEAKER_00", local_name="Priya")

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript == "[00:03] Priya: Hello."


def test_chat_reports_a_recording_without_any_transcript(engine):
    _insert_transcript(engine, segments=None)

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript == "Diarized transcript not found."


def _count_queries(engine) -> int:
    statements: list[str] = []

    def _record(conn, cursor, statement, *_):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    return len(statements)


def test_chat_query_count_does_not_grow_with_linked_speakers(engine):
    # Every chat turn rebuilds the transcript. Loading each linked global
    # speaker lazily cost a query per speaker (plus its tags), so a meeting
    # of twelve linked speakers ran 32 queries where one speaker ran a few.
    _insert_transcript(engine, segments=None)
    _insert_speaker(engine, "SPEAKER_00", global_name="Person 0")
    _insert_utterance(engine, 1, (0, 1000), "SPEAKER_00", "line 1")
    one_speaker = _count_queries(engine)

    for index in range(1, 12):
        _insert_speaker(engine, f"SPEAKER_{index:02d}", global_name=f"Person {index}")
        _insert_utterance(
            engine,
            index + 1,
            (index * 1000, index * 1000 + 900),
            f"SPEAKER_{index:02d}",
            f"line {index + 1}",
        )

    assert _count_queries(engine) == one_speaker
