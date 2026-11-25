"""The final pass reads the lines a person edited from the database and leaves
them to the person (#813): ``final_merge_config`` on a real schema, then the
real merge."""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.tests.sqlite_schemas import TRANSCRIPT_UTTERANCES_SCHEMA
from backend.tests.storage_hygiene_harness import add_recording
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.tests.test_sentence_start import OVERLAP, TOKENS, _whisper
from backend.tests.test_transcript_utils import FakeDiarization
from backend.utils.canonical_pipeline.preservation import preserve_human_segments
from backend.worker.tasks.final_segments import (
    combine_and_consolidate_segments,
    final_merge_config,
)

# The line holding "so" (1.05-1.3 s), the reply word the correction moves.
SO_LINE_MS = (1000, 1300)
# The line "It is done." before it, which ends the sentence the reply follows.
DONE_LINE_MS = (0, 1000)


@pytest.fixture(params=["sqlite", "postgres"])
def database(request, tmp_path) -> Iterator[tuple[Session, int, int]]:
    """A session, this recording's id and another recording's id."""
    importlib.import_module("backend.models.registry")
    if request.param == "postgres":
        engine = request.getfixturevalue("settling_engine")
        ids = (add_recording(engine, "a.wav"), add_recording(engine, "b.wav"))
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'edited-lines.sqlite3'}")
        with engine.begin() as connection:
            connection.execute(text(TRANSCRIPT_UTTERANCES_SCHEMA))
        ids = (741, 742)
    with Session(engine) as session:
        yield session, *ids
    engine.dispose()


def _add_line(
    session: Session, recording_id: int, line: dict, span: tuple[int, int] = SO_LINE_MS
) -> None:
    """One line over ``span``, "so" by default: ``line`` sets its state and edits."""
    payload = line.pop("payload", None)
    session.add(
        TranscriptUtterance(
            public_id=f"line-at-{span[0]}",
            recording_id=recording_id,
            sort_key="0",
            start_ms=span[0],
            end_ms=span[1],
            text=line.pop("text", "so" if span == SO_LINE_MS else "It is done."),
            state=line.pop("state", TranscriptUtteranceState.PROVISIONAL),
            source_kind="live",
            manual_speaker_locked=line.pop("speaker_locked", False),
            manual_text_locked=line.pop("text_locked", False),
            confidence_payload=payload,
        )
    )
    session.commit()
    assert not line


PROTECTED = {"human_protection": {"version": 1, "retained": True}, "asr_segments": []}


@pytest.mark.parametrize(
    ("line", "moved"),
    [
        ({"speaker_locked": True}, False),
        ({"text_locked": True}, False),
        ({"payload": PROTECTED}, False),
        ({"payload": {"asr_segments": [], "human_protection": None}}, True),
        ({"other": True, "speaker_locked": True}, True),
        ({"state": TranscriptUtteranceState.SUPERSEDED, "payload": PROTECTED}, True),
    ],
    ids=[
        "speaker-chosen",
        "wording-edited",
        "splits-protected",
        "machine-line",
        "another-recordings-edit",
        "superseded-edit",
    ],
)
def test_the_merge_leaves_only_this_recordings_active_edited_lines_alone(
    database, line, moved
):
    session, recording_id, other_id = database
    line = dict(line)
    _add_line(session, other_id if line.pop("other", False) else recording_id, line)

    config = final_merge_config({}, None, session, recording_id)
    result = combine_and_consolidate_segments(
        _whisper(TOKENS),
        FakeDiarization(OVERLAP),
        enable_diarization=True,
        recording_id=recording_id,
        config=config,
    )

    assert [seg["speaker"] for seg in result if "so" in seg["text"]] == [
        "T" if moved else "L"
    ]


def _merge(session: Session, recording_id: int) -> list[dict]:
    return combine_and_consolidate_segments(
        _whisper(TOKENS),
        FakeDiarization(OVERLAP),
        enable_diarization=True,
        recording_id=recording_id,
        config=final_merge_config({}, None, session, recording_id),
    )


@pytest.mark.parametrize(
    ("line", "moved"),
    [
        ({"text_locked": True, "payload": PROTECTED}, True),
        ({"payload": PROTECTED}, True),
        ({"speaker_locked": True}, False),
        ({"speaker_locked": True, "text_locked": True}, False),
        ({"text_locked": True, "text": "It is done and"}, False),
    ],
    ids=[
        "wording-edited",
        "splits-protected",
        "speaker-chosen",
        "speaker-and-wording",
        "wording-without-a-full-stop",
    ],
)
def test_a_reply_start_after_an_edited_line_moves_unless_its_speaker_was_chosen(
    database, line, moved
):
    """#1035: kept wording leaves the line's words the diariser's, and ends the
    sentence by its own last word; a chosen speaker is applied after the merge."""
    session, recording_id, _ = database
    _add_line(session, recording_id, dict(line), DONE_LINE_MS)

    result = _merge(session, recording_id)

    assert [seg["speaker"] for seg in result if "so" in seg["text"]] == [
        "T" if moved else "L"
    ]


def test_the_processed_transcript_keeps_the_edit_and_moves_the_reply_start(database):
    """The final lines over the edited "It is done.": the person's wording, then
    the whole reply with the person replying."""
    session, recording_id, _ = database
    _add_line(
        session, recording_id, {"text_locked": True, "payload": PROTECTED}, DONE_LINE_MS
    )
    lines = session.exec(
        select(TranscriptUtterance).where(
            TranscriptUtterance.recording_id == recording_id
        )
    ).all()

    shown = preserve_human_segments(lines, _merge(session, recording_id))

    assert [(seg["speaker"], seg["text"]) for seg in shown] == [
        ("L", "It is done."),
        ("T", "so we go"),
    ]
