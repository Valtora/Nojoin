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
    settle_with_k,
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
from backend.worker.tasks.final_segments import combine_and_consolidate_segments


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


@pytest.mark.parametrize("short_first", [True, False])
@pytest.mark.parametrize(
    "pause_s,gap_s,count",
    [
        (0.5, 0.499, 1),
        (0.5, 0.5, 1),
        (0.5, 0.8, 2),
        (1.0, 0.8, 1),
        (1.0, 1.0, 1),
        (1.0, 1.001, 2),
    ],
)
def test_worker_final_pause_survives_short_segment_folding(
    short_first: bool, pause_s: float, gap_s: float, count: int
) -> None:
    first_end = 0.08 if short_first else 0.6
    words = [
        {"word": " Yes", "start": 0.0, "end": first_end},
        {
            "word": " later",
            "start": first_end + gap_s,
            "end": first_end + gap_s + (0.6 if short_first else 0.08),
        },
    ]
    final = combine_and_consolidate_segments(
        {"segments": [{"words": words}]},
        FakeDiarization([(0.0, 10.0, "A")]),
        enable_diarization=True,
        recording_id=1,
        config={"line_break_pause_s": pause_s},
    )
    assert len(final) == count
    assert [word for segment in final for word in segment["words"]] == words
    assert " ".join(segment["text"] for segment in final) == "Yes later"
    assert final[0]["start"] == 0.0
    assert final[-1]["end"] == words[-1]["end"]


def test_worker_folding_uses_larger_pause_without_affecting_later_calls() -> None:
    words = [
        {"word": " Yes", "start": 0.0, "end": 0.08},
        {"word": " later", "start": 1.28, "end": 1.88},
    ]
    for pause_s, count in [(0.5, 2), (2.0, 1), (0.5, 2)]:
        final = combine_and_consolidate_segments(
            {"segments": [{"words": words}]},
            FakeDiarization([(0.0, 0.08, "A"), (1.28, 1.88, "B")]),
            enable_diarization=True,
            recording_id=1,
            config={"line_break_pause_s": pause_s, "word_flip_max_duration_s": 0.0},
        )
        assert len(final) == count
        assert [word for segment in final for word in segment["words"]] == words
        assert final[-1]["speaker"] == "B"


@pytest.mark.parametrize("pause_s", [0.5, 2.0])
def test_worker_without_diarization_keeps_raw_asr_segment(pause_s: float) -> None:
    words = [
        {"word": " Yes", "start": 0.0, "end": 0.08, "confidence": 0.9},
        {"word": " later", "start": 0.88, "end": 1.5, "confidence": 0.8},
    ]
    transcription = {
        "segments": [
            {
                "id": "raw-asr",
                "start": 0.0,
                "end": 1.5,
                "text": " Yes later ",
                "words": words,
            }
        ]
    }
    final = combine_and_consolidate_segments(
        transcription,
        None,
        enable_diarization=False,
        recording_id=1,
        config={"line_break_pause_s": pause_s},
    )
    assert final == [
        {
            "id": "raw-asr",
            "start": 0.0,
            "end": 1.5,
            "text": "Yes later",
            "speaker": "UNKNOWN",
            "overlapping_speakers": [],
            "words": words,
        }
    ]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("pause_s", [0.5, 2.0])
@pytest.mark.parametrize("combination_empty", [False, True])
def test_worker_raw_asr_fallback_keeps_default_folding(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    pause_s: float,
    combination_empty: bool,
) -> None:
    words = [
        {"word": " Yes", "start": 0.0, "end": 0.08},
        {"word": " later", "start": 0.88, "end": 1.5},
    ]
    transcription = {
        "segments": [
            {
                "id": f"raw-{index}",
                "start": word["start"],
                "end": word["end"],
                "text": word["word"],
                "words": [word],
            }
            for index, word in enumerate(words)
        ]
    }
    turns = None
    if combination_empty:
        turns = FakeDiarization([(0.0, 2.0, "A")])
        monkeypatch.setattr(
            "backend.utils.transcript_utils.combine_transcription_diarization",
            lambda *_args, **_kwargs: [],
        )
    default = combine_and_consolidate_segments(
        transcription,
        turns,
        enable_diarization=enabled,
        recording_id=1,
    )
    configured = combine_and_consolidate_segments(
        transcription,
        turns,
        enable_diarization=enabled,
        recording_id=1,
        config={"line_break_pause_s": pause_s},
    )
    assert configured == default
    assert len(configured) == 1
    assert configured[0]["text"] == "Yes later"
    assert configured[0]["words"] == words


@pytest.mark.parametrize("shorts_speaker", ["A", "B"])
def test_worker_short_chain_extends_previous_without_crossing_pause(
    shorts_speaker: str,
) -> None:
    words = [
        {"word": " First", "start": 0.0, "end": 0.6},
        {"word": " yes", "start": 0.8, "end": 0.88},
        {"word": " so", "start": 1.08, "end": 1.16},
        {"word": " later", "start": 1.96, "end": 2.56},
    ]
    turns = FakeDiarization(
        [
            (0.0, 0.6, "A"),
            (0.8, 0.88, shorts_speaker),
            (1.08, 1.16, "C"),
            (1.96, 2.56, "D"),
        ]
    )
    final = combine_and_consolidate_segments(
        {"segments": [{"words": words}]},
        turns,
        enable_diarization=True,
        recording_id=1,
        config={"line_break_pause_s": 0.5, "word_flip_max_duration_s": 0.0},
    )
    assert [(segment["start"], segment["end"]) for segment in final] == [
        (0.0, 1.16),
        (1.96, 2.56),
    ]
    assert [word for segment in final for word in segment["words"]] == words


def test_worker_short_fold_excludes_same_speaker_beyond_pause() -> None:
    words = [
        {"word": " First", "start": 0.0, "end": 0.6},
        {"word": " yes", "start": 1.4, "end": 1.48},
        {"word": " later", "start": 1.68, "end": 2.28},
    ]
    final = combine_and_consolidate_segments(
        {"segments": [{"words": words}]},
        FakeDiarization([(0.0, 1.48, "A"), (1.68, 2.28, "B")]),
        enable_diarization=True,
        recording_id=1,
        config={"line_break_pause_s": 0.5},
    )
    assert [(segment["speaker"], segment["text"]) for segment in final] == [
        ("A", "First"),
        ("B", "yes later"),
    ]
    assert [word for segment in final for word in segment["words"]] == words


def test_worker_invalid_pause_warns_once_and_keeps_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transcription = {
        "segments": [
            {
                "words": [
                    {"word": " Yes", "start": 0.0, "end": 0.08},
                    {"word": " later", "start": 0.88, "end": 1.5},
                ]
            }
        ]
    }
    turns = FakeDiarization([(0.0, 2.0, "A")])
    default = combine_and_consolidate_segments(
        transcription,
        turns,
        enable_diarization=True,
        recording_id=1,
    )
    invalid = combine_and_consolidate_segments(
        transcription,
        turns,
        enable_diarization=True,
        recording_id=1,
        config={"line_break_pause_s": 0.0},
    )
    assert invalid == default
    assert (
        len(
            [
                record
                for record in caplog.records
                if "Ignoring invalid line_break_pause_s" in record.message
            ]
        )
        == 1
    )


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settle_with_k(monkeypatch, 1)
    recording_id = _seed(settling_engine)
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
    monkeypatch: pytest.MonkeyPatch,
    pause_s: float,
    count: int,
    configured_on: str,
) -> None:
    settle_with_k(monkeypatch, 99)
    recording_id = _seed(settling_engine)
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
