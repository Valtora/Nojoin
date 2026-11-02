"""An edited live line leaves processing on a processed speaker, never a live label.

The live cases edit the line through the API, then run the real speaker
assignment and canonical finalisation and read the result back through the
API. The settled capture runs through the real settling writer, on SQLite and
PostgreSQL. Names and text are made up.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine
from sqlmodel import Session

from backend.models.recording import Recording
from backend.tests.canonical_engine import _recording, _transcript
from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.tests.live_naming_fixtures import ONE, TWO
from backend.tests.test_live_settling_publication import _request
from backend.tests.test_manual_speaker_carry import (
    SHORT_PICK,
    SHORT_PICK_FINAL,
    _final,
    _publish,
    _read,
    _summary,
)
from backend.tests.test_manual_speaker_carry import (
    live_api as live_api,
)
from backend.utils.canonical_pipeline import (
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
    serialize_canonical_utterances,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    publish_live_settling,
)
from backend.utils.live_speaker_labels import live_speaker_display_name
from backend.utils.word_text import reconstruct_text_from_words
from backend.worker.tasks import speaker_assignment


async def _edit(client: AsyncClient, recording: Recording, index: int) -> None:
    line = (await _read(client, recording))[0][index]
    edited = await client.patch(
        f"/api/v1/transcripts/{recording.public_id}/utterances/{line['id']}/text",
        json={"text": "Edited words", "expected_revision": line["revision"]},
    )
    assert edited.status_code == 200, edited.text


def _no_live_labels(lines: list[dict[str, Any]]) -> bool:
    return not any(live_speaker_display_name(line["speaker"]) for line in lines)


@pytest.mark.anyio
@pytest.mark.parametrize("live_api", [SHORT_PICK], indirect=True)
@pytest.mark.parametrize("final", ["timed", "untimed", "no-speakers"])
async def test_an_edited_live_line_takes_the_final_speaker_of_its_words(
    live_api: tuple[Any, ...], final: str
) -> None:
    client, sessions, recording = live_api
    # The 8-10 s line of LIVE_PROVISIONAL_1, edited and never moved.
    await _edit(client, recording, 1)
    if final == "no-speakers":
        layout = [(start, end, "UNKNOWN") for start, end, _ in SHORT_PICK_FINAL]
    else:
        layout = SHORT_PICK_FINAL
    await _publish(sessions, recording, _final(layout, timed=final != "untimed"))
    lines, speakers = await _read(client, recording)
    edited = next(line for line in lines if line["text"] == "Edited words")
    assert edited["text_manually_edited"] is True
    assert edited["speaker_manually_edited"] is False
    assert _no_live_labels(lines)
    if final == "no-speakers":
        assert edited["speaker"] == "UNKNOWN"
        assert edited["recording_speaker_id"] is None
        assert speakers == []
        return
    other_voice = [(21.0, 25.0, "SPEAKER_01", False), (26.0, 30.0, "SPEAKER_01", False)]
    if final == "timed":
        assert _summary(lines) == [
            (0.05, 7.95, "SPEAKER_00", False),
            (8.0, 10.0, "SPEAKER_00", False),
            (10.05, 19.95, "SPEAKER_00", False),
            *other_voice,
        ]
        assert edited["recording_speaker_id"] == lines[0]["recording_speaker_id"]
    else:
        # A segment with no timed word is reviewed whole on the edited line.
        assert _summary(lines) == [(8.0, 10.0, "SPEAKER_00", False), *other_voice]
    assert edited["recording_speaker_id"] is not None
    assert edited["recording_speaker_id"] != lines[-1]["recording_speaker_id"]


def _runs(*runs: tuple[int, str]) -> list[dict[str, Any]]:
    """Three 300 ms words from each run's start, 100 ms apart."""
    return [
        {
            "word": word,
            "start_ms": start + 400 * index,
            "end_ms": start + 400 * index + 300,
        }
        for start, run in runs
        for index, word in enumerate(run.split("|"))
    ]


RUNS = (
    (100, "Opening| words| here"),
    (3000, " Carol| speaks| briefly"),
    (5900, " Then| someone| else"),
)


def _decide(start_ms: int, end_ms: int) -> LiveLabelDecision:
    """Live speaker 1 for the first two runs, live speaker 2 for the last."""
    return LiveLabelDecision(ONE if start_ms < 5000 else TWO, True, 0.9)


def _final_segment(runs: tuple[tuple[int, str], ...], speaker: str) -> dict[str, Any]:
    words = [
        {
            "word": word["word"],
            "start": word["start_ms"] / 1000,
            "end": word["end_ms"] / 1000,
        }
        for word in _runs(*runs)
    ]
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "speaker": speaker,
        "text": reconstruct_text_from_words(words),
        "words": words,
    }


def test_an_edited_line_of_a_settled_capture_leaves_processing_on_a_speaker(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = {
            "version": 1,
            "eligible": True,
            "generation": "edited-line-test",
            "capture_epoch": 0,
            "sealed": False,
            "phase": "live",
            "policy": {"enabled": True},
        }
        session.add(transcript)
        session.commit()
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 0, end_ms=8000),
            words=_runs(*RUNS),
            decide_speaker=_decide,
        ).accepted
        session.commit()
        assert close_live_settling(session, recording_id=recording_id)
        session.commit()
        live = serialize_canonical_utterances(session, recording_id)
        assert [line["speaker"] for line in live] == [ONE, ONE, TWO]
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=live[1]["id"],
            text="Carol speaks, edited",
        )
        session.commit()
        # One final segment joins the first two runs; the last is its own.
        final = [
            _final_segment(RUNS[:2], "SPEAKER_00"),
            _final_segment(RUNS[2:], "SPEAKER_01"),
        ]
        recording = session.get(Recording, recording_id)
        speaker_assignment.assign_and_identify_speakers(
            SimpleNamespace(
                session=session, merged_config={}, recording_id=recording_id
            ),
            recording,
            final,
            None,
        )
        finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=final
        )
        refresh_transcript_projection_from_canonical(session, recording_id)
        session.commit()
        session.expunge_all()
        lines = serialize_canonical_utterances(session, recording_id)
    assert [
        (line["text"], line["speaker"], line["text_manually_edited"]) for line in lines
    ] == [
        ("Opening words here", "SPEAKER_00", False),
        ("Carol speaks, edited", "SPEAKER_00", True),
        ("Then someone else", "SPEAKER_01", False),
    ]
    assert lines[1]["recording_speaker_id"] == lines[0]["recording_speaker_id"]
    assert lines[1]["recording_speaker_id"] is not None
    assert not lines[1]["speaker_manually_edited"]
