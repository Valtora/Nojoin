"""Live line pauses match final word grouping without changing lock requirements."""

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.tests.test_live_settling_publication import (
    _active,
    _request,
    _seed,
    _words,
)
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.tests.test_transcript_utils import FakeDiarization
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)
from backend.utils.canonical_pipeline.live_settling import (
    expire_live_settling,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.live_settling_lines import (
    LineBoundaries,
    partition_words,
)
from backend.utils.canonical_pipeline.live_settling_projection import SETTLING_KEY
from backend.utils.transcript_utils import combine_transcription_diarization


def _word(index: int, start: int, end: int, *, locked: bool = True) -> dict[str, Any]:
    return {
        "id": str(index),
        "word": f" word{index}",
        "start_ms": start,
        "end_ms": end,
        "locked": locked,
    }


@pytest.mark.parametrize("gap_ms,count", [(500, 1), (1000, 1), (1001, 2), (1200, 2)])
def test_live_and_final_use_the_same_default_word_gap(gap_ms: int, count: int) -> None:
    words = [_word(0, 0, 500), _word(1, 500 + gap_ms, 1000 + gap_ms)]
    decisions = [LiveLabelDecision("A", True, 1.0)] * 2
    live = partition_words(words, decisions)
    final = combine_transcription_diarization(
        {
            "segments": [
                {
                    "words": [
                        {
                            "word": word["word"],
                            "start": word["start_ms"] / 1000,
                            "end": word["end_ms"] / 1000,
                        }
                        for word in words
                    ]
                }
            ]
        },
        FakeDiarization([(0.0, 10.0, "A")]),
    )
    assert len(live) == len(final) == count
    assert [[word["word"] for word in part.words] for part in live] == [
        [word["word"] for word in segment["words"]] for segment in final
    ]


@pytest.mark.parametrize(
    "pause_s,gap_s,count",
    [
        (0.5, 0.5, 1),
        (0.5, 0.6, 2),
        (2.0, 1.2, 1),
        (2.0, 2.001, 2),
    ],
)
def test_final_word_grouping_reads_each_processing_config(
    pause_s: float,
    gap_s: float,
    count: int,
) -> None:
    transcription = {
        "segments": [
            {
                "words": [
                    {"word": " One", "start": 0.0, "end": 0.5},
                    {"word": " two", "start": 0.5 + gap_s, "end": 1.0 + gap_s},
                ]
            }
        ]
    }
    turns = FakeDiarization([(0.0, 10.0, "A")])
    original = combine_transcription_diarization(transcription, turns)
    configured = combine_transcription_diarization(
        transcription, turns, config={"line_break_pause_s": pause_s}
    )
    assert len(configured) == count
    assert combine_transcription_diarization(transcription, turns) == original
    assert [word for segment in configured for word in segment["words"]] == [
        word for segment in original for word in segment["words"]
    ]


def test_invalid_final_pause_uses_default_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transcription = {
        "segments": [
            {
                "words": [
                    {"word": f" word{index}", "start": float(index), "end": index + 0.5}
                    for index in range(10)
                ]
            }
        ]
    }
    turns = FakeDiarization([(0.0, 10.0, "A")])
    assert combine_transcription_diarization(
        transcription, turns, config={"line_break_pause_s": 0.0}
    ) == combine_transcription_diarization(transcription, turns)
    warnings = [
        record for record in caplog.records if "line_break_pause_s" in record.message
    ]
    assert len(warnings) == 1


def test_speaker_cap_and_retained_barriers_still_break_lines() -> None:
    words = [_word(index, index * 500, (index + 1) * 500) for index in range(62)]
    same = [LiveLabelDecision("A", True, 1.0)] * len(words)
    capped = partition_words(words, same)
    assert [len(part.words) for part in capped] == [60, 2]
    assert capped[0].closed and capped[0].line_locked
    changed = partition_words(words[:2], [same[0], LiveLabelDecision("B", True, 1.0)])
    assert [part.speaker for part in changed] == ["A", "B"]
    barrier = partition_words(words[:2], same[:2], barriers=[500])
    retained = partition_words(
        words[:2], same[:2], boundaries=LineBoundaries(closed_ends={"0": 500})
    )
    assert len(barrier) == len(retained) == 2
    assert barrier[0].boundary_locked and retained[0].boundary_locked


@pytest.mark.parametrize(
    "words_locked,speaker_locked,next_locked,expected",
    [
        (True, True, True, True),
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
    ],
)
def test_closed_line_still_requires_boundary_words_and_speaker_locks(
    words_locked: bool,
    speaker_locked: bool,
    next_locked: bool,
    expected: bool,
) -> None:
    parts = partition_words(
        [
            _word(0, 0, 500, locked=words_locked),
            _word(1, 1700, 2000, locked=next_locked),
        ],
        [
            LiveLabelDecision("A", speaker_locked, 1.0),
            LiveLabelDecision("A", True, 1.0),
        ],
    )
    assert parts[0].closed
    assert parts[0].line_locked is expected
    assert not parts[-1].line_locked


def test_tail_waits_for_more_than_one_second_of_confirmed_silence(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine, k=1)
    with Session(settling_engine) as session:
        for step in range(2):
            publish_live_settling(
                session, request=_request(session, recording_id, step), words=_words()
            )
            session.commit()
        for step, silence_ms in enumerate((500, 1000, 1001), start=2):
            request = replace(
                _request(session, recording_id, step, end_ms=850 + silence_ms),
                silence_start_ms=850,
            )
            assert expire_live_settling(session, request=request).accepted
            session.commit()
            state = _active(session, recording_id)[0].confidence_payload[SETTLING_KEY]
            assert state["line_closed"] is (silence_ms > 1000)
            assert state["line_locked"] is (silence_ms > 1000)


@pytest.mark.parametrize("pause_s,count", [(0.5, 2), (2.0, 1)])
@pytest.mark.parametrize("configured_on", ["publish", "expire"])
def test_recording_pause_survives_speaker_only_reprojection(
    settling_engine: Engine,
    pause_s: float,
    count: int,
    configured_on: str,
) -> None:
    recording_id = _seed(settling_engine, k=99)
    words = [
        {"word": "One", "start_ms": 0, "end_ms": 500},
        {"word": " two", "start_ms": 1300, "end_ms": 1800},
    ]
    with Session(settling_engine) as session:
        request = _request(session, recording_id, 0)
        if configured_on == "publish":
            request = replace(request, line_break_pause_s=pause_s)
        assert publish_live_settling(session, request=request, words=words).accepted
        session.commit()
        if configured_on == "expire":
            assert expire_live_settling(
                session,
                request=replace(
                    _request(session, recording_id, 1), line_break_pause_s=pause_s
                ),
            ).accepted
            session.commit()
        assert len(_active(session, recording_id)) == count
        assert apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=lambda _start, _end: LiveLabelDecision("B", False, None),
        ).changed
        session.commit()
        rows = _active(session, recording_id)
        assert len(rows) == count
        assert [row.speaker_label for row in rows] == ["B"] * count
        assert " ".join(row.text for row in rows) == "One two"
