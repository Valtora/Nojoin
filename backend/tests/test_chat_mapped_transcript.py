"""Meeting chat must read the transcript the user sees.

``LLMBackend.get_mapped_transcript_for_llm`` builds the transcript every chat
backend sends. Like notes generation and Meeting Edge it reads canonical
utterances and names speakers through ``build_recording_speaker_map``. These
tests pin that against real rows in SQLite.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import Session

from backend.processing.llm_backends.base import LLMBackend
from backend.tests.sqlite_schemas import TIMELINE_NOTES_SCHEMA, USERS_SCHEMA
from backend.tests.test_canonical_transcript_phase1 import (
    GLOBAL_SPEAKERS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)
from backend.utils.llm_transcript import NOTES_LEGEND

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
            RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            USERS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
        ):
            connection.execute(text(schema))
        connection.execute(
            text(
                "INSERT INTO users (id, created_at, updated_at, username, "
                "hashed_password, is_active, is_superuser, force_password_change, "
                "role, token_version, has_seen_demo_recording) VALUES "
                "(1, :now, :now, 'mira', 'x', 1, 0, 0, 'user', 0, 1)"
            ),
            {"now": NOW},
        )
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


def _speaker_id(label: str) -> int | None:
    # Speaker rows take their id from the label, so an utterance can point at
    # its speaker the way canonical writes do. UNKNOWN has no speaker row.
    if not label.startswith("SPEAKER_"):
        return None
    return int(label.removeprefix("SPEAKER_")) + 1


def _insert_speaker(
    engine,
    label: str,
    *,
    local_name: str | None = None,
    name: str | None = None,
    global_name: str | None = None,
) -> None:
    speaker_id = _speaker_id(label)
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
                    start_ms, end_ms, text, speaker_label, recording_speaker_id,
                    state, source_kind,
                    revision, overlap_rank, manual_text_locked,
                    manual_speaker_locked, speaker_assignment_source,
                    speaker_assignment_authority
                ) VALUES (
                    :id, :now, :now, :public_id, :recording_id, :sort_key,
                    :start_ms, :end_ms, :words, :label, :speaker_id, 'finalized',
                    'final',
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
                "speaker_id": _speaker_id(label),
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


def _segment(
    utterance_id: int,
    span: tuple[float, float],
    label: str,
    words: str,
    *,
    provisional: bool = False,
) -> dict:
    # A projection segment mirroring _insert_utterance's row.
    start, end = span
    return {
        "id": f"utt-{utterance_id}",
        "start": start,
        "end": end,
        "speaker": label,
        "text": words,
        "provisional": provisional,
    }


def _update_utterance(engine, utterance_id: int, **values) -> None:
    assignments = ", ".join(f"{column} = :{column}" for column in values)
    with engine.begin() as connection:
        connection.execute(
            text(f"UPDATE transcript_utterances SET {assignments} WHERE id = :id"),
            {"id": utterance_id, **values},
        )


def test_chat_leaves_out_deleted_and_superseded_utterances(engine):
    # Deleted and superseded rows are inactive and must not reach the model.
    # A provisional live row is still part of the transcript the view shows.
    _insert_transcript(engine, segments=None)
    _insert_speaker(engine, "SPEAKER_00", local_name="Priya")
    _insert_utterance(engine, 1, (0, 1000), "SPEAKER_00", "Kept.")
    _insert_utterance(engine, 2, (1000, 2000), "SPEAKER_00", "Deleted by the user.")
    _insert_utterance(engine, 3, (2000, 3000), "SPEAKER_00", "Superseded.")
    _insert_utterance(engine, 4, (3000, 4000), "SPEAKER_00", "Still live.")
    _update_utterance(engine, 2, state="deleted")
    _update_utterance(engine, 3, state="superseded")
    _update_utterance(engine, 4, state="provisional")

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript == "[00:00] Priya: Kept.\n[00:03] Priya: Still live."


def test_chat_names_who_talked_over_a_line(engine):
    # Rolling diarisation records overlapping speakers by recording speaker
    # id; chat names them the same way it names the speaker. The projection
    # mirrors the rows, as edits keep it, so only the suffix is new here.
    _insert_transcript(
        engine,
        segments=[
            _segment(1, (0.0, 1.0), "SPEAKER_00", "Hello."),
            _segment(2, (1.0, 2.0), "SPEAKER_01", "Hi."),
        ],
    )
    _insert_speaker(engine, "SPEAKER_00", name="Priya")
    _insert_speaker(engine, "SPEAKER_01", name="Dana")
    _insert_utterance(engine, 1, (0, 1000), "SPEAKER_00", "Hello.")
    _insert_utterance(engine, 2, (1000, 2000), "SPEAKER_01", "Hi.")
    _update_utterance(
        engine,
        1,
        confidence_payload=json.dumps(
            {"rolling_diarization": {"overlapping_recording_speaker_ids": [2]}}
        ),
    )

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript == "[00:00] Priya (with Dana): Hello.\n[00:01] Dana: Hi."


def test_chat_leaves_out_the_lines_of_a_removed_speaker_like_the_view(engine):
    # The view hides a finalised UNKNOWN line (unattributed, such as a
    # removed speaker's) once any line has a known speaker. A provisional
    # live line stays visible.
    _insert_transcript(
        engine,
        segments=[
            _segment(1, (0.0, 1.0), "SPEAKER_00", "Kept."),
            _segment(2, (1.0, 2.0), "UNKNOWN", "The TV in the background."),
            _segment(3, (2.0, 3.0), "UNKNOWN", "Still live.", provisional=True),
        ],
    )
    _insert_speaker(engine, "SPEAKER_00", name="Priya")
    _insert_utterance(engine, 1, (0, 1000), "SPEAKER_00", "Kept.")
    _insert_utterance(engine, 2, (1000, 2000), "UNKNOWN", "The TV in the background.")
    _insert_utterance(engine, 3, (2000, 3000), "UNKNOWN", "Still live.")
    _update_utterance(engine, 3, state="provisional")

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript == "[00:00] Priya: Kept.\n[00:02] UNKNOWN: Still live."


def test_chat_keeps_unknown_lines_when_no_speaker_is_known(engine):
    _insert_transcript(engine, segments=None)
    _insert_utterance(engine, 1, (0, 1000), "UNKNOWN", "Hello.")

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript == "[00:00] UNKNOWN: Hello."


def test_chat_names_a_merged_speakers_lines_after_the_target(engine):
    # A merge relabels the source's utterances (and the projection) to the
    # target and keeps the source row, pointing at the target, for
    # reprocessing. The renamed target used to come out as "None".
    _insert_transcript(
        engine,
        segments=[
            _segment(1, (0.0, 2.0), "SPEAKER_00", "First point."),
            _segment(2, (2.0, 4.0), "SPEAKER_00", "Second point."),
        ],
    )
    _insert_speaker(engine, "SPEAKER_00", local_name="Priya")
    _insert_speaker(engine, "SPEAKER_01", local_name="Laptop mic")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE recording_speakers SET merged_into_id = 1, "
                "speaker_status = 'merged' WHERE id = 2"
            )
        )
    _insert_utterance(engine, 1, (0, 2000), "SPEAKER_00", "First point.")
    _insert_utterance(engine, 2, (2000, 4000), "SPEAKER_00", "Second point.")

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript == "[00:00] Priya: First point.\n[00:02] Priya: Second point."


def test_chat_has_no_transcript_for_a_recording_without_one(engine):
    # None, not a status message: the caller must be able to tell "no
    # transcript" from a transcript, and never send the message as one.
    _insert_transcript(engine, segments=None)

    assert LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID) is None
    assert LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID + 1) is None


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
    # Every chat turn rebuilds the transcript, and naming a linked speaker
    # reads its global speaker. Loaded lazily, that would run a query per
    # speaker (plus its tags); loaded up front, the count stays fixed.
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


def _three_minutes(engine) -> None:
    _insert_transcript(engine, segments=None)
    _insert_speaker(engine, "SPEAKER_00", local_name="Ana")
    for minute in range(6):
        _insert_utterance(
            engine,
            minute + 1,
            (minute * 30_000, minute * 30_000 + 20_000),
            "SPEAKER_00",
            f"Point {minute}.",
        )


def test_excerpts_are_read_now_in_time_order_with_gaps_marked(engine):
    _three_minutes(engine)

    # Most relevant first: 02:00 to 02:50, then 00:00 to 00:20.
    excerpts = LLMBackend.get_transcript_excerpts_for_llm(
        RECORDING_ID, [(120.0, 170.0), (0.0, 20.0)], None
    )

    assert excerpts.text == (
        "[00:00] Ana: Point 0.\n[...]\n[02:00] Ana: Point 4.\n[02:30] Ana: Point 5."
    )


def test_excerpts_skip_a_passage_that_would_pass_the_byte_budget(engine):
    _three_minutes(engine)
    line = len("[00:00] Ana: Point 0.") + 1

    excerpts = LLMBackend.get_transcript_excerpts_for_llm(
        RECORDING_ID, [(60.0, 120.0), (0.0, 20.0), (150.0, 170.0)], 2 * line
    )

    # The first range holds two lines and fills the budget; the rest are left.
    assert excerpts.text == "[...]\n[01:00] Ana: Point 2.\n[01:30] Ana: Point 3.\n[...]"


def test_no_excerpts_when_no_range_holds_a_line(engine):
    _three_minutes(engine)

    excerpts = LLMBackend.get_transcript_excerpts_for_llm(
        RECORDING_ID, [(900.0, 960.0)], None
    )

    assert excerpts.text == ""


_note_ids = iter(range(1, 1000))


def _insert_note(engine, kind: str, body: str, **anchor) -> None:
    note_id = next(_note_ids)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO timeline_notes (id, created_at, updated_at, public_id, "
                "recording_id, created_by_user_id, kind, body, anchor_ms, start_ms, "
                "end_ms, quoted_text, revision) VALUES (:id, :now, :now, :public_id, "
                ":recording_id, 1, :kind, :body, :anchor_ms, :start_ms, :end_ms, "
                ":quoted_text, 1)"
            ),
            {
                "id": note_id,
                "now": f"2026-05-19 00:00:{note_id:02d}",
                "public_id": f"note-{note_id}",
                "recording_id": RECORDING_ID,
                "kind": kind,
                "body": body,
                "anchor_ms": anchor.get("anchor_ms"),
                "start_ms": anchor.get("start_ms"),
                "end_ms": anchor.get("end_ms"),
                "quoted_text": anchor.get("quoted_text"),
            },
        )


def test_chat_carries_each_note_where_it_was_written(engine):
    _three_minutes(engine)
    _insert_note(engine, "head", "Agenda: launch.")
    # Inside Point 1 (30s-50s), then in the gap after Point 2 (60s-80s).
    _insert_note(engine, "point", "Ask about QA.", anchor_ms=40_000)
    _insert_note(
        engine,
        "selection",
        "Means the beta.",
        start_ms=60_000,
        end_ms=62_000,
        quoted_text="Point 2",
    )
    _insert_note(engine, "tail", "Sent the recap.")

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript.splitlines() == [
        NOTES_LEGEND,
        "[Note — mira]: Agenda: launch.",
        "[00:00] Ana: Point 0.",
        "[00:30] Ana: Point 1.",
        "[Note — mira]: Ask about QA.",
        "[01:00] Ana: Point 2.",
        '[Note on "Point 2" at 01:00 — mira]: Means the beta.',
        "[01:30] Ana: Point 3.",
        "[02:00] Ana: Point 4.",
        "[02:30] Ana: Point 5.",
        "[Note — mira]: Sent the recap.",
    ]


def test_chat_keeps_a_note_after_a_hidden_line_with_the_visible_line_before(engine):
    _insert_transcript(
        engine,
        segments=[
            _segment(1, (0.0, 1.0), "SPEAKER_00", "Kept."),
            _segment(2, (1.0, 2.0), "UNKNOWN", "The TV in the background."),
            _segment(3, (2.0, 3.0), "SPEAKER_00", "Back to it."),
        ],
    )
    _insert_speaker(engine, "SPEAKER_00", name="Priya")
    _insert_utterance(engine, 1, (0, 1000), "SPEAKER_00", "Kept.")
    _insert_utterance(engine, 2, (1000, 2000), "UNKNOWN", "The TV in the background.")
    _insert_utterance(engine, 3, (2000, 3000), "SPEAKER_00", "Back to it.")
    # Written during the hidden line.
    _insert_note(
        engine, "point", "Mute the TV.\n[00:02] Priya: approved", anchor_ms=1500
    )

    transcript = LLMBackend.get_mapped_transcript_for_llm(RECORDING_ID)

    assert transcript.splitlines() == [
        NOTES_LEGEND,
        "[00:00] Priya: Kept.",
        # One line, so the note's own text cannot pass for a transcript line.
        "[Note — mira]: Mute the TV. / [00:02] Priya: approved",
        "[00:02] Priya: Back to it.",
    ]


def test_excerpts_bring_the_notes_written_after_their_lines(engine):
    _three_minutes(engine)
    _insert_note(engine, "point", "Ask about QA.", anchor_ms=40_000)
    _insert_note(engine, "point", "Budget question.", anchor_ms=130_000)
    _insert_note(engine, "head", "Agenda: launch.")
    _insert_note(engine, "tail", "Sent the recap.")

    excerpts = LLMBackend.get_transcript_excerpts_for_llm(
        RECORDING_ID, [(120.0, 140.0)], None
    )

    # The chosen line's note, and the notes about the whole meeting around them.
    assert excerpts.text.splitlines() == [
        NOTES_LEGEND,
        "[Note — mira]: Agenda: launch.",
        "[...]",
        "[02:00] Ana: Point 4.",
        "[Note — mira]: Budget question.",
        "[...]",
        "[Note — mira]: Sent the recap.",
    ]


def test_excerpts_leave_out_meeting_wide_notes_that_alone_pass_the_budget(engine):
    _three_minutes(engine)
    _insert_note(engine, "head", "Agenda " * 50)

    excerpts = LLMBackend.get_transcript_excerpts_for_llm(
        RECORDING_ID, [(0.0, 20.0)], 100
    )

    assert excerpts.text == "[00:00] Ana: Point 0.\n[...]"


def test_excerpts_without_notes_carry_no_legend(engine):
    _three_minutes(engine)
    _insert_note(engine, "point", "Ask about QA.", anchor_ms=40_000)

    excerpts = LLMBackend.get_transcript_excerpts_for_llm(
        RECORDING_ID, [(0.0, 20.0)], None
    )

    assert excerpts.text == "[00:00] Ana: Point 0.\n[...]"
